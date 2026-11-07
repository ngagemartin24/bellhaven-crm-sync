"""Carry out an approved card, one checked step at a time (DESIGN 11). The only code that writes to the CRM.

Each step carries what it expects to find (fixed when the card was built from the data you reviewed). Before the
first write every expectation about an existing record is checked; before each step its own record is read again.
Equal to what was reviewed: write. Already at the target: done, no write. Anything else: stop and say what changed.
A create whose answer never arrived is never sent again; Check again looks for it instead.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from .crm import CrmError
from .ledger import Ledger
from .models import Step
from .normalize import full_address, same_person


@dataclass(frozen=True)
class ApplyResult:
    status: str  # done | failed | needs-reconciliation | refused
    done: int
    total: int
    message: str


def utcnow() -> datetime:
    return datetime.now(UTC)


class _Stop(Exception):
    """Something changed since review; nothing more is written."""


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, int | float) or isinstance(b, int | float):
        try:
            return float(a or 0) == float(b or 0)
        except (TypeError, ValueError):
            return False
    return (a or "") == (b or "")


def _show(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "(empty)" if v in (None, "") else str(v)


def _resolve(value, created: dict[int, str]):
    if isinstance(value, dict) and set(value) == {"$created"}:
        return created.get(value["$created"])
    if isinstance(value, dict):
        return {k: _resolve(v, created) for k, v in value.items()}
    return value


def _append(note: str | None, line: str) -> str:
    note = (note or "").rstrip()
    return f"{note}\n{line}" if note else line


def _refused(card, message: str) -> ApplyResult:
    return ApplyResult("refused", _done_count(card), len(card.steps), message)


def _done_count(card) -> int:
    op = card.operation
    return sum(1 for s in op["steps"] if s["status"] == "done") if op else 0


def _not_now(card) -> str:
    if card.status in ("done", "rejected", "acknowledged", "superseded", "discarded"):
        return "Already decided"
    return "Not available now"


# ---- reading records ------------------------------------------------------------------------------
def _target(step: Step) -> tuple[str, str]:
    kind, rid = step.path.strip("/").split("/", 1)
    return kind, rid


def _read(crm, step: Step) -> dict:
    kind, rid = _target(step)
    return crm.get_account(rid) if kind == "accounts" else crm.get_contact(rid)


def _at_target(step: Step, rec: dict, created: dict[int, str]) -> bool:
    body = _resolve(step.body, created)
    if any(v is None for v in body.values()):
        return False
    if step.note_append and step.note_append not in (rec.get("note") or ""):
        return False
    return all(_same(rec.get(k), v) for k, v in body.items())


def _changed(step: Step, rec: dict, m: int) -> str | None:
    for field, reviewed in step.expect.get("fields", {}).items():
        if not _same(rec.get(field), reviewed):
            return (
                f"Changed since review at step {step.n} of {m}: {field} is now {_show(rec.get(field))}, "
                f"not {_show(reviewed)} as reviewed"
            )
    return None


def _absent_problem(step: Step, crm, created: dict[int, str], m: int) -> str | None:
    """A create's expectation: nothing like it exists already (11 step 2)."""
    if "absent_account" in step.expect:
        want = step.expect["absent_account"]
        key = full_address(
            want["billing_street"], want["billing_city"], want["billing_state"], want["billing_zip"]
        )
        for a in crm.list_accounts():
            if (
                a.get("status") == "Active"
                and a.get("parent_id") == want["parent_id"]
                and full_address(
                    a.get("billing_street"),
                    a.get("billing_city"),
                    a.get("billing_state"),
                    a.get("billing_zip"),
                )
                == key
            ):
                return (
                    f"Changed since review at step {step.n} of {m}: an Active account under the operator "
                    f"already has this address: {a.get('name')} ({a['account_id']})"
                )
    if "absent_contact" in step.expect:
        want = _resolve(step.expect["absent_contact"], created)
        if want.get("account_id"):
            for c in crm.list_contacts():
                if (
                    c.get("account_id") == want["account_id"]
                    and c.get("is_active")
                    and same_person(c.get("name") or "", want["name"])
                ):
                    return (
                        f"Changed since review at step {step.n} of {m}: {want['name']} is already a contact "
                        "on that account"
                    )
    return None


