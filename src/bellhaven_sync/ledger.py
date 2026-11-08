"""The decision memory: runs, cards, decisions and every write step, in one SQLite file (DESIGN 10).

Every status move is one guarded UPDATE (`... WHERE id=? AND status=?`), so a double click, a second tab or a
re-run can never move a card twice. Decisions are stored per change key, never per card (10.2).
"""

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

from .models import Card, Change, Location, Step, Verdict, digest
from .scrape import ScrapeReport

RECONCILE_WAIT = timedelta(minutes=10)
LIVE_OPS = ("applying", "failed", "needs-reconciliation")

SCHEMA_VERSION = 2  # 2: cards.needs_complete_read (cards that rest on a location's absence)
ABSENCE_KINDS = ("gone", "chow", "duplicates")  # with no website location, these rest on an absence

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, complete INTEGER, reason TEXT, pages_failed TEXT,
  found INTEGER, stated INTEGER, accounts_read INTEGER, locations_json TEXT, verdicts_json TEXT, counts_json TEXT);
CREATE TABLE IF NOT EXISTS changes (
  change_key TEXT PRIMARY KEY, kind TEXT, record TEXT, field TEXT, value TEXT, location_slug TEXT,
  first_run INTEGER, last_run INTEGER);
CREATE TABLE IF NOT EXISTS decisions (
  change_key TEXT, decision TEXT, card_id TEXT, at TEXT, reviewer_note TEXT);
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY, change_set TEXT, kind TEXT, section TEXT, title TEXT, summary TEXT, location_slug TEXT,
  account_ids TEXT, band TEXT, changes TEXT, evidence TEXT, steps TEXT, status TEXT, alt_group TEXT, flag TEXT,
  built_run INTEGER, created_at TEXT, refreshed_at TEXT, needs_complete_read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS operations (
  id INTEGER PRIMARY KEY, card_id TEXT, status TEXT, started_at TEXT, finished_at TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS steps (
  op_id INTEGER, n INTEGER, method TEXT, path TEXT, body TEXT, expect TEXT, before TEXT, intent_at TEXT,
  response_status INTEGER, response_body TEXT, created_id TEXT, status TEXT, message TEXT, wrote INTEGER DEFAULT 0,
  checked_empty_at TEXT, adopted_id TEXT, PRIMARY KEY (op_id, n));
CREATE TABLE IF NOT EXISTS reservations (op_id INTEGER, account_id TEXT, location_slug TEXT);
CREATE TABLE IF NOT EXISTS alt_groups (id TEXT PRIMARY KEY, location_slug TEXT, status TEXT, owner_op INTEGER);
CREATE INDEX IF NOT EXISTS decisions_key ON decisions(change_key);
CREATE INDEX IF NOT EXISTS cards_status ON cards(status);
"""


def now_iso(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


@dataclass(frozen=True)
class CardRow(Card):
    id: str = ""
    status: str = ""
    version: str = ""
    flag: str | None = None
    prior_rejections: list = field(default_factory=list)
    operation: dict | None = None


def _changes_json(changes) -> str:
    return json.dumps([[c.kind, c.record, c.field, c.value, c.location_slug] for c in changes])


def _steps_json(steps) -> str:
    return json.dumps([{**asdict(s), "change_keys": list(s.change_keys)} for s in steps])


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        existing = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cards'"
        ).fetchone()
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"{self.path} was written by a newer version (schema {version})")
        self._db.executescript(SCHEMA)
        if not existing:
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif version < SCHEMA_VERSION:
            self._upgrade()

    def _upgrade(self) -> None:
        """Version 1 had no absence flag: add it, mark the cards that rest on an absence, keep everything else."""
        with self.tx() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(cards)")}
            if "needs_complete_read" not in cols:
                db.execute("ALTER TABLE cards ADD COLUMN needs_complete_read INTEGER DEFAULT 0")
                marks = ",".join("?" * len(ABSENCE_KINDS))
                db.execute(
                    f"UPDATE cards SET needs_complete_read=1 WHERE location_slug IS NULL AND kind IN ({marks})",
                    ABSENCE_KINDS,
                )
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def tx(self):
        """One SQLite transaction; card and operation always move together inside one."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ---- runs --------------------------------------------------------------------------------------
    def start_run(self) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO runs(started_at) VALUES (?)", (now_iso(),)).lastrowid

    def finish_run(
        self, run_id: int, report: ScrapeReport, accounts_read: int, counts: dict | None = None
    ) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE runs SET finished_at=?, complete=?, reason=?, pages_failed=?, found=?, stated=?, "
                "accounts_read=?, locations_json=?, counts_json=? WHERE id=?",
                (
                    now_iso(),
                    int(report.complete),
                    report.reason,
                    json.dumps(report.pages_failed),
                    report.found,
                    report.stated,
                    accounts_read,
                    json.dumps([{**asdict(x), "offerings": list(x.offerings)} for x in report.locations]),
                    json.dumps(counts or {}),
                    run_id,
                ),
            )

    def latest_run(self) -> dict | None:
        rows = self._q("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        r = dict(rows[0])
        r["complete"] = bool(r["complete"])
        r["counts"] = json.loads(r.pop("counts_json") or "{}")
        r["pages_failed"] = json.loads(r["pages_failed"] or "[]")
        return r

    def latest_complete_locations(self) -> list[Location]:
        rows = self._q("SELECT locations_json FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1")
        if not rows:
            return []
        return [Location(**{**x, "offerings": tuple(x["offerings"])}) for x in json.loads(rows[0][0])]

    def record_verdicts(self, run_id: int, verdicts: list[Verdict]) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE runs SET verdicts_json=? WHERE id=?",
                (json.dumps([asdict(v) for v in verdicts]), run_id),
            )

    def verdicts(self, run_id: int | None = None) -> list[Verdict]:
        if run_id is None:
            rows = self._q(
                "SELECT verdicts_json FROM runs WHERE complete=1 AND verdicts_json IS NOT NULL "
                "ORDER BY id DESC LIMIT 1"
            )
        else:
            rows = self._q("SELECT verdicts_json FROM runs WHERE id=?", (run_id,))
        if not rows or not rows[0][0]:
            return []
        return [
            Verdict(**{**v, "account_ids": tuple(v["account_ids"]), "change_sets": tuple(v["change_sets"])})
            for v in json.loads(rows[0][0])
        ]

    # ---- cards ---------------------------------------------------------------------------------------
    def decided_keys(self) -> frozenset[str]:
        return frozenset(r[0] for r in self._q("SELECT DISTINCT change_key FROM decisions"))

    def _insert_card(self, db, run_id: int | None, card: Card, status: str = "pending") -> str:
        card_id = secrets.token_hex(6)
        stamp = now_iso()
        db.execute(
            "INSERT INTO cards(id, change_set, kind, section, title, summary, location_slug, account_ids, band, "
            "changes, evidence, steps, status, alt_group, flag, built_run, created_at, refreshed_at, "
            "needs_complete_read) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)",
            (
                card_id,
                card.change_set,
                card.kind,
                card.section,
                card.title,
                card.summary,
                card.location_slug,
                json.dumps(list(card.account_ids)),
                card.band,
                _changes_json(card.changes),
                json.dumps(card.evidence),
                _steps_json(card.steps),
                status,
                card.alt_group,
                run_id,
                stamp,
                stamp,
                int(card.needs_complete_read),
            ),
        )
        for c in card.changes:
            db.execute(
                "INSERT INTO changes VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(change_key) DO UPDATE SET last_run=?",
                (
                    c.key,
                    c.kind,
                    c.record,
                    c.field,
                    json.dumps(c.value),
                    c.location_slug,
                    run_id,
                    run_id,
                    run_id,
                ),
            )
        if card.alt_group:
            db.execute(
                "INSERT OR IGNORE INTO alt_groups VALUES (?,?,'open',NULL)",
                (card.alt_group, card.location_slug),
            )
            db.execute(
                "UPDATE alt_groups SET status='open', owner_op=NULL WHERE id=? AND status LIKE 'closed%'",
                (card.alt_group,),
            )
        return card_id

    def record_cards(self, run_id: int, cards: list[Card], complete: bool) -> dict[str, int]:
        """10.3 steps 3-5: keep a pending card whose change set is unchanged; new card otherwise.

        The run built its cards from decisions and reservations read earlier. Inside this write transaction they
        are read again, so a change decided or claimed while the run was working is never recorded as new."""
        counts = {"new": 0, "refreshed": 0, "superseded": 0, "blocked": 0, "unblocked": 0, "skipped": 0}
        with self.tx() as db:
            open_cards = {
                r["change_set"]: r
                for r in db.execute(
                    "SELECT id, change_set, status, steps, evidence, summary, built_run FROM cards "
                    "WHERE status IN ('pending','blocked')"
                )
            }
            decided = {r[0] for r in db.execute("SELECT DISTINCT change_key FROM decisions")}
            accounts, locations = self._live_reservations(db)
            seen: set[str] = set()
            for card in cards:
                seen.add(card.change_set)
                row = open_cards.get(card.change_set)
                if row is None and (
                    card.change_keys & decided
                    or _touched_accounts(card) & accounts
                    or (card.location_slug and card.location_slug in locations)
                ):
                    counts["skipped"] += 1
                    continue
                if row is None:
                    self._insert_card(db, run_id, card)
                    counts["new"] += 1
                    continue
                counts["refreshed"] += 1
                if (row["steps"], row["evidence"], row["summary"]) != (
                    _steps_json(card.steps),
                    json.dumps(card.evidence),
                    card.summary,
                ):
                    db.execute(
                        "UPDATE cards SET steps=?, evidence=?, summary=?, title=?, refreshed_at=? WHERE id=?",
                        (
                            _steps_json(card.steps),
                            json.dumps(card.evidence),
                            card.summary,
                            card.title,
                            now_iso(),
                            row["id"],
                        ),
                    )
                if row["status"] == "blocked" and complete:
                    db.execute(
                        "UPDATE cards SET status='pending' WHERE id=? AND status='blocked'", (row["id"],)
                    )
                    counts["unblocked"] += 1
            if complete:
                for change_set, row in open_cards.items():
                    # a card the ledger raised itself (none of these) stays until a person acknowledges it
                    if change_set not in seen and row["built_run"] is not None:
                        db.execute(
                            "UPDATE cards SET status='superseded' WHERE id=? AND status=?",
                            (row["id"], row["status"]),
                        )
                        counts["superseded"] += 1
            else:
                cur = db.execute(
                    "UPDATE cards SET status='blocked' WHERE needs_complete_read=1 AND status='pending'"
                )
                counts["blocked"] += cur.rowcount
        return counts

    def _row(self, r: sqlite3.Row) -> CardRow:
        changes = tuple(Change(*c) for c in json.loads(r["changes"]))
        steps = tuple(Step(**{**s, "change_keys": tuple(s["change_keys"])}) for s in json.loads(r["steps"]))
        op = self._q("SELECT * FROM operations WHERE card_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
        operation = None
        if op:
            operation = dict(op[0])
            operation["steps"] = [
                dict(s) for s in self._q("SELECT * FROM steps WHERE op_id=? ORDER BY n", (operation["id"],))
            ]
        return CardRow(
            change_set=r["change_set"],
            kind=r["kind"],
            section=r["section"],
            title=r["title"],
            summary=r["summary"],
            location_slug=r["location_slug"],
            account_ids=tuple(json.loads(r["account_ids"])),
            band=r["band"],
            changes=changes,
            steps=steps,
            evidence=json.loads(r["evidence"]),
            alt_group=r["alt_group"],
            needs_complete_read=bool(r["needs_complete_read"]),
            id=r["id"],
            status=r["status"],
            version=digest([r["change_set"], r["refreshed_at"]]),
            flag=r["flag"],
            prior_rejections=self._prior_rejections(changes),
            operation=operation,
        )

    def _prior_rejections(self, changes) -> list[dict]:
        out = []
        for c in changes:
            if c.kind != "set_field":
                continue
            for r in self._q(
                "SELECT ch.value, d.at FROM decisions d JOIN changes ch ON ch.change_key=d.change_key "
                "WHERE d.decision='rejected' AND ch.kind='set_field' AND ch.record=? AND ch.field=? "
                "AND ch.change_key<>? ORDER BY d.at DESC",
                (c.record, c.field, c.key),
            ):
                out.append(
                    {"record": c.record, "field": c.field, "value": json.loads(r["value"]), "at": r["at"]}
                )
        return out

    def cards(self, status: str | None = None) -> list[CardRow]:
        sql = "SELECT * FROM cards" + (" WHERE status=?" if status else "") + " ORDER BY created_at, id"
        return [self._row(r) for r in self._q(sql, (status,) if status else ())]

    def card(self, card_id: str) -> CardRow:
        rows = self._q("SELECT * FROM cards WHERE id=?", (card_id,))
        if not rows:
            raise KeyError(card_id)
        return self._row(rows[0])

    def set_status(self, card_id: str, expected: str, new: str) -> bool:
        with self.tx() as db:
            return (
                db.execute(
                    "UPDATE cards SET status=? WHERE id=? AND status=?", (new, card_id, expected)
                ).rowcount
                == 1
            )

    def flag(self, card_id: str, reason: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE cards SET flag=? WHERE id=?", (reason, card_id))

    # ---- decisions ---------------------------------------------------------------------------------------
    def decide(self, card_id: str, decision: str, note: str = "") -> None:
        with self.tx() as db:
            self._decide(db, card_id, decision, note)
            if decision == "rejected":
                self._maybe_close_group(db, card_id)

    def close_card(self, card_id: str, version: str, decision: str, note: str = "") -> bool:
        """Reject or acknowledge: pending -> rejected/acknowledged, decisions recorded, a group closed when it was the
        last open choice; all in one transaction, guarded by the reviewed version."""
        status = {"rejected": "rejected", "acknowledged": "acknowledged"}[decision]
        with self.tx() as db:
            r = db.execute(
                "SELECT change_set, refreshed_at, status FROM cards WHERE id=?", (card_id,)
            ).fetchone()
            if (
                r is None
                or r["status"] != "pending"
                or digest([r["change_set"], r["refreshed_at"]]) != version
            ):
                return False
            db.execute("UPDATE cards SET status=? WHERE id=? AND status='pending'", (status, card_id))
            self._decide(db, card_id, decision, note)
            if decision == "rejected":
                self._maybe_close_group(db, card_id)
            return True

    def _decide(self, db, card_id: str, decision: str, note: str) -> None:
        row = db.execute("SELECT changes FROM cards WHERE id=?", (card_id,)).fetchone()
        at = now_iso()
        for c in json.loads(row["changes"]):
            key = Change(*c).key
            db.execute("INSERT INTO decisions VALUES (?,?,?,?,?)", (key, decision, card_id, at, note))

    def _maybe_close_group(self, db, card_id: str) -> None:
        row = db.execute(
            "SELECT alt_group, location_slug, title FROM cards WHERE id=?", (card_id,)
        ).fetchone()
        if not row or not row["alt_group"]:
            return
        others = db.execute(
            "SELECT COUNT(*) FROM cards WHERE alt_group=? AND id<>? AND status IN "
            "('pending','blocked','applying','failed','needs-reconciliation')",
            (row["alt_group"], card_id),
        ).fetchone()[0]
        if others:
            return
        if (
            db.execute(
                "UPDATE alt_groups SET status='closed-none' WHERE id=? AND status='open'", (row["alt_group"],)
            ).rowcount
            != 1
        ):
            return
        description = (
            f"Every choice for {row['title']} was rejected (none of these). Decide by hand whether it "
            "needs a new CRM account or matches another record."
        )
        change = Change(
            "attention",
            row["location_slug"],
            "",
            digest(["none of these", row["alt_group"]]),
            row["location_slug"],
        )
        card = Card(
            change_set=digest([change.key]),
            kind="attention",
            section="Needs attention",
            title=row["title"],
            summary=description,
            location_slug=row["location_slug"],
            account_ids=(),
            band=None,
            changes=(change,),
            steps=(),
            evidence={"conflicts": [description]},
            alt_group=None,
        )
        self._insert_card(db, None, card)

    def _live_reservations(self, db) -> tuple[set[str], set[str]]:
        rows = db.execute(
            "SELECT r.account_id, r.location_slug FROM reservations r JOIN operations o ON o.id=r.op_id "
            f"WHERE o.status IN {LIVE_OPS}"
        ).fetchall()
        return {r[0] for r in rows if r[0]}, {r[1] for r in rows if r[1]}

    def _conflict(self, db, r) -> str | None:
        keys = [Change(*c).key for c in json.loads(r["changes"])]
        if (
            keys
            and db.execute(
                f"SELECT 1 FROM decisions WHERE change_key IN ({','.join('?' * len(keys))}) LIMIT 1", keys
            ).fetchone()
        ):
            return "Already decided"
        accounts, locations = self._live_reservations(db)
        busy = sorted(_row_accounts(r) & accounts)
        if busy:
            return f"Another change to {busy[0]} is unfinished; finish or discard it first"
        if r["location_slug"] and r["location_slug"] in locations and not r["alt_group"]:
            return "Another change for this location is unfinished; finish or discard it first"
        return None

    def claim_conflict(self, card_id: str) -> str | None:
        """Why a claim would be refused now: its change already decided, or a record it writes held by an
        unfinished operation (10.5)."""
        with self.tx() as db:
            r = db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            return self._conflict(db, r) if r else "Not available now"

    def claim(self, card_id: str, version: str, note: str = "") -> int | None:
        """11 step 1: card pending -> applying, a new operation, its reservations and its group, in one go."""
        try:
            return self._claim(card_id, version, note)
        except _Abort:
            return None

    def _claim(self, card_id: str, version: str, note: str) -> int | None:
        with self.tx() as db:
            r = db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if (
                r is None
                or r["status"] != "pending"
                or digest([r["change_set"], r["refreshed_at"]]) != version
                or self._conflict(db, r)
            ):
                return None
            if (
                db.execute(
                    "UPDATE cards SET status='applying' WHERE id=? AND status='pending'", (card_id,)
                ).rowcount
                != 1
            ):
                return None
            op_id = db.execute(
                "INSERT INTO operations(card_id, status, started_at) VALUES (?, 'applying', ?)",
                (card_id, now_iso()),
            ).lastrowid
            if r["alt_group"]:
                taken = db.execute(
                    "UPDATE alt_groups SET status='claimed', owner_op=? WHERE id=? AND status='open'",
                    (op_id, r["alt_group"]),
                ).rowcount
                if taken != 1:
                    raise _Abort()
            for a in sorted(_row_accounts(r)):
                db.execute("INSERT INTO reservations VALUES (?,?,NULL)", (op_id, a))
            if r["location_slug"]:
                db.execute("INSERT INTO reservations VALUES (?,NULL,?)", (op_id, r["location_slug"]))
            self._decide(db, card_id, "approved", note)
            return op_id

    # ---- operations and steps -------------------------------------------------------------------------------
    def operation(self, op_id: int) -> dict:
        op = dict(self._q("SELECT * FROM operations WHERE id=?", (op_id,))[0])
        op["steps"] = [dict(s) for s in self._q("SELECT * FROM steps WHERE op_id=? ORDER BY n", (op_id,))]
        return op

    def step_intent(
        self,
        op_id: int,
        n: int,
        method: str,
        path: str,
        body: dict,
        before: dict | None,
        expect: dict | None = None,
    ) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO steps(op_id, n, method, path, body, expect, before, intent_at, status) "
                "VALUES (?,?,?,?,?,?,?,?, 'intent') ON CONFLICT(op_id, n) DO UPDATE SET body=excluded.body, "
                "before=excluded.before, intent_at=excluded.intent_at, status='intent', message=NULL",
                (op_id, n, method, path, json.dumps(body), json.dumps(expect), json.dumps(before), now_iso()),
            )

    def step_done(
        self,
        op_id: int,
        n: int,
        status: int | None,
        response: dict | None,
        created_id: str | None,
        wrote: bool = True,
    ) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE steps SET status='done', response_status=?, response_body=?, created_id=?, wrote=? "
                "WHERE op_id=? AND n=?",
                (status, json.dumps(response), created_id, int(wrote), op_id, n),
            )
            if created_id:
                db.execute("INSERT INTO reservations VALUES (?,?,NULL)", (op_id, created_id))

    def step_failed(self, op_id: int, n: int, status: int | None, message: str) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE steps SET status='failed', response_status=?, message=? WHERE op_id=? AND n=?",
                (status, message, op_id, n),
            )

    def step_unknown(self, op_id: int, n: int, message: str) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE steps SET status='unknown', message=? WHERE op_id=? AND n=?", (message, op_id, n)
            )

    def step_checked_empty(self, op_id: int, n: int, at: datetime) -> None:
        with self.tx() as db:
            db.execute("UPDATE steps SET checked_empty_at=? WHERE op_id=? AND n=?", (now_iso(at), op_id, n))

    def adopted_ids(self) -> set[str]:
        return {r[0] for r in self._q("SELECT created_id FROM steps WHERE created_id IS NOT NULL")}

    def finish_operation(self, op_id: int, status: str, message: str | None = None) -> None:
        """Operation and card move together; done also releases reservations and settles an alternative group."""
        with self.tx() as db:
            card_id = db.execute("SELECT card_id FROM operations WHERE id=?", (op_id,)).fetchone()[0]
            db.execute(
                "UPDATE operations SET status=?, finished_at=?, message=? WHERE id=?",
                (status, now_iso(), message, op_id),
            )
            db.execute("UPDATE cards SET status=? WHERE id=?", (status, card_id))
            if status == "done":
                db.execute("DELETE FROM reservations WHERE op_id=?", (op_id,))
                self._settle(db, op_id)

    def reopen_operation(self, op_id: int, expected: str) -> bool:
        """Retry or Check again: failed or needs-reconciliation -> applying, card with it."""
        with self.tx() as db:
            if (
                db.execute(
                    "UPDATE operations SET status='applying', message=NULL WHERE id=? AND status=?",
                    (op_id, expected),
                ).rowcount
                != 1
            ):
                return False
            card_id = db.execute("SELECT card_id FROM operations WHERE id=?", (op_id,)).fetchone()[0]
            db.execute("UPDATE cards SET status='applying' WHERE id=? AND status=?", (card_id, expected))
            return True

    def settle_alt_group(self, op_id: int) -> None:
        with self.tx() as db:
            self._settle(db, op_id)

    def _settle(self, db, op_id: int) -> None:
        group = db.execute(
            "SELECT id FROM alt_groups WHERE owner_op=? AND status='claimed'", (op_id,)
        ).fetchone()
        if not group:
            return
        db.execute("UPDATE alt_groups SET status='closed' WHERE id=?", (group[0],))
        owner_card = db.execute("SELECT card_id FROM operations WHERE id=?", (op_id,)).fetchone()[0]
        for sib in db.execute(
            "SELECT id FROM cards WHERE alt_group=? AND id<>? AND status IN ('pending','blocked')",
            (group[0], owner_card),
        ).fetchall():
            self._decide(db, sib[0], "rejected", "another choice was approved")
            db.execute("UPDATE cards SET status='superseded' WHERE id=?", (sib[0],))

    def discard(self, op_id: int, now: datetime) -> bool:
        """Never once a write of the operation is confirmed (R4-1); an uncertain create waits 10 minutes and an
        empty Check again made after that point (11 step 5)."""
        with self.tx() as db:
            op = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
            if op is None or op["status"] not in ("failed", "needs-reconciliation"):
                return False
            steps = db.execute("SELECT * FROM steps WHERE op_id=?", (op_id,)).fetchall()
            if any(s["wrote"] for s in steps):
                return False
            if op["status"] == "needs-reconciliation":
                for s in steps:
                    if s["method"] == "POST" and s["status"] in ("intent", "unknown"):
                        ready = parse_iso(s["intent_at"]) + RECONCILE_WAIT
                        if (
                            now < ready
                            or not s["checked_empty_at"]
                            or parse_iso(s["checked_empty_at"]) < ready
                        ):
                            return False
            db.execute(
                "UPDATE operations SET status='discarded', finished_at=? WHERE id=?", (now_iso(now), op_id)
            )
            db.execute("UPDATE cards SET status='discarded' WHERE id=?", (op["card_id"],))
            db.execute("DELETE FROM decisions WHERE card_id=?", (op["card_id"],))
            db.execute("DELETE FROM reservations WHERE op_id=?", (op_id,))
            db.execute("UPDATE alt_groups SET status='open', owner_op=NULL WHERE owner_op=?", (op_id,))
            return True

    def recover_interrupted(self) -> dict[str, int]:
        """10.4 at app start: an unanswered POST needs reconciliation; anything else failed, Retry resumes."""
        counts = {"needs-reconciliation": 0, "failed": 0}
        for op in self._q("SELECT id FROM operations WHERE status='applying'"):
            steps = self._q("SELECT method, status FROM steps WHERE op_id=?", (op["id"],))
            if any(s["method"] == "POST" and s["status"] in ("intent", "unknown") for s in steps):
                self.finish_operation(
                    op["id"],
                    "needs-reconciliation",
                    "The app stopped before a create's answer arrived; Check again looks for it",
                )
                counts["needs-reconciliation"] += 1
            else:
                self.finish_operation(op["id"], "failed", "Interrupted; Retry resumes")
                counts["failed"] += 1
        return counts

    # ---- reservations and history ----------------------------------------------------------------------------
    def reserved_accounts(self) -> set[str]:
        return {
            r[0]
            for r in self._q(
                "SELECT r.account_id FROM reservations r JOIN operations o ON o.id=r.op_id "
                f"WHERE r.account_id IS NOT NULL AND o.status IN {LIVE_OPS}"
            )
        }

    def reserved_locations(self) -> set[str]:
        return {
            r[0]
            for r in self._q(
                "SELECT r.location_slug FROM reservations r JOIN operations o ON o.id=r.op_id "
                f"WHERE r.location_slug IS NOT NULL AND o.status IN {LIVE_OPS}"
            )
        }

    def history(self) -> list[dict]:
        out = []
        for d in self._q(
            "SELECT card_id, decision, MAX(at) AS at, reviewer_note FROM decisions "
            "GROUP BY card_id, decision ORDER BY at DESC"
        ):
            card = self._q(
                "SELECT id, title, kind, section, summary, status FROM cards WHERE id=?", (d["card_id"],)
            )
            ops = [
                dict(o)
                for o in self._q("SELECT * FROM operations WHERE card_id=? ORDER BY id", (d["card_id"],))
            ]
            for o in ops:
                o["steps"] = [
                    dict(s) for s in self._q("SELECT * FROM steps WHERE op_id=? ORDER BY n", (o["id"],))
                ]
            out.append(
                {
                    "decision": d["decision"],
                    "at": d["at"],
                    "note": d["reviewer_note"],
                    "card": dict(card[0]) if card else {"id": d["card_id"]},
                    "operations": ops,
                }
            )
        return out


def _touched_accounts(card: Card) -> set[str]:
    out = set(card.account_ids)
    out |= {s.path.rsplit("/", 1)[1] for s in card.steps if s.path.startswith("/accounts/")}
    return out


def _row_accounts(r) -> set[str]:
    out = set(json.loads(r["account_ids"]))
    out |= {s["path"].rsplit("/", 1)[1] for s in json.loads(r["steps"]) if s["path"].startswith("/accounts/")}
    return out


class _Abort(Exception):
    """Rolls a claim back when its alternative group was already taken."""
