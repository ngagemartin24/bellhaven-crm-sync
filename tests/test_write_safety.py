"""Write safety under failure and concurrency (DESIGN 10-11). Each test pins one finding of the code review."""

import json
from collections import Counter

from conftest import (
    DAY,
    FETCH,
    PARENT,
    SITE,
    chow_card_existing,
    contact_id,
    details_card,
    full_id,
    new_card,
    only,
    ready,
    run_after,
    union_square_cards,
)
from fake_site import fake_fetcher

from bellhaven_sync import pipeline
from bellhaven_sync.apply import acknowledge, approve, check_again, discard, reject, retry
from bellhaven_sync.pipeline import run_once

INCOMPLETE = fake_fetcher(SITE, fail={"/communities/bellhaven-of-tiffin"})


def dup_card(l, slug):
    return only(l.cards("pending"), kind="duplicates", slug=slug)


def gone_card(l, sfx):
    return only(l.cards("pending"), kind="gone", account=sfx)


# ---- the facts a relationship rests on are checked before any write -----------------------------
def test_buyer_moved_since_review_stops_the_sale(tmp_path):
    l, crm = ready(tmp_path)
    c = chow_card_existing(l, "Z2LGDM")  # Sandusky points at Millstone's account at the same address
    crm.patch_account_raw(full_id("63BVK3"), {"billing_street": "1 Elsewhere Rd", "billing_city": "Norwalk"})
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and "billing_street" in r.message and crm.writes == []


def test_retired_survivor_stops_the_duplicate_retirement(tmp_path):
    l, crm = ready(tmp_path)
    c = dup_card(l, "bellhaven-of-owosso")  # keep 2ZTRE6, retire 15UA71 and move Tricia Lindqvist
    crm.patch_account_raw(
        full_id("2ZTRE6"), {"status": "Inactive", "duplicate_of_account": full_id("J8DYYV")}
    )
    r = approve(c.id, c.version, l, crm)
    assert r.status == "failed" and crm.writes == []
    assert crm.get_contact(contact_id("Tricia Lindqvist"))["account_id"] == full_id("15UA71")


# ---- an incomplete website read stops every change that rests on an absence -----------------------
def test_incomplete_read_blocks_the_sale_card(tmp_path):
    l, crm = ready(tmp_path)
    c = chow_card_existing(l, "Z2LGDM")
    run_once(INCOMPLETE, crm, l, PARENT, DAY)
    assert l.card(c.id).status == "blocked"
    assert approve(c.id, c.version, l, crm).status == "refused" and crm.writes == []


def test_retry_of_an_absence_change_waits_for_a_complete_read(tmp_path):
    l, crm = ready(tmp_path)
    c = gone_card(l, "7DTQP7")
    crm.fail_next("PATCH", 422, "status: not accepted")
    assert approve(c.id, c.version, l, crm).status == "failed"
    run_once(INCOMPLETE, crm, l, PARENT, DAY)
    r = retry(c.id, l, crm)
    assert r.status == "refused" and "complete" in r.message and crm.writes == []
    run_once(FETCH, crm, l, PARENT, DAY)
    assert retry(c.id, l, crm).status == "done"


# ---- a run never revives a change decided while it was running ------------------------------------
def _decide_during_propose(monkeypatch, act):
    real, fired = pipeline.propose, []

    def racing(*args, **kwargs):
        out = real(*args, **kwargs)
        if not fired:
            fired.append(True)
            act()
        return out

    monkeypatch.setattr(pipeline, "propose", racing)


def test_rejection_during_a_run_is_not_revived(tmp_path, monkeypatch):
    l, crm = ready(tmp_path)
    goshen = details_card(l, "NRLNME")
    _decide_during_propose(monkeypatch, lambda: reject(goshen.id, goshen.version, l))
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards() if c.change_set == goshen.change_set and c.status == "pending"]


def test_approval_during_a_run_is_not_proposed_twice(tmp_path, monkeypatch):
    l, crm = ready(tmp_path)
    batavia = new_card(l, "bellhaven-of-batavia")
    _decide_during_propose(monkeypatch, lambda: l.claim(batavia.id, batavia.version))
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards("pending") if c.change_set == batavia.change_set]


