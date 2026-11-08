"""Compare a CRM's current records with the expected end state (DESIGN 9). Shared by the tests and scripts/verify.py.

Every field not named in the expected file must still equal the snapshot taken before any change, so an extra
write anywhere shows up as a mismatch. `updated_at` and `parent_name` (both set by the CRM) are ignored.
"""

import json
from pathlib import Path

IGNORED = {"updated_at", "parent_name", "created_at", "created_by_candidate"}
OPERATOR_NAME = "Bellhaven Senior Living (Parent Account)"
CREATED_FIELDS = (
    "name",
    "billing_street",
    "billing_city",
    "billing_state",
    "billing_zip",
    "care_type",
    "status",
    "phone",
)


def _same(a, b) -> bool:
    if isinstance(a, int | float) or isinstance(b, int | float):
        try:
            return float(a or 0) == float(b or 0)
        except (TypeError, ValueError):
            return False
    return (a or "") == (b or "")


def _short(account_id: str) -> str:
    return account_id[-6:] if account_id else account_id


def diff_against_expected(crm, snapshot_dir: Path, expected_path: Path) -> list[str]:
    snap_accounts = {
        a["account_id"]: a for a in json.loads((Path(snapshot_dir) / "crm-accounts.json").read_text())["data"]
    }
    snap_contacts = {
        c["contact_id"]: c for c in json.loads((Path(snapshot_dir) / "crm-contacts.json").read_text())["data"]
    }
    expected = json.loads(Path(expected_path).read_text())
    accounts = {a["account_id"]: a for a in crm.list_accounts()}
    contacts = {c["contact_id"]: c for c in crm.list_contacts()}
    out: list[str] = []
    operator = [i for i, a in snap_accounts.items() if a["name"] == OPERATOR_NAME]
    operator_id = operator[0] if len(operator) == 1 else None

    # the accounts this run created, matched to the expected rows by name and street
    new_ids = [i for i in accounts if i not in snap_accounts]
    created_for: dict[str, str] = {}
    for row in expected["created_accounts"]:
        hits = [
            i
            for i in new_ids
            if accounts[i].get("name") == row["name"]
            and accounts[i].get("billing_street") == row["billing_street"]
        ]
        if len(hits) != 1:
            out.append(f"{row['slug']} created account: expected exactly 1, found {len(hits)}")
            continue
        a = accounts[hits[0]]
        created_for[row["slug"]] = hits[0]
        # every field of a created record: named ones as expected, the parent, the note; all else empty or zero
        for f in sorted(set(a) - IGNORED - {"account_id", "note"}):
            want = row[f] if f in CREATED_FIELDS else (operator_id if f == "parent_id" else "")
            if not _same(a.get(f), want):
                out.append(f"{row['slug']} {f}: expected {want!r}, got {a.get(f)!r}")
        note = a.get("note") or ""
        if not note:
            out.append(f"{row['slug']} note: expected the creation note, got nothing")
        for text in row.get("note_contains", []):
            if text not in note:
                out.append(f"{row['slug']} note: expected to contain {text!r}")
        people = [c for c in contacts.values() if c.get("account_id") == hits[0]]
        if not row.get("administrator"):
            if people:
                out.append(f"{row['slug']} contacts: expected none, found {len(people)}")
            continue
        if len(people) != 1:
            out.append(
                f"{row['slug']} administrator {row['administrator']!r}: expected 1 contact, found {len(people)}"
            )
            continue
        admin = people[0]
        want_admin = {
            "account_id": hits[0],
            "name": row["administrator"],
            "title": "Administrator",
            "phone": row["phone"],
            "is_active": True,
        }
        for f in sorted(set(admin) - IGNORED - {"contact_id"}):
            want = want_admin.get(f, "")
            if not _same(admin.get(f), want):
                out.append(f"{row['slug']} administrator {f}: expected {want!r}, got {admin.get(f)!r}")
    for i in new_ids:
        if i not in created_for.values():
            out.append(f"{_short(i)} unexpected new account: {accounts[i].get('name')!r}")

    # every account that existed before
    for i, before in snap_accounts.items():
        now = accounts.get(i)
        if now is None:
            out.append(f"{_short(i)} missing from the CRM")
            continue
        want = dict(expected["accounts"].get(i, {}))
        note_contains = want.pop("note_contains", [])
        for f in sorted(set(before) | set(now)):
            if f in IGNORED or f == "note":
                continue
            target = want.get(f, before.get(f))
            if isinstance(target, dict) and "created_for" in target:
                target = created_for.get(
                    target["created_for"], f"<account created for {target['created_for']}>"
                )
            if not _same(now.get(f), target):
                out.append(f"{_short(i)} {f}: expected {target!r}, got {now.get(f)!r}")
        old_note, new_note = before.get("note") or "", now.get("note") or ""
        if not note_contains:
            if new_note != old_note:
                out.append(f"{_short(i)} note: expected unchanged, got {new_note!r}")
        else:
            if not new_note.startswith(old_note):
                out.append(f"{_short(i)} note: the earlier note was not kept")
            for s in note_contains:
                if s not in new_note:
                    out.append(f"{_short(i)} note: expected to contain {s!r}")

    # contacts: the moved ones, the administrators of created accounts, nothing else
    moved = {m["contact_id"]: m["account_id"] for m in expected["moved_contacts"]}
    for i, before in snap_contacts.items():
        now = contacts.get(i)
        if now is None:
            out.append(f"contact {i} missing from the CRM")
            continue
        for f in sorted(set(before) | set(now)):
            if f in IGNORED:
                continue
            target = moved[i] if f == "account_id" and i in moved else before.get(f)
            if not _same(now.get(f), target):
                out.append(f"contact {before.get('name')} {f}: expected {target!r}, got {now.get(f)!r}")
    allowed_new = {
        c_id
        for c_id, c in contacts.items()
        if c_id not in snap_contacts and c.get("account_id") in created_for.values()
    }
    for c_id, c in contacts.items():
        if c_id not in snap_contacts and c_id not in allowed_new:
            out.append(
                f"contact {c.get('name')} unexpected new contact on {_short(c.get('account_id') or '')}"
            )
    expected_new = sum(1 for r in expected["created_accounts"] if r.get("administrator"))
    if len(allowed_new) != expected_new:
        out.append(f"new contacts: expected {expected_new}, got {len(allowed_new)}")
    return out
