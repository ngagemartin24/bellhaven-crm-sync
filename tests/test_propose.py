from collections import Counter

from conftest import (
    ACCOUNTS,
    CARDS,
    CEDAR_TRAIL_ID,
    CONTACTS,
    DAY,
    HARBORVIEW_ID,
    OPERATOR_ID,
    PROPOSAL,
    REPORT,
    acct,
    card,
    contact_id,
    full_id,
    incomplete,
    loc_with,
    replace,
    report_with,
    sorted_slugs_of_unchanged_rows,
    suffix,
    suffixes,
)

from bellhaven_sync.models import Change
from bellhaven_sync.propose import propose


def test_card_counts_by_section():
    assert Counter(c.section for c in CARDS) == {
        "Change of ownership": 3,
        "Duplicates": 5,
        "Wrong parent": 4,
        "New accounts": 4,
        "Gone from the website": 2,
        "Possible matches": 1,
        "Renames": 7,
        "Details": 16,
    }


def test_tiffin_change_of_ownership_shape():
    c = card(kind="chow", slug="bellhaven-of-tiffin")
    assert [(s.method, s.path) for s in c.steps] == [
        ("POST", "/accounts"),
        ("POST", "/contacts"),
        ("PATCH", f"/accounts/{full_id('WSXZZB')}"),
    ]
    assert c.steps[0].body["name"] == "Bellhaven of Tiffin" and c.steps[0].body["parent_id"] == OPERATOR_ID
    assert c.steps[1].body["name"] == "Walt Osei" and c.steps[2].body == {
        "chow_current_account": {"$created": 1}
    }
    assert c.steps[2].note_append is None


def test_sandusky_points_at_millstone_only():
    c = card(kind="chow", account="Z2LGDM")
    assert c.steps[0].body == {"chow_current_account": full_id("63BVK3")} and c.steps[0].note_append is None
    assert (
        c.steps[1].path == f"/accounts/{full_id('63BVK3')}"
        and c.steps[1].body == {}
        and c.steps[1].note_append
    )


def test_direct_reparents():
    assert card(kind="wrong_parent", account="9MB6KL").steps[0].body == {"parent_id": OPERATOR_ID}
    assert card(kind="wrong_parent", account="CAE2X4").steps[0].body == {
        "parent_id": OPERATOR_ID,
        "name": "Bellhaven of Kettering",
    }
    assert not any(
        c
        for c in CARDS
        if suffixes(c.account_ids) & {"4R00CD", "6FNN8V", "EKKA3T"} and c.kind in ("chow", "wrong_parent")
    )


def test_owosso_duplicate_moves_tricia():
    c = card(kind="duplicates", slug="bellhaven-of-owosso")
    assert ("PATCH", f"/contacts/{contact_id('Tricia Lindqvist')}") in [(s.method, s.path) for s in c.steps]


def test_gone_cards_and_note():
    gone = [c for c in CARDS if c.kind == "gone"]
    assert suffixes(a for c in gone for a in c.account_ids) == {"7DTQP7", "HWR7QE"}
    assert gone[0].steps[0].note_append == (
        "[Bellhaven sync 2026-11-07] Not listed on Bellhaven's website as of 2026-11-07; no other CRM account "
        "at this address. Confirm closure or sale before re-parenting."
    )


def test_steps_carry_what_was_reviewed():
    lima = card(kind="wrong_parent", account="9MB6KL").steps[0].expect["fields"]
    assert lima == {"parent_id": HARBORVIEW_ID, "lifetime_revenue": 47000, "outstanding_ar": 0, "note": ""}
    tiffin_ptr = card(kind="chow", slug="bellhaven-of-tiffin").steps[2].expect["fields"]
    assert tiffin_ptr == {
        "chow_current_account": "",
        "duplicate_of_account": "",
        "parent_id": CEDAR_TRAIL_ID,
        "lifetime_revenue": 84000,
        "outstanding_ar": 12400,
    }
    goshen = card(kind="details", account="NRLNME").steps[0].expect["fields"]
    assert goshen == {"phone": "(517) 409-8156"}


