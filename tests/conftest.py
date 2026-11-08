"""Shared test setup. No test touches the network: only loopback (the browser test's own server) is allowed."""

import json
import socket
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests
from fake_site import fake_fetcher

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_request = requests.Session.request
    real_connect = socket.create_connection

    def guarded_request(self, method, url, *args, **kwargs):
        if urlparse(url).hostname not in LOOPBACK:
            raise RuntimeError("network disabled in tests")
        return real_request(self, method, url, *args, **kwargs)

    def guarded_connect(address, *args, **kwargs):
        if address[0] not in LOOPBACK:
            raise RuntimeError("network disabled in tests")
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", guarded_request)
    monkeypatch.setattr(socket, "create_connection", guarded_connect)


# ---- shared fixtures and lookups (Task 3) -------------------------------------------------------

SNAP = Path(__file__).parent / "fixtures" / "snapshot"
SITE = SNAP / "site"
BASE = "https://analyst-assessment-production.up.railway.app"
FETCH = fake_fetcher(SITE)
PARENT = "Bellhaven Senior Living (Parent Account)"
DAY = date(2026, 11, 7)

_ACCOUNTS = json.loads((SNAP / "crm-accounts.json").read_text())["data"]
_CONTACTS = json.loads((SNAP / "crm-contacts.json").read_text())["data"]


def full_id(sfx: str) -> str:
    """The one snapshot account or contact id ending with sfx."""
    ids = [a["account_id"] for a in _ACCOUNTS] + [c["contact_id"] for c in _CONTACTS]
    hits = [i for i in ids if i.endswith(sfx)]
    if len(hits) != 1:
        raise KeyError(f"{sfx!r} matches {len(hits)} ids")
    return hits[0]


def suffix(record_id: str) -> str:
    return record_id[-6:]


def suffixes(ids) -> set[str]:
    return {suffix(i) for i in ids}


def acct(sfx: str) -> dict:
    return dict(next(a for a in _ACCOUNTS if a["account_id"] == full_id(sfx)))


def acct_by_name(name: str) -> dict:
    [a] = [a for a in _ACCOUNTS if a["name"] == name]
    return dict(a)


def contact_id(name: str) -> str:
    [c] = [c for c in _CONTACTS if c["name"] == name]
    return c["contact_id"]


OPERATOR_ID = acct_by_name(PARENT)["account_id"]
HARBORVIEW_ID = acct_by_name("Harborview Care Group (Parent Account)")["account_id"]
CEDAR_TRAIL_ID = acct_by_name("Cedar Trail Communities (Parent Account)")["account_id"]


# ---- matching fixtures (Task 4) ------------------------------------------------------------------
def _match_fixtures():
    from bellhaven_sync import match as match_module
    from bellhaven_sync import rules
    from bellhaven_sync.scrape import scrape

    report = scrape(FETCH, BASE)
    accounts = [dict(a) for a in _ACCOUNTS]
    contacts = [dict(c) for c in _CONTACTS]
    eligible = {a["account_id"] for a in accounts if rules.eligible(a, accounts, set())}
    by_account = match_module.contacts_by_account(contacts)
    return report, accounts, contacts, by_account, eligible, match_module


REPORT, ACCOUNTS, CONTACTS, CONTACTS_BY_ACCOUNT, ELIGIBLE, match_module = _match_fixtures()
LOC = {loc.slug: loc for loc in REPORT.locations}
MATCHES = match_module.match_all(REPORT.locations, ACCOUNTS, CONTACTS, ELIGIBLE)


def loc_with(slug: str, **fields):
    from dataclasses import replace as dc_replace

    return dc_replace(LOC[slug], **fields)


def replace(accounts: list[dict], record: dict) -> list[dict]:
    return [record if a["account_id"] == record["account_id"] else a for a in accounts]


def all_candidate_suffixes(slug: str) -> set[str]:
    m = MATCHES[slug]
    return suffixes(list(m.group) + [c.account_id for c in m.lows + m.ruled_out])


def sig(**true_fields):
    from bellhaven_sync.models import Signals

    base = {
        "street": False,
        "admin": False,
        "phone": False,
        "zip": False,
        "city": False,
        "name": 0.0,
        "po_box": False,
    }
    return Signals(**{**base, **true_fields})


# ---- proposal fixtures (Task 5) -----------------------------------------------------------------
def _proposal():
    from bellhaven_sync.propose import propose

    return propose(REPORT, ACCOUNTS, CONTACTS, OPERATOR_ID, DAY, set(), set())


PROPOSAL = _proposal()
CARDS = PROPOSAL.cards


def card(kind: str, slug: str | None = None, account: str | None = None):
    hits = [
        c
        for c in CARDS
        if c.kind == kind
        and (slug is None or c.location_slug == slug)
        and (account is None or full_id(account) in c.account_ids)
    ]
    assert len(hits) == 1, f"{len(hits)} {kind} cards for {slug or account}"
    return hits[0]


def report_with(loc):
    from dataclasses import replace as dc_replace

    return dc_replace(REPORT, locations=[loc if x.slug == loc.slug else x for x in REPORT.locations])


def incomplete(report):
    from dataclasses import replace as dc_replace

    return dc_replace(report, complete=False, reason="found 34, stated 35")


def sorted_slugs_of_unchanged_rows() -> list[str]:
    return sorted(
        [
            "bellhaven-of-maplewood",
            "bellhaven-of-new-carlisle",
            "bellhaven-of-wooster",
            "bellhaven-terrace-of-akron",
            "bellhaven-of-saline",
            "bellhaven-of-meadville",
            "bellhaven-woods-of-toledo",
            "bellhaven-of-kalamazoo",
            "bellhaven-court-of-altoona",
        ]
    )


