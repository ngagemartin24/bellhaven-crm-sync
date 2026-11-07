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
