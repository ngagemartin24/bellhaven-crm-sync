"""The review app (DESIGN 13): one proposal at a time, its evidence beside it, and nothing written without a click.

Writes happen only on POST, each form carries an anti-forgery token and the version of the card it showed, and
only one app process may run against a decisions file (10.4).
"""

import fcntl
import hmac
import json
import secrets
from collections.abc import Callable
from datetime import date, datetime
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import apply, rules
from .config import Config
from .ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]
OPEN_STATUSES = ("failed", "needs-reconciliation", "applying", "pending", "blocked")
CLOSED = ("done", "rejected", "acknowledged", "superseded", "discarded")
STATUS_WORDS = {
    "pending": "Waiting for review",
    "blocked": "Held until a complete run",
    "applying": "Writing now",
    "failed": "Stopped",
    "needs-reconciliation": "Checking whether a create landed",
    "done": "Approved and written",
    "rejected": "Rejected",
    "acknowledged": "Acknowledged",
    "superseded": "Replaced by a newer proposal",
    "discarded": "Discarded",
}
FIELD_WORDS = {
    "parent_id": "Parent",
    "name": "Name",
    "status": "Status",
    "phone": "Phone",
    "billing_street": "Street",
    "billing_city": "City",
    "billing_state": "State",
    "billing_zip": "ZIP",
    "care_type": "Care type",
    "duplicate_of_account": "Duplicate of",
    "chow_current_account": "Current account (CHOW)",
    "account_id": "Account",
    "note": "Note",
    "title": "Title",
    "is_active": "Active",
}
_HELD_LOCKS: dict[str, Any] = {}


class AppLockedError(Exception):
    """Another review app already holds this decisions file."""


def _lock(db_path: Path) -> None:
    key = str(Path(db_path).resolve())
    if key in _HELD_LOCKS:
        raise AppLockedError(f"Another review app is already running on {db_path}")
    handle = open(f"{db_path}.lock", "w")  # noqa: SIM115 - held open for the life of the process
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        handle.close()
        raise AppLockedError(f"Another review app is already running on {db_path}") from e
    _HELD_LOCKS[key] = handle


def _money(v) -> str:
    return f"${rules.money(v):,}"