def test_claim_refuses_a_change_already_decided(tmp_path):
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    l.decide(c.id, "rejected")  # a decision exists though the card still shows pending
    assert approve(c.id, c.version, l, crm).status == "refused" and crm.writes == []


def test_claim_refuses_while_another_change_to_the_record_is_unfinished(tmp_path):
    l, crm = ready(tmp_path)
    pending = [c for c in l.cards("pending") if not c.alt_group]
    shared = next(a for a, n in Counter(a for c in pending for a in c.account_ids).items() if n > 1)
    first, second = [c for c in pending if shared in c.account_ids][:2]
    crm.fail_next("PATCH", 422, "not accepted")
    assert approve(first.id, first.version, l, crm).status == "failed"
    r = approve(second.id, second.version, l, crm)
    assert r.status == "refused" and "unfinished" in r.message and crm.writes == []


# ---- an answer that never arrived is not a failure ------------------------------------------------
def test_lost_answer_to_an_update_cannot_be_discarded(tmp_path):
    l, crm = ready(tmp_path)
    c = dup_card(l, "bellhaven-of-owosso")
    crm.lose_next_response("PATCH")  # the retirement lands; its answer is lost
    assert approve(c.id, c.version, l, crm).status == "failed"
    r = discard(c.id, l, crm)
    assert r.status == "refused" and "landed" in r.message
    assert retry(c.id, l, crm).status == "done"
    assert crm.get_contact(contact_id("Tricia Lindqvist"))["account_id"] == full_id("2ZTRE6")


def test_a_server_error_on_a_create_is_uncertain(tmp_path):
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    crm.fail_next("POST", 502, "Bad Gateway")
    assert approve(c.id, c.version, l, crm).status == "needs-reconciliation"
    assert retry(c.id, l, crm).status == "refused"  # never re-sent


def test_a_create_answered_without_an_id_is_adopted_not_resent(tmp_path, monkeypatch):
    l, crm = ready(tmp_path)
    c = new_card(l, "bellhaven-of-batavia")
    real = crm.create_account
    monkeypatch.setattr(crm, "create_account", lambda body: {**real(body), "account_id": None})
    assert approve(c.id, c.version, l, crm).status == "needs-reconciliation"
    monkeypatch.setattr(crm, "create_account", real)
    assert check_again(c.id, l, crm).status == "done"
    assert sum(1 for m, p, _ in crm.writes if m == "POST" and p == "/accounts") == 1


def test_retry_keeps_the_original_request_and_before_values(tmp_path):
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    crm.lose_next_response("PATCH")
    approve(c.id, c.version, l, crm)
    first = l.card(c.id).operation["steps"][0]
    assert retry(c.id, l, crm).status == "done"
    step = l.card(c.id).operation["steps"][0]
    assert json.loads(step["body"]) == json.loads(first["body"]) != {}
    assert json.loads(step["before"]) == json.loads(first["before"]) and step["wrote"] == 1


# ---- the reviewer's words and an unresolved "none of these" are kept ---------------------------------
def test_approval_keeps_the_reviewer_note(tmp_path):
    l, crm = ready(tmp_path)
    c = details_card(l, "NRLNME")
    assert approve(c.id, c.version, l, crm, note="phone confirmed with the front desk").status == "done"
    assert [h["note"] for h in l.history() if h["card"]["id"] == c.id] == [
        "phone confirmed with the front desk"
    ]


def test_none_of_these_survives_the_next_run_until_acknowledged(tmp_path):
    l, crm = ready(tmp_path)
    new, pm = union_square_cards(l)
    reject(pm.id, pm.version, l)
    reject(new.id, new.version, l)
    open_ = [c for c in run_after(l, crm) if c.location_slug == "bellhaven-at-union-square"]
    assert [c.kind for c in open_] == ["attention"]
    assert acknowledge(open_[0].id, open_[0].version, l).status == "done"
    assert not [c for c in run_after(l, crm) if c.location_slug == "bellhaven-at-union-square"]
