from conftest import DAY, FETCH, PARENT, SNAP, fresh
from fake_crm import FakeCrm

from bellhaven_sync.ledger import Ledger
from bellhaven_sync.pipeline import run_once


def test_first_run_records_42(tmp_path):
    s = run_once(FETCH, FakeCrm.from_snapshot(SNAP), Ledger(tmp_path / "d.db"), PARENT, DAY)
    assert s.complete and sum(s.by_section.values()) == 42 and s.counts["new"] == 42


def test_second_run_same_cards_same_ids(tmp_path):
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    ids = {c.id for c in l.cards("pending")}
    s = run_once(FETCH, crm, l, PARENT, DAY)
    assert {c.id for c in l.cards("pending")} == ids and s.counts["new"] == 0


def test_ledger_survives_reopening(tmp_path):
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    again = Ledger(tmp_path / "d.db")
    assert len(again.cards("pending")) == 42 and again.latest_run()["complete"]


def test_claim_is_one_shot(tmp_path):  # 11 step 1: a double click sees "already decided"
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    c = l.cards("pending")[0]
    assert l.claim(c.id, c.version) is not None
    assert l.claim(c.id, c.version) is None and l.card(c.id).status == "applying"


def test_claim_refuses_a_changed_version(tmp_path):
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    c = l.cards("pending")[0]
    assert l.claim(c.id, "stale") is None and l.card(c.id).status == "pending"
