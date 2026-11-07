"""Every judgment call in one file (SC-7). Each rule has a test; change a rule here and run the tests.

The matching thresholds, the billing rule, the duplicate-survivor order, which fields sync, what happens to an
account that left the website, and the wording of CRM notes all live here.
"""

from datetime import date

from .models import Account, Location, Signals
from .normalize import is_po_box, map_care, streets_equivalent

# ---- matching (DESIGN 7) ---------------------------------------------------------------------------
WEIGHTS = {"street": 50, "admin": 25, "phone": 10, "zip": 5, "city": 5}
NAME_MAX = 10
LOW_NAME_OVERLAP = 0.5


def passes_gate(loc: Location, acc: Account, s: Signals) -> bool:
    """Compare a pair only when the state matches and something local is shared (7.2)."""
    same_state = (acc.get("billing_state") or "").strip().upper() == loc.state
    return same_state and (s.city or s.zip or s.phone or s.admin)


def is_state_conflict(loc: Location, acc: Account, s: Signals) -> bool:
    """Different state but the same street and ZIP, or the same phone and administrator: a possible typo (7.2)."""
    same_state = (acc.get("billing_state") or "").strip().upper() == loc.state
    return not same_state and ((s.street and s.zip) or (s.phone and s.admin))


def score(s: Signals) -> int:
    """Points for ordering candidates and showing evidence; the band decides (7.3)."""
    return sum(w for name, w in WEIGHTS.items() if getattr(s, name)) + round(NAME_MAX * s.name)


def band(s: Signals) -> str | None:
    """High, Medium, Low or no match (7.4). Same town alone is never a match."""
    place = s.city or s.zip
    if s.street and place:
        return "High"
    if place and (not s.street or s.po_box) and (s.admin or s.phone):
        return "Medium"
    if place and not (s.admin or s.phone) and s.name >= LOW_NAME_OVERLAP:
        return "Low"
    return None


# ---- money and the billing rule (8.3) --------------------------------------------------------------
def money(v) -> int:
    if v is None or v == "":
        return 0
    return int(float(v))


def needs_chow(acc: Account) -> bool:
    """The SOP: revenue history AND outstanding AR means keep the old account and create a new one."""
    return money(acc.get("lifetime_revenue")) > 0 and money(acc.get("outstanding_ar")) > 0


# ---- who takes part (7.1) ---------------------------------------------------------------------------
PARENT_SUFFIX = "(Parent Account)"
HARBORVIEW_PARENT_NAME = "Harborview Care Group (Parent Account)"


def is_parent_account(acc: Account, accounts: list[Account]) -> bool:
    if (acc.get("name") or "").endswith(PARENT_SUFFIX):
        return True
    return any(a.get("parent_id") == acc["account_id"] for a in accounts)


def eligible(acc: Account, accounts: list[Account], reserved_accounts: set[str]) -> bool:
    return not (
        is_parent_account(acc, accounts)
        or acc.get("duplicate_of_account")
        or acc.get("chow_current_account")
        or acc["account_id"] in reserved_accounts
    )


# ---- duplicates (8.4) -------------------------------------------------------------------------------
def survivor_sort_key(acc: Account, loc: Location, contacts_by_account: dict, operator_id: str) -> tuple:
    """Lower sorts first: billing history, under the operator, has contacts, closest to the site, Harborview, id."""
    from .normalize import norm_phone, same_person

    revenue, ar = money(acc.get("lifetime_revenue")), money(acc.get("outstanding_ar"))
    contacts = contacts_by_account.get(acc["account_id"], [])
    closeness = sum(
        [
            (acc.get("billing_street") or "") == loc.street,
            bool(loc.phone) and norm_phone(acc.get("phone")) == norm_phone(loc.phone),
            any(same_person(c.get("name"), loc.administrator) for c in contacts),
        ]
    )
    return (
        0 if (revenue > 0 or ar > 0) else 1,
        -revenue,
        0 if acc.get("parent_id") == operator_id else 1,
        0 if contacts else 1,
        -closeness,
        0 if acc.get("parent_name") == HARBORVIEW_PARENT_NAME else 1,
        acc["account_id"],
    )


# ---- which fields sync (8.7) -------------------------------------------------------------------------
DETAIL_FIELDS = ("phone", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type")


def can_sync_street(crm_street: str, site_street: str) -> bool:
    """Never rewrite a PO Box billing street, and never rewrite a street that differs only in spelling."""
    return not is_po_box(crm_street) and not streets_equivalent(crm_street, site_street)


def care_compatible(crm_care: str, loc: Location) -> bool:
    return (crm_care or "") in {map_care(o) for o in loc.offerings}


def care_for_new(loc: Location) -> str:
    return map_care(loc.offerings[0]) if loc.offerings else ""


# ---- accounts that left the website (8.6) --------------------------------------------------------------
def successor_case(
    old: Account,
    same_address_others: list[Account],
    loc_offerings: tuple[str, ...] | None,
    reserved: set[str],
) -> tuple[str, str | None]:
    """What happened to an operator account no longer on the website, judged by the other accounts at its address."""
    if not same_address_others:
        return ("gone", None)
    if any(a["account_id"] in reserved for a in same_address_others):
        return ("wait", None)

    def compatible(a: Account) -> bool:
        if loc_offerings is not None:
            return (a.get("care_type") or "") in {map_care(o) for o in loc_offerings}
        mine, theirs = old.get("care_type") or "", a.get("care_type") or ""
        return not mine or not theirs or mine == theirs

    buyers = [
        a
        for a in same_address_others
        if a.get("status") == "Active"
        and not a.get("duplicate_of_account")
        and not a.get("chow_current_account")
        and compatible(a)
    ]
    if len(buyers) != 1:
        return ("attention", None)
    return ("sold_chow" if needs_chow(old) else "sold_duplicate", buyers[0]["account_id"])


# ---- review sections (8.1) ------------------------------------------------------------------------------
SECTIONS = (
    "Change of ownership",
    "Duplicates",
    "Wrong parent",
    "New accounts",
    "Gone from the website",
    "Possible matches",
    "Renames",
    "Details",
    "Needs attention",
)
SECTION_FOR_KIND = {
    "chow": "Change of ownership",
    "duplicates": "Duplicates",
    "wrong_parent": "Wrong parent",
    "new_account": "New accounts",
    "gone": "Gone from the website",
    "possible_match": "Possible matches",
    "rename": "Renames",
    "details": "Details",
    "attention": "Needs attention",
}


# ---- CRM notes (8.9) --------------------------------------------------------------------------------------
GONE_WHAT = "Not listed on Bellhaven's website as of {day}"
GONE_WHY = "no other CRM account at this address. Confirm closure or sale before re-parenting."


def note_line(day: date, what: str, why: str) -> str:
    return f"[Bellhaven sync {day.isoformat()}] {what}; {why}"
