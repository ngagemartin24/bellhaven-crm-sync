"""Blind check: an independent model classifies every location from the same inputs, never seeing ours.

    uv run python scripts/blind_check.py prepare --out blind/     # reads the website and the CRM, read-only
    # give the folder to the reviewing model; it writes blind/answers.json
    uv run python scripts/blind_check.py compare blind/answers.json

`compare` turns our proposals into the same answer vocabulary, prints every disagreement, and flags the
affected cards ("Blind check disagrees: ...") so the reviewer sees them in the app before deciding.
"""

import argparse
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from bellhaven_sync.config import load_config
from bellhaven_sync.crm import CrmClient
from bellhaven_sync.ledger import Ledger
from bellhaven_sync.scrape import http_fetcher, scrape

ROOT = Path(__file__).resolve().parent.parent
OPEN = ("pending", "blocked")
FIELDS = ("outcome", "account_ids", "survivor", "parent_action", "rename_to")

SCHEMA = """{
  "locations": {
    "<location slug>": {
      "outcome": "match | fix | new | look_alike",
      "account_ids": ["<every CRM account that is this facility>"],
      "survivor": "<account id to keep when there are duplicates, else null>",
      "parent_action": "none | reparent | chow_new",
      "rename_to": "<the website's exact name when the account's name must change, else null>"
    }
  },
  "leftovers": {
    "<account id>": {"outcome": "needs_review | chow_existing | duplicate | inactive", "target": "<account id or null>"}
  }
}"""

PROMPT = f"""# Independent check: match a care operator's website to its CRM accounts

You are checking another analyst's work without seeing it. Work only from the three files in this folder.

- `locations.json`: every community on Bellhaven Senior Living's website (name, address, phone, care
  offerings, administrator).
- `accounts.json`: every CRM account. `parent_id` links a facility to its parent company's account; the
  operator's own parent account is named "Bellhaven Senior Living (Parent Account)". Each account carries
  `lifetime_revenue` and `outstanding_ar`.
- `contacts.json`: people on CRM accounts (`account_id`, `name`, `title`, `is_active`).

The CRM is out of date: facilities are bought, sold, renamed and duplicated, and some accounts resemble a
location without being it. For every website location decide:

- `outcome`: "match" (an account is right as it is), "fix" (the account exists but needs a change: its
  parent, its name, duplicate copies, its phone or ZIP), "new" (no account exists, so one is created), or
  "look_alike" (only a different facility that resembles it exists, so one is created).
- `account_ids`: every account that is this facility (several when there are duplicates); [] for new.
- `survivor`: with duplicates, the copy to keep; otherwise null.
- `parent_action`: "none" when the account already sits under the operator's parent account. Otherwise
  apply this rule: before an account moves to a different parent, check its billing fields. If it has
  revenue history AND outstanding AR greater than zero, the existing account is left exactly as it is, a
  new account is created under the correct parent, and the old account points to it through
  `chow_current_account` ("chow_new"). Otherwise the existing account moves directly ("reparent").
- `rename_to`: the website's exact name when the account's name is outdated or differs; otherwise null.

Then cover every account under the operator's parent account that is not on the website ("leftovers"):
"needs_review" (unclear, a person should look), "chow_existing" (another operator's existing account is
the same facility; give its id as `target`), "duplicate" (`target` = the copy that survives) or "inactive".

CRM conventions: `status` is Active, Inactive or Needs Review; `note` is free text; a duplicate gets
`duplicate_of_account` = the surviving account's id and status Inactive; there is no merge or delete.

Write `answers.json` in exactly this shape, with full account ids:

```json
{SCHEMA}
```
"""


def prepare(fetch, crm, base_url: str, out: Path) -> None:
    report = scrape(fetch, base_url)
    out.mkdir(parents=True, exist_ok=True)
    (out / "locations.json").write_text(json.dumps([asdict(loc) for loc in report.locations], indent=1))
    (out / "accounts.json").write_text(json.dumps(crm.list_accounts(), indent=1))
    (out / "contacts.json").write_text(json.dumps(crm.list_contacts(), indent=1))
    (out / "PROMPT.md").write_text(PROMPT)


def _steps(cards):
    return [s for c in cards for s in c.steps]


def _location_answer(v, cards) -> dict:
    outcome = {
        "Confident match": "match",
        "Medium match, nothing to change": "match",
        "Match, needs a fix": "fix",
        "New": "new",
        "Possible match only": "new",
    }.get(v.verdict, "needs_attention")
    if outcome == "new":  # the look-alike link is offered only as an alternative, not as our answer
        return {
            "outcome": "new",
            "account_ids": [],
            "survivor": None,
            "parent_action": "none",
            "rename_to": None,
        }
    cards = [c for c in cards if c.kind != "possible_match"]
    steps = _steps(cards)
    survivor = next((s.body["duplicate_of_account"] for s in steps if "duplicate_of_account" in s.body), None)
    if any(c.kind == "chow" for c in cards) and any(s.method == "POST" for s in steps):
        parent = "chow_new"
    elif any(c.kind == "wrong_parent" for c in cards):
        parent = "reparent"
    else:
        parent = "none"
    rename = next((s.body["name"] for s in steps if s.method == "PATCH" and "name" in s.body), None)
    return {
        "outcome": outcome,
        "account_ids": sorted(v.account_ids) if outcome in ("match", "fix") else [],
        "survivor": survivor,
        "parent_action": parent,
        "rename_to": rename,
    }