def test_retirement_is_one_change_per_loser():
    c = card(kind="duplicates", slug="bellhaven-gardens-of-monroe")
    assert sorted((ch.kind, suffix(ch.record), suffix(ch.value)) for ch in c.changes) == [
        ("retire_duplicate", "KM4FHM", "AGG1S5"),
        ("retire_duplicate", "LA9HTX", "AGG1S5"),
    ]


def test_details_never_rewrite_spelling_or_po_box():
    bodies = [s.body for c in CARDS if c.kind == "details" for s in c.steps]
    assert not any("billing_street" in b for b in bodies)
    assert card(kind="details", account="L09P4F").steps[0].body == {"billing_zip": "45662"}


def test_union_square_alternatives_share_a_group():
    new = card(kind="new_account", slug="bellhaven-at-union-square")
    pm = card(kind="possible_match", slug="bellhaven-at-union-square")
    assert new.alt_group == pm.alt_group is not None


def test_incomplete_scrape_builds_no_gone_cards():
    cards = propose(incomplete(REPORT), ACCOUNTS, CONTACTS, OPERATOR_ID, DAY, set(), set()).cards
    assert not any(c.kind == "gone" for c in cards)


def test_conflict_becomes_attention_card():  # 8.8
    cards = propose(
        report_with(loc_with(state="MI", slug="bellhaven-of-tiffin")),
        ACCOUNTS,
        CONTACTS,
        OPERATOR_ID,
        DAY,
        set(),
        set(),
    ).cards
    a = [c for c in cards if c.location_slug == "bellhaven-of-tiffin"]
    assert [c.kind for c in a] == ["attention"] and a[0].steps == ()


def test_decided_field_never_rides_in_a_body():  # round 3, P3
    rejected_phone = Change(
        "set_field", full_id("NRLNME"), "phone", "(330) 405-6040", "bellhaven-of-goshen"
    ).key
    moved = replace(ACCOUNTS, dict(acct("NRLNME"), billing_street="1600 College Avenue"))
    p = propose(REPORT, moved, CONTACTS, OPERATOR_ID, DAY, set(), set(), frozenset({rejected_phone}))
    [d] = [c for c in p.cards if c.kind == "details" and full_id("NRLNME") in c.account_ids]
    assert d.steps[0].body == {"billing_street": "1610 College Ave"}


def test_verdicts_classify_all_35_and_leftovers():  # brief item 2, consistency E1
    v = {x.subject: x.verdict for x in PROPOSAL.verdicts}
    assert len(v) == 38
    assert [s for s, x in v.items() if x == "Confident match"] == sorted_slugs_of_unchanged_rows()
    assert v["bellhaven-of-ashtabula"] == "Medium match, nothing to change"
    assert v[full_id("Z2LGDM")] == "Sold" and v[full_id("7DTQP7")] == "Gone from the website"
    assert v["bellhaven-of-batavia"] == "New" and v["bellhaven-of-tiffin"] == "Match, needs a fix"
    assert v["bellhaven-at-union-square"] == "Possible match only"


def test_money_never_written():
    assert not any(
        k in s.body for c in CARDS for s in c.steps for k in ("lifetime_revenue", "outstanding_ar")
    )


def test_reserved_location_gets_no_create():  # 10.5
    p = propose(REPORT, ACCOUNTS, CONTACTS, OPERATOR_ID, DAY, set(), {"bellhaven-of-batavia"})
    assert not any(c.location_slug == "bellhaven-of-batavia" for c in p.cards)


def test_evidence_is_json_ready():
    import json

    for c in CARDS:
        json.dumps(c.evidence)
        json.dumps([s.body for s in c.steps] + [s.expect for s in c.steps])
