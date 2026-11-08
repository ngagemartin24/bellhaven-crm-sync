"""Turns matches into review cards, each with its evidence and ordered CRM writes (DESIGN 8).

propose() is pure: it reads the scrape and the CRM records it is given and returns cards and verdicts. Changes
already true in the CRM, changes a reviewer already decided, and changes touching an unfinished operation are
dropped before any card or write step is built (DESIGN 10.2, 10.3, 10.5).
"""

from dataclasses import asdict, dataclass
from datetime import date

from . import rules
from .match import Candidate, LocationMatch, contacts_by_account, match_all
from .models import Account, Card, Change, Contact, Location, Step, Verdict, digest
from .normalize import full_address, norm_phone, norm_zip
from .scrape import ScrapeReport

ACCOUNT_VIEW = (
    "name",
    "parent_id",
    "parent_name",
    "billing_street",
    "billing_city",
    "billing_state",
    "billing_zip",
    "phone",
    "care_type",
    "status",
    "lifetime_revenue",
    "outstanding_ar",
    "duplicate_of_account",
    "chow_current_account",
    "note",
)
SIGNAL_POINTS = (
    ("street", "Same street"),
    ("admin", "Administrator on file"),
    ("phone", "Same phone"),
    ("zip", "Same ZIP"),
    ("city", "Same city"),
)


@dataclass(frozen=True)
class Proposal:
    cards: list[Card]
    verdicts: list[Verdict]


def _sentence(text: str) -> str:
    """First letter up, the rest untouched (str.capitalize would lowercase facility names)."""
    return text[:1].upper() + text[1:]


def _dollars(v) -> str:
    return rules.dollars(v)


ADDRESS = ("billing_street", "billing_city", "billing_state", "billing_zip")


def _survivor_still(acc: Account, loc: Location) -> dict:
    """What a duplicate retirement rests on, for the survivor it does not write: still a live, separate account at
    the reviewed place (or at the website's, should a details card have synced it first)."""
    return {
        "path": f"/accounts/{acc['account_id']}",
        "label": acc.get("name") or acc["account_id"],
        "fields": {"duplicate_of_account": acc.get("duplicate_of_account", "")},
        "one_of": {
            "billing_street": [acc.get("billing_street", ""), loc.street],
            "billing_city": [acc.get("billing_city", ""), loc.city],
            "billing_state": [acc.get("billing_state", ""), loc.state],
        },
        "active": True,
    }


def _buyer_still(acc: Account) -> dict:
    """What a sale rests on: the buyer is still the live account at the old account's address, same care type."""
    keys = (*ADDRESS, "care_type", "parent_id", "duplicate_of_account", "chow_current_account")
    return {
        "path": f"/accounts/{acc['account_id']}",
        "label": acc.get("name") or acc["account_id"],
        "fields": {k: acc.get(k, "") for k in keys},
        "active": True,
    }


def _patch_expect(acc: Account, written, facts=(), with_note: bool = False) -> dict:
    keys = list(written) + [f for f in facts if f not in written] + (["note"] if with_note else [])
    return {"fields": {k: acc.get(k, "") for k in keys}}


