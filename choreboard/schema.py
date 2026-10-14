"""The SQLite schema, as numbered migrations tracked in PRAGMA user_version.

Append to MIGRATIONS; never edit one that has shipped.

History is deliberately NOT tied to rules: outcomes and the ledger reference
an occurrence by (chore, date, source, source_id), with no foreign key to
rules, so editing or deleting a rule can't touch what already happened.
"""
from __future__ import annotations

import sqlite3

MIGRATIONS: list[str] = [
    # 1: the model in DESIGN.md
    """
    CREATE TABLE people (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        color       TEXT NOT NULL DEFAULT '',
        email       TEXT UNIQUE,                 -- identity from the signed-in token
        role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    CREATE TABLE chores (
        id          INTEGER PRIMARY KEY,
        title       TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        icon        TEXT NOT NULL DEFAULT '',
        points      INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- Exactly one of weekdays / every_days(+anchor) / on_date.
    CREATE TABLE rules (
        id          INTEGER PRIMARY KEY,
        chore_id    INTEGER NOT NULL REFERENCES chores(id),
        person_id   INTEGER NOT NULL REFERENCES people(id),
        weekdays    TEXT,                        -- ISO days, '1,3' = Mon, Wed
        every_days  INTEGER,
        anchor      TEXT,
        on_date     TEXT,
        due         TEXT,                        -- 'HH:MM', household time
        start_date  TEXT,
        end_date    TEXT,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    CREATE TABLE overrides (
        id          INTEGER PRIMARY KEY,
        chore_id    INTEGER NOT NULL REFERENCES chores(id),
        date        TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('reassign', 'skip', 'add')),
        -- An override of a deleted rule has nothing left to change.
        rule_id     INTEGER REFERENCES rules(id) ON DELETE CASCADE,
        person_id   INTEGER REFERENCES people(id),
        due         TEXT,
        group_id    TEXT,
        reason      TEXT NOT NULL DEFAULT '',
        created_by  INTEGER REFERENCES people(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX overrides_date ON overrides(date);
    CREATE INDEX overrides_group ON overrides(group_id);

    -- One live outcome per occurrence; an undone one is kept (undone_at set).
    CREATE TABLE outcomes (
        id          INTEGER PRIMARY KEY,
        chore_id    INTEGER NOT NULL REFERENCES chores(id),
        date        TEXT NOT NULL,
        source      TEXT NOT NULL CHECK (source IN ('rule', 'add')),
        source_id   INTEGER NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('done', 'skipped', 'failed', 'covered', 'failed_covered')),
        assignee_id INTEGER NOT NULL REFERENCES people(id),   -- who was on the hook
        done_by_id  INTEGER REFERENCES people(id),            -- who actually did it
        note        TEXT NOT NULL DEFAULT '',
        recorded_by INTEGER REFERENCES people(id),
        recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        undone_at   TEXT,
        undone_by   INTEGER REFERENCES people(id),
        undo_note   TEXT NOT NULL DEFAULT ''
    );
    CREATE UNIQUE INDEX outcomes_live ON outcomes(chore_id, date, source, source_id) WHERE undone_at IS NULL;
    CREATE INDEX outcomes_date ON outcomes(date);

    -- Append-only. A balance is a sum; an undo appends a reversal.
    CREATE TABLE ledger (
        id          INTEGER PRIMARY KEY,
        person_id   INTEGER NOT NULL REFERENCES people(id),
        delta       INTEGER NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('earned', 'deduction', 'adjustment', 'reversal')),
        note        TEXT NOT NULL DEFAULT '',
        outcome_id  INTEGER REFERENCES outcomes(id),
        reverses_id INTEGER REFERENCES ledger(id),
        created_by  INTEGER REFERENCES people(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX ledger_person ON ledger(person_id);
    """,
    # 2: API tokens for machines (nav, automations). Only a hash is stored.
    """
    CREATE TABLE tokens (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        prefix      TEXT NOT NULL,               -- first characters, to recognise it in a list
        hash        TEXT NOT NULL UNIQUE,        -- sha256 of the whole token
        person_id   INTEGER NOT NULL REFERENCES people(id),   -- whose name it acts in
        scopes      TEXT NOT NULL,               -- comma list: read, write, act_as
        created_by  INTEGER REFERENCES people(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        last_used   TEXT,
        revoked_at  TEXT
    );
    """,
    # 3: a daily time window -- a start time ("not before") beside `due`.
    """
    ALTER TABLE rules ADD COLUMN available TEXT;
    ALTER TABLE overrides ADD COLUMN available TEXT;
    """,
    # 4: notifications (notify.py) -- an event log, who wants what, where, and
    # an outbox with every delivery attempt's result.
    """
    CREATE TABLE events (
        id          INTEGER PRIMARY KEY,
        kind        TEXT NOT NULL,
        actor_id    INTEGER REFERENCES people(id),
        chore_id    INTEGER REFERENCES chores(id),
        date        TEXT,
        title       TEXT NOT NULL,
        body        TEXT NOT NULL,
        payload     TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX events_created ON events(created_at);

    -- chore_id NULL: all of my chores (or, for household events, everything).
    CREATE TABLE subscriptions (
        id          INTEGER PRIMARY KEY,
        person_id   INTEGER NOT NULL REFERENCES people(id),
        event       TEXT NOT NULL,
        channel     TEXT NOT NULL CHECK (channel IN ('email', 'discord')),
        chore_id    INTEGER REFERENCES chores(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE UNIQUE INDEX subscriptions_one ON subscriptions(person_id, event, channel, COALESCE(chore_id, 0));

    -- Where a person gets each channel. No email row = their sign-in email.
    CREATE TABLE person_channels (
        person_id   INTEGER NOT NULL REFERENCES people(id),
        channel     TEXT NOT NULL CHECK (channel IN ('email', 'discord')),
        address     TEXT NOT NULL,
        enabled     INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (person_id, channel)
    );

    CREATE TABLE deliveries (
        id          INTEGER PRIMARY KEY,
        event_id    INTEGER NOT NULL REFERENCES events(id),
        person_id   INTEGER NOT NULL REFERENCES people(id),
        channel     TEXT NOT NULL,
        address     TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('queued', 'sending', 'sent', 'failed')),
        attempts    INTEGER NOT NULL DEFAULT 0,
        next_at     TEXT NOT NULL,
        last_error  TEXT,
        sent_at     TEXT
    );
    CREATE INDEX deliveries_due ON deliveries(status, next_at);
    CREATE INDEX deliveries_person ON deliveries(person_id);
    """,
    # 5: the in-app inbox. Any event a person hasn't ignored lands here, with
    # a read state, whatever channels they chose; 'app' is a subscription
    # channel meaning "inbox only". SQLite can't alter a CHECK, so the
    # subscriptions table is rebuilt to allow it.
    """
    CREATE TABLE notifications (
        id          INTEGER PRIMARY KEY,
        person_id   INTEGER NOT NULL REFERENCES people(id),
        event_id    INTEGER NOT NULL REFERENCES events(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        read_at     TEXT,
        UNIQUE (person_id, event_id)
    );
    CREATE INDEX notifications_unread ON notifications(person_id, read_at);

    CREATE TABLE subscriptions_new (
        id          INTEGER PRIMARY KEY,
        person_id   INTEGER NOT NULL REFERENCES people(id),
        event       TEXT NOT NULL,
        channel     TEXT NOT NULL CHECK (channel IN ('app', 'email', 'discord')),
        chore_id    INTEGER REFERENCES chores(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    INSERT INTO subscriptions_new (id, person_id, event, channel, chore_id, created_at)
        SELECT id, person_id, event, channel, chore_id, created_at FROM subscriptions;
    DROP TABLE subscriptions;
    ALTER TABLE subscriptions_new RENAME TO subscriptions;
    CREATE UNIQUE INDEX subscriptions_one ON subscriptions(person_id, event, channel, COALESCE(chore_id, 0));
    """,
    # 6: server settings set from the app (settings.py). Secrets are stored
    # encrypted with a key from the environment, never the key itself.
    """
    CREATE TABLE settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        secret      INTEGER NOT NULL DEFAULT 0,
        updated_by  INTEGER REFERENCES people(id),
        updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    """,
]


def connect(path: str) -> sqlite3.Connection:
    """Open the database and bring it up to date. `:memory:` works for tests."""
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for n, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        # Statement by statement inside one transaction: executescript()
        # would commit on its own, so a failed migration could half-apply.
        conn.execute("BEGIN")
        try:
            for stmt in _statements(sql):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {n}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _statements(sql: str) -> list[str]:
    """Split a migration into statements: strip `--` comments (they may hold
    semicolons), then split on `;`. Migrations have no semicolons inside
    string literals or triggers, which keeps this honest."""
    code = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    return [stmt.strip() for stmt in code.split(";") if stmt.strip()]
