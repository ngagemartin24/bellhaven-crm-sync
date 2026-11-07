from conftest import only, ready, run_after, union_square_cards

from bellhaven_sync.apply import approve, discard, reject, retry
from bellhaven_sync.ledger import Ledger


def test_two_alternatives_one_wins(tmp_path):
    l, _crm = ready(tmp_path)
    new, pm = union_square_cards(l)
    l2 = Ledger(l.path)
    ops = [l.claim(new.id, new.version), l2.claim(pm.id, pm.version)]
    assert sum(op is not None for op in ops) == 1


def test_owner_retries_and_finishes(tmp_path):  # round 3, P5
    l, crm = ready(tmp_path)
    new, pm = union_square_cards(l)
    crm.fail_next("POST", 500, "boom")
    assert approve(new.id, new.version, l, crm).status == "failed"
    assert retry(new.id, l, crm).status == "done" and l.card(pm.id).status == "superseded"


def test_rejecting_every_choice_raises_attention(tmp_path):  # 10.6, consistency E3
    l, _crm = ready(tmp_path)
    new, pm = union_square_cards(l)
    reject(pm.id, pm.version, l)
    reject(new.id, new.version, l)
    assert [c.kind for c in l.cards("pending") if c.location_slug == "bellhaven-at-union-square"] == [
        "attention"
    ]


def test_owner_retries_and_discard_reopens(tmp_path):  # round 2, N4
    l, crm = ready(tmp_path)
    new, pm = union_square_cards(l)
    crm.fail_next("POST", 422, "bad field")
    assert approve(new.id, new.version, l, crm).status == "failed"
    assert (
        approve(pm.id, pm.version, l, crm).status == "refused"
    )  # the group belongs to the new-account operation
    assert discard(new.id, l).status == "done" and l.card(pm.id).status == "pending"
    new2 = only(run_after(l, crm), kind="new_account", slug="bellhaven-at-union-square")
    assert approve(new2.id, new2.version, l, crm).status == "done" and l.card(pm.id).status == "superseded"