class _Builder:
    def __init__(
        self,
        accounts: list[Account],
        contacts: list[Contact],
        operator_id: str,
        today: date,
        reserved_accounts: set[str],
        reserved_locations: set[str],
        decided: frozenset[str],
    ):
        self.accounts = accounts
        self.by_id = {a["account_id"]: a for a in accounts}
        self.by_account = contacts_by_account(contacts)
        self.op = operator_id
        self.op_name = self.by_id.get(operator_id, {}).get("name", "the operator")
        self.today = today
        self.reserved_accounts = reserved_accounts
        self.reserved_locations = reserved_locations
        self.decided = decided
        self.cards: list[Card] = []
        self.verdicts: list[Verdict] = []

    # ---- filters and helpers ----------------------------------------------------------------------
    def keep(self, changes: list[Change]) -> list[Change]:
        """Drop decided changes and changes touching an unfinished operation (10.3 step 2, 10.5)."""
        out = []
        for c in changes:
            if c.key in self.decided:
                continue
            if c.location_slug and c.location_slug in self.reserved_locations:
                continue
            touched = {c.record} | ({c.value} if isinstance(c.value, str) else set())
            if touched & self.reserved_accounts:
                continue
            out.append(c)
        return out

    def note(self, what: str, why: str) -> str:
        return rules.note_line(self.today, what, why)

    def add(
        self,
        kind: str,
        title: str,
        summary: str,
        slug: str | None,
        account_ids,
        band: str | None,
        changes: list[Change],
        steps: list[Step],
        evidence: dict,
        alt_group: str | None = None,
        needs_complete_read: bool = False,
    ) -> Card:
        card = Card(
            change_set=digest(sorted(c.key for c in changes)),
            kind=kind,
            section=rules.SECTION_FOR_KIND[kind],
            title=title,
            summary=summary,
            location_slug=slug,
            account_ids=tuple(account_ids),
            band=band,
            changes=tuple(changes),
            steps=tuple(steps),
            evidence=evidence,
            alt_group=alt_group,
            needs_complete_read=needs_complete_read,
        )
        self.cards.append(card)
        return card

    def view(self, account_id: str) -> dict:
        acc = self.by_id[account_id]
        return {
            **{k: acc.get(k, "") for k in ACCOUNT_VIEW},
            "account_id": account_id,
            "contacts": [
                {"contact_id": c.get("contact_id"), "name": c.get("name"), "title": c.get("title")}
                for c in self.by_account.get(account_id, [])
            ],
        }

    def billing(self, acc: Account, moving: bool) -> dict:
        rev, ar = rules.money(acc.get("lifetime_revenue")), rules.money(acc.get("outstanding_ar"))
        if not moving:
            rule = "No parent change, so the billing rule does not apply."
        elif rules.needs_chow(acc):
            rule = (
                "Revenue history AND outstanding AR: keep this account exactly as it is, create a new account "
                "under the correct parent, and point the old one at it (billing SOP)."
            )
        else:
            had = (
                "revenue history" if rev > 0 else ("outstanding AR" if ar > 0 else "no revenue history or AR")
            )
            rule = f"Not both revenue and AR ({had}): re-parent the existing account directly (billing SOP)."
        return {
            "lifetime_revenue": str(rev),
            "outstanding_ar": str(ar),
            "has_revenue": rev > 0,
            "has_ar": ar > 0,
            "needs_chow": rules.needs_chow(acc),
            "rule": rule,
        }

    def signal_rows(self, candidates: list[Candidate]) -> list[dict]:
        rows = []
        for c in candidates:
            s = c.signals
            rows.append(
                {
                    "account_id": c.account_id,
                    "name": self.by_id[c.account_id].get("name"),
                    "band": c.band,
                    "score": c.score,
                    "signals": [
                        {"label": label, "ok": getattr(s, key), "points": rules.WEIGHTS[key]}
                        for key, label in SIGNAL_POINTS
                    ]
                    + [
                        {
                            "label": "Name overlap",
                            "ok": s.name >= rules.LOW_NAME_OVERLAP,
                            "points": round(rules.NAME_MAX * s.name),
                            "detail": f"{s.name:.2f}",
                        }
                    ],
                    "po_box": s.po_box,
                }
            )
        return rows

    def evidence(self, m: LocationMatch | None, account_ids, billing: dict | None = None, **extra) -> dict:
        ruled_out = []
        if m is not None:
            for c in m.ruled_out:
                if c.band is None:
                    reason = "same town only: no shared street, administrator or phone"
                elif m.group:
                    reason = "weaker than the match above"
                else:
                    reason = f"{c.band} match not used"
                ruled_out.append(
                    {
                        "account_id": c.account_id,
                        "name": self.by_id[c.account_id].get("name"),
                        "reason": reason,
                    }
                )
        return {
            "site": (
                {**asdict(m.location), "offerings": list(m.location.offerings)} if m is not None else None
            ),
            "crm": [self.view(a) for a in account_ids if a in self.by_id],
            "signals": self.signal_rows(m.candidates) if m is not None else [],
            "ruled_out": ruled_out,
            "conflicts": list(m.conflicts) if m is not None else [],
            "billing": billing,
            **extra,
        }

    def identity(self, loc: Location) -> dict:
        return {
            "name": loc.name,
            "billing_street": loc.street,
            "billing_city": loc.city,
            "billing_state": loc.state,
            "billing_zip": loc.zip,
            "parent_id": self.op,
            "administrator": loc.administrator,
        }

    def create_steps(self, loc: Location, note: str, key: str) -> list[Step]:
        body = {
            "name": loc.name,
            "billing_street": loc.street,
            "billing_city": loc.city,
            "billing_state": loc.state,
            "billing_zip": loc.zip,
            "phone": loc.phone,
            "care_type": rules.care_for_new(loc),
            "parent_id": self.op,
            "status": "Active",
            "note": note,
        }
        absent = {
            k: body[k]
            for k in ("parent_id", "billing_street", "billing_city", "billing_state", "billing_zip")
        }
        steps = [Step(1, "POST", "/accounts", body, {"absent_account": absent}, None, (key,))]
        if loc.administrator:
            contact = {
                "account_id": {"$created": 1},
                "name": loc.administrator,
                "title": "Administrator",
                "phone": loc.phone,
                "is_active": True,
            }
            steps.append(
                Step(
                    2,
                    "POST",
                    "/contacts",
                    contact,
                    {"absent_contact": {"account_id": {"$created": 1}, "name": loc.administrator}},
                    None,
                    (key,),
                )
            )
        return steps

    # ---- one website location ----------------------------------------------------------------------
    def location(self, m: LocationMatch) -> None:
        loc = m.location
        start = len(self.cards)
        attention_lows = [
            c for c in m.lows if rules.look_alike_needs_person(self.by_id[c.account_id], self.op)
        ]
        if m.conflicts:
            self.attention(loc.slug, loc.slug, loc.name, m.conflicts, self.evidence(m, m.group))
        elif m.group:
            ordered = sorted(
                m.group, key=lambda i: rules.survivor_sort_key(self.by_id[i], loc, self.by_account, self.op)
            )
            if len(ordered) > 1:
                self.duplicates(m, ordered[0], ordered[1:])
            self.matched(m, ordered[0])
        elif attention_lows:
            lines = [
                f"{self.by_id[c.account_id].get('name')} ({c.account_id}) may be this facility, but linking "
                "it would need the billing route (revenue and AR); decide by hand"
                for c in attention_lows
            ]
            self.attention(
                loc.slug, loc.slug, loc.name, lines, self.evidence(m, [c.account_id for c in m.lows])
            )
        else:
            alt = f"alt:{loc.slug}" if m.lows else None
            self.new_account(m, alt)
            for c in m.lows:
                self.possible_match(m, c, alt)
        made = self.cards[start:]
        self.verdicts.append(self.location_verdict(m, made, bool(attention_lows)))

    def location_verdict(self, m: LocationMatch, made: list[Card], attention_lows: bool) -> Verdict:
        loc = m.location
        sets = tuple(c.change_set for c in made)
        if m.conflicts or attention_lows:
            verdict, reason = (
                "Needs attention",
                "; ".join(m.conflicts) or "a look-alike needs the billing route",
            )
        elif m.group:
            top = next(c for c in m.candidates if c.account_id in m.group)
            shared = ", ".join(label.lower() for key, label in SIGNAL_POINTS if getattr(top.signals, key))
            if made:
                verdict, reason = "Match, needs a fix", f"{m.band} match ({shared}); see the cards"
            elif m.band == "High":
                verdict, reason = "Confident match", f"High match ({shared}); nothing to change"
            else:
                verdict, reason = "Medium match, nothing to change", f"Medium match ({shared})"
        elif m.lows:
            verdict, reason = "Possible match only", "only a look-alike by name in the same town"
        else:
            verdict, reason = "New", "no CRM account at this address, phone or administrator"
        return Verdict(loc.slug, loc.name, verdict, m.band, tuple(m.group), sets, reason)

    def matched(self, m: LocationMatch, aid: str) -> None:
        loc, acc = m.location, self.by_id[aid]
        if acc.get("parent_id") != self.op and rules.needs_chow(acc):
            self.chow_new(m, acc)
            return
        own = []
        if acc.get("parent_id") != self.op:
            own.append(Change("set_field", aid, "parent_id", self.op, loc.slug))
        if acc.get("name") != loc.name:
            own.append(Change("set_field", aid, "name", loc.name, loc.slug))
        if acc.get("status") != "Active":
            own.append(Change("set_field", aid, "status", "Active", loc.slug))
        own = self.keep(own)
        if own:
            self.ownership_card(m, acc, own)
        details = self.keep(self.detail_changes(acc, loc))
        if details:
            fields = {c.field: c.value for c in details}
            labels = [
                f"{f.replace('billing_', '')} {acc.get(f) or '(blank)'} → {v}" for f, v in fields.items()
            ]
            step = Step(
                1,
                "PATCH",
                f"/accounts/{aid}",
                fields,
                _patch_expect(acc, fields),
                None,
                tuple(c.key for c in details),
            )
            self.add(
                "details",
                loc.name,
                "Update " + "; ".join(labels),
                loc.slug,
                [aid],
                m.band,
                details,
                [step],
                self.evidence(m, [aid]),
            )

    def detail_changes(self, acc: Account, loc: Location) -> list[Change]:
        site = {
            "phone": loc.phone,
            "billing_street": loc.street,
            "billing_city": loc.city,
            "billing_state": loc.state,
            "billing_zip": loc.zip,
            "care_type": rules.care_for_new(loc),
        }
        differs = {
            "phone": bool(loc.phone) and norm_phone(acc.get("phone")) != norm_phone(loc.phone),
            "billing_street": bool(loc.street)
            and rules.can_sync_street(acc.get("billing_street") or "", loc.street),
            "billing_city": (acc.get("billing_city") or "").strip().lower() != loc.city.lower(),
            "billing_state": (acc.get("billing_state") or "").strip().upper() != loc.state,
            "billing_zip": norm_zip(acc.get("billing_zip")) != loc.zip,
            "care_type": bool(loc.offerings) and not rules.care_compatible(acc.get("care_type"), loc),
        }
        return [
            Change("set_field", acc["account_id"], f, site[f], loc.slug)
            for f in rules.DETAIL_FIELDS
            if differs[f]
        ]

    def ownership_card(self, m: LocationMatch, acc: Account, own: list[Change]) -> None:
        loc, aid = m.location, acc["account_id"]
        fields = {c.field: c.value for c in own}
        moving = "parent_id" in fields
        what, summary = [], []
        if moving:
            was = acc.get("parent_name") or "no parent"
            what.append(f"moved under {self.op_name} (was {was})")
            summary.append(
                f"Move to Bellhaven directly (was {was}; {_dollars(acc.get('lifetime_revenue'))} revenue, "
                f"{_dollars(acc.get('outstanding_ar'))} AR)"
            )
        if "name" in fields:
            what.append(f"renamed from {acc.get('name')!r}")
            summary.append(f"Rename {acc.get('name')!r} → {loc.name!r}")
        if "status" in fields:
            what.append(f"status {acc.get('status')} → Active")
            summary.append(f"Status {acc.get('status')} → Active")
        why = f"listed on Bellhaven's website as {loc.name} ({loc.url})"
        if moving:
            why += "; not both revenue and AR, so it moves directly (billing SOP)"
        step = Step(
            1,
            "PATCH",
            f"/accounts/{aid}",
            fields,
            _patch_expect(acc, fields, ("lifetime_revenue", "outstanding_ar") if moving else (), True),
            self.note(_sentence(", ".join(what)), why),
            tuple(c.key for c in own),
        )
        self.add(
            "wrong_parent" if moving else "rename",
            loc.name,
            "; ".join(summary),
            loc.slug,
            [aid],
            m.band,
            own,
            [step],
            self.evidence(m, [aid], self.billing(acc, moving)),
        )

    def duplicates(self, m: LocationMatch, survivor: str, losers: list[str]) -> None:
        loc, sacc = m.location, self.by_id[survivor]
        changes = self.keep([Change("retire_duplicate", lid, "", survivor, loc.slug) for lid in losers])
        if not changes:
            return
        still = [_survivor_still(sacc, loc)]
        steps: list[Step] = []
        for ch in changes:
            lacc = self.by_id[ch.record]
            steps.append(
                Step(
                    len(steps) + 1,
                    "PATCH",
                    f"/accounts/{ch.record}",
                    {"duplicate_of_account": survivor, "status": "Inactive"},
                    {
                        **_patch_expect(
                            lacc,
                            ("duplicate_of_account", "status"),
                            ("parent_id", "chow_current_account"),
                            True,
                        ),
                        "related": still,
                    },
                    self.note(
                        f"Duplicate of {sacc.get('name')} ({survivor}); marked Inactive",
                        f"same facility as Bellhaven's {loc.name} at {loc.street}, {loc.city}",
                    ),
                    (ch.key,),
                )
            )
            for c in self.by_account.get(ch.record, []):
                steps.append(
                    Step(
                        len(steps) + 1,
                        "PATCH",
                        f"/contacts/{c['contact_id']}",
                        {"account_id": survivor},
                        {"fields": {"account_id": ch.record}, "related": still},
                        None,
                        (ch.key,),
                    )
                )
        billed = [
            a
            for a in [survivor, *losers]
            if rules.money(self.by_id[a].get("lifetime_revenue")) > 0
            or rules.money(self.by_id[a].get("outstanding_ar")) > 0
        ]
        warning = (
            "More than one copy has billing history; check before approving." if len(billed) > 1 else None
        )
        moved = sum(1 for s in steps if s.path.startswith("/contacts/"))
        summary = (
            f"Keep {sacc.get('name')}; retire {len(changes)} duplicate cop{'y' if len(changes) == 1 else 'ies'}"
            + (f" and move {moved} contact{'s' if moved != 1 else ''}" if moved else "")
        )
        self.add(
            "duplicates",
            loc.name,
            summary,
            loc.slug,
            [survivor, *[c.record for c in changes]],
            m.band,
            changes,
            steps,
            self.evidence(m, [survivor, *losers], survivor=survivor, warning=warning),
        )

    def chow_new(self, m: LocationMatch, old: Account) -> None:
        loc, oid = m.location, old["account_id"]
        create = Change("create_account", f"loc:{loc.slug}", "", self.identity(loc), loc.slug)
        pointer = Change("set_chow", oid, "", {"created_for": loc.slug}, loc.slug)
        if len(self.keep([create, pointer])) < 2:
            return  # the two halves are decided together; never half a change of ownership
        rev, ar = _dollars(old.get("lifetime_revenue")), _dollars(old.get("outstanding_ar"))
        note = self.note(
            f"Created for {loc.name} under {self.op_name}",
            f"the previous account {old.get('name')} ({oid}) under {old.get('parent_name') or 'no parent'}"
            f" is kept unchanged for billing ({rev} revenue, {ar} outstanding AR) and points here",
        )
        steps = self.create_steps(loc, note, create.key)
        steps.append(
            Step(
                len(steps) + 1,
                "PATCH",
                f"/accounts/{oid}",
                {"chow_current_account": {"$created": 1}},
                _patch_expect(
                    old,
                    ("chow_current_account",),
                    ("duplicate_of_account", "parent_id", "lifetime_revenue", "outstanding_ar"),
                ),
                None,
                (pointer.key,),
            )
        )
        summary = f"Move to Bellhaven: new account; old kept for billing ({rev} revenue, {ar} AR)"
        self.add(
            "chow",
            loc.name,
            summary,
            loc.slug,
            [oid],
            m.band,
            [create, pointer],
            steps,
            self.evidence(m, [oid], self.billing(old, True)),
        )

    def new_account(self, m: LocationMatch, alt: str | None) -> None:
        loc = m.location
        create = Change("create_account", f"loc:{loc.slug}", "", self.identity(loc), loc.slug)
        if not self.keep([create]):
            return
        steps = self.create_steps(
            loc,
            self.note(f"Created from Bellhaven's website ({loc.url})", "this location had no CRM account"),
            create.key,
        )
        summary = f"Create {loc.name} ({loc.city}, {loc.state}) under Bellhaven"
        if loc.administrator:
            summary += f", with administrator {loc.administrator}"
        if alt:
            summary += "; or choose the possible match"
        self.add(
            "new_account",
            loc.name,
            summary,
            loc.slug,
            [],
            None,
            [create],
            steps,
            self.evidence(m, [c.account_id for c in m.lows]),
            alt,
        )

    def possible_match(self, m: LocationMatch, cand: Candidate, alt: str | None) -> None:
        loc, acc, aid = m.location, self.by_id[cand.account_id], cand.account_id
        changes = []
        if acc.get("parent_id") != self.op:
            changes.append(Change("set_field", aid, "parent_id", self.op, loc.slug))
        if acc.get("name") != loc.name:
            changes.append(Change("set_field", aid, "name", loc.name, loc.slug))
        if acc.get("status") != "Active":
            changes.append(Change("set_field", aid, "status", "Active", loc.slug))
        changes = self.keep(changes + self.detail_changes(acc, loc))
        if not changes:
            return
        fields = {c.field: c.value for c in changes}
        moving = "parent_id" in fields
        step = Step(
            1,
            "PATCH",
            f"/accounts/{aid}",
            fields,
            _patch_expect(acc, fields, ("lifetime_revenue", "outstanding_ar") if moving else (), True),
            self.note(
                f"Linked to Bellhaven's {loc.name}; updated {', '.join(fields)}",
                "a reviewer confirmed this is the same facility",
            ),
            tuple(c.key for c in changes),
        )
        summary = (
            f"Treat {acc.get('name')} as this facility: update {', '.join(f.replace('billing_', '') for f in fields)}"
            f" (name overlap only; no shared street, administrator or phone)"
        )
        self.add(
            "possible_match",
            loc.name,
            summary,
            loc.slug,
            [aid],
            cand.band,
            changes,
            [step],
            self.evidence(m, [aid], self.billing(acc, moving)),
            alt,
        )

    def attention(self, subject: str, slug: str | None, title: str, lines: list[str], evidence: dict) -> None:
        description = "; ".join(lines)
        change = Change("attention", subject, "", digest(description), slug)
        if not self.keep([change]):
            return
        self.add(
            "attention",
            title,
            description,
            slug,
            [a["account_id"] for a in evidence.get("crm", [])],
            None,
            [change],
            [],
            evidence,
        )

    # ---- operator accounts no longer on the website (8.6) ------------------------------------------------
    def leftovers(self, matches: dict[str, LocationMatch]) -> None:
        matched = {aid for m in matches.values() for aid in m.group}
        children = [
            a
            for a in self.accounts
            if a.get("parent_id") == self.op
            and a["account_id"] not in matched
            and a.get("status") != "Inactive"
            and rules.eligible(a, self.accounts, self.reserved_accounts)
        ]
        for old in sorted(children, key=lambda a: a["account_id"]):
            self.leftover(old)

    def leftover(self, old: Account) -> None:
        oid = old["account_id"]
        addr = full_address(
            old.get("billing_street"),
            old.get("billing_city"),
            old.get("billing_state"),
            old.get("billing_zip"),
        )
        others = [
            a
            for a in self.accounts
            if a["account_id"] != oid
            and a.get("parent_id") != self.op
            and not rules.is_parent_account(a, self.accounts)
            and full_address(
                a.get("billing_street"), a.get("billing_city"), a.get("billing_state"), a.get("billing_zip")
            )
            == addr
        ]
        case, buyer = rules.successor_case(old, others, None, self.reserved_accounts)
        start = len(self.cards)
        ev = self.evidence(
            None,
            [oid, *[a["account_id"] for a in others]],
            self.billing(old, case != "gone"),
            same_address=[a["account_id"] for a in others],
        )
        if case == "sold_chow":
            self.sold_chow(old, self.by_id[buyer], ev)
            verdict, reason = "Sold", f"{self.by_id[buyer].get('name')} is at the same address"
        elif case == "sold_duplicate":
            self.sold_duplicate(old, self.by_id[buyer], ev)
            verdict, reason = "Sold", f"{self.by_id[buyer].get('name')} is at the same address"
        elif case == "gone":
            change = Change("set_field", oid, "status", rules.GONE_STATUS, None)
            kept = self.keep([change]) if old.get("status") != rules.GONE_STATUS else []
            if kept:
                day = self.today.isoformat()
                step = Step(
                    1,
                    "PATCH",
                    f"/accounts/{oid}",
                    {"status": rules.GONE_STATUS},
                    _patch_expect(old, ("status",), ("parent_id",), True),
                    self.note(rules.GONE_WHAT.format(day=day), rules.GONE_WHY),
                    (change.key,),
                )
                self.add(
                    "gone",
                    old.get("name"),
                    f"Not on Bellhaven's website: mark {rules.GONE_STATUS}; parent unchanged",
                    None,
                    [oid],
                    None,
                    kept,
                    [step],
                    ev,
                    needs_complete_read=True,
                )
            verdict, reason = "Gone from the website", "no other CRM account at this address"
        elif case == "attention":
            names = ", ".join(a.get("name", "") for a in others)
            lines = [
                (
                    f"{old.get('name')} is no longer on Bellhaven's website; the accounts at its address "
                    f"({names}) are not exactly one Active, compatible account"
                )
            ]
            self.attention(oid, None, old.get("name"), lines, ev)
            verdict, reason = "Needs attention", lines[0]
        else:
            verdict, reason = "Needs attention", "waiting for an unfinished change at the same address"
        sets = tuple(c.change_set for c in self.cards[start:])
        self.verdicts.append(Verdict(oid, old.get("name", ""), verdict, None, (oid,), sets, reason))

    def sold_chow(self, old: Account, buyer: Account, ev: dict) -> None:
        oid, bid = old["account_id"], buyer["account_id"]
        pointer = Change("set_chow", oid, "", bid, None)
        if not self.keep([pointer]):
            return
        rev, ar = _dollars(old.get("lifetime_revenue")), _dollars(old.get("outstanding_ar"))
        steps = [
            Step(
                1,
                "PATCH",
                f"/accounts/{oid}",
                {"chow_current_account": bid},
                _patch_expect(
                    old,
                    ("chow_current_account",),
                    ("duplicate_of_account", "parent_id", "lifetime_revenue", "outstanding_ar"),
                ),
                None,
                (pointer.key,),
            ),
            Step(
                2,
                "PATCH",
                f"/accounts/{bid}",
                {},
                _patch_expect(
                    buyer,
                    (),
                    (
                        "status",
                        "parent_id",
                        "duplicate_of_account",
                        "chow_current_account",
                        *ADDRESS,
                        "care_type",
                    ),
                    True,
                ),
                self.note(
                    f"Now the current account for {old.get('name')} ({oid})",
                    f"{old.get('name')} is no longer on Bellhaven's website and this account is at the same "
                    f"address; the old account is kept unchanged for billing ({rev} revenue, {ar} outstanding "
                    "AR) and points here",
                ),
                (pointer.key,),
            ),
        ]
        summary = f"Sold to {buyer.get('parent_name') or 'another owner'}: point the old account at {buyer.get('name')}; old kept for billing ({rev} revenue, {ar} AR)"
        self.add(
            "chow",
            old.get("name"),
            summary,
            None,
            [oid, bid],
            None,
            [pointer],
            steps,
            ev,
            needs_complete_read=True,
        )

    def sold_duplicate(self, old: Account, buyer: Account, ev: dict) -> None:
        oid, bid = old["account_id"], buyer["account_id"]
        change = Change("retire_duplicate", oid, "", bid, None)
        if not self.keep([change]):
            return
        still = [_buyer_still(buyer)]
        steps = [
            Step(
                1,
                "PATCH",
                f"/accounts/{oid}",
                {"duplicate_of_account": bid, "status": "Inactive"},
                {
                    **_patch_expect(
                        old, ("duplicate_of_account", "status"), ("parent_id", "chow_current_account"), True
                    ),
                    "related": still,
                },
                self.note(
                    f"Duplicate of {buyer.get('name')} ({bid}); marked Inactive",
                    "no longer on Bellhaven's website; the current owner's account is at the same address",
                ),
                (change.key,),
            )
        ]
        for c in self.by_account.get(oid, []):
            steps.append(
                Step(
                    len(steps) + 1,
                    "PATCH",
                    f"/contacts/{c['contact_id']}",
                    {"account_id": bid},
                    {"fields": {"account_id": oid}, "related": still},
                    None,
                    (change.key,),
                )
            )
        self.add(
            "duplicates",
            old.get("name"),
            f"Sold: retire as a duplicate of {buyer.get('name')}",
            None,
            [bid, oid],
            None,
            [change],
            steps,
            ev,
            needs_complete_read=True,
        )


def propose(
    report: ScrapeReport,
    accounts: list[Account],
    contacts: list[Contact],
    operator_id: str,
    today: date,
    reserved_accounts: set[str],
    reserved_locations: set[str],
    decided: frozenset[str] = frozenset(),
) -> Proposal:
    eligible_ids = {a["account_id"] for a in accounts if rules.eligible(a, accounts, reserved_accounts)}
    matches = match_all(report.locations, accounts, contacts, eligible_ids)
    b = _Builder(accounts, contacts, operator_id, today, reserved_accounts, reserved_locations, decided)
    for slug in sorted(matches):
        b.location(matches[slug])
    if report.complete:
        b.leftovers(matches)
    cards = sorted(b.cards, key=lambda c: (rules.SECTIONS.index(c.section), c.title or "", c.change_set))
    return Proposal(cards, sorted(b.verdicts, key=lambda v: v.subject))
