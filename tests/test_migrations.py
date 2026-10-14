"""Upgrading a database that already holds data -- not just a fresh one."""
import datetime as dt
import sqlite3

from choreboard import schema
from choreboard.service import Service


def test_upgrading_from_version_2_keeps_everything(tmp_path):
    path = str(tmp_path / "old.db")
    # Build a version-2 database by hand: migrations 1 and 2 only.
    conn = sqlite3.connect(path, isolation_level=None)
    for n, sql in enumerate(schema.MIGRATIONS[:2], start=1):
        for stmt in schema._statements(sql):
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {n}")
    conn.execute("INSERT INTO people (name, email, role) VALUES ('Eric', 'e@x', 'admin')")
    conn.execute("INSERT INTO chores (title, points) VALUES ('Walk the dog', 3)")
    conn.execute("INSERT INTO rules (chore_id, person_id, weekdays, due, created_at)"
                 " VALUES (1, 1, '1,3', '18:00', '2026-01-01T12:00:00Z')")
    conn.execute("INSERT INTO outcomes (chore_id, date, source, source_id, status, assignee_id, done_by_id)"
                 " VALUES (1, '2026-10-19', 'rule', 1, 'done', 1, 1)")
    conn.execute("INSERT INTO ledger (person_id, delta, kind, note, outcome_id) VALUES (1, 3, 'earned', 'x', 1)")
    conn.close()

    svc = Service(schema.connect(path))
    assert svc.db.execute("PRAGMA user_version").fetchone()[0] == len(schema.MIGRATIONS)
    rule = svc.rules()[0]
    assert (rule.due.isoformat(), rule.available) == ("18:00:00", None), "old rules have no start time"
    assert svc.balance(1) == 3
    entry = svc.calendar(dt.date(2026, 10, 19), dt.date(2026, 10, 19))[0]
    assert entry.outcome["status"] == "done"


def test_a_failed_migration_changes_nothing(tmp_path, monkeypatch):
    path = str(tmp_path / "x.db")
    schema.connect(path).close()
    before = sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0]
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS + ["CREATE TABLE ok (a INTEGER);\nNOT VALID SQL"])
    try:
        schema.connect(path)
        raise AssertionError("the broken migration should have failed")
    except sqlite3.OperationalError:
        pass
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == before
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'ok'").fetchone() is None, "rolled back whole"
