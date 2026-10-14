"""Who may do what. The ONE place permissions live (DESIGN.md: Permissions).

Members (everyone signed in):
  - mark THEIR OWN chore done, and undo that, inside its window: from its
    start time (or the start of its day) until one hour after its due time
    (or the end of its day);
  - propose a swap of their own chore with someone else's, and approve,
    decline or cancel swap requests they're part of;
  - manage their own notification settings.
Admins: everything, any time -- every outcome for anyone, swaps without
approval, chores, schedules, people, points, settings and tokens.

`allowed` is a yes/no for the service; `reason` says why not, in words the
UI can show ("opens at 4 PM", "only Tucker can mark this"). Both read the
same rules, so what the UI offers and what the server permits can't drift.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

GRACE = dt.timedelta(hours=1)

# Admin-only, with no exceptions for members.
ADMIN_ONLY = {
    "settings.read", "settings.write", "tokens.read", "tokens.write",
    "people.write", "chores.write", "rules.write", "overrides.write", "ledger.write", "swaps.direct",
}


def is_admin(actor: dict[str, Any] | None) -> bool:
    return bool(actor) and actor.get("role") == "admin" and bool(actor.get("active", 1))  # type: ignore[union-attr]


def window(date: dt.date, available: dt.time | None, due: dt.time | None) -> tuple[dt.datetime, dt.datetime]:
    """When a member may mark a chore done (naive local time): from its start
    time, or the start of its day, to an hour after its due time, or after
    the end of its day."""
    start = dt.datetime.combine(date, available or dt.time(0))
    end = dt.datetime.combine(date, due) if due else dt.datetime.combine(date + dt.timedelta(days=1), dt.time(0))
    return start, end + GRACE


def _clock(t: dt.datetime) -> str:
    return t.strftime("%-I:%M %p").replace(":00 ", " ")


def reason(actor: dict[str, Any] | None, action: str, **ctx: Any) -> str | None:
    """None if allowed; otherwise why not, in words for a person."""
    if actor is None or not actor.get("active", 1):
        return "sign in first"
    if is_admin(actor):
        return None
    if action in ADMIN_ONLY:
        return "only an admin can do that"
    me = actor.get("id")

    if action in ("outcomes.write", "outcomes.undo"):
        if ctx.get("status", "done") != "done":
            return "only an admin can record that"
        if ctx.get("assignee") != me:
            return f"only {ctx.get('assignee_name') or 'the person it belongs to'} can mark this"
        if ctx.get("done_by") not in (None, me):
            return "only an admin can record that someone else did it"
        now, date = ctx.get("now"), ctx.get("date")
        if now is not None and date is not None:
            start, end = window(date, ctx.get("available"), ctx.get("due"))
            if now < start:
                when = _clock(start) if start.date() == now.date() else start.strftime("%a %b %-d, ") + _clock(start)
                return f"opens at {when}"
            if now > end:
                return "the time for this has passed; ask an admin"
        return None

    if action == "swaps.propose":
        if ctx.get("from_person") != me:
            return "you can only offer a swap of your own chore"
        return None
    if action == "swaps.answer":
        if ctx.get("to_person") != me:
            return "only the person asked can answer"
        return None
    if action == "swaps.cancel":
        if ctx.get("from_person") != me:
            return "only the person who asked can cancel"
        return None

    if action == "notifications.write":
        if ctx.get("person_id") not in (None, me):
            return "those are someone else's settings"
        return None

    return None


def allowed(actor: dict[str, Any] | None, action: str, **ctx: Any) -> bool:
    return reason(actor, action, **ctx) is None
