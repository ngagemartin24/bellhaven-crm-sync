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

    base = {"street": False, "admin": False, "phone": False, "zip": False, "city": False, "name": 0.0, "po_box": False}
    return Signals(**{**base, **true_fields})


# ---- proposal fixtures (Task 5) -----------------------------------------------------------------
def _proposal():
    from bellhaven_sync.propose import propose

    return propose(REPORT, ACCOUNTS, CONTACTS, OPERATOR_ID, DAY, set(), set())


PROPOSAL = _proposal()
CARDS = PROPOSAL.cards


def card(kind: str, slug: str | None = None, account: str | None = None):
    hits = [c for c in CARDS if c.kind == kind and (slug is None or c.location_slug == slug)
            and (account is None or full_id(account) in c.account_ids)]
    assert len(hits) == 1, f"{len(hits)} {kind} cards for {slug or account}"
    return hits[0]


def report_with(loc):
    from dataclasses import replace as dc_replace

    return dc_replace(REPORT, locations=[loc if x.slug == loc.slug else x for x in REPORT.locations])


def incomplete(report):
    from dataclasses import replace as dc_replace

    return dc_replace(report, complete=False, reason="found 34, stated 35")


def sorted_slugs_of_unchanged_rows() -> list[str]:
    return sorted([
        "bellhaven-of-maplewood", "bellhaven-of-new-carlisle", "bellhaven-of-wooster", "bellhaven-terrace-of-akron",
        "bellhaven-of-saline", "bellhaven-of-meadville", "bellhaven-woods-of-toledo", "bellhaven-of-kalamazoo",
        "bellhaven-court-of-altoona",
    ])
