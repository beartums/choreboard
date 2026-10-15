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
import logging
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

from . import notify, policy, settings
from .calendar import Occurrence, OccurrenceKey, Override, Pattern, Rule, calendar, plan_swap

log = logging.getLogger("choreboard.service")
# Choreboard itself acting: creating the first person, and someone joining on
# their first sign-in. Never a caller -- nothing outside the service gets it.
SYSTEM = {"id": None, "role": "admin", "active": 1, "name": "Choreboard"}
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


class PendingSwap(Conflict):
    """An admin's change to a day in a pending swap, without saying how to
    settle it. The UI asks: approve it, or decline it, then do this."""
    code = "pending_swap"

    def __init__(self, request: dict[str, Any]):
        super().__init__("this day is in a pending swap request; approve or decline it first "
                         "(pending: \"approve\" | \"decline\")")
        self.request = request


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


def _local_day(utc_stamp: str | None) -> dt.date | None:
    """A stored UTC timestamp ('2026-10-14T23:30:00Z') as the household's
    calendar day -- the pod's local time (TZ is set in its Deployment), so a
    rule made at 7:30 PM Eastern starts that day, not tomorrow."""
    if not utc_stamp:
        return None
    t = dt.datetime.fromisoformat(utc_stamp.replace("Z", "+00:00"))
    return t.astimezone().date()


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
    def __init__(self, conn: sqlite3.Connection, clock: Any = None):
        self.db = conn
        # Naive LOCAL time (the pod's TZ): chore windows are household times.
        self.clock = clock or dt.datetime.now

    # --- plumbing -----------------------------------------------------------

    def _tx(self) -> "_Tx":
        return _Tx(self.db)

    def _one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def _check(self, actor: dict[str, Any] | None, action: str, **ctx: Any) -> None:
        why = policy.reason(actor, action, **ctx)
        if why is not None:
            raise Forbidden(why)

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

    # --- notifications: what happened, in words ---------------------------

    def _name(self, pid: int | None) -> str:
        if pid is None:
            return "Someone"
        row = self._one("SELECT name FROM people WHERE id = ?", pid)
        return row["name"] if row else f"#{pid}"

    def _actor_name(self, actor: dict[str, Any] | None) -> str:
        """Who did it, in words: "Tucker", or "Tucker (Eric, as Tucker)" when an
        admin acted as them."""
        name = self._name((actor or {}).get("id"))
        via = (actor or {}).get("via_id")
        return f"{name} ({self._name(via)}, as {name})" if via else name

    def _what(self, chore_id: int, date: dt.date, available: dt.time | None = None,
              due: dt.time | None = None) -> str:
        """'🐕 Walk the dog on Wed Oct 21, 4-6 PM'."""
        c = self._one("SELECT title, icon FROM chores WHERE id = ?", chore_id)
        title = f"{c['icon'] + ' ' if c and c['icon'] else ''}{c['title'] if c else 'a chore'}"
        when = date.strftime("%a %b %-d")
        fmt = lambda t: t.strftime("%-I:%M %p").replace(":00 ", " ")  # noqa: E731
        window = (f", {fmt(available)}–{fmt(due)}" if available and due else f", after {fmt(available)}" if available
                  else f", by {fmt(due)}" if due else "")
        return f"{title} on {when}{window}"

    def _emit(self, kind: str, actor: dict[str, Any] | None, subjects: list[int] | None, title: str, body: str,
              chore_id: int | None = None, date: dt.date | None = None) -> None:
        """Tell whoever wants to know. Never fails the action that caused it:
        the change has happened, and a broken notification must not undo it."""
        try:
            with self._tx():
                notify.emit(self.db, kind, actor_id=(actor or {}).get("id"), subjects=subjects, title=title,
                            body=body, chore_id=chore_id, date=date, via_id=(actor or {}).get("via_id"))
        except Exception:  # noqa: BLE001
            log.exception("couldn't record a %s notification", kind)

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
        return self.create_person(SYSTEM, name, email=email, role="admin")

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
        chore = self._chore(cur.lastrowid)
        self._emit("new_chore", actor, None, f"New chore: {(icon + ' ') if icon else ''}{chore['title']}",
                   f"{self._actor_name(actor)} added {(icon + ' ') if icon else ''}{chore['title']}"
                   f" ({points} point{'' if points == 1 else 's'}).", chore_id=chore["id"])
        return chore

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
                    due=_t(row["due"]), start=_d(row["start_date"]), end=_d(row["end_date"]),
                    available=_t(row["available"]), created=_local_day(row["created_at"]))

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
                     start: dt.date | None, end: dt.date | None,
                     available: dt.time | None = None) -> tuple[Any, ...]:
        self._chore(chore_id)
        self._person(person_id)
        if start and end and end < start:
            raise Invalid("end is before start")
        if available and due and available >= due:
            raise Invalid("the start time must be before the due time")
        wd = ",".join(str(d) for d in sorted(pattern.weekdays)) if pattern.weekdays else None
        return (chore_id, person_id, wd, pattern.every_days, _iso(pattern.anchor), _iso(pattern.on),
                _iso(due), _iso(start), _iso(end), _iso(available))

    def create_rule(self, actor: dict[str, Any] | None, chore_id: int, person_id: int, pattern: Pattern, *,
                    due: dt.time | None = None, start: dt.date | None = None,
                    end: dt.date | None = None, available: dt.time | None = None) -> Rule:
        self._check(actor, "rules.write")
        vals = self._rule_values(chore_id, person_id, pattern, due, start, end, available)
        cur = self.db.execute(
            "INSERT INTO rules (chore_id, person_id, weekdays, every_days, anchor, on_date, due, start_date, end_date,"
            " available) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", vals)
        rule = self.rule(cur.lastrowid)
        self._schedule_note(actor, [rule.person_id], rule.chore_id, "added you to")
        return rule

    def update_rule(self, actor: dict[str, Any] | None, rid: int, chore_id: int, person_id: int,
                    pattern: Pattern, *, due: dt.time | None = None, start: dt.date | None = None,
                    end: dt.date | None = None, available: dt.time | None = None) -> Rule:
        """Edit in place. Past outcomes are keyed by occurrence and keep their
        own assignee, so nothing recorded changes."""
        self._check(actor, "rules.write")
        before = self.rule(rid)
        vals = self._rule_values(chore_id, person_id, pattern, due, start, end, available)
        self.db.execute(
            "UPDATE rules SET chore_id = ?, person_id = ?, weekdays = ?, every_days = ?, anchor = ?, on_date = ?,"
            " due = ?, start_date = ?, end_date = ?, available = ?,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
            " WHERE id = ?", (*vals, rid))
        after = self.rule(rid)
        if after != before:
            self._schedule_note(actor, [before.person_id, after.person_id], after.chore_id, "changed your schedule for")
        return after

    def delete_rule(self, actor: dict[str, Any] | None, rid: int) -> None:
        """Its overrides go with it; its outcomes and points stay."""
        self._check(actor, "rules.write")
        rule = self.rule(rid)
        self.db.execute("DELETE FROM rules WHERE id = ?", (rid,))
        self._schedule_note(actor, [rule.person_id], rule.chore_id, "took you off")

    def _schedule_note(self, actor: dict[str, Any] | None, people: list[int], chore_id: int, verb: str) -> None:
        c = self._one("SELECT title, icon FROM chores WHERE id = ?", chore_id)
        title = f"{(c['icon'] + ' ') if c and c['icon'] else ''}{c['title'] if c else 'a chore'}"
        self._emit("schedule", actor, people, f"Schedule changed: {title}",
                   f"{self._actor_name(actor)} {verb} the schedule for {title}.", chore_id=chore_id)

    # --- the calendar -------------------------------------------------------

    @staticmethod
    def _override(row: sqlite3.Row) -> Override:
        return Override(id=row["id"], chore_id=row["chore_id"], date=dt.date.fromisoformat(row["date"]),
                        kind=row["kind"], rule_id=row["rule_id"], person_id=row["person_id"],
                        due=_t(row["due"]), group_id=row["group_id"], reason=row["reason"],
                        available=_t(row["available"]))

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
        entries = [Entry(o, outcomes.get(o.key)) for o in occs]
        # What was recorded is always shown, even when the schedule no longer
        # produces that day -- its rule was edited or deleted, or now begins
        # later. History is keyed by occurrence precisely so it outlives the
        # rule; hiding it would make points appear from nowhere.
        shown = {o.key for o in occs}
        for key, out in outcomes.items():
            if key in shown or (person_id is not None and out["assignee_id"] != person_id):
                continue
            entries.append(Entry(Occurrence(
                key=key, chore_id=key[0], date=key[1], person_id=out["assignee_id"], due=None,
                rule_id=key[3] if key[2] == "rule" else None, unscheduled=True), out))
        entries.sort(key=lambda e: (e.occurrence.date, e.occurrence.due or dt.time.max, e.occurrence.chore_id))
        return entries

    def occurrence(self, key: OccurrenceKey, include_skipped: bool = False) -> Occurrence:
        """The occurrence a key names, as the calendar computes it today."""
        for o in calendar(self.rules(), self._overrides(key[1], key[1]), key[1], key[1]):
            if o.key == key:
                if o.skipped and not include_skipped:
                    raise Conflict(f"{key_str(key)} is skipped that day")
                return o
        raise NotFound(f"nothing is scheduled as {key_str(key)}")

    def _times(self, key: OccurrenceKey) -> tuple[dt.time | None, dt.time | None]:
        """An occurrence's daily window, if it's still on the calendar."""
        for o in calendar(self.rules(), self._overrides(key[1], key[1]), key[1], key[1]):
            if o.key == key:
                return o.available, o.due
        return None, None

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
            "INSERT INTO overrides (chore_id, date, kind, rule_id, person_id, due, available, group_id, reason,"
            " created_by, via_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (o.chore_id, o.date.isoformat(), o.kind, o.rule_id, o.person_id, _iso(o.due), _iso(o.available),
             o.group_id, o.reason, (actor or {}).get("id"), (actor or {}).get("via_id")))
        return int(cur.lastrowid)

    def reassign(self, actor: dict[str, Any] | None, key: OccurrenceKey, person_id: int, reason: str,
                 pending: str | None = None) -> Occurrence:
        """One occurrence goes to someone else, that date only."""
        self._check(actor, "overrides.write")
        self._settle(actor, [key], pending)
        occ = self.occurrence(key)
        if occ.rule_id is None:
            raise Invalid("an added occurrence is changed by editing its override, not reassigned")
        self._person(person_id)
        self._unrecorded(key)
        return self._do_give(actor, occ, person_id, reason)

    def _do_give(self, actor: dict[str, Any] | None, occ: Occurrence, person_id: int, reason: str) -> Occurrence:
        key = occ.key
        with self._tx():
            self._insert_override(Override(0, occ.chore_id, occ.date, "reassign", rule_id=occ.rule_id,
                                           person_id=person_id, reason=reason), actor)
        what, by = self._what(occ.chore_id, occ.date, occ.available, occ.due), self._actor_name(actor)
        why = f" Reason: {reason}" if reason else ""
        if occ.person_id != person_id:
            self._emit("assigned", actor, [person_id], f"You've been given {what}",
                       f"{by} gave you {what} (was {self._name(occ.person_id)}'s).{why}", occ.chore_id, occ.date)
            self._emit("unassigned", actor, [occ.person_id], f"{what} is no longer yours",
                       f"{by} gave your {what} to {self._name(person_id)}.{why}", occ.chore_id, occ.date)
        return self.occurrence(key)

    def skip(self, actor: dict[str, Any] | None, key: OccurrenceKey, reason: str,
             pending: str | None = None) -> Occurrence:
        """An occurrence doesn't happen that date (planned ahead; compare the
        `skipped` outcome, recorded on the day)."""
        self._check(actor, "overrides.write")
        self._settle(actor, [key], pending)
        occ = self.occurrence(key)
        if occ.rule_id is None:
            raise Invalid("remove an added occurrence instead of skipping it")
        self._unrecorded(key)
        with self._tx():
            self._insert_override(Override(0, occ.chore_id, occ.date, "skip", rule_id=occ.rule_id,
                                           reason=reason), actor)
        what = self._what(occ.chore_id, occ.date, occ.available, occ.due)
        self._emit("excused", actor, [occ.person_id], f"Skipped: {what}",
                   f"{self._actor_name(actor)} skipped your {what}." + (f" Reason: {reason}" if reason else ""),
                   occ.chore_id, occ.date)
        return self.occurrence(key, include_skipped=True)

    def add(self, actor: dict[str, Any] | None, chore_id: int, date: dt.date, person_id: int,
            reason: str, due: dt.time | None = None, available: dt.time | None = None) -> Occurrence:
        """An extra occurrence of a chore for someone on a date."""
        self._check(actor, "overrides.write")
        self._chore(chore_id)
        self._person(person_id)
        try:
            o = Override(0, chore_id, date, "add", person_id=person_id, due=due, available=available, reason=reason)
        except ValueError as e:
            raise Invalid(str(e)) from None
        with self._tx():
            oid = self._insert_override(o, actor)
        what = self._what(chore_id, date, available, due)
        self._emit("assigned", actor, [person_id], f"Extra chore: {what}",
                   f"{self._actor_name(actor)} added {what} for you." + (f" Reason: {reason}" if reason else ""),
                   chore_id, date)
        return self.occurrence((chore_id, date, "add", oid))

    def swap(self, actor: dict[str, Any] | None, a: OccurrenceKey, b: OccurrenceKey, reason: str,
             pending: str | None = None) -> str:
        """Two people trade two occurrences, on those dates only, at once (an
        admin's swap). Returns the swap's group id, which undoes it. Members
        propose instead: propose_swap."""
        self._check(actor, "swaps.direct")
        if not reason.strip():
            raise Invalid("a swap needs a reason")
        self._settle(actor, [a, b], pending)
        return self._do_swap(actor, a, b, reason)

    def _do_swap(self, actor: dict[str, Any] | None, a: OccurrenceKey, b: OccurrenceKey, reason: str,
                 allow_request: int | None = None) -> str:
        occ_a, occ_b = self.occurrence(a), self.occurrence(b)
        self._unrecorded(a)
        self._unrecorded(b)
        self._no_pending([a, b], ignore=allow_request)
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
        by = self._actor_name(actor)
        wa = self._what(occ_a.chore_id, occ_a.date, occ_a.available, occ_a.due)
        wb = self._what(occ_b.chore_id, occ_b.date, occ_b.available, occ_b.due)
        actor_id = (actor or {}).get("id")
        for me, mine, theirs, other in ((occ_a.person_id, wa, wb, occ_b.person_id), (occ_b.person_id, wb, wa, occ_a.person_id)):
            who = self._name(other)
            head = (f"{who} swapped with you" if actor_id == other
                    else f"{by} swapped your {mine} with {who}'s")
            self._emit("swapped", actor, [me], f"Swapped: you now do {theirs}",
                       f"{head}: you now do {theirs}; {who} does your {mine}. Reason: {reason.strip()}",
                       occ_a.chore_id, occ_a.date)
        return group

    def _later_change(self, rows: list[sqlite3.Row]) -> str | None:
        """Swaps stack: an undo peels the newest off first. If a later
        override sits on top of any of these on its day, say which."""
        for r in rows:
            if r["kind"] == "add" or r["rule_id"] is None:
                continue
            top = self._one("SELECT id, group_id FROM overrides WHERE rule_id = ? AND date = ? AND kind != 'add'"
                            " ORDER BY id DESC LIMIT 1", r["rule_id"], r["date"])
            if top and top["id"] != r["id"]:
                return (f"a later {'swap' if top['group_id'] else 'change'} on "
                        f"{dt.date.fromisoformat(r['date']).strftime('%a %b %-d')} has to be undone first")
        return None

    def remove_override_group(self, actor: dict[str, Any] | None, group_or_id: str, *,
                              _approved: bool = False) -> int:
        """Undo a swap (by group id) or a single override (by numeric id), as
        long as nothing has been recorded against what it changed and nothing
        later is stacked on it. `_approved`: an undo request the other side
        approved (the service checked who may)."""
        if not _approved:
            self._check(actor, "overrides.write")
        if group_or_id.isdigit():                        # an override id; groups are "sw_..."
            rows = self._all("SELECT * FROM overrides WHERE id = ?", int(group_or_id))
        else:
            rows = self._all("SELECT * FROM overrides WHERE group_id = ?", group_or_id)
        if not rows:
            raise NotFound(f"no override {group_or_id}")
        later = self._later_change(rows)
        if later:
            raise Conflict(later)
        affected: set[int] = set()
        notes = []
        for r in rows:
            o = self._override(r)
            key = (o.chore_id, o.date, "add", o.id) if o.kind == "add" else (o.chore_id, o.date, "rule", o.rule_id)
            self._unrecorded(key)  # type: ignore[arg-type]
            if o.person_id:
                affected.add(o.person_id)
            if o.rule_id:
                rr = self._one("SELECT person_id FROM rules WHERE id = ?", o.rule_id)
                if rr:
                    affected.add(rr["person_id"])
            notes.append(self._what(o.chore_id, o.date, o.available, o.due))
        with self._tx():
            self.db.executemany("DELETE FROM overrides WHERE id = ?", [(r["id"],) for r in rows])
        kind = "swap" if not group_or_id.isdigit() else "change"
        self._emit("undone", actor, sorted(affected), f"Undone: a {kind}",
                   f"{self._actor_name(actor)} undid a {kind}: {'; '.join(notes)} — back to how it was before.",
                   rows[0]["chore_id"], dt.date.fromisoformat(rows[0]["date"]))
        return len(rows)

    # --- swap requests (a member proposes; the other side approves) ---------

    @staticmethod
    def _req_keys(r: sqlite3.Row | dict[str, Any]) -> tuple[OccurrenceKey, OccurrenceKey]:
        return ((r["a_chore"], dt.date.fromisoformat(r["a_date"]), r["a_source"], r["a_source_id"]),
                (r["b_chore"], dt.date.fromisoformat(r["b_date"]), r["b_source"], r["b_source_id"]))

    def _expire_requests(self) -> None:
        """A request whose earlier day has passed can no longer happen."""
        today = self.clock().date().isoformat()
        self.db.execute("UPDATE swap_requests SET status = 'expired', decided_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
                        " WHERE status = 'pending' AND MIN(a_date, b_date) < ?", (today,))

    def pending_requests(self) -> list[dict[str, Any]]:
        self._expire_requests()
        return [dict(r) for r in self._all("SELECT * FROM swap_requests WHERE status = 'pending' ORDER BY id")]

    def _settle(self, actor: dict[str, Any] | None, keys: list[OccurrenceKey], how: str | None) -> None:
        """Before an admin's change: a pending request on any of these days is
        approved or declined, as they chose -- or the change is refused with
        PendingSwap, which carries the request so the UI can ask. Members
        can't override a pending swap at all."""
        hits = [r for r in self.pending_requests() if set(self._req_keys(r)) & set(keys)]
        if not hits:
            return
        if not policy.is_admin(actor):
            raise Conflict("one of these is already in a pending swap request; answer or cancel that first")
        if how not in ("approve", "decline"):
            r = self.request(hits[0]["id"])
            raise PendingSwap({**r, "from_name": self._name(r["from_person"]), "to_name": self._name(r["to_person"])})
        for r in hits:
            self.answer_swap(actor, r["id"], how == "approve", "settled by an admin's change")

    def _no_pending(self, keys: list[OccurrenceKey], ignore: int | None) -> None:
        for r in self.pending_requests():
            if r["id"] == ignore:
                continue
            if set(self._req_keys(r)) & set(keys):
                raise Conflict("one of these is already in a pending swap request; answer or cancel that first")

    def request(self, rid: int) -> dict[str, Any]:
        self._expire_requests()
        row = self._one("SELECT * FROM swap_requests WHERE id = ?", rid)
        if row is None:
            raise NotFound(f"no swap request {rid}")
        out = dict(row)
        a, b = self._req_keys(row)
        out["a"], out["b"] = key_str(a), key_str(b)
        return out

    def requests_for(self, person_id: int) -> list[dict[str, Any]]:
        """Pending requests a person is part of, either side."""
        return [self.request(r["id"]) for r in self.pending_requests()
                if person_id in (r["from_person"], r["to_person"])]

    def propose_swap(self, actor: dict[str, Any] | None, a: OccurrenceKey, b: OccurrenceKey, reason: str) -> dict[str, Any]:
        """Offer my chore `a` for someone else's `b`. Tentative until the
        other side approves; nothing on the calendar changes until then."""
        if not reason.strip():
            raise Invalid("a swap needs a reason")
        occ_a, occ_b = self.occurrence(a), self.occurrence(b)
        self._check(actor, "swaps.propose", from_person=occ_a.person_id)
        if occ_a.person_id == occ_b.person_id:
            raise Invalid("both chores are already the same person's")
        if occ_a.rule_id is None or occ_b.rule_id is None:
            raise Invalid("only scheduled chores can be swapped")
        self._unrecorded(a)
        self._unrecorded(b)
        self._no_pending([a, b], ignore=None)
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO swap_requests (a_chore, a_date, a_source, a_source_id, b_chore, b_date, b_source,"
                " b_source_id, from_person, to_person, reason, via_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*(a[0], a[1].isoformat(), a[2], a[3]), *(b[0], b[1].isoformat(), b[2], b[3]),
                 occ_a.person_id, occ_b.person_id, reason.strip(), (actor or {}).get("via_id")))
        rid = int(cur.lastrowid)
        wa = self._what(occ_a.chore_id, occ_a.date, occ_a.available, occ_a.due)
        wb = self._what(occ_b.chore_id, occ_b.date, occ_b.available, occ_b.due)
        self._emit("swap_requested", actor, [occ_b.person_id], f"{self._name(occ_a.person_id)} wants to swap with you",
                   f"{self._actor_name(actor)} asks: you do their {wa}, they do your {wb}. "
                   f"Reason: {reason.strip()}. Approve or decline it on the calendar.", occ_b.chore_id, occ_b.date)
        return self.request(rid)

    def propose_give(self, actor: dict[str, Any] | None, key: OccurrenceKey, person_id: int,
                     reason: str) -> dict[str, Any]:
        """Ask someone to take my chore that day. Tentative until they accept
        (admins just give it: reassign). Stored as a request of kind 'give'
        whose two sides are the same occurrence."""
        if not reason.strip():
            raise Invalid("say why")
        occ = self.occurrence(key)
        self._check(actor, "swaps.propose", from_person=occ.person_id)
        if occ.rule_id is None:
            raise Invalid("only scheduled chores can be given")
        self._person(person_id)
        if person_id == occ.person_id:
            raise Invalid("it's already theirs")
        self._unrecorded(key)
        self._no_pending([key], ignore=None)
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO swap_requests (a_chore, a_date, a_source, a_source_id, b_chore, b_date, b_source,"
                " b_source_id, from_person, to_person, reason, kind, via_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'give', ?)",
                (*(key[0], key[1].isoformat(), key[2], key[3]), *(key[0], key[1].isoformat(), key[2], key[3]),
                 occ.person_id, person_id, reason.strip(), (actor or {}).get("via_id")))
        rid = int(cur.lastrowid)
        what = self._what(occ.chore_id, occ.date, occ.available, occ.due)
        self._emit("swap_requested", actor, [person_id], f"{self._name(occ.person_id)} asks you to take a chore",
                   f"{self._actor_name(actor)} asks you to take their {what}. Reason: {reason.strip()}. "
                   f"Accept or decline it on the calendar.", occ.chore_id, occ.date)
        return self.request(rid)

    def _swap_sides(self, group: str) -> tuple[list[sqlite3.Row], list[OccurrenceKey]]:
        rows = self._all("SELECT * FROM overrides WHERE group_id = ? ORDER BY id", group)
        keys = [(r["chore_id"], dt.date.fromisoformat(r["date"]), "rule", r["rule_id"]) for r in rows if r["rule_id"]]
        return rows, keys

    def request_undo(self, actor: dict[str, Any] | None, key: OccurrenceKey, reason: str) -> dict[str, Any]:
        """Ask to undo an approved swap I'm part of; the other side approves
        (admins just undo it: remove_override_group)."""
        if not reason.strip():
            raise Invalid("say why")
        occ = self.occurrence(key)
        group = occ.group_id
        if not group or not group.startswith("sw_"):
            raise Invalid("that day isn't swapped")
        self._check(actor, "swaps.propose", from_person=occ.person_id)
        rows, keys = self._swap_sides(group)
        later = self._later_change(rows)
        if later:
            raise Conflict(later)
        other = next((k for k in keys if k != key), None)
        if other is None:
            raise Invalid("that swap has only one side")
        for k in keys:
            self._unrecorded(k)
        self._no_pending(keys, ignore=None)
        to_person = self.occurrence(other).person_id
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO swap_requests (a_chore, a_date, a_source, a_source_id, b_chore, b_date, b_source,"
                " b_source_id, from_person, to_person, reason, kind, undo_group, via_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'undo', ?, ?)",
                (*(key[0], key[1].isoformat(), key[2], key[3]), *(other[0], other[1].isoformat(), other[2], other[3]),
                 occ.person_id, to_person, reason.strip(), group, (actor or {}).get("via_id")))
        rid = int(cur.lastrowid)
        what = self._what(occ.chore_id, occ.date, occ.available, occ.due)
        self._emit("swap_requested", actor, [to_person], f"{self._name(occ.person_id)} wants to undo a swap",
                   f"{self._actor_name(actor)} asks to undo your swap ({what}). Reason: {reason.strip()}. "
                   f"Approve or decline it on the calendar.", occ.chore_id, occ.date)
        return self.request(rid)

    def answer_swap(self, actor: dict[str, Any] | None, rid: int, approve: bool, note: str = "") -> dict[str, Any]:
        """The other side says yes (the swap happens now, if both chores are
        still as they were) or no."""
        r = self.request(rid)
        if r["status"] != "pending":
            raise Conflict(f"that request is already {r['status']}")
        self._check(actor, "swaps.answer", to_person=r["to_person"])
        a, b = self._req_keys(r)
        group = None
        if approve and r.get("kind") == "undo":
            # Undo the swap as asked -- if it's still the newest thing on both days.
            self.remove_override_group(None, r["undo_group"], _approved=True)
            group = r["undo_group"]
        elif approve and r.get("kind") == "give":
            occ = self.occurrence(a)
            if occ.person_id != r["from_person"]:
                with self._tx():
                    self.db.execute("UPDATE swap_requests SET status = 'expired', decided_at ="
                                    " strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (rid,))
                raise Conflict("that chore has changed hands since the request; ask again")
            self._unrecorded(a)
            self._do_give({"id": r["from_person"], "role": "member", "active": 1, "via_id": r.get("via_id")},
                          occ, r["to_person"], r["reason"])
        elif approve:
            occ_a, occ_b = self.occurrence(a), self.occurrence(b)
            if (occ_a.person_id, occ_b.person_id) != (r["from_person"], r["to_person"]):
                with self._tx():
                    self.db.execute("UPDATE swap_requests SET status = 'expired', decided_at ="
                                    " strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (rid,))
                raise Conflict("those chores have changed hands since the request; ask again")
            group = self._do_swap({"id": r["from_person"], "role": "member", "active": 1}, a, b, r["reason"],
                                  allow_request=rid)
        with self._tx():
            self.db.execute("UPDATE swap_requests SET status = ?, group_id = ?, answer = ?, decided_by = ?,"
                            " decided_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                            ("approved" if approve else "declined", group, note.strip(), (actor or {}).get("id"), rid))
        by = self._actor_name(actor)
        what = self._what(a[0], a[1])
        noun = {"undo": "undo", "give": "hand-off"}.get(r.get("kind") or "", "swap")
        self._emit("swap_answered", actor, [r["from_person"]],
                   f"{noun[0].upper() + noun[1:]} {'approved' if approve else 'declined'}: {what}",
                   f"{by} {'approved' if approve else 'declined'} your {noun} request for {what}."
                   + (f" {note.strip()}" if note.strip() else ""), a[0], a[1])
        return self.request(rid)

    def cancel_swap(self, actor: dict[str, Any] | None, rid: int) -> dict[str, Any]:
        r = self.request(rid)
        if r["status"] != "pending":
            raise Conflict(f"that request is already {r['status']}")
        self._check(actor, "swaps.cancel", from_person=r["from_person"])
        with self._tx():
            self.db.execute("UPDATE swap_requests SET status = 'cancelled', decided_by = ?,"
                            " decided_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                            ((actor or {}).get("id"), rid))
        return self.request(rid)

    # --- what can this person do here? -------------------------------------

    def capabilities(self, actor: dict[str, Any] | None, e: Entry,
                     pending: dict[OccurrenceKey, dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
        """(actions allowed on this entry, why the common ones aren't). The
        UI offers exactly these; the server enforces the same rules."""
        o, out = e.occurrence, e.outcome
        can: list[str] = []
        why: dict[str, str] = {}
        admin = policy.is_admin(actor)
        now = self.clock()
        req = pending.get(o.key)
        if out:
            r = policy.reason(actor, "outcomes.undo", status=out["status"], assignee=out["assignee_id"],
                              assignee_name=self._name(out["assignee_id"]), done_by=out["done_by_id"], now=now,
                              date=o.date, available=o.available, due=o.due)
            (can.append("undo") if r is None else why.__setitem__("undo", r))
            return can, why
        if o.skipped:
            if admin:
                can.append("unskip")
            return can, why
        r = policy.reason(actor, "outcomes.write", status="done", assignee=o.person_id,
                          assignee_name=self._name(o.person_id), now=now, date=o.date, available=o.available, due=o.due)
        (can.append("done") if r is None else why.__setitem__("done", r))
        swapped = bool(o.group_id and o.group_id.startswith("sw_"))
        if admin:
            can += ["skipped", "failed", "covered", "failed_covered"]
            # On a day in a pending swap, these first settle it (the UI asks:
            # approve or decline?); members can't override one at all.
            if o.rule_id is not None:
                can += ["give", "skip", "swap"]
            if o.override_id is not None and not swapped:
                can.append("undo_change")
        if swapped and not req:
            later = self._later_change(self._swap_sides(o.group_id)[0])   # type: ignore[arg-type]
            action = "undo_swap" if admin else "request_undo"
            if later:
                why[action] = later
            elif admin or o.person_id == (actor or {}).get("id"):
                can.append(action)
        if req:
            me = (actor or {}).get("id")
            if req["to_person"] == me or admin:
                can += ["approve_swap", "decline_swap"]
            if req["from_person"] == me or admin:
                can.append("cancel_swap")
        elif o.rule_id is not None and not admin:
            r = policy.reason(actor, "swaps.propose", from_person=o.person_id)
            (can.extend(["propose_swap", "propose_give"]) if r is None else why.__setitem__("propose_swap", r))
        return can, why

    def calendar_json(self, actor: dict[str, Any] | None, start: dt.date, end: dt.date,
                      person_id: int | None = None, include_skipped: bool = True) -> list[dict[str, Any]]:
        """The calendar as the API returns it: each entry with `can` (what the
        caller may do), `cannot` (why not, for the common actions) and
        `pending_swap` (a tentative swap it's part of)."""
        entries = self.calendar(start, end, person_id, include_skipped)
        pending: dict[OccurrenceKey, dict[str, Any]] = {}
        for r in self.pending_requests():
            a, b = self._req_keys(r)
            for mine, other in ((a, b), (b, a)) if a != b else ((a, a),):
                pending[mine] = {"id": r["id"], "from_person": r["from_person"], "to_person": r["to_person"],
                                 "with": key_str(other), "reason": r["reason"], "kind": r.get("kind") or "swap",
                                 "side": "from" if mine == a else "to"}
        out = []
        for e, j in zip(entries, entries_json(entries)):
            can, why = self.capabilities(actor, e, pending)
            j["can"], j["cannot"] = can, why
            j["pending_swap"] = pending.get(e.occurrence.key)
            out.append(j)
        return out

    # --- outcomes and the ledger --------------------------------------------

    def record(self, actor: dict[str, Any] | None, key: OccurrenceKey, status: str, *,
               note: str = "", done_by: int | None = None, deduct: int | None = None) -> dict[str, Any]:
        """What happened to an occurrence. Points follow the household's model
        (DESIGN.md): done and covered earn the chore's points for whoever did
        it; failed deducts an EXPLICIT amount from the assignee; nothing is
        ever deducted unless `deduct` says so."""
        if status not in STATUSES:
            raise Invalid(f"status is one of {', '.join(STATUSES)}")
        occ = self.occurrence(key)
        self._check(actor, "outcomes.write", status=status, assignee=occ.person_id,
                    assignee_name=self._name(occ.person_id), done_by=done_by, now=self.clock(),
                    date=occ.date, available=occ.available, due=occ.due)
        note = note.strip()
        if status in NEEDS_NOTE and not note:
            raise Invalid(f"'{status}' needs a note saying why")
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
                " recorded_by, via_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key[0], key[1].isoformat(), key[2], key[3], status, assignee, done_by, note, by,
                 (actor or {}).get("via_id")))
            oid = int(cur.lastrowid)
            label = f"{chore['title']} {key[1].isoformat()}"
            if status in ("done", "covered", "failed_covered") and chore["points"]:
                self._ledger(done_by, chore["points"], "earned", label + (f": {note}" if note else ""), oid, by)
            if status in DEDUCTS and deduct:
                self._ledger(assignee, -deduct, "deduction", f"{label}: {note}", oid, by)
        self._outcome_note(actor, occ, status, assignee, done_by, deduct, note)
        return self.outcome(oid)

    def _outcome_note(self, actor: dict[str, Any] | None, occ: Occurrence, status: str, assignee: int,
                      done_by: int | None, deduct: int | None, note: str) -> None:
        what, by = self._what(occ.chore_id, occ.date, occ.available, occ.due), self._actor_name(actor)
        why = f" Reason: {note}" if note else ""
        pts = f" −{deduct} point{'' if deduct == 1 else 's'}." if deduct else ""
        if status == "done":
            self._emit("done_by_other", actor, [assignee], f"Marked done: {what}", f"{by} marked your {what} done.",
                       occ.chore_id, occ.date)
        elif status == "skipped":
            self._emit("excused", actor, [assignee], f"Excused: {what}", f"{by} marked your {what} as not needed.{why}",
                       occ.chore_id, occ.date)
        elif status == "covered":
            self._emit("covered", actor, [assignee], f"Covered: {what}",
                       f"{self._name(done_by)} did your {what} (recorded by {by}).{why}", occ.chore_id, occ.date)
        else:                                            # failed, failed_covered
            cover = f" {self._name(done_by)} covered it." if status == "failed_covered" else ""
            self._emit("missed", actor, [assignee], f"Missed: {what}",
                       f"{by} marked your {what} as missed.{cover}{pts}{why}", occ.chore_id, occ.date)

    def outcome(self, oid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM outcomes WHERE id = ?", oid)
        if row is None:
            raise NotFound(f"no outcome {oid}")
        return dict(row)

    def undo(self, actor: dict[str, Any] | None, oid: int, note: str) -> dict[str, Any]:
        """Take back an outcome. It stays in the record, marked undone, and
        each of its points is reversed by a new ledger entry -- the ledger is
        never edited."""
        out = self.outcome(oid)
        okey = (out["chore_id"], dt.date.fromisoformat(out["date"]), out["source"], out["source_id"])
        times = self._times(okey)
        self._check(actor, "outcomes.undo", status=out["status"], assignee=out["assignee_id"],
                    assignee_name=self._name(out["assignee_id"]), done_by=out["done_by_id"], now=self.clock(),
                    date=okey[1], available=times[0], due=times[1])
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
        date = dt.date.fromisoformat(out["date"])
        what = self._what(out["chore_id"], date)
        people = [p for p in (out["assignee_id"], out["done_by_id"]) if p is not None]
        self._emit("undone", actor, people, f"Undone: {what} ({out['status'].replace('_', ' ')})",
                   f"{self._name(by)} undid what was recorded for {what}: {note.strip()}. Any points from it are reversed.",
                   out["chore_id"], date)
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
        self._emit("points", actor, [person_id], f"Points {'+' if delta > 0 else '−'}{abs(delta)}",
                   f"{self._actor_name(actor)} {'gave you' if delta > 0 else 'took'} {abs(delta)} point"
                   f"{'' if abs(delta) == 1 else 's'}: {note.strip()}.")
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


    # --- notification settings ------------------------------------------------

    def subscriptions(self, person_id: int) -> list[dict[str, Any]]:
        self._person(person_id)
        return [dict(r) for r in self._all(
            "SELECT id, event, channel, chore_id FROM subscriptions WHERE person_id = ? ORDER BY event, channel, chore_id",
            person_id)]

    def subscribe(self, actor: dict[str, Any] | None, person_id: int, event: str, channel: str,
                  chore_id: int | None = None) -> dict[str, Any]:
        """Turn one notification on. Idempotent."""
        self._check(actor, "notifications.write", person_id=person_id)
        self._person(person_id)
        if event not in notify.EVENTS:
            raise Invalid(f"event is one of {', '.join(notify.EVENTS)}")
        if channel not in notify.SUBSCRIBE_CHANNELS:
            raise Invalid(f"channel is one of {', '.join(notify.SUBSCRIBE_CHANNELS)}")
        if chore_id is not None:
            if notify.EVENTS[event][1] != "mine":
                raise Invalid(f"'{event}' isn't about one chore")
            self._chore(chore_id)
        self.db.execute("INSERT OR IGNORE INTO subscriptions (person_id, event, channel, chore_id) VALUES (?, ?, ?, ?)",
                        (person_id, event, channel, chore_id))
        row = self._one("SELECT id, event, channel, chore_id FROM subscriptions WHERE person_id = ? AND event = ?"
                        " AND channel = ? AND COALESCE(chore_id, 0) = COALESCE(?, 0)", person_id, event, channel, chore_id)
        return dict(row)

    def unsubscribe(self, actor: dict[str, Any] | None, person_id: int, sub_id: int) -> None:
        self._check(actor, "notifications.write", person_id=person_id)
        if not self.db.execute("DELETE FROM subscriptions WHERE id = ? AND person_id = ?", (sub_id, person_id)).rowcount:
            raise NotFound(f"no subscription {sub_id}")

    def channels(self, person_id: int) -> dict[str, dict[str, Any]]:
        """Each channel's address for a person, and whether it's their own
        setting or the default (email: their sign-in address)."""
        p = self._person(person_id)
        rows = {r["channel"]: dict(r) for r in self._all(
            "SELECT channel, address, enabled FROM person_channels WHERE person_id = ?", person_id)}
        out = {}
        for ch in notify.CHANNELS:
            if ch in rows:
                out[ch] = {"address": rows[ch]["address"], "enabled": bool(rows[ch]["enabled"]), "default": False}
            else:
                out[ch] = {"address": p["email"] if ch == "email" else None, "enabled": ch == "email" and bool(p["email"]),
                           "default": True}
        return out

    def set_channel(self, actor: dict[str, Any] | None, person_id: int, channel: str, address: str | None,
                    enabled: bool = True) -> dict[str, dict[str, Any]]:
        """Set where a person gets a channel; address None clears it (email
        falls back to the sign-in address)."""
        self._check(actor, "notifications.write", person_id=person_id)
        self._person(person_id)
        if channel not in notify.CHANNELS:
            raise Invalid(f"channel is one of {', '.join(notify.CHANNELS)}")
        address = (address or "").strip()
        if not address:
            self.db.execute("DELETE FROM person_channels WHERE person_id = ? AND channel = ?", (person_id, channel))
            return self.channels(person_id)
        if channel == "email" and ("@" not in address or " " in address):
            raise Invalid("that doesn't look like an email address")
        if channel == "discord" and not (address.isdigit() and 15 <= len(address) <= 21):
            raise Invalid("a Discord user id is a 17-20 digit number (Developer Mode → Copy User ID), not a username")
        self.db.execute("INSERT INTO person_channels (person_id, channel, address, enabled) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT (person_id, channel) DO UPDATE SET address = excluded.address,"
                        " enabled = excluded.enabled", (person_id, channel, address, 1 if enabled else 0))
        return self.channels(person_id)

    def inbox(self, person_id: int, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        """A person's notifications, newest first."""
        self._person(person_id)
        return [dict(r) for r in self._all(
            "SELECT n.id, n.read_at, n.created_at, e.kind, e.title, e.body, e.chore_id, e.date, e.actor_id"
            " FROM notifications n JOIN events e ON e.id = n.event_id WHERE n.person_id = ?"
            + (" AND n.read_at IS NULL" if unread_only else "") + " ORDER BY n.id DESC LIMIT ?", person_id, limit)]

    def unread(self, person_id: int) -> int:
        return int(self._one("SELECT COUNT(*) FROM notifications WHERE person_id = ? AND read_at IS NULL",
                             person_id)[0])

    def mark_read(self, actor: dict[str, Any] | None, person_id: int, ids: list[int] | None = None,
                  read: bool = True) -> int:
        """Mark some (or, with ids None, all) of a person's notifications read
        -- or unread again. Returns how many changed."""
        self._check(actor, "notifications.write", person_id=person_id)
        stamp = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')" if read else "NULL"
        sql = f"UPDATE notifications SET read_at = {stamp} WHERE person_id = ? AND read_at IS {'NULL' if read else 'NOT NULL'}"
        args: list[Any] = [person_id]
        if ids is not None:
            if not ids:
                return 0
            sql += f" AND id IN ({','.join('?' * len(ids))})"
            args += ids
        return self.db.execute(sql, args).rowcount

    def discord_invite(self) -> str:
        return settings.get(self.db, "discord.invite")

    def request_discord(self, actor: dict[str, Any] | None) -> dict[str, Any]:
        """Ask the admins to add me to the Discord server. Once a day per
        person; it reaches every admin's inbox regardless of subscriptions."""
        self._check(actor, "notifications.write", person_id=(actor or {}).get("id"))
        me = self._person(actor["id"])                         # type: ignore[index]
        recent = self._one("SELECT 1 FROM events WHERE kind = 'discord_request' AND actor_id = ?"
                           " AND created_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 day')", me["id"])
        if recent:
            raise Conflict("you already asked today; an admin has it in their inbox")
        admins = [r["id"] for r in self._all("SELECT id FROM people WHERE role = 'admin' AND active = 1")]
        if admins == [me["id"]] or not admins:
            raise Invalid("you're the only admin -- invite yourself (Settings has the invite link)")
        did = notify.address_of(self.db, me["id"], "discord")
        ident = f" Their Discord user id is {did}." if did else " They haven't set their Discord user id yet."
        with self._tx():
            notify.emit(self.db, "discord_request", actor_id=me["id"], subjects=admins,
                        title=f"{me['name']} wants to join the Discord server",
                        body=f"{me['name']} ({me['email'] or 'no email'}) asked to be added to the household's Discord "
                             f"server so Choreboard can message them.{ident}")
        return {"asked": len([a for a in admins if a != me["id"]]), "invite": self.discord_invite() or None}

    def deliveries(self, person_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Recent notifications for a person, with each one's outcome."""
        self._person(person_id)
        return [dict(r) for r in self._all(
            "SELECT d.id, d.channel, d.address, d.status, d.attempts, d.last_error, d.sent_at, e.kind, e.title,"
            " e.body, e.created_at FROM deliveries d JOIN events e ON e.id = d.event_id"
            " WHERE d.person_id = ? ORDER BY d.id DESC LIMIT ?", person_id, limit)]

    def test_notification(self, actor: dict[str, Any] | None, person_id: int, channel: str) -> int:
        """Queue a test message to one of a person's channels."""
        self._check(actor, "notifications.write", person_id=person_id)
        if channel not in notify.CHANNELS:
            raise Invalid(f"channel is one of {', '.join(notify.CHANNELS)}")
        address = notify.address_of(self.db, person_id, channel)
        with self._tx():
            cur = self.db.execute("INSERT INTO events (kind, actor_id, title, body) VALUES ('points', NULL, ?, ?)",
                                  ("Choreboard test", "This is a test notification. If you can read it, it works."))
            self.db.execute("INSERT INTO deliveries (event_id, person_id, channel, address, status, next_at)"
                            " VALUES (?, ?, ?, ?, 'queued', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                            (cur.lastrowid, person_id, channel, address or ""))
        return int(cur.lastrowid)

    # --- server settings (admins) --------------------------------------------

    def server_settings(self, actor: dict[str, Any] | None) -> dict[str, Any]:
        self._check(actor, "settings.read")
        return {"can_store_secrets": settings.can_store_secrets(), "key_env": settings.KEY_ENV,
                "fields": settings.describe(self.db)}

    def update_server_settings(self, actor: dict[str, Any] | None, changes: dict[str, str | None]) -> dict[str, Any]:
        self._check(actor, "settings.write")
        try:
            with self._tx():
                settings.put(self.db, changes, (actor or {}).get("id"))
        except settings.SettingsError as e:
            raise Invalid(str(e)) from None
        return self.server_settings(actor)

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

    def tokens(self, actor: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Every token, newest first, with whose name it acts in."""
        self._check(actor, "tokens.read")
        out = []
        for r in self._all("SELECT id FROM tokens ORDER BY id DESC"):
            t = self._token(r["id"])
            t["person_name"] = self._name(t["person_id"])
            out.append(t)
        return out

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
               "person_id": o.person_id, "available": _iso(o.available), "due": _iso(o.due),
               "source": o.source, "rule_id": o.rule_id,
               "scheduled_person_id": o.scheduled_person_id, "override_id": o.override_id,
               "skipped": o.skipped, "reason": o.reason, "group_id": o.group_id,
               "unscheduled": o.unscheduled, "outcome": e.outcome}
