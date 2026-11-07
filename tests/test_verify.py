from conftest import SNAP, end_state, full_id
from fake_crm import FakeCrm
from verify import verify_main


def test_verify_passes_on_expected_state(tmp_path, capsys):
    _, crm = end_state(tmp_path)
    assert verify_main(crm) == 0
    assert capsys.readouterr().out.startswith("PASS")


def test_verify_reports_a_mismatch(tmp_path, capsys):
    _, crm = end_state(tmp_path)
    crm.patch_account_raw(full_id("NRLNME"), {"phone": "(000) 000-0000"})
    assert verify_main(crm) == 1 and "NRLNME phone" in capsys.readouterr().out


def test_verify_fails_on_the_untouched_crm(capsys):  # the check is not vacuous
    assert verify_main(FakeCrm.from_snapshot(SNAP)) == 1
    assert "FAIL" in capsys.readouterr().out