# ---- the run ---------------------------------------------------------------------------------------
def _run(op_id: int, ledger: Ledger, crm) -> ApplyResult:
    op = ledger.operation(op_id)
    card = ledger.card(op["card_id"])
    steps, m = card.steps, len(card.steps)
    log = {s["n"]: s for s in op["steps"]}
    created = {n: s["created_id"] for n, s in log.items() if s["status"] == "done" and s["created_id"]}
    todo = [s for s in steps if log.get(s.n, {}).get("status") != "done"]
    done = m - len(todo)
    current: Step | None = None
    try:
        # 11 step 3: every expectation about records that already exist, before the first write
        for s in todo:
            current = s
            if s.method == "PATCH":
                rec = _read(crm, s)
                if not _at_target(s, rec, created):
                    problem = _changed(s, rec, m)
                    if problem:
                        raise _Stop(problem)
            else:
                problem = _absent_problem(s, crm, created, m)
                if problem:
                    raise _Stop(problem)
        for s in todo:
            current = s
            if s.method == "PATCH":
                _patch(s, op_id, ledger, crm, created, m)
            else:
                _post(s, op_id, ledger, crm, created, m)
            done += 1
    except _Stop as e:
        ledger.finish_operation(op_id, "failed", str(e))
        return ApplyResult("failed", done, m, str(e))
    except CrmError as e:
        message = f"Failed at step {current.n} of {m}: {e.message}"
        if current.n in {s["n"] for s in ledger.operation(op_id)["steps"]}:
            ledger.step_failed(op_id, current.n, e.status, e.message)
        ledger.finish_operation(op_id, "failed", message)
        return ApplyResult("failed", done, m, message)
    except requests.RequestException as e:
        step_log = {s["n"]: s for s in ledger.operation(op_id)["steps"]}
        if current.method == "POST" and current.n in step_log:
            message = (
                f"No answer to the create at step {current.n} of {m} ({type(e).__name__}); it may have "
                "landed. Check again looks for it; it is never sent twice"
            )
            ledger.step_unknown(op_id, current.n, message)
            ledger.finish_operation(op_id, "needs-reconciliation", message)
            return ApplyResult("needs-reconciliation", done, m, message)
        message = (
            f"Failed at step {current.n} of {m}: no answer from the CRM ({type(e).__name__}); "
            "Retry checks whether it landed"
        )
        if current.n in step_log:
            ledger.step_failed(op_id, current.n, None, message)
        ledger.finish_operation(op_id, "failed", message)
        return ApplyResult("failed", done, m, message)
    ledger.finish_operation(op_id, "done")
    return ApplyResult("done", m, m, f"Applied {m} step{'s' if m != 1 else ''}")


def _patch(step: Step, op_id: int, ledger: Ledger, crm, created: dict[int, str], m: int) -> None:
    rec = _read(crm, step)
    if _at_target(step, rec, created):
        ledger.step_intent(op_id, step.n, "PATCH", step.path, {}, None, step.expect)
        ledger.step_done(op_id, step.n, None, None, None, wrote=False)
        return
    problem = _changed(step, rec, m)
    if problem:
        raise _Stop(problem)
    body = _resolve(step.body, created)
    if step.note_append and step.note_append not in (rec.get("note") or ""):
        body = {**body, "note": _append(rec.get("note"), step.note_append)}
    ledger.step_intent(op_id, step.n, "PATCH", step.path, body, {k: rec.get(k) for k in body}, step.expect)
    kind, rid = _target(step)
    response = crm.patch_account(rid, body) if kind == "accounts" else crm.patch_contact(rid, body)
    missing = [k for k, v in body.items() if not _same(response.get(k), v)]
    if missing:
        raise CrmError(0, f"the CRM did not confirm {', '.join(missing)}")
    ledger.step_done(op_id, step.n, 200, {k: response.get(k) for k in body}, None)


def _post(step: Step, op_id: int, ledger: Ledger, crm, created: dict[int, str], m: int) -> None:
    problem = _absent_problem(step, crm, created, m)
    if problem:
        raise _Stop(problem)
    body = _resolve(step.body, created)
    ledger.step_intent(op_id, step.n, "POST", step.path, body, None, step.expect)
    response = crm.create_account(body) if step.path == "/accounts" else crm.create_contact(body)
    new_id = response.get("account_id") if step.path == "/accounts" else response.get("contact_id")
    if not new_id:
        raise CrmError(0, "the CRM answered without an id")
    created[step.n] = new_id
    ledger.step_done(op_id, step.n, 201, {"id": new_id}, new_id)


# ---- the actions -------------------------------------------------------------------------------------
def approve(card_id: str, version: str, ledger: Ledger, crm) -> ApplyResult:
    card = ledger.card(card_id)
    if card.status != "pending":
        return _refused(card, _not_now(card))
    if card.kind == "attention":
        return _refused(card, "Needs attention cards are acknowledged, not applied")
    if card.version != version:
        return _refused(card, "This card changed since you opened it; review it again")
    op_id = ledger.claim(card_id, version)
    if op_id is None:
        if card.alt_group:
            return _refused(card, "Another choice for this location is being applied")
        return _refused(card, "Already decided")
    return _run(op_id, ledger, crm)


