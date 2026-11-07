import pytest
from conftest import (
    DAY,
    FETCH,
    PARENT,
    SITE,
    SNAP,
    contact_id,
    fresh,
    full_id,
    goshen_with_phone,
    only,
    suffix,
)
from fake_crm import FakeCrm
from fake_site import fake_fetcher

from bellhaven_sync.ledger import Ledger
from bellhaven_sync.pipeline import RunAborted, run_once


def reject(l, c):
    l.decide(c.id, "rejected")
    l.set_status(c.id, "pending", "rejected")


def test_rejected_never_returns(tmp_path):
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    reject(l, only(l.cards("pending"), kind="possible_match"))
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards("pending") if c.kind == "possible_match"]


def test_regrouped_rejection_stays_rejected(tmp_path):
    crm = FakeCrm.from_snapshot(SNAP)
    crm.patch_account_raw(full_id("NRLNME"), {"billing_street": "1600 College Avenue"})
    l = Ledger(tmp_path / "d.db")
    run_once(FETCH, crm, l, PARENT, DAY)
    d = only(l.cards("pending"), kind="details", account="NRLNME")
    assert {"phone", "billing_street"} <= set(d.steps[0].body)
    reject(l, d)
    crm.patch_account_raw(full_id("NRLNME"), {"billing_street": "1610 College Avenue"})
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards("pending") if full_id("NRLNME") in c.account_ids]


def test_monroe_retirement_returns_whole(tmp_path):  # 10.2, round 3 P5
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    reject(l, only(l.cards("pending"), kind="duplicates", slug="bellhaven-gardens-of-monroe"))
    crm.patch_account_raw(full_id("KM4FHM"), {"lifetime_revenue": 1})  # KM4FHM now survives (billing first)
    run_once(FETCH, crm, l, PARENT, DAY)
    m2 = only(l.cards("pending"), kind="duplicates", slug="bellhaven-gardens-of-monroe")
    assert {
        suffix(s.path.rsplit("/", 1)[1]): s.body for s in m2.steps if s.path.startswith("/accounts/")
    } == {
        "LA9HTX": {"duplicate_of_account": full_id("KM4FHM"), "status": "Inactive"},
        "AGG1S5": {"duplicate_of_account": full_id("KM4FHM"), "status": "Inactive"},
    }
    [move] = [s for s in m2.steps if s.path.startswith("/contacts/")]  # Elaine Vogel leaves AGG1S5
    assert move.path.endswith(contact_id("Elaine Vogel")) and move.body == {"account_id": full_id("KM4FHM")}


def test_prior_rejection_is_linked(tmp_path):  # 10.3 step 4
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    reject(l, only(l.cards("pending"), kind="details", account="NRLNME"))
    new_site = fake_fetcher(
        SITE, replace={"/communities/bellhaven-of-goshen": goshen_with_phone("(330) 405-9999")}
    )
    run_once(new_site, crm, l, PARENT, DAY)
    d = only(l.cards("pending"), kind="details", account="NRLNME")
    assert d.prior_rejections[0]["value"] == "(330) 405-6040"


def test_run_records_verdicts(tmp_path):
    l, crm = fresh(tmp_path)
    s = run_once(FETCH, crm, l, PARENT, DAY)
    assert len(s.verdicts) == 38 and len(l.verdicts()) == 38


def test_crm_read_failure_leaves_ledger_untouched(tmp_path):
    l = Ledger(tmp_path / "d.db")
    crm = FakeCrm.from_snapshot(SNAP)
    crm.fail_next("GET", 500, "down")
    with pytest.raises(RunAborted):
        run_once(FETCH, crm, l, PARENT, DAY)
    assert l.latest_run() is None and l.cards() == []


def test_operator_must_exist_exactly_once(tmp_path):
    with pytest.raises(RunAborted):
        run_once(FETCH, FakeCrm.from_snapshot(SNAP), Ledger(tmp_path / "d.db"), "No Such Parent", DAY)
