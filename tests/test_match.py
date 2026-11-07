import pytest
from conftest import (
    ACCOUNTS,
    CONTACTS_BY_ACCOUNT,
    ELIGIBLE,
    LOC,
    MATCHES,
    acct,
    acct_by_name,
    all_candidate_suffixes,
    full_id,
    loc_with,
    match_module,
    replace,
    sig,
    suffixes,
)

from bellhaven_sync.match import match_location
from bellhaven_sync.rules import band, eligible, money, needs_chow, successor_case


@pytest.mark.parametrize("slug,group,band_", [
    ("bellhaven-of-maplewood", ["J8DYYV"], "High"),
    ("bellhaven-of-ashtabula", ["EPSLSV"], "Medium"),
    ("bellhaven-of-owosso", ["2ZTRE6", "15UA71"], "High"),
    ("bellhaven-gardens-of-monroe", ["AGG1S5", "LA9HTX", "KM4FHM"], "High"),
    ("bellhaven-of-kettering", ["CAE2X4", "RXS09J", "ALS9GP"], "High"),
    ("bellhaven-of-port-clinton", ["KB8932", "LTSN24"], "High"),
    ("bellhaven-shores-of-erie", ["YHN220", "SZLZHH"], "High"),
])
def test_groups(slug, group, band_):
    m = MATCHES[slug]
    assert suffixes(m.group) == set(group) and m.band == band_


def test_look_alikes():
    us = MATCHES["bellhaven-at-union-square"]
    assert us.group == [] and suffixes(c.account_id for c in us.lows) == {"RLLJ9P"}
    assert "10RGY5" not in all_candidate_suffixes("amberly-manor")  # Colorado
    assert "RES536" not in all_candidate_suffixes("bellhaven-of-carlisle")  # New Carlisle, OH
    assert MATCHES["bellhaven-of-maplewood"].lows == []  # 2DJ971 suppressed


def test_new_locations_have_no_match():
    for s in ("bellhaven-of-batavia", "bellhaven-of-carlisle", "amberly-manor", "bellhaven-at-union-square"):
        assert MATCHES[s].group == []


def test_no_conflicts_today():
    assert all(m.conflicts == [] for m in MATCHES.values())


def test_state_conflict_is_reported():  # same street+zip, different state
    m = match_location(loc_with(state="MI", slug="bellhaven-of-tiffin"), ACCOUNTS, CONTACTS_BY_ACCOUNT, ELIGIBLE)
    assert m.group == [] and m.conflicts


def test_care_type_splits_a_duplicate_group():  # 7.5
    odd = dict(acct("LTSN24"), care_type="Independent Living")
    m = match_location(LOC["bellhaven-of-port-clinton"], replace(ACCOUNTS, odd), CONTACTS_BY_ACCOUNT, ELIGIBLE)
    assert suffixes(m.group) == {"KB8932"} and m.conflicts


def test_money_and_billing_rule():  # Review Focus 2
    assert money("5200") == 5200 and money(None) == 0 and money("") == 0
    assert needs_chow(acct("WSXZZB")) and needs_chow(acct("CRBLFT")) and needs_chow(acct("Z2LGDM"))
    assert not needs_chow(acct("9MB6KL")) and not needs_chow(acct("YCZ4YM")) and not needs_chow(acct("CAE2X4"))


def test_band_rules_live_in_rules():  # SC-7, F1
    assert band(sig(street=True, city=True)) == "High"
    assert band(sig(street=False, city=True, phone=True)) == "Medium"
    assert band(sig(street=False, city=True, name=0.6)) == "Low"
    assert band(sig(city=True, zip=True)) is None  # same town is not a match
    assert not hasattr(match_module, "band")


def test_successor_cases():  # 8.6, consistency E4
    assert successor_case(acct("Z2LGDM"), [acct("63BVK3")], None, set()) == ("sold_chow", full_id("63BVK3"))
    assert successor_case(acct("7DTQP7"), [], None, set()) == ("gone", None)
    assert successor_case(acct("Z2LGDM"), [dict(acct("63BVK3"), status="Inactive")], None, set()) == ("attention", None)
    assert successor_case(acct("Z2LGDM"), [acct("63BVK3")], None, {full_id("63BVK3")}) == ("wait", None)


def test_eligibility_excludes_retired_and_parents():
    a = dict(acct("WSXZZB"), chow_current_account="X")
    assert not eligible(a, ACCOUNTS, set())
    assert not eligible(acct_by_name("Bellhaven Senior Living (Parent Account)"), ACCOUNTS, set())


def test_two_mediums_without_a_high_is_a_conflict():  # ruling: never guess between two medium matches
    second = dict(acct("EPSLSV"), account_id="001ZZZZZZZZZZZZZZZ", name="Ashtabula Copy",
                  phone=LOC["bellhaven-of-ashtabula"].phone)
    accounts = ACCOUNTS + [second]
    m = match_location(LOC["bellhaven-of-ashtabula"], accounts, CONTACTS_BY_ACCOUNT, ELIGIBLE | {second["account_id"]})
    assert m.group == [] and m.conflicts


@pytest.mark.parametrize("slug,survivor", [
    ("bellhaven-gardens-of-monroe", "AGG1S5"),
    ("bellhaven-of-owosso", "2ZTRE6"),
    ("bellhaven-of-kettering", "CAE2X4"),
    ("bellhaven-of-port-clinton", "KB8932"),
    ("bellhaven-shores-of-erie", "YHN220"),
])
def test_survivor_order_matches_answer_key(slug, survivor):  # 8.4 and section 9
    from conftest import OPERATOR_ID

    from bellhaven_sync.rules import survivor_sort_key

    by_id = {a["account_id"]: a for a in ACCOUNTS}
    m = MATCHES[slug]
    ranked = sorted(m.group, key=lambda i: survivor_sort_key(by_id[i], m.location, CONTACTS_BY_ACCOUNT, OPERATOR_ID))
    assert ranked[0].endswith(survivor)
