from datetime import timedelta

from conftest import (
    DAY,
    FETCH,
    PARENT,
    batavia_record_under_operator,
    chow_card,
    details_card,
    full_id,
    new_card,
    ready,
    run_after,
    utcnow,
    wrong_parent_card,
)

from bellhaven_sync.apply import approve, check_again, discard, reject, retry
from bellhaven_sync.pipeline import run_once


def test_failure_then_retry_no_second_create(tmp_path):
    l, crm = ready(tmp_path)
    c = chow_card(l, "bellhaven-of-tiffin")
    crm.fail_next("POST", 500, "boom", after=1)  # step 1 succeeds, step 2 fails
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and r.message.startswith("Failed at step 2 of 3")
    assert retry(c.id, l, crm).status == "done"
    assert (
        sum(1 for (m, p, _) in crm.writes if m == "POST" and p == "/accounts") == 1
    )  # the account step was not re-sent
    assert (
        sum(1 for a in crm.list_accounts() if a["name"] == "Bellhaven of Tiffin") == 2
    )  # the kept billing account + the new one


def test_lost_post_reconciles_without_reposting(tmp_path):
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.lose_next_response("POST")
    assert approve(c.id, c.version, l, crm).status == "needs-reconciliation"
    assert discard(c.id, l).status == "refused"  # too soon: it may have landed
    assert check_again(c.id, l, crm).status == "done"
    assert sum(1 for (m, p, _) in crm.writes if m == "POST" and p == "/accounts") == 1


def test_changed_phone_stops_before_writing(tmp_path):  # round 2, N1
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    crm.patch_account_raw(full_id("NRLNME"), {"phone": "(330) 555-0123"})
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and "phone is now (330) 555-0123, not (517) 409-8156" in r.message
    assert crm.writes == []
    assert discard(c.id, l).status == "done" and run_once(FETCH, crm, l, PARENT, DAY).counts["new"] >= 1


def test_new_ar_stops_reparent(tmp_path):
    l, crm = ready(tmp_path)
    c = wrong_parent_card(l, "9MB6KL")
    crm.patch_account_raw(full_id("9MB6KL"), {"outstanding_ar": 100})
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and "outstanding_ar" in r.message and crm.writes == []


def test_created_elsewhere_stops_create(tmp_path):
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.add_account(batavia_record_under_operator())
    assert approve(c.id, c.version, l, crm).status == "failed" and crm.writes == []


def test_half_done_chow_reserves_both_accounts(tmp_path):
    l, crm = ready(tmp_path)
    c = chow_card(l, "bellhaven-of-tiffin")
    crm.fail_next("PATCH", 500, "boom")  # steps 1-2 (POSTs) succeed, step 3 fails
    assert approve(c.id, c.version, l, crm).status == "failed"
    assert discard(c.id, l).status == "refused"
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [
        x
        for x in l.cards("pending")
        if x.location_slug == "bellhaven-of-tiffin" or full_id("WSXZZB") in x.account_ids
    ]


def test_cleared_ar_stops_chow_before_any_create(tmp_path):  # round 3, R3-3
    l, crm = ready(tmp_path)
    c = chow_card(l, "bellhaven-of-tiffin")
    crm.patch_account_raw(full_id("WSXZZB"), {"outstanding_ar": 0})
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and "outstanding_ar" in r.message and crm.writes == []


def test_same_second_create_is_adopted(tmp_path):  # round 3, R3-2
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.lose_next_response("POST")
    crm.freeze_clock("2026-11-07 23:00:00Z")
    approve(c.id, c.version, l, crm)
    assert check_again(c.id, l, crm).status == "done"


def test_uncertain_discard_waits_ten_minutes(tmp_path):  # 11 step 5
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.drop_next_write("POST")
    t0 = utcnow()
    assert approve(c.id, c.version, l, crm).status == "needs-reconciliation"
    assert check_again(c.id, l, crm, now=lambda: t0 + timedelta(minutes=5)).message == "Not found yet"
    assert discard(c.id, l, now=lambda: t0 + timedelta(minutes=5)).status == "refused"
    check_again(c.id, l, crm, now=lambda: t0 + timedelta(minutes=11))
    assert discard(c.id, l, now=lambda: t0 + timedelta(minutes=11)).status == "done"


def test_discard_refused_after_account_landed(tmp_path):  # round 4, R4-1
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.drop_next_write("POST", after=1)  # account lands, contact uncertain
    t0 = utcnow()
    assert approve(c.id, c.version, l, crm).status == "needs-reconciliation"
    check_again(c.id, l, crm, now=lambda: t0 + timedelta(minutes=11))
    assert discard(c.id, l, now=lambda: t0 + timedelta(minutes=11)).status == "refused"


def test_rejected_write_shape_stops_cleanly(tmp_path):  # Review Focus 1
    l, crm = ready(tmp_path)
    crm.reject_fields({"care_type"})
    c = new_card(l, "bellhaven-of-batavia")
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and "Failed at step 1 of 2" in r.message and crm.writes == []


def test_double_approve_writes_once(tmp_path):  # 11 step 1
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    assert approve(c.id, c.version, l, crm).status == "done"
    assert approve(c.id, c.version, l, crm).status == "refused" and len(crm.writes) == 1


def test_stale_version_is_refused(tmp_path):
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    assert approve(c.id, "old", l, crm).status == "refused" and crm.writes == []
    assert reject(c.id, "old", l).status == "refused"


def test_retry_after_lost_patch_answer_does_not_rewrite(tmp_path):
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    crm.lose_next_response("PATCH")
    assert approve(c.id, c.version, l, crm).status == "failed"
    assert retry(c.id, l, crm).status == "done" and len(crm.writes) == 1  # already at target: no second write


def test_note_line_appended_once_and_earlier_note_kept(tmp_path):
    l, crm = ready(tmp_path)
    crm.patch_account_raw(full_id("9MB6KL"), {"note": "Called 10/2"})
    run_after(l, crm)  # the card now reviews the edited note
    c = wrong_parent_card(l, "9MB6KL")
    assert approve(c.id, c.version, l, crm).status == "done"
    note = crm.get_account(full_id("9MB6KL"))["note"]
    assert note.startswith("Called 10/2\n[Bellhaven sync") and note.count("[Bellhaven sync") == 1