def reject(card_id: str, version: str, ledger: Ledger, note: str = "") -> ApplyResult:
    card = ledger.card(card_id)
    if card.status != "pending":
        return _refused(card, _not_now(card))
    if not ledger.close_card(card_id, version, "rejected", note):
        return _refused(card, "This card changed since you opened it; review it again")
    return ApplyResult("done", 0, len(card.steps), "Rejected; it will not be proposed again")


def acknowledge(card_id: str, version: str, ledger: Ledger) -> ApplyResult:
    card = ledger.card(card_id)
    if card.status != "pending" or card.kind != "attention":
        return _refused(card, _not_now(card) if card.status != "pending" else "Only Needs attention cards")
    if not ledger.close_card(card_id, version, "acknowledged"):
        return _refused(card, "This card changed since you opened it; review it again")
    return ApplyResult("done", 0, 0, "Acknowledged")


def retry(card_id: str, ledger: Ledger, crm) -> ApplyResult:
    card = ledger.card(card_id)
    if card.status != "failed" or not card.operation:
        return _refused(card, _not_now(card))
    if not ledger.reopen_operation(card.operation["id"], "failed"):
        return _refused(card, "Not available now")
    return _run(card.operation["id"], ledger, crm)


def _identity(step: Step, created: dict[int, str]) -> dict:
    body = _resolve(step.body, created)
    if step.path == "/accounts":
        return {
            k: body.get(k)
            for k in ("name", "billing_street", "billing_city", "billing_state", "billing_zip", "parent_id")
        }
    return {k: body.get(k) for k in ("name", "title", "account_id")}


def check_again(card_id: str, ledger: Ledger, crm, now: Callable[[], datetime] = utcnow) -> ApplyResult:
    """11 step 5: look for the create whose answer never arrived; adopt exactly one match, never re-send."""
    card = ledger.card(card_id)
    if card.status != "needs-reconciliation" or not card.operation:
        return _refused(card, _not_now(card))
    op = card.operation
    m = len(card.steps)
    log = {s["n"]: s for s in op["steps"]}
    created = {n: s["created_id"] for n, s in log.items() if s["status"] == "done" and s["created_id"]}
    open_posts = [
        s
        for s in card.steps
        if s.method == "POST" and log.get(s.n, {}).get("status") in ("intent", "unknown")
    ]
    if not open_posts:
        if ledger.reopen_operation(op["id"], "needs-reconciliation"):
            return _run(op["id"], ledger, crm)
        return _refused(card, "Not available now")
    step = open_posts[0]
    want = _identity(step, created)
    taken = ledger.adopted_ids()
    try:
        records = crm.list_accounts() if step.path == "/accounts" else crm.list_contacts()
    except (CrmError, requests.RequestException) as e:
        return ApplyResult("needs-reconciliation", _done_count(card), m, f"Could not read the CRM: {e}")
    id_field = "account_id" if step.path == "/accounts" else "contact_id"
    hits = [
        r
        for r in records
        if r.get("created_by_candidate")
        and r[id_field] not in taken
        and all(_same(r.get(k), v) for k, v in want.items())
    ]
    if not hits:
        ledger.step_checked_empty(op["id"], step.n, now())
        return ApplyResult("needs-reconciliation", _done_count(card), m, "Not found yet")
    if len(hits) > 1:
        ids = ", ".join(r[id_field] for r in hits)
        return ApplyResult(
            "needs-reconciliation",
            _done_count(card),
            m,
            f"Several records could be this create ({ids}); a person must decide",
        )
    ledger.step_done(op["id"], step.n, None, {"adopted": hits[0][id_field]}, hits[0][id_field])
    if not ledger.reopen_operation(op["id"], "needs-reconciliation"):
        return _refused(card, "Not available now")
    return _run(op["id"], ledger, crm)


def discard(card_id: str, ledger: Ledger, now: Callable[[], datetime] = utcnow) -> ApplyResult:
    card = ledger.card(card_id)
    if card.status not in ("failed", "needs-reconciliation") or not card.operation:
        return _refused(card, _not_now(card))
    if any(s["wrote"] for s in card.operation["steps"]):
        return _refused(card, "A write already landed; Retry or Check again finishes it")
    if not ledger.discard(card.operation["id"], now()):
        return _refused(
            card,
            "Wait 10 minutes after the create, then Check again; Discard is allowed only after "
            "a later check found nothing",
        )
    return ApplyResult("done", 0, len(card.steps), "Discarded; the next run proposes it again")
