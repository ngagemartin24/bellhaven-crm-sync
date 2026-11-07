"""The whole intended review against the hand-written answer key (DESIGN 9)."""

from conftest import DAY, EXPECTED, FETCH, PARENT, SNAP, end_state, full_id

from bellhaven_sync.pipeline import run_once
from bellhaven_sync.verify_lib import diff_against_expected


def test_end_state(tmp_path):
    l, crm = end_state(tmp_path)
    assert diff_against_expected(crm, SNAP, EXPECTED) == []
    assert len(crm.list_accounts()) == 127 and len(crm.list_contacts()) == 73
    assert run_once(FETCH, crm, l, PARENT, DAY).counts["new"] == 0


def test_old_chow_accounts_get_one_write_only(tmp_path):
    _l, crm = end_state(tmp_path)
    for s in ("WSXZZB", "CRBLFT", "Z2LGDM"):
        assert [list(b) for (m, p, b) in crm.writes if p.endswith(full_id(s))] == [["chow_current_account"]]


def test_the_checker_catches_an_extra_write(tmp_path):  # the end-state test is not vacuous
    _l, crm = end_state(tmp_path)
    crm.patch_account_raw(full_id("J8DYYV"), {"phone": "(000) 000-0000"})
    crm.patch_account_raw(full_id("WSXZZB"), {"note": "touched"})
    problems = diff_against_expected(crm, SNAP, EXPECTED)
    assert any("J8DYYV phone" in p for p in problems) and any("WSXZZB note" in p for p in problems)
