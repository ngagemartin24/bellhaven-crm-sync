import pytest
from conftest import chow_card_existing, new_card, ready
from fake_crm import SimulatedCrash

from bellhaven_sync.apply import approve, check_again, retry
from bellhaven_sync.ledger import Ledger


def test_crash_after_post_reconciles(tmp_path):  # round 3, R3-1
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.crash_after_write("POST")
    with pytest.raises(SimulatedCrash):
        approve(c.id, c.version, l, crm)
    assert Ledger(l.path).recover_interrupted() == {"needs-reconciliation": 1, "failed": 0}
    assert check_again(c.id, l, crm).status == "done"
    assert sum(1 for (m, p, _) in crm.writes if m == "POST" and p == "/accounts") == 1


def test_crash_between_patches_retries(tmp_path):
    l, crm = ready(tmp_path)
    c = chow_card_existing(l, "Z2LGDM")
    crm.crash_after_write("PATCH")
    with pytest.raises(SimulatedCrash):
        approve(c.id, c.version, l, crm)
    assert Ledger(l.path).recover_interrupted() == {"needs-reconciliation": 0, "failed": 1}
    assert retry(c.id, l, crm).status == "done"
    assert (
        sum(1 for (m, p, _) in crm.writes if p.endswith(c.account_ids[0])) == 1
    )  # the pointer was not re-sent