# ---- ledger and run fixtures (Task 6) -----------------------------------------------------------
def fresh(tmp_path):
    from fake_crm import FakeCrm

    from bellhaven_sync.ledger import Ledger

    return Ledger(tmp_path / "d.db"), FakeCrm.from_snapshot(SNAP)


def only(cards, kind: str, slug: str | None = None, account: str | None = None):
    hits = [
        c
        for c in cards
        if c.kind == kind
        and (slug is None or c.location_slug == slug)
        and (account is None or full_id(account) in c.account_ids)
    ]
    assert len(hits) == 1, f"{len(hits)} {kind} cards for {slug or account}"
    return hits[0]


def goshen_with_phone(phone: str) -> str:
    return (SITE / "communities" / "bellhaven-of-goshen.html").read_text().replace("(330) 405-6040", phone)


# ---- apply fixtures (Task 7) ----------------------------------------------------------------------
EXPECTED = Path(__file__).parent / "fixtures" / "expected_end_state.json"


def utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def ready(tmp_path):
    """A ledger and fake CRM after one daily run: 42 pending cards."""
    from bellhaven_sync.pipeline import run_once

    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    return l, crm


def run_after(l, crm):
    from bellhaven_sync.pipeline import run_once

    run_once(FETCH, crm, l, PARENT, DAY)
    return l.cards("pending")


def end_state(tmp_path):
    """The intended review: reject the Union Square possible match, approve the other 41."""
    from bellhaven_sync.apply import approve, reject

    l, crm = ready(tmp_path)
    pm = only(l.cards("pending"), kind="possible_match")
    assert reject(pm.id, pm.version, l).status == "done"
    for c in l.cards("pending"):
        r = approve(c.id, c.version, l, crm)
        assert r.status == "done", (c.title, r.message)
    return l, crm


def chow_card(l, slug):
    return only(l.cards("pending"), kind="chow", slug=slug)


def chow_card_existing(l, sfx):
    return only(l.cards("pending"), kind="chow", account=sfx)


def new_card(l, slug):
    return only(l.cards("pending"), kind="new_account", slug=slug)


def wrong_parent_card(l, sfx):
    return only(l.cards("pending"), kind="wrong_parent", account=sfx)


def details_card(l, sfx):
    return only(l.cards("pending"), kind="details", account=sfx)


def union_square_cards(l):
    slug = "bellhaven-at-union-square"
    return new_card(l, slug), only(l.cards("pending"), kind="possible_match", slug=slug)


def batavia_record_under_operator():
    rec = dict(acct("9MB6KL"))
    rec.update(
        account_id="001ELSEWHERE00000001",
        name="Bellhaven of Batavia",
        billing_street="2000 Hospital Dr",
        billing_city="Batavia",
        billing_state="OH",
        billing_zip="45103",
        parent_id=OPERATOR_ID,
        status="Active",
        note="",
        lifetime_revenue=0,
        outstanding_ar=0,
    )
    return rec


# ---- review app fixtures (Task 8) --------------------------------------------------------------
def CFG_AT(tmp_path):
    from bellhaven_sync.config import Config

    return Config(
        site_url=BASE,
        crm_url="http://crm.invalid/api/v1",
        crm_browser_url="http://crm.invalid/crm/{token}/accounts/{account_id}",
        operator_parent_name=PARENT,
        db_path=tmp_path / "d.db",
        token="test-token",
    )


class AppEnv:
    def __init__(self, tmp_path):
        from fastapi.testclient import TestClient

        from bellhaven_sync.app import create_app

        self.l, self.fake = ready(tmp_path)
        self.cfg = CFG_AT(tmp_path)
        self.client = TestClient(create_app(self.cfg, self.l, lambda: self.fake, lambda: DAY))
        self.client.get("/")  # sets the anti-forgery cookie

    @property
    def token(self):
        return self.client.cookies["bh_csrf"]

    def pending(self, kind, slug=None, account=None):
        return only(self.l.cards("pending"), kind=kind, slug=slug, account=account)

    def any_pending(self):
        return self.l.cards("pending")[0]

    def post(self, card, action, **kw):
        data = {"csrf": self.token, "version": card.version, **kw.pop("data", {})}
        kw.setdefault("follow_redirects", False)
        return self.client.post(f"/cards/{card.id}/{action}", data=data, **kw)

    def card_in_status(self, status):
        from bellhaven_sync.apply import approve

        if status == "failed":
            c = self.pending("details", account="NRLNME")
            self.fake.fail_next("PATCH", 422, "boom")  # a definite refusal: nothing written
        else:
            c = self.pending("new_account", slug="bellhaven-of-batavia")
            self.fake.drop_next_write("POST")
        approve(c.id, c.version, self.l, self.fake)
        return self.l.card(c.id)


@pytest.fixture
def env(tmp_path):
    return AppEnv(tmp_path)


def ledger_with_card_in(status, tmp_path):
    """A ledger left with one operation mid-apply, as if the app stopped."""
    assert status == "applying"
    l, _ = ready(tmp_path)
    c = only(l.cards("pending"), kind="details", account="NRLNME")
    assert l.claim(c.id, c.version) is not None
    return l


def expected_answers() -> dict:
    """The blind check's answer key in its own vocabulary, written by hand from DESIGN section 9."""
    raw = json.loads((Path(__file__).parent / "fixtures" / "blind_expected.json").read_text())
    locs = {}
    for slug, a in raw["locations"].items():
        locs[slug] = {
            **a,
            "account_ids": sorted(full_id(s) for s in a["account_ids"]),
            "survivor": full_id(a["survivor"]) if a["survivor"] else None,
        }
    left = {
        full_id(s): {**a, "target": full_id(a["target"]) if a["target"] else None}
        for s, a in raw["leftovers"].items()
    }
    return {"locations": locs, "leftovers": left}