def _when(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def _show(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class View:
    """Turns a card and its evidence into what the screen shows."""

    def __init__(self, cfg: Config, ledger: Ledger):
        self.cfg, self.ledger = cfg, ledger

    def crm_link(self, account_id: str | None) -> str | None:
        if not account_id or not self.cfg.token:
            return None
        return self.cfg.crm_browser_url.format(token=quote(self.cfg.token), account_id=quote(account_id))

    def names(self, card) -> dict[str, str]:
        out = {}
        for v in card.evidence.get("crm") or []:
            out[v["account_id"]] = v.get("name") or v["account_id"]
            if v.get("parent_id"):
                out.setdefault(v["parent_id"], v.get("parent_name") or v["parent_id"])
        for c in card.evidence.get("candidates_named", []):
            out.setdefault(c["account_id"], c["name"])
        if card.evidence.get("site"):
            out.setdefault("__site__", card.evidence["site"]["name"])
        return out

    def label(self, value, names: dict[str, str], field: str | None = None) -> str:
        if isinstance(value, dict) and "$created" in value:
            return "the new account"
        if isinstance(value, dict) and "created_for" in value:
            return "the new account"
        if field in ("parent_id", "duplicate_of_account", "chow_current_account", "account_id") and value:
            name = names.get(value)
            if name is None and value == self.operator_id:
                name = self.cfg.operator_parent_name
            return f"{name} ({value[-6:]})" if name else str(value)
        return _show(value)

    @cached_property
    def operator_id(self) -> str | None:
        for c in self.ledger.cards():
            for s in c.steps:
                if s.method == "POST" and s.path == "/accounts":
                    return s.body.get("parent_id")
        return None

    def changes(self, card) -> list[dict]:
        """One row per field written: the record, the value as reviewed and the value after (from the steps)."""
        names = self.names(card)
        rows = []
        for s in card.steps:
            kind, _, rid = s.path.strip("/").partition("/")
            if s.method == "POST":
                record = "New account" if kind == "accounts" else "New contact"
                for field, value in s.body.items():
                    if field in ("note",) or (field == "account_id" and kind == "contacts"):
                        continue
                    rows.append(
                        {
                            "record": record,
                            "field": FIELD_WORDS.get(field, field),
                            "before": "—",
                            "after": self.label(value, names, field),
                            "new": True,
                        }
                    )
                if s.body.get("note"):
                    rows.append(
                        {
                            "record": record,
                            "field": "Note",
                            "before": "—",
                            "after": s.body["note"],
                            "note": True,
                            "new": True,
                        }
                    )
                continue
            if kind == "contacts":
                record = f"Contact {self._contact_name(card, rid) or rid[-6:]}"
            else:
                record = names.get(rid, rid[-6:])
            reviewed = s.expect.get("fields", {})
            for field, value in s.body.items():
                rows.append(
                    {
                        "record": record,
                        "field": FIELD_WORDS.get(field, field),
                        "before": self.label(reviewed.get(field), names, field),
                        "after": self.label(value, names, field),
                    }
                )
            if s.note_append:
                rows.append(
                    {"record": record, "field": "Note", "before": "", "after": s.note_append, "note": True}
                )
        return rows

    def change_groups(self, card) -> list[dict]:
        """The rows of changes(), grouped per record in step order: a new record shows only its values."""
        groups: list[dict] = []
        for r in self.changes(card):
            if not groups or groups[-1]["record"] != r["record"]:
                groups.append({"record": r["record"], "new": bool(r.get("new")), "rows": []})
            groups[-1]["rows"].append(r)
        return groups

    def _contact_name(self, card, contact_id: str) -> str | None:
        for v in card.evidence.get("crm") or []:
            for c in v.get("contacts", []):
                if c.get("contact_id") == contact_id:
                    return c.get("name")
        return None

    def writes(self, card) -> list[dict]:
        out = []
        for s in card.steps:
            body = dict(s.body)
            if s.note_append:
                body["note"] = "(current note) + " + s.note_append
            out.append(
                {"n": s.n, "line": f"{s.method} {s.path}", "body": json.dumps(body, indent=2, default=str)}
            )
        return out

    def comparison(self, card) -> dict | None:
        site = card.evidence.get("site")
        crm = card.evidence.get("crm") or []
        if not site and not crm:
            return None

        def row(label, site_value, crm_values):
            return {
                "label": label,
                "site": _show(site_value) if site else None,
                "crm": [
                    {"value": _show(v), "differs": bool(site) and _norm(v) != _norm(site_value)}
                    for v in crm_values
                ],
            }

        rows = [
            row("Name", site and site["name"], [a.get("name") for a in crm]),
            row("Street", site and site["street"], [a.get("billing_street") for a in crm]),
            row(
                "City",
                site and f"{site['city']}, {site['state']} {site['zip']}",
                [f"{a.get('billing_city')}, {a.get('billing_state')} {a.get('billing_zip')}" for a in crm],
            ),
            row("Phone", site and site["phone"], [a.get("phone") for a in crm]),
            row("Care", site and ", ".join(site.get("offerings") or []), [a.get("care_type") for a in crm]),
            row(
                "Administrator",
                site and site.get("administrator"),
                [
                    ", ".join(f"{c['name']} ({c.get('title') or 'no title'})" for c in a.get("contacts", []))
                    or None
                    for a in crm
                ],
            ),
        ]
        for r in rows[4:]:
            for c in r["crm"]:
                c["differs"] = False  # care labels and contact lists are compared in the signals, not here
        owners = [
            {
                "parent": a.get("parent_name") or "No parent",
                "status": a.get("status"),
                "money": f"{_money(a.get('lifetime_revenue'))} revenue · {_money(a.get('outstanding_ar'))} AR",
                "id": a["account_id"],
                "link": self.crm_link(a["account_id"]),
            }
            for a in crm
        ]
        return {"site": site, "crm": crm, "rows": rows, "owners": owners}

    def alternatives(self, card) -> list:
        if not card.alt_group:
            return []
        return [
            c
            for c in self.ledger.cards()
            if c.alt_group == card.alt_group
            and c.id != card.id
            and c.status not in ("superseded", "discarded")
        ]


def _norm(v) -> str:
    return " ".join(str(v or "").lower().replace(".", "").replace(",", "").split())


def create_app(
    cfg: Config, ledger: Ledger, crm_factory: Callable[[], Any], today: Callable[[], date]
) -> FastAPI:
    _lock(ledger.path)
    recovered = ledger.recover_interrupted()
    app = FastAPI(title="Bellhaven CRM sync", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.filters["fromjson"] = lambda v: json.loads(v) if v else {}

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        view = View(cfg, ledger)
        token = request.cookies.get("bh_csrf") or secrets.token_urlsafe(32)
        flash = unquote(request.cookies.get("bh_flash") or "") or None
        flash_kind = request.cookies.get("bh_flash_kind", "ok")
        run = ledger.latest_run()
        open_cards = [c for c in ledger.cards() if c.status in OPEN_STATUSES]
        response = templates.TemplateResponse(
            request,
            name,
            {
                "csrf": token,
                "flash": flash,
                "flash_kind": flash_kind,
                "run": run,
                "when": _when,
                "queue_count": sum(1 for c in open_cards if c.status != "blocked"),
                "open_count": len(open_cards),
                "recovered": recovered,
                "status_words": STATUS_WORDS,
                "money": _money,
                "view": view,
                "cfg": cfg,
                **ctx,
            },
        )
        if "bh_csrf" not in request.cookies:
            response.set_cookie("bh_csrf", token, httponly=True, samesite="strict", secure=False)
        if flash:
            response.delete_cookie("bh_flash")
            response.delete_cookie("bh_flash_kind")
        return response

    def ordered(cards):
        return sorted(
            cards,
            key=lambda c: (
                0 if c.status in ("failed", "needs-reconciliation", "applying") else 1,
                0 if (c.flag or c.evidence.get("warning")) else 1,
                rules.SECTIONS.index(c.section) if c.section in rules.SECTIONS else 99,
                c.title or "",
                c.id,
            ),
        )

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request, card: str | None = None, location: str | None = None):
        open_cards = [c for c in ledger.cards() if c.status in OPEN_STATUSES]
        locations = sorted(
            {(c.location_slug, c.title) for c in open_cards if c.location_slug}, key=lambda x: x[1]
        )
        shown = ordered([c for c in open_cards if not location or c.location_slug == location])
        sections = []
        for name in rules.SECTIONS:
            items = [c for c in shown if c.section == name]
            if items:
                sections.append({"name": name, "cards": items})
        attention = [c for c in shown if c.status in ("failed", "needs-reconciliation", "applying")]
        selected = None
        if card:
            try:
                selected = ledger.card(card)
            except KeyError:
                selected = None
        if selected is None and shown:
            selected = shown[0]
        return page(
            request,
            "queue.html",
            sections=sections,
            selected=selected,
            attention=attention,
            location=location,
            locations=locations,
            shown=shown,
        )

    @app.get("/locations", response_class=HTMLResponse)
    def locations_page(request: Request, verdict: str | None = None):
        verdicts = ledger.verdicts()
        cards = ledger.cards()
        by_set: dict[str, list] = {}
        for c in cards:
            by_set.setdefault(c.change_set, []).append(c)
        counts: dict[str, int] = {}
        for v in verdicts:
            counts[v.verdict] = counts.get(v.verdict, 0) + 1
        rows = []
        for v in verdicts:
            if verdict and v.verdict != verdict:
                continue
            fixes = [c for s in v.change_sets for c in by_set.get(s, [])]
            fixes = [c for c in fixes if c.status not in ("superseded", "discarded")]
            rows.append(
                {"v": v, "fixes": fixes, "links": [(a, View(cfg, ledger).crm_link(a)) for a in v.account_ids]}
            )
        order = rules.VERDICTS
        return page(
            request,
            "locations.html",
            rows=rows,
            counts=counts,
            order=order,
            current=verdict,
            total=len(verdicts),
        )

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        return page(request, "history.html", entries=ledger.history(), field_words=FIELD_WORDS)

    def next_card(after) -> str | None:
        shown = ordered([c for c in ledger.cards() if c.status in OPEN_STATUSES and c.status != "blocked"])
        if not shown:
            return None
        same = [c for c in shown if c.section == after.section and c.id != after.id]
        return (same or [c for c in shown if c.id != after.id] or shown)[0].id

    def act(request: Request, card_id: str, csrf: str, version: str, action: str, run: Callable):
        cookie = request.cookies.get("bh_csrf") or ""
        if not cookie or not hmac.compare_digest(cookie, csrf or ""):
            raise HTTPException(403, "This form has expired. Reload the page and try again.")
        try:
            card = ledger.card(card_id)
        except KeyError as e:
            raise HTTPException(404, "No such proposal") from e
        if card.version != version:
            raise HTTPException(409, "This card changed since you opened it. Reload.")
        result = run(card)
        if result.status == "refused":
            raise HTTPException(409, result.message)
        words = {
            "approve": "Approved",
            "retry": "Retried",
            "check": "Checked",
            "discard": "Discarded",
            "reject": "Rejected",
            "acknowledge": "Acknowledged",
        }[action]
        if result.status == "done" and action in ("approve", "retry", "check"):
            message, kind = f"{words}: {result.done} of {result.total} writes done", "ok"
        elif result.status == "done":
            message, kind = f"{words}: {result.message}", "ok"
        else:
            message, kind = result.message, "bad"
        target = card.id if result.status != "done" else (next_card(card) or card.id)
        response = RedirectResponse(f"/?{urlencode({'card': target})}#card-{target}", status_code=303)
        response.set_cookie(
            "bh_flash", quote(f"{card.title}: {message}"), max_age=30, httponly=True, samesite="strict"
        )
        response.set_cookie("bh_flash_kind", kind, max_age=30, httponly=True, samesite="strict")
        return response

    @app.post("/cards/{card_id}/approve")
    def approve_card(request: Request, card_id: str, csrf: str = Form(""), version: str = Form("")):
        return act(
            request,
            card_id,
            csrf,
            version,
            "approve",
            lambda c: apply.approve(c.id, version, ledger, crm_factory()),
        )

    @app.post("/cards/{card_id}/reject")
    def reject_card(
        request: Request, card_id: str, csrf: str = Form(""), version: str = Form(""), note: str = Form("")
    ):
        return act(
            request, card_id, csrf, version, "reject", lambda c: apply.reject(c.id, version, ledger, note)
        )

    @app.post("/cards/{card_id}/acknowledge")
    def acknowledge_card(request: Request, card_id: str, csrf: str = Form(""), version: str = Form("")):
        return act(
            request, card_id, csrf, version, "acknowledge", lambda c: apply.acknowledge(c.id, version, ledger)
        )

    @app.post("/cards/{card_id}/retry")
    def retry_card(request: Request, card_id: str, csrf: str = Form(""), version: str = Form("")):
        return act(
            request, card_id, csrf, version, "retry", lambda c: apply.retry(c.id, ledger, crm_factory())
        )

    @app.post("/cards/{card_id}/check-again")
    def check_card(request: Request, card_id: str, csrf: str = Form(""), version: str = Form("")):
        return act(
            request, card_id, csrf, version, "check", lambda c: apply.check_again(c.id, ledger, crm_factory())
        )

    @app.post("/cards/{card_id}/discard")
    def discard_card(request: Request, card_id: str, csrf: str = Form(""), version: str = Form("")):
        return act(request, card_id, csrf, version, "discard", lambda c: apply.discard(c.id, ledger))

    @app.exception_handler(HTTPException)
    def refused(request: Request, exc: HTTPException):
        response = templates.TemplateResponse(
            request,
            "message.html",
            {"status": exc.status_code, "message": exc.detail},
            status_code=exc.status_code,
        )
        return response

    return app
