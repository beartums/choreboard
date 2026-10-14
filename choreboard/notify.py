"""Notifications: an event log, per-person subscriptions, and an outbox the
background worker delivers by email or Discord DM.

How it fits together:
  - service.py calls `emit` after a change succeeds, naming who it's about
    (the subjects) and who did it (the actor). The event is stored, then fanned
    out: one queued delivery per (subject, channel) whose subscription matches.
  - Nobody is notified about their own action.
  - Any event a person hasn't ignored also goes in their inbox
    (`notifications`, with a read state), whatever channels they chose.
  - `deliver_due` (run by the worker in api.py) sends queued deliveries, with
    retries and backoff; every attempt's result is kept, so "why didn't I get
    it" has an answer.

Channels are pluggable: a Sender has `send(address, title, body)`. Email is
SMTP (Gmail with an app password, in griflab); Discord is a bot that opens a
DM with the person's Discord user id. Neither is required: a channel that
isn't configured fails its deliveries at once with a clear reason.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import smtplib
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Iterable, Protocol

log = logging.getLogger("choreboard.notify")

# Channels a delivery is SENT on. 'app' is a subscription channel too ("inbox
# only"); every subscription, of any channel, also puts the event in the inbox.
CHANNELS = ("email", "discord")
SUBSCRIBE_CHANNELS = ("app", *CHANNELS)

# kind -> (label, who it's about). "mine": the people the event is about, and a
# subscription can narrow to one chore. "household": everyone.
EVENTS: dict[str, tuple[str, str]] = {
    "assigned": ("A chore was given to me", "mine"),
    "unassigned": ("A chore of mine was given to someone else", "mine"),
    "swapped": ("Someone swapped a chore with me", "mine"),
    "missed": ("My chore was marked missed", "mine"),
    "excused": ("My chore was excused or skipped", "mine"),
    "covered": ("Someone covered my chore", "mine"),
    "done_by_other": ("Someone marked my chore done", "mine"),
    "undone": ("Something recorded on my chore was undone", "mine"),
    "schedule": ("My schedule changed", "mine"),
    "points": ("My points were adjusted", "mine"),
    "new_chore": ("A new chore was added", "household"),
}

# Retry delays after a failed attempt; after the last, the delivery fails.
BACKOFF = [dt.timedelta(minutes=1), dt.timedelta(minutes=5), dt.timedelta(minutes=30), dt.timedelta(hours=2)]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- emitting ----------------------------------------------------------------

def emit(db: sqlite3.Connection, kind: str, *, actor_id: int | None, subjects: Iterable[int] | None,
         title: str, body: str, chore_id: int | None = None, date: dt.date | None = None,
         **payload: Any) -> int:
    """Record an event and queue its deliveries. `subjects` None means
    everyone (household events). Returns the event id."""
    if kind not in EVENTS:
        raise ValueError(f"unknown event {kind}")
    cur = db.execute(
        "INSERT INTO events (kind, actor_id, chore_id, date, title, body, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, actor_id, chore_id, date.isoformat() if date else None, title, body, json.dumps(payload, default=str)))
    event_id = int(cur.lastrowid)
    if subjects is None:
        people = [r[0] for r in db.execute("SELECT id FROM people WHERE active = 1")]
    else:
        people = sorted(set(subjects))
    for pid in people:
        if pid == actor_id:
            continue                                  # never about your own action
        wanted = _channels_for(db, pid, kind, chore_id)
        if not wanted:
            continue                                  # ignored: the default
        db.execute("INSERT OR IGNORE INTO notifications (person_id, event_id) VALUES (?, ?)", (pid, event_id))
        for channel in wanted:
            if channel not in CHANNELS:
                continue                              # 'app': the inbox row is the whole delivery
            address = address_of(db, pid, channel)
            db.execute(
                "INSERT INTO deliveries (event_id, person_id, channel, address, status, next_at)"
                " VALUES (?, ?, ?, ?, 'queued', ?)", (event_id, pid, channel, address or "", _now()))
    return event_id


def _channels_for(db: sqlite3.Connection, person_id: int, kind: str, chore_id: int | None) -> list[str]:
    """The channels this person wants this event on: a subscription for all
    their chores (chore_id NULL), or for this chore."""
    rows = db.execute(
        "SELECT DISTINCT channel FROM subscriptions WHERE person_id = ? AND event = ?"
        " AND (chore_id IS NULL OR chore_id = ?)", (person_id, kind, chore_id)).fetchall()
    return [r[0] for r in rows]


def address_of(db: sqlite3.Connection, person_id: int, channel: str) -> str | None:
    """Where a person gets a channel: their own setting, or for email their
    sign-in address."""
    row = db.execute("SELECT address, enabled FROM person_channels WHERE person_id = ? AND channel = ?",
                     (person_id, channel)).fetchone()
    if row is not None:
        return row[0] if row[1] else None
    if channel == "email":
        r = db.execute("SELECT email FROM people WHERE id = ?", (person_id,)).fetchone()
        return r[0] if r else None
    return None


# --- delivering --------------------------------------------------------------

class Sender(Protocol):
    def send(self, address: str, title: str, body: str) -> None:
        """Deliver, or raise with a reason."""


class NotConfigured(Exception):
    """A channel the server has no credentials for: fail now, don't retry."""


