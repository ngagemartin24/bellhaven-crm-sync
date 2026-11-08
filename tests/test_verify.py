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


def _created(crm, name):
    return next(a for a in crm.list_accounts() if a["name"] == name and a.get("created_by_candidate"))


def test_verify_checks_every_field_of_a_created_account(tmp_path, capsys):  # code review round 5, defect 6
    _, crm = end_state(tmp_path)
    crm.patch_account_raw(
        _created(crm, "Bellhaven of Batavia")["account_id"], {"duplicate_of_account": full_id("RLLJ9P")}
    )
    assert verify_main(crm) == 1 and "duplicate_of_account" in capsys.readouterr().out


def test_verify_checks_the_created_account_note(tmp_path, capsys):
    _, crm = end_state(tmp_path)
    crm.patch_account_raw(_created(crm, "Bellhaven of Batavia")["account_id"], {"note": ""})
    assert verify_main(crm) == 1 and "note" in capsys.readouterr().out


def test_verify_checks_every_field_of_a_created_contact(tmp_path, capsys):
    _, crm = end_state(tmp_path)
    batavia = _created(crm, "Bellhaven of Batavia")["account_id"]
    admin = next(c for c in crm.contacts.values() if c["account_id"] == batavia)
    admin.update(phone="(000) 000-0000", email="invented@example.com")
    out = verify_main(crm), capsys.readouterr().out
    assert out[0] == 1 and "phone" in out[1] and "email" in out[1]
