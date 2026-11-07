import json

from blind_check import compare, our_answers, prepare
from conftest import BASE, DAY, FETCH, PARENT, SNAP, expected_answers, fresh, full_id, only
from fake_crm import FakeCrm

from bellhaven_sync.pipeline import run_once


def ran(tmp_path):
    l, crm = fresh(tmp_path)
    run_once(FETCH, crm, l, PARENT, DAY)
    return l


def test_prepare_writes_inputs_and_no_answers(tmp_path):
    out = tmp_path / "blind"
    prepare(FETCH, FakeCrm.from_snapshot(SNAP), BASE, out)
    assert sorted(p.name for p in out.iterdir()) == [
        "PROMPT.md",
        "accounts.json",
        "contacts.json",
        "locations.json",
    ]
    assert len(json.loads((out / "locations.json").read_text())) == 35
    assert len(json.loads((out / "accounts.json").read_text())) == 121
    prompt = (out / "PROMPT.md").read_text()
    assert "chow_current_account" in prompt and "duplicate_of_account" in prompt and '"leftovers"' in prompt
    assert "Union Square" not in prompt and "Tiffin" not in prompt  # no hints about any facility


def test_our_answers_equal_the_hand_written_key(tmp_path):
    assert our_answers(ran(tmp_path)) == expected_answers()


def test_agreement_flags_nothing(tmp_path):
    l = ran(tmp_path)
    assert compare(expected_answers(), l) == []
    assert not [c for c in l.cards("pending") if c.flag]


def test_disagreement_flags_the_location_cards(tmp_path):
    l = ran(tmp_path)
    answers = expected_answers()
    answers["locations"]["bellhaven-at-union-square"] = {
        **answers["locations"]["bellhaven-at-union-square"],
        "outcome": "match",
        "account_ids": [full_id("RLLJ9P")],
    }
    rows = compare(answers, l)
    assert [r["subject"] for r in rows] == ["bellhaven-at-union-square"]
    assert only(l.cards("pending"), kind="possible_match").flag.startswith("Blind check disagrees")


def test_look_alike_counts_as_new(tmp_path):
    l = ran(tmp_path)
    answers = expected_answers()
    answers["locations"]["bellhaven-at-union-square"]["outcome"] = "look_alike"
    assert compare(answers, l) == []


def test_missing_and_leftover_disagreements(tmp_path):
    l = ran(tmp_path)
    answers = expected_answers()
    del answers["locations"]["bellhaven-of-goshen"]
    answers["leftovers"][full_id("7DTQP7")] = {"outcome": "inactive", "target": None}
    subjects = sorted(r["subject"] for r in compare(answers, l))
    assert subjects == [full_id("7DTQP7"), "bellhaven-of-goshen"]
    assert only(l.cards("pending"), kind="gone", account="7DTQP7").flag