class Permanent(Exception):
    """A refusal retrying can't fix (e.g. Discord's 4xx): fail now, with why."""


@dataclass
class SmtpSender:
    host: str
    port: int
    user: str
    password: str
    sender: str
    timeout: float = 20

    def send(self, address: str, title: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = address
        msg["Subject"] = title
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(msg)


@dataclass
class DiscordSender:
    """A bot DMs the person. The bot must share a server with them, and the
    address is their numeric Discord user id (Settings -> Advanced ->
    Developer Mode, then right-click yourself -> Copy User ID)."""
    token: str
    api: str = "https://discord.com/api/v10"
    timeout: float = 15
    # Discord REQUIRES this exact form for bots ("DiscordBot ($url, $version)");
    # anything else can be refused before it reaches the API.
    user_agent: str = "DiscordBot (https://github.com/beartums/choreboard, 0.6)"

    def _call(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(self.api + path, method=method, data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bot {self.token}", "Content-Type": "application/json",
                                              "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()[:400].decode(errors="replace")
            # 4xx is Discord saying no (bad token, can't DM, unknown user):
            # retrying won't change it. 429 (rate limit) and 5xx can pass.
            err = Permanent if 400 <= e.code < 500 and e.code != 429 else RuntimeError
            raise err(discord_reason(e.code, raw)) from None

    def send(self, address: str, title: str, body: str) -> None:
        dm = self._call("POST", "/users/@me/channels", {"recipient_id": address})
        self._call("POST", f"/channels/{dm['id']}/messages", {"content": f"**{title}**\n{body}"[:2000]})


# Discord's JSON error codes worth translating into what to do about them.
DISCORD_CODES = {
    50007: "Discord won't let the bot DM this person: the bot must share a server with them, and their privacy "
           "settings for that server must allow direct messages from members",
    50278: "the bot isn't in any server with this person -- invite it to a server they're in "
           "(Developer Portal -> OAuth2 -> URL Generator -> scope 'bot')",
    10013: "Discord doesn't know that user id -- check it (Developer Mode -> Copy User ID)",
    50035: "Discord rejected the request as invalid -- check the user id is the numeric id, not a username",
    40333: "Discord refused the request before the API (an internal or Cloudflare block)",
}


def discord_reason(status: int, raw: str) -> str:
    """A Discord error in words: the code's meaning where known, else the
    status and the start of the body."""
    if status == 401:
        return "Discord 401: the bot token is wrong or was reset -- paste it again in Settings"
    try:
        code = json.loads(raw).get("code")
    except (ValueError, AttributeError):
        code = None
    if code in DISCORD_CODES:
        return f"Discord {status} ({code}): {DISCORD_CODES[code]}"
    if raw.lstrip().startswith("<"):
        return f"Discord {status}: refused before reaching the API (an HTML page, not JSON)"
    return f"Discord {status}: {raw[:200]}"


def claim_due(db: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Queued deliveries whose time has come, marked 'sending' so a second
    worker pass doesn't take them too."""
    rows = [dict(r) for r in db.execute(
        "SELECT d.*, e.title, e.body FROM deliveries d JOIN events e ON e.id = d.event_id"
        " WHERE d.status = 'queued' AND d.next_at <= ? ORDER BY d.id LIMIT ?", (_now(), limit))]
    db.executemany("UPDATE deliveries SET status = 'sending' WHERE id = ?", [(r["id"],) for r in rows])
    return rows


def finish(db: sqlite3.Connection, d: dict[str, Any], error: str | None, retry: bool = True) -> None:
    attempts = d["attempts"] + 1
    if error is None:
        db.execute("UPDATE deliveries SET status = 'sent', attempts = ?, sent_at = ?, last_error = NULL WHERE id = ?",
                   (attempts, _now(), d["id"]))
    elif retry and attempts <= len(BACKOFF):
        nxt = (dt.datetime.now(dt.timezone.utc) + BACKOFF[attempts - 1]).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute("UPDATE deliveries SET status = 'queued', attempts = ?, next_at = ?, last_error = ? WHERE id = ?",
                   (attempts, nxt, error[:500], d["id"]))
    else:
        db.execute("UPDATE deliveries SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
                   (attempts, error[:500], d["id"]))


def send_one(d: dict[str, Any], senders: dict[str, Sender], link: str = "") -> tuple[str | None, bool]:
    """Try one delivery: (error or None, whether a failure is worth retrying).
    No I/O on the database, so the caller can run it outside the lock."""
    sender = senders.get(d["channel"])
    if sender is None:
        return f"{d['channel']} isn't set up on this server", False
    if not d["address"]:
        return f"no {d['channel']} address for this person", False
    body = d["body"] + (f"\n\n{link}" if link else "")
    try:
        sender.send(d["address"], d["title"], body)
        return None, True
    except (NotConfigured, Permanent) as e:
        return str(e), False
    except Exception as e:  # noqa: BLE001 -- the reason is recorded; transient ones retry
        log.warning("delivery %s via %s failed: %s", d["id"], d["channel"], e)
        return f"{type(e).__name__}: {e}", True
