"""choreboard's operations over the database: the calendar with outcomes,
overrides and swaps, outcomes and the points ledger, and the CRUD the setup
needs. The HTTP layer is a thin skin over this; everything that decides
anything is here and tested without a server.

Every operation takes the acting person (`actor`) and asks `policy.allowed`
first. Permissions are deliberately permissive for now (DESIGN.md: "worked
out later, in one place") -- that one place is policy.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

from . import policy
from .calendar import Occurrence, OccurrenceKey, Override, Pattern, Rule, calendar, plan_swap

SCOPES = ("read", "write", "act_as")
TOKEN_PREFIX = "cb_"
STATUSES = ("done", "skipped", "failed", "covered", "failed_covered")
NEEDS_NOTE = ("failed", "covered", "failed_covered")      # the household's rule
NEEDS_DOER = ("covered", "failed_covered")
DEDUCTS = ("failed", "failed_covered")


class Error(Exception):
    status = 400


class Invalid(Error):
    status = 400


class NotFound(Error):
    status = 404


class Conflict(Error):
    status = 409


class Forbidden(Error):
    status = 403


@dataclass(frozen=True)
class Entry:
    """One day's occurrence, as the API returns it: the calendar's answer
    plus whatever has been recorded against it."""
    occurrence: Occurrence
    outcome: dict[str, Any] | None


def _d(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


def _t(s: str | None) -> dt.time | None:
    return dt.time.fromisoformat(s) if s else None


def _iso(x: dt.date | dt.time | None) -> str | None:
    if x is None:
        return None
    return x.strftime("%H:%M") if isinstance(x, dt.time) else x.isoformat()


def key_str(k: OccurrenceKey) -> str:
    """'chore:date:source:id', the occurrence id the API exposes."""
    return f"{k[0]}:{k[1].isoformat()}:{k[2]}:{k[3]}"


def parse_key(s: str) -> OccurrenceKey:
    try:
        chore, date, source, sid = s.split(":")
        if source not in ("rule", "add"):
            raise ValueError
        return int(chore), dt.date.fromisoformat(date), source, int(sid)
    except ValueError:
        raise Invalid(f"not an occurrence id: {s!r} (want chore:YYYY-MM-DD:rule|add:id)") from None


class Service:
    def __init__(self, conn: sqlite3.Connection):
        self.db = conn

    # --- plumbing -----------------------------------------------------------

    def _tx(self) -> "_Tx":
        return _Tx(self.db)

    def _one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def _check(self, actor: dict[str, Any] | None, action: str, **ctx: Any) -> None:
        if not policy.allowed(actor, action, **ctx):
            raise Forbidden(f"not allowed: {action}")

    def _person(self, pid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM people WHERE id = ?", pid)
        if row is None:
            raise NotFound(f"no person {pid}")
        return dict(row)

    def _chore(self, cid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM chores WHERE id = ?", cid)
        if row is None:
            raise NotFound(f"no chore {cid}")
        return dict(row)

    # --- people and chores --------------------------------------------------

    def create_person(self, actor: dict[str, Any] | None, name: str, *, email: str | None = None,
                      color: str = "", role: str = "member") -> dict[str, Any]:
        self._check(actor, "people.write")
        if not name.strip():
            raise Invalid("name is required")
        if role not in ("admin", "member"):
            raise Invalid("role is admin or member")
        try:
            cur = self.db.execute("INSERT INTO people (name, email, color, role) VALUES (?, ?, ?, ?)",
                                  (name.strip(), (email or "").strip().lower() or None, color, role))
        except sqlite3.IntegrityError:
            raise Conflict(f"someone already has {email}") from None
        return self._person(cur.lastrowid)

    def bootstrap(self, name: str, email: str) -> dict[str, Any]:
        """The first person, made an admin, with no actor: there is nobody
        yet to act. Refused once anyone exists."""
        if self._one("SELECT 1 FROM people LIMIT 1"):
            raise Conflict("already set up")
        if not email.strip():
            raise Invalid("the first person needs the email they sign in with")
        return self.create_person({"id": None, "active": 1}, name, email=email, role="admin")

    def person_by_email(self, email: str, include_inactive: bool = False) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM people WHERE email = ?" + ("" if include_inactive else " AND active = 1"),
                        email.strip().lower())
        return dict(row) if row else None

    def update_person(self, actor: dict[str, Any] | None, pid: int, **fields: Any) -> dict[str, Any]:
        """Change name, email, color, role or active."""
        self._check(actor, "people.write")
        self._person(pid)
        allowed = {"name", "email", "color", "role", "active"}
        bad = set(fields) - allowed
        if bad:
            raise Invalid(f"can't change {', '.join(sorted(bad))}")
        if "role" in fields and fields["role"] not in ("admin", "member"):
            raise Invalid("role is admin or member")
        if "name" in fields and not str(fields["name"]).strip():
            raise Invalid("name can't be empty")
        if "email" in fields:
            fields["email"] = (fields["email"] or "").strip().lower() or None
        if "active" in fields:
            fields["active"] = 1 if fields["active"] else 0
        if fields:
            try:
                self.db.execute(f"UPDATE people SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                                (*fields.values(), pid))
            except sqlite3.IntegrityError:
                raise Conflict(f"someone already has {fields.get('email')}") from None
        return self._person(pid)

    def person(self, pid: int) -> dict[str, Any]:
        return self._person(pid)

    def people(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._all("SELECT * FROM people WHERE active = 1 ORDER BY id")]

    def create_chore(self, actor: dict[str, Any] | None, title: str, *, points: int = 0,
                     description: str = "", icon: str = "") -> dict[str, Any]:
        self._check(actor, "chores.write")
        if not title.strip():
            raise Invalid("title is required")
        if points < 0:
            raise Invalid("points can't be negative")
        cur = self.db.execute("INSERT INTO chores (title, points, description, icon) VALUES (?, ?, ?, ?)",
                              (title.strip(), points, description, icon))
        return self._chore(cur.lastrowid)

    def update_chore(self, actor: dict[str, Any] | None, cid: int, **fields: Any) -> dict[str, Any]:
        """Change title, description, icon, points or active. Points already
        earned are in the ledger and don't change."""
        self._check(actor, "chores.write")
        self._chore(cid)
        bad = set(fields) - {"title", "description", "icon", "points", "active"}
        if bad:
            raise Invalid(f"can't change {', '.join(sorted(bad))}")
        if "points" in fields and (not isinstance(fields["points"], int) or fields["points"] < 0):
            raise Invalid("points is a whole number, 0 or more")
        if "title" in fields and not str(fields["title"]).strip():
            raise Invalid("title can't be empty")
        if "active" in fields:
            fields["active"] = 1 if fields["active"] else 0
        if fields:
            self.db.execute(f"UPDATE chores SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                            (*fields.values(), cid))
        return self._chore(cid)

    def chore(self, cid: int) -> dict[str, Any]:
        return self._chore(cid)

    def chores(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._all("SELECT * FROM chores WHERE active = 1 ORDER BY id")]

    # --- rules --------------------------------------------------------------

    @staticmethod
    def _rule(row: sqlite3.Row) -> Rule:
        wd = frozenset(int(x) for x in row["weekdays"].split(",")) if row["weekdays"] else None
        return Rule(id=row["id"], chore_id=row["chore_id"], person_id=row["person_id"],
                    pattern=Pattern(weekdays=wd, every_days=row["every_days"], anchor=_d(row["anchor"]),
                                    on=_d(row["on_date"])),
                    due=_t(row["due"]), start=_d(row["start_date"]), end=_d(row["end_date"]))

    def rule(self, rid: int) -> Rule:
        row = self._one("SELECT * FROM rules WHERE id = ?", rid)
        if row is None:
            raise NotFound(f"no rule {rid}")
        return self._rule(row)

    def rules(self, chore_id: int | None = None) -> list[Rule]:
        rows = (self._all("SELECT * FROM rules WHERE chore_id = ? ORDER BY id", chore_id) if chore_id
                else self._all("SELECT * FROM rules ORDER BY id"))
        return [self._rule(r) for r in rows]

    def _rule_values(self, chore_id: int, person_id: int, pattern: Pattern, due: dt.time | None,
                     start: dt.date | None, end: dt.date | None) -> tuple[Any, ...]:
        self._chore(chore_id)
        self._person(person_id)
        if start and end and end < start:
            raise Invalid("end is before start")
        wd = ",".join(str(d) for d in sorted(pattern.weekdays)) if pattern.weekdays else None
        return (chore_id, person_id, wd, pattern.every_days, _iso(pattern.anchor), _iso(pattern.on),
                _iso(due), _iso(start), _iso(end))

    def create_rule(self, actor: dict[str, Any] | None, chore_id: int, person_id: int, pattern: Pattern, *,
                    due: dt.time | None = None, start: dt.date | None = None,
                    end: dt.date | None = None) -> Rule:
        self._check(actor, "rules.write")
        vals = self._rule_values(chore_id, person_id, pattern, due, start, end)
        cur = self.db.execute(
            "INSERT INTO rules (chore_id, person_id, weekdays, every_days, anchor, on_date, due, start_date, end_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", vals)
        return self.rule(cur.lastrowid)

    def update_rule(self, actor: dict[str, Any] | None, rid: int, chore_id: int, person_id: int,
                    pattern: Pattern, *, due: dt.time | None = None, start: dt.date | None = None,
                    end: dt.date | None = None) -> Rule:
        """Edit in place. Past outcomes are keyed by occurrence and keep their
        own assignee, so nothing recorded changes."""
        self._check(actor, "rules.write")
        self.rule(rid)
        vals = self._rule_values(chore_id, person_id, pattern, due, start, end)
        self.db.execute(
            "UPDATE rules SET chore_id = ?, person_id = ?, weekdays = ?, every_days = ?, anchor = ?, on_date = ?,"
            " due = ?, start_date = ?, end_date = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
            " WHERE id = ?", (*vals, rid))
        return self.rule(rid)

    def delete_rule(self, actor: dict[str, Any] | None, rid: int) -> None:
        """Its overrides go with it; its outcomes and points stay."""
        self._check(actor, "rules.write")
        self.rule(rid)
        self.db.execute("DELETE FROM rules WHERE id = ?", (rid,))

    # --- the calendar -------------------------------------------------------

    @staticmethod
    def _override(row: sqlite3.Row) -> Override:
        return Override(id=row["id"], chore_id=row["chore_id"], date=dt.date.fromisoformat(row["date"]),
                        kind=row["kind"], rule_id=row["rule_id"], person_id=row["person_id"],
                        due=_t(row["due"]), group_id=row["group_id"], reason=row["reason"])

    def _overrides(self, start: dt.date, end: dt.date) -> list[Override]:
        return [self._override(r) for r in self._all(
            "SELECT * FROM overrides WHERE date BETWEEN ? AND ? ORDER BY id", start.isoformat(), end.isoformat())]

    def calendar(self, start: dt.date, end: dt.date, person_id: int | None = None,
                 include_skipped: bool = True) -> list[Entry]:
        if (end - start).days > 366:
            raise Invalid("ask for at most a year at a time")
        occs = calendar(self.rules(), self._overrides(start, end), start, end, person_id, include_skipped)
        outcomes = {
            (r["chore_id"], dt.date.fromisoformat(r["date"]), r["source"], r["source_id"]): dict(r)
            for r in self._all("SELECT * FROM outcomes WHERE undone_at IS NULL AND date BETWEEN ? AND ?",
                               start.isoformat(), end.isoformat())}
        return [Entry(o, outcomes.get(o.key)) for o in occs]

    def occurrence(self, key: OccurrenceKey, include_skipped: bool = False) -> Occurrence:
        """The occurrence a key names, as the calendar computes it today."""
        for o in calendar(self.rules(), self._overrides(key[1], key[1]), key[1], key[1]):
            if o.key == key:
                if o.skipped and not include_skipped:
                    raise Conflict(f"{key_str(key)} is skipped that day")
                return o
        raise NotFound(f"nothing is scheduled as {key_str(key)}")

    def _live_outcome(self, key: OccurrenceKey) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM outcomes WHERE chore_id = ? AND date = ? AND source = ? AND source_id = ?"
                        " AND undone_at IS NULL", key[0], key[1].isoformat(), key[2], key[3])
        return dict(row) if row else None

    def _unrecorded(self, key: OccurrenceKey) -> None:
        if self._live_outcome(key):
            raise Conflict(f"{key_str(key)} already has an outcome; undo it first")

    # --- overrides and swaps ------------------------------------------------

    def _insert_override(self, o: Override, actor: dict[str, Any] | None) -> int:
        cur = self.db.execute(
            "INSERT INTO overrides (chore_id, date, kind, rule_id, person_id, due, group_id, reason, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (o.chore_id, o.date.isoformat(), o.kind, o.rule_id, o.person_id, _iso(o.due), o.group_id,
             o.reason, (actor or {}).get("id")))
        return int(cur.lastrowid)

    def reassign(self, actor: dict[str, Any] | None, key: OccurrenceKey, person_id: int, reason: str) -> Occurrence:
        """One occurrence goes to someone else, that date only."""
        self._check(actor, "overrides.write")
        occ = self.occurrence(key)
        if occ.rule_id is None:
            raise Invalid("an added occurrence is changed by editing its override, not reassigned")
        self._person(person_id)
        self._unrecorded(key)
        with self._tx():
            self._insert_override(Override(0, occ.chore_id, occ.date, "reassign", rule_id=occ.rule_id,
                                           person_id=person_id, reason=reason), actor)
        return self.occurrence(key)

    def skip(self, actor: dict[str, Any] | None, key: OccurrenceKey, reason: str) -> Occurrence:
        """An occurrence doesn't happen that date (planned ahead; compare the
        `skipped` outcome, recorded on the day)."""
        self._check(actor, "overrides.write")
        occ = self.occurrence(key)
        if occ.rule_id is None:
            raise Invalid("remove an added occurrence instead of skipping it")
        self._unrecorded(key)
        with self._tx():
            self._insert_override(Override(0, occ.chore_id, occ.date, "skip", rule_id=occ.rule_id,
                                           reason=reason), actor)
        return self.occurrence(key, include_skipped=True)

    def add(self, actor: dict[str, Any] | None, chore_id: int, date: dt.date, person_id: int,
            reason: str, due: dt.time | None = None) -> Occurrence:
        """An extra occurrence of a chore for someone on a date."""
        self._check(actor, "overrides.write")
        self._chore(chore_id)
        self._person(person_id)
        with self._tx():
            oid = self._insert_override(Override(0, chore_id, date, "add", person_id=person_id, due=due,
                                                 reason=reason), actor)
        return self.occurrence((chore_id, date, "add", oid))

    def swap(self, actor: dict[str, Any] | None, a: OccurrenceKey, b: OccurrenceKey, reason: str) -> str:
        """Two people trade two occurrences, on those dates only. Returns the
        swap's group id, which undoes it."""
        self._check(actor, "overrides.write")
        if not reason.strip():
            raise Invalid("a swap needs a reason")
        occ_a, occ_b = self.occurrence(a), self.occurrence(b)
        self._unrecorded(a)
        self._unrecorded(b)
        # Prefixed: remove_override_group tells a group from a numeric override
        # id by isdigit(), and ~1 in 280 bare 12-hex ids is all digits (a
        # property test found one: "162687106237").
        group = "sw_" + uuid.uuid4().hex[:12]
        try:
            plan = plan_swap(occ_a, occ_b, group, reason.strip())
        except ValueError as e:
            raise Invalid(str(e)) from None
        with self._tx():
            self._insert_override(plan.a, actor)
            self._insert_override(plan.b, actor)
        return group

    def remove_override_group(self, actor: dict[str, Any] | None, group_or_id: str) -> int:
        """Undo a swap (by group id) or a single override (by numeric id), as
        long as nothing has been recorded against what it changed."""
        self._check(actor, "overrides.write")
        if group_or_id.isdigit():                        # an override id; groups are "sw_..."
            rows = self._all("SELECT * FROM overrides WHERE id = ?", int(group_or_id))
        else:
            rows = self._all("SELECT * FROM overrides WHERE group_id = ?", group_or_id)
        if not rows:
            raise NotFound(f"no override {group_or_id}")
        for r in rows:
            o = self._override(r)
            key = (o.chore_id, o.date, "add", o.id) if o.kind == "add" else (o.chore_id, o.date, "rule", o.rule_id)
            self._unrecorded(key)  # type: ignore[arg-type]
        with self._tx():
            self.db.executemany("DELETE FROM overrides WHERE id = ?", [(r["id"],) for r in rows])
        return len(rows)

    # --- outcomes and the ledger --------------------------------------------

    def record(self, actor: dict[str, Any] | None, key: OccurrenceKey, status: str, *,
               note: str = "", done_by: int | None = None, deduct: int | None = None) -> dict[str, Any]:
        """What happened to an occurrence. Points follow the household's model
        (DESIGN.md): done and covered earn the chore's points for whoever did
        it; failed deducts an EXPLICIT amount from the assignee; nothing is
        ever deducted unless `deduct` says so."""
        self._check(actor, "outcomes.write", key=key)
        if status not in STATUSES:
            raise Invalid(f"status is one of {', '.join(STATUSES)}")
        note = note.strip()
        if status in NEEDS_NOTE and not note:
            raise Invalid(f"'{status}' needs a note saying why")
        occ = self.occurrence(key)
        chore = self._chore(occ.chore_id)
        assignee = occ.person_id
        if status in NEEDS_DOER:
            if done_by is None:
                raise Invalid(f"'{status}' needs done_by: who did it instead")
            if done_by == assignee:
                raise Invalid(f"done_by is the assignee; record 'done' instead")
            self._person(done_by)
        elif status == "done":
            if done_by not in (None, assignee):
                raise Invalid("done by someone else is 'covered'")
            done_by = assignee
        elif done_by is not None:
            raise Invalid(f"'{status}' has no done_by")
        if status in DEDUCTS:
            if deduct is None or deduct < 0:
                raise Invalid(f"'{status}' needs deduct: the points to take (0 for none), stated explicitly")
        elif deduct is not None:
            raise Invalid(f"'{status}' deducts nothing")

        self._unrecorded(key)
        by = (actor or {}).get("id")
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO outcomes (chore_id, date, source, source_id, status, assignee_id, done_by_id, note,"
                " recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key[0], key[1].isoformat(), key[2], key[3], status, assignee, done_by, note, by))
            oid = int(cur.lastrowid)
            label = f"{chore['title']} {key[1].isoformat()}"
            if status in ("done", "covered", "failed_covered") and chore["points"]:
                self._ledger(done_by, chore["points"], "earned", label + (f": {note}" if note else ""), oid, by)
            if status in DEDUCTS and deduct:
                self._ledger(assignee, -deduct, "deduction", f"{label}: {note}", oid, by)
        return self.outcome(oid)

    def outcome(self, oid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM outcomes WHERE id = ?", oid)
        if row is None:
            raise NotFound(f"no outcome {oid}")
        return dict(row)

    def undo(self, actor: dict[str, Any] | None, oid: int, note: str) -> dict[str, Any]:
        """Take back an outcome. It stays in the record, marked undone, and
        each of its points is reversed by a new ledger entry -- the ledger is
        never edited."""
        self._check(actor, "outcomes.write", outcome_id=oid)
        out = self.outcome(oid)
        if out["undone_at"]:
            raise Conflict(f"outcome {oid} is already undone")
        if not note.strip():
            raise Invalid("an undo needs a note saying why")
        by = (actor or {}).get("id")
        with self._tx():
            self.db.execute("UPDATE outcomes SET undone_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), undone_by = ?,"
                            " undo_note = ? WHERE id = ?", (by, note.strip(), oid))
            for e in self._all("SELECT * FROM ledger WHERE outcome_id = ? AND kind != 'reversal'", oid):
                self._ledger(e["person_id"], -e["delta"], "reversal", f"undo: {note.strip()}", oid, by,
                             reverses=e["id"])
        return self.outcome(oid)

    def adjust(self, actor: dict[str, Any] | None, person_id: int, delta: int, note: str) -> dict[str, Any]:
        """A hand adjustment, up or down, always with a reason."""
        self._check(actor, "ledger.write")
        self._person(person_id)
        if delta == 0:
            raise Invalid("an adjustment of 0 changes nothing")
        if not note.strip():
            raise Invalid("an adjustment needs a note saying why")
        with self._tx():
            lid = self._ledger(person_id, delta, "adjustment", note.strip(), None, (actor or {}).get("id"))
        return dict(self._one("SELECT * FROM ledger WHERE id = ?", lid))

    def _ledger(self, person_id: int | None, delta: int, kind: str, note: str, outcome_id: int | None,
                by: int | None, reverses: int | None = None) -> int:
        assert person_id is not None
        cur = self.db.execute(
            "INSERT INTO ledger (person_id, delta, kind, note, outcome_id, reverses_id, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)", (person_id, delta, kind, note, outcome_id, reverses, by))
        return int(cur.lastrowid)

    def balance(self, person_id: int) -> int:
        self._person(person_id)
        return int(self._one("SELECT COALESCE(SUM(delta), 0) FROM ledger WHERE person_id = ?", person_id)[0])

    def ledger(self, person_id: int, limit: int = 100) -> list[dict[str, Any]]:
        self._person(person_id)
        return [dict(r) for r in self._all(
            "SELECT * FROM ledger WHERE person_id = ? ORDER BY id DESC LIMIT ?", person_id, limit)]


    # --- API tokens -----------------------------------------------------------

    def create_token(self, actor: dict[str, Any] | None, name: str, scopes: list[str],
                     person_id: int | None = None) -> tuple[str, dict[str, Any]]:
        """A token for a machine. Returns (the token, its record); the token
        is shown once -- only its hash is kept. It acts in `person_id`'s name
        (default: whoever creates it); `act_as` lets it name another person
        per request, which is how nav acts for the viewer."""
        self._check(actor, "tokens.write")
        if not name.strip():
            raise Invalid("a token needs a name")
        bad = set(scopes) - set(SCOPES)
        if bad or not scopes:
            raise Invalid(f"scopes are some of {', '.join(SCOPES)}")
        owner = person_id if person_id is not None else (actor or {}).get("id")
        if owner is None:
            raise Invalid("whose name does the token act in? (person_id)")
        self._person(owner)
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        cur = self.db.execute(
            "INSERT INTO tokens (name, prefix, hash, person_id, scopes, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), raw[:10], _hash(raw), owner, ",".join(sorted(set(scopes))), (actor or {}).get("id")))
        return raw, self._token(cur.lastrowid)

    def _token(self, tid: int) -> dict[str, Any]:
        row = self._one("SELECT id, name, prefix, person_id, scopes, created_by, created_at, last_used, revoked_at"
                        " FROM tokens WHERE id = ?", tid)
        if row is None:
            raise NotFound(f"no token {tid}")
        return {**dict(row), "scopes": row["scopes"].split(",")}

    def tokens(self) -> list[dict[str, Any]]:
        return [self._token(r["id"]) for r in self._all("SELECT id FROM tokens ORDER BY id")]

    def revoke_token(self, actor: dict[str, Any] | None, tid: int) -> dict[str, Any]:
        self._check(actor, "tokens.write")
        self._token(tid)
        self.db.execute("UPDATE tokens SET revoked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
                        " WHERE id = ? AND revoked_at IS NULL", (tid,))
        return self._token(tid)

    def token_lookup(self, raw: str) -> dict[str, Any] | None:
        """The live token a presented string is, or None. Marks it used."""
        if not raw.startswith(TOKEN_PREFIX):
            return None
        row = self._one("SELECT id FROM tokens WHERE hash = ? AND revoked_at IS NULL", _hash(raw))
        if row is None:
            return None
        self.db.execute("UPDATE tokens SET last_used = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                        (row["id"],))
        return self._token(row["id"])


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class _Tx:
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on error. Nesting isn't needed."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")


def entries_json(entries: list[Entry]) -> Iterator[dict[str, Any]]:
    """The calendar as plain JSON-able dicts (the HTTP layer's shape)."""
    for e in entries:
        o = e.occurrence
        yield {"id": key_str(o.key), "chore_id": o.chore_id, "date": o.date.isoformat(),
               "person_id": o.person_id, "due": _iso(o.due), "source": o.source, "rule_id": o.rule_id,
               "scheduled_person_id": o.scheduled_person_id, "override_id": o.override_id,
               "skipped": o.skipped, "reason": o.reason, "group_id": o.group_id, "outcome": e.outcome}
