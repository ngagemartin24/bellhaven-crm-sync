"""The CRM API. Opened read-only by default; only apply.py opens it for writing (DESIGN 4, 12)."""

from typing import Any

import requests

from .models import Account, Contact

PAGE_SIZE = 200
TIMEOUT_S = 20


class ReadOnlyError(Exception):
    """A write was attempted through a read-only client."""


class CrmError(Exception):
    """The CRM answered with an error. Carries the CRM's own explanation, never the key."""

    def __init__(self, status: int, message: str):
        super().__init__(f"CRM error {status}: {message}")
        self.status = status
        self.message = message


def _detail(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (getattr(response, "text", "") or "no details")[:300]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):
        parts = []
        for item in detail:
            loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
            parts.append(f"{loc}: {item.get('msg', '')}" if loc else str(item.get("msg", item)))
        return "; ".join(parts)
    return str(detail)[:300]


class CrmClient:
    def __init__(self, base_url: str, token: str, read_only: bool = True, session: Any = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._session = session or requests.Session()
        self.read_only = read_only

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        r = self._session.request(
            method, self._base + path, headers=self._headers, params=params, json=body, timeout=TIMEOUT_S
        )
        if r.status_code >= 400:
            raise CrmError(r.status_code, _detail(r))
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return data

    def _write(self, method: str, path: str, body: dict) -> dict:
        if self.read_only:
            raise ReadOnlyError(f"{method} {path} refused: this client is read-only")
        return self._call(method, path, body=body)

    def _list(self, path: str) -> list[dict]:
        records: list[dict] = []
        page = 1
        while True:
            batch = self._call("GET", path, params={"page": page, "page_size": PAGE_SIZE})["data"]
            records.extend(batch)
            if len(batch) < PAGE_SIZE:
                return records
            page += 1

    def list_accounts(self) -> list[Account]:
        return self._list("/accounts")

    def list_contacts(self) -> list[Contact]:
        return self._list("/contacts")

    def get_account(self, account_id: str) -> Account:
        return self._call("GET", f"/accounts/{account_id}")

    def get_contact(self, contact_id: str) -> Contact:
        return self._call("GET", f"/contacts/{contact_id}")

    def patch_account(self, account_id: str, body: dict) -> Account:
        return self._write("PATCH", f"/accounts/{account_id}", body)

    def patch_contact(self, contact_id: str, body: dict) -> Contact:
        return self._write("PATCH", f"/contacts/{contact_id}", body)

    def create_account(self, body: dict) -> Account:
        return self._write("POST", "/accounts", body)

    def create_contact(self, body: dict) -> Contact:
        return self._write("POST", "/contacts", body)
