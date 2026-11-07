"""An in-memory CRM that behaves like the real API, with switches for failures (DESIGN 16)."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

from bellhaven_sync.crm import CrmError

STATUSES = {"Active", "Inactive", "Needs Review"}
ACCOUNT_DEFAULTS = {
    "name": "",
    "parent_id": "",
    "parent_name": "",
    "billing_street": "",
    "billing_city": "",
    "billing_state": "",
    "billing_zip": "",
    "phone": "",
    "care_type": "",
    "status": "Active",
    "note": "",
    "duplicate_of_account": "",
    "chow_current_account": "",
    "lifetime_revenue": 0,
    "outstanding_ar": 0,
}
CONTACT_DEFAULTS = {"account_id": "", "name": "", "title": "", "email": "", "phone": "", "is_active": True}
SERVER_FIELDS = {"account_id", "contact_id", "parent_name", "created_by_candidate", "updated_at"}


class SimulatedCrash(Exception):
    """Stands in for the app process dying right after a write landed."""


class FakeCrm:
    def __init__(self, accounts: list[dict], contacts: list[dict]):
        self.accounts = {a["account_id"]: dict(a) for a in accounts}
        self.contacts = {c["contact_id"]: dict(c) for c in contacts}
        self.writes: list[tuple[str, str, dict]] = []
        self._faults: list[dict] = []
        self._rejected: set[str] = set()
        self._counter = 0
        self._clock: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot_dir: Path) -> "FakeCrm":
        accounts = json.loads((Path(snapshot_dir) / "crm-accounts.json").read_text())["data"]
        contacts = json.loads((Path(snapshot_dir) / "crm-contacts.json").read_text())["data"]
        return cls(accounts, contacts)

    # ---- switches for tests -----------------------------------------------------------------
    def fail_next(self, method: str, status: int, message: str, after: int = 0) -> None:
        self._faults.append(
            {"method": method, "kind": "fail", "skip": after, "status": status, "message": message}
        )

    def lose_next_response(self, method: str, after: int = 0) -> None:
        self._faults.append({"method": method, "kind": "lose", "skip": after})

    def drop_next_write(self, method: str, after: int = 0) -> None:
        self._faults.append({"method": method, "kind": "drop", "skip": after})

    def crash_after_write(self, method: str, after: int = 0) -> None:
        self._faults.append({"method": method, "kind": "crash", "skip": after})

    def reject_fields(self, names: set[str]) -> None:
        self._rejected |= set(names)

    def freeze_clock(self, ts: str) -> None:
        self._clock = ts

    def add_account(self, record: dict) -> None:
        self.accounts[record["account_id"]] = dict(record)

    def patch_account_raw(self, account_id: str, fields: dict) -> None:
        """Someone else edits the record: no write is logged."""
        self.accounts[account_id].update(fields)
        self._refresh(self.accounts[account_id])

    # ---- internals ----------------------------------------------------------------------------
    def _now(self) -> str:
        return self._clock or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")

    def _fire(self, method: str) -> list[dict]:
        fired = []
        for f in list(self._faults):
            if f["method"] != method:
                continue
            if f["skip"] == 0:
                fired.append(f)
                self._faults.remove(f)
            else:
                f["skip"] -= 1
        return fired

    def _refresh(self, account: dict) -> None:
        parent = self.accounts.get(account.get("parent_id") or "")
        account["parent_name"] = parent["name"] if parent else ""
        account["updated_at"] = self._now()

    def _check(self, body: dict, defaults: dict) -> None:
        unknown = set(body) - set(defaults)
        if unknown:
            raise CrmError(422, f"unknown field(s): {', '.join(sorted(unknown))}")
        hit = set(body) & self._rejected
        if hit:
            raise CrmError(422, f"field(s) not accepted: {', '.join(sorted(hit))}")
        if "status" in body and body["status"] not in STATUSES:
            raise CrmError(422, f"status: Input should be one of {sorted(STATUSES)}")

    def _write(self, method: str, path: str, body: dict, defaults: dict, do):
        fired = self._fire(method)
        for f in fired:
            if f["kind"] == "fail":
                raise CrmError(f["status"], f["message"])
            if f["kind"] == "drop":
                raise requests.ConnectionError("simulated: request never arrived")
        self._check(body, defaults)
        result = do()
        self.writes.append((method, path, copy.deepcopy(body)))
        for f in fired:
            if f["kind"] == "lose":
                raise requests.ConnectionError("simulated: response lost after the write")
            if f["kind"] == "crash":
                raise SimulatedCrash(path)
        return copy.deepcopy(result)

    def _read(self, store: dict, record_id: str, what: str) -> dict:
        for f in self._fire("GET"):
            if f["kind"] == "fail":
                raise CrmError(f["status"], f["message"])
        if record_id not in store:
            raise CrmError(404, f"{what} not found")
        return copy.deepcopy(store[record_id])

    # ---- the API ------------------------------------------------------------------------------
    def list_accounts(self) -> list[dict]:
        for f in self._fire("GET"):
            if f["kind"] == "fail":
                raise CrmError(f["status"], f["message"])
        return copy.deepcopy(list(self.accounts.values()))

    def list_contacts(self) -> list[dict]:
        for f in self._fire("GET"):
            if f["kind"] == "fail":
                raise CrmError(f["status"], f["message"])
        return copy.deepcopy(list(self.contacts.values()))

    def get_account(self, account_id: str) -> dict:
        return self._read(self.accounts, account_id, "account")

    def get_contact(self, contact_id: str) -> dict:
        return self._read(self.contacts, contact_id, "contact")

    def patch_account(self, account_id: str, body: dict) -> dict:
        if account_id not in self.accounts:
            raise CrmError(404, "account not found")

        def do():
            rec = self.accounts[account_id]
            rec.update(body)
            self._refresh(rec)
            return rec

        return self._write("PATCH", f"/accounts/{account_id}", body, ACCOUNT_DEFAULTS, do)

    def patch_contact(self, contact_id: str, body: dict) -> dict:
        if contact_id not in self.contacts:
            raise CrmError(404, "contact not found")
        if "account_id" in body and body["account_id"] not in self.accounts:
            raise CrmError(422, "account_id: account not found")

        def do():
            rec = self.contacts[contact_id]
            rec.update(body)
            rec["updated_at"] = self._now()
            return rec

        return self._write("PATCH", f"/contacts/{contact_id}", body, CONTACT_DEFAULTS, do)

    def create_account(self, body: dict) -> dict:
        def do():
            self._counter += 1
            rec = {
                **ACCOUNT_DEFAULTS,
                **body,
                "account_id": f"001FAKE{self._counter:011d}",
                "created_by_candidate": True,
            }
            self._refresh(rec)
            self.accounts[rec["account_id"]] = rec
            return rec

        return self._write("POST", "/accounts", body, ACCOUNT_DEFAULTS, do)

    def create_contact(self, body: dict) -> dict:
        if body.get("account_id") not in self.accounts:
            raise CrmError(422, "account_id: account not found")

        def do():
            self._counter += 1
            rec = {
                **CONTACT_DEFAULTS,
                **body,
                "contact_id": f"003FAKE{self._counter:011d}",
                "created_by_candidate": True,
                "updated_at": self._now(),
            }
            self.contacts[rec["contact_id"]] = rec
            return rec

        return self._write("POST", "/contacts", body, CONTACT_DEFAULTS, do)
