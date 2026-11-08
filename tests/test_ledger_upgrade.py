"""A decision database written before the absence flag existed opens, gains the column and keeps its history."""

import sqlite3

from conftest import ready

from bellhaven_sync.ledger import SCHEMA_VERSION, Ledger


def _flags(l):
    return {c.id: c.needs_complete_read for c in l.cards()}


def test_an_older_ledger_is_upgraded_in_place(tmp_path):
    l, _crm = ready(tmp_path)
    path, want = l.path, _flags(l)
    decided = l._q("SELECT COUNT(*) AS n FROM decisions")[0]["n"]
    assert any(want.values()) and not all(want.values())
    l.close()
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE cards DROP COLUMN needs_complete_read")
    db.execute("PRAGMA user_version=0")
    db.commit()
    db.close()

    with Ledger(path) as up:
        assert _flags(up) == want
        assert up._q("SELECT COUNT(*) AS n FROM decisions")[0]["n"] == decided
        assert up._q("PRAGMA user_version")[0][0] == SCHEMA_VERSION


def test_a_new_ledger_starts_at_the_current_version(tmp_path):
    with Ledger(tmp_path / "new.db") as l:
        assert l._q("PRAGMA user_version")[0][0] == SCHEMA_VERSION
