"""Scores every website location against every eligible CRM account (DESIGN 7). Thresholds live in rules.py."""

from dataclasses import dataclass, field

from . import rules
from .models import Account, Contact, Location, Signals
from .normalize import (
    full_address,
    is_po_box,
    jaccard,
    name_tokens,
    norm_phone,
    norm_street,
    norm_zip,
    same_person,
)


@dataclass(frozen=True)
class Candidate:
    account_id: str
    signals: Signals
    score: int
    band: str | None


@dataclass
class LocationMatch:
    location: Location
    group: list[str]
    band: str | None
    lows: list[Candidate] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    ruled_out: list[Candidate] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)


def contacts_by_account(contacts: list[Contact]) -> dict[str, list[Contact]]:
    out: dict[str, list[Contact]] = {}
    for c in contacts:
        if c.get("is_active"):
            out.setdefault(c.get("account_id") or "", []).append(c)
    return out


def signals(loc: Location, acc: Account, by_account: dict[str, list[Contact]]) -> Signals:
    crm_street = acc.get("billing_street") or ""
    phone = norm_phone(acc.get("phone"))
    return Signals(
        street=bool(crm_street) and norm_street(crm_street) == norm_street(loc.street),
        admin=bool(loc.administrator) and any(
            same_person(c.get("name"), loc.administrator) for c in by_account.get(acc["account_id"], [])),
        phone=bool(phone) and phone == norm_phone(loc.phone),
        zip=bool(acc.get("billing_zip")) and norm_zip(acc.get("billing_zip")) == norm_zip(loc.zip),
        city=" ".join((acc.get("billing_city") or "").lower().split()) == loc.city.lower(),
        name=jaccard(name_tokens(acc.get("name")), name_tokens(loc.name)),
        po_box=is_po_box(crm_street),
    )


def _label(acc: Account) -> str:
    return f"{acc.get('name')} ({acc['account_id']})"


def match_location(loc: Location, accounts: list[Account], by_account: dict[str, list[Contact]],
                   eligible_ids: set[str]) -> LocationMatch:
    by_id = {a["account_id"]: a for a in accounts}
    candidates: list[Candidate] = []
    conflicts: list[str] = []
    for acc in accounts:
        if acc["account_id"] not in eligible_ids:
            continue
        s = signals(loc, acc, by_account)
        if not rules.passes_gate(loc, acc, s):
            if rules.is_state_conflict(loc, acc, s):
                conflicts.append(f"{_label(acc)} matches this location's street and ZIP or phone and administrator, "
                                 f"but its state is {acc.get('billing_state')!r}, not {loc.state}")
            continue
        candidates.append(Candidate(acc["account_id"], s, rules.score(s), rules.band(s)))
    candidates.sort(key=lambda c: (-c.score, c.account_id))

    def address(acc_id: str):
        a = by_id[acc_id]
        return full_address(a.get("billing_street"), a.get("billing_city"), a.get("billing_state"),
                            a.get("billing_zip"))

    highs = [c for c in candidates if c.band == "High"]
    mediums = [c for c in candidates if c.band == "Medium"]
    group: list[str] = []
    match_band: str | None = None
    lows: list[Candidate] = []
    if highs:
        home = address(highs[0].account_id)
        members = [c.account_id for c in highs if address(c.account_id) == home]
        for c in highs:
            if address(c.account_id) != home:
                conflicts.append(f"{_label(by_id[c.account_id])} is also a strong match but at a different address")
        if len(members) > 1:
            fits = [m for m in members if rules.care_compatible(by_id[m].get("care_type"), loc)]
            if fits:
                for m in list(members):
                    care = by_id[m].get("care_type") or ""
                    if care and m not in fits:
                        members.remove(m)
                        conflicts.append(f"{_label(by_id[m])} is at the same address with a different care type "
                                         f"({care})")
        for c in mediums:
            conflicts.append(f"{_label(by_id[c.account_id])} also partly matches (medium) beside the strong match")
        group, match_band = members, "High"
    elif len(mediums) == 1:
        group, match_band = [mediums[0].account_id], "Medium"
    elif len(mediums) > 1:
        conflicts.append("several accounts partly match (medium) and none strongly: "
                         + ", ".join(_label(by_id[c.account_id]) for c in mediums))
    else:
        lows = [c for c in candidates if c.band == "Low"]

    shown = set(group) | {c.account_id for c in lows}
    ruled_out = [c for c in candidates if c.account_id not in shown]
    return LocationMatch(loc, group, match_band, lows, conflicts, ruled_out, candidates)


def match_all(locations: list[Location], accounts: list[Account], contacts: list[Contact],
              eligible_ids: set[str]) -> dict[str, LocationMatch]:
    by_account = contacts_by_account(contacts)
    return {loc.slug: match_location(loc, accounts, by_account, eligible_ids) for loc in locations}
