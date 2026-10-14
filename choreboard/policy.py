"""Who may do what. The ONE place permissions live.

For now (DESIGN.md: "worked out later") every signed-in person may do
everything except change server settings, which is for admins; an anonymous
caller may do nothing. Every operation in
service.py asks here first, so tightening this later -- say, only admins
write rules, or people record outcomes only on their own chores -- is a
change to this function and its tests, and nowhere else.

Actions: people.write, chores.write, rules.write, overrides.write,
outcomes.write (ctx: key or outcome_id), ledger.write, tokens.write,
notifications.write (ctx: person_id -- whose settings),
settings.read, settings.write (admins only).
"""
from __future__ import annotations

from typing import Any


# The first real rule: server settings (credentials) are for admins only.
ADMIN_ONLY = {"settings.read", "settings.write"}


def allowed(actor: dict[str, Any] | None, action: str, **ctx: Any) -> bool:
    if actor is None or not actor.get("active", 1):
        return False
    if action in ADMIN_ONLY:
        return actor.get("role") == "admin"
    return True
