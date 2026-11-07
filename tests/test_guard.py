from conftest import DAY, FETCH, PARENT, SITE, SNAP, fresh, full_id
from fake_crm import FakeCrm
from fake_site import fake_fetcher

from bellhaven_sync.ledger import Ledger
from bellhaven_sync.pipeline import run_once


def test_incomplete_run_blocks_gone_cards(tmp_path):  # SC-6
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    s = run_once(fake_fetcher(SITE, fail={"/communities/bellhaven-of-tiffin"}), crm, l, PARENT, DAY)
    assert not s.complete and s.counts["superseded"] == 0
    assert {c.status for c in l.cards() if c.kind == "gone"} == {"blocked"}
    run_once(FETCH, crm, l, PARENT, DAY)
    assert {c.status for c in l.cards() if c.kind == "gone"} == {"pending"}


def test_already_inactive_operator_account_gets_no_card(tmp_path):
    crm = FakeCrm.from_snapshot(SNAP)
    crm.patch_account_raw(full_id("7DTQP7"), {"status": "Inactive"})
    l = Ledger(tmp_path / "d.db")
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards() if full_id("7DTQP7") in c.account_ids]