def _leftover_answer(v, cards) -> dict:
    steps = _steps(cards)
    if v.verdict == "Gone from the website":
        return {"outcome": "needs_review", "target": None}
    for s in steps:
        if "chow_current_account" in s.body:
            return {"outcome": "chow_existing", "target": s.body["chow_current_account"]}
        if "duplicate_of_account" in s.body:
            return {"outcome": "duplicate", "target": s.body["duplicate_of_account"]}
    return {"outcome": "needs_attention", "target": None}


def our_answers(ledger: Ledger) -> dict:
    """Our latest complete run, in the blind check's vocabulary."""
    by_set = defaultdict(list)
    for c in ledger.cards():
        if c.status not in ("superseded", "discarded"):
            by_set[c.change_set].append(c)
    locations, leftovers = {}, {}
    for v in ledger.verdicts():
        cards = [c for s in v.change_sets for c in by_set.get(s, [])]
        if v.account_ids and v.subject == v.account_ids[0]:
            leftovers[v.subject] = _leftover_answer(v, cards)
        else:
            locations[v.subject] = _location_answer(v, cards)
    return {"locations": locations, "leftovers": leftovers}


def _norm_name(s):
    return " ".join(str(s).replace("–", "-").replace("—", "-").casefold().split()) if s else None


def _normalize_location(a: dict | None) -> dict | None:
    if a is None:
        return None
    outcome = "new" if a.get("outcome") == "look_alike" else a.get("outcome")
    if outcome == "new":  # a new account: nothing else to compare
        return {"outcome": "new"}
    return {
        "outcome": outcome,
        "account_ids": sorted(a.get("account_ids") or []),
        "survivor": a.get("survivor"),
        "parent_action": a.get("parent_action") or "none",
        "rename_to": _norm_name(a.get("rename_to")),
    }


def _normalize_leftover(a: dict | None) -> dict | None:
    return None if a is None else {"outcome": a.get("outcome"), "target": a.get("target")}


def _short(a: dict | None) -> str:
    if a is None:
        return "no answer"
    parts = []
    for k, val in a.items():
        if isinstance(val, list):
            val = ",".join(x[-6:] for x in val) or "-"
        elif isinstance(val, str) and len(val) == 18 and val.startswith("001"):
            val = val[-6:]
        parts.append(f"{k}={val}")
    return " ".join(parts)


def compare(answers: dict, ledger: Ledger) -> list[dict]:
    """Every subject where the blind answer differs from ours; flags that subject's open cards."""
    ours = our_answers(ledger)
    # a duplicate copy we retire inside a location's group, listed again as a leftover with the same survivor
    retired = {
        (aid, loc["survivor"])
        for loc in ours["locations"].values()
        if loc.get("survivor")
        for aid in loc.get("account_ids") or []
        if aid != loc["survivor"]
    }
    rows = []
    for group, norm in (("locations", _normalize_location), ("leftovers", _normalize_leftover)):
        mine, theirs = ours[group], answers.get(group, {})
        for subject in sorted(set(mine) | set(theirs)):
            a, b = norm(mine.get(subject)), norm(theirs.get(subject))
            if a == b:
                continue
            if a is None and b and b["outcome"] == "duplicate" and (subject, b["target"]) in retired:
                continue
            cards = [
                c
                for c in ledger.cards()
                if c.status in OPEN and (c.location_slug == subject or subject in c.account_ids)
            ]
            for c in cards:
                ledger.flag(c.id, f"Blind check disagrees: {_short(b)} (ours: {_short(a)})")
            rows.append({"subject": subject, "ours": _short(a), "theirs": _short(b), "flagged": len(cards)})
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("prepare")
    pr.add_argument("--out", type=Path, default=ROOT / "blind")
    co = sub.add_parser("compare")
    co.add_argument("answers", type=Path)
    args = p.parse_args(argv)
    cfg = load_config(ROOT / "config.toml")
    if args.command == "prepare":
        if not cfg.token:
            raise SystemExit("Set BELLHAVEN_CRM_TOKEN first.")
        prepare(http_fetcher(cfg.site_url), CrmClient(cfg.crm_url, cfg.token), cfg.site_url, args.out)
        print(f"Wrote {args.out}/: give PROMPT.md and the three JSON files to the reviewing model.")
        return 0
    with Ledger(cfg.db_path) as ledger:
        rows = compare(json.loads(args.answers.read_text()), ledger)
    if not rows:
        print("The blind answers agree with every location and leftover account.")
        return 0
    print(f"{len(rows)} disagreement(s); the affected cards are flagged in the review app:")
    for r in rows:
        print(
            f"- {r['subject']}\n    ours:   {r['ours']}\n    theirs: {r['theirs']}  ({r['flagged']} card(s) flagged)"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
