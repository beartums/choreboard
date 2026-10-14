"""Server settings kept in the database -- for now, the notification
channels' credentials -- so an admin can set them up from the app.

Secrets are ENCRYPTED with a key from the environment
(`CHOREBOARD_SECRET_KEY`), never stored in the database. Without the key,
secrets can't be saved: encrypting with a key that lives next to the
ciphertext would protect nothing. A database backup alone is ciphertext; the
key has to be kept (and backed up) with the deployment's other secrets.

Environment variables always win over stored values, so a deployment that
injects credentials (e.g. a SealedSecret) keeps working unchanged; the app
shows which source each setting comes from.

Secrets are write-only through the API: it says whether one is set, never
what it is.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CHOREBOARD_SECRET_KEY"
MIN_KEY_LEN = 32


@dataclass(frozen=True)
class Field:
    key: str
    env: str
    label: str
    secret: bool = False
    default: str = ""
    help: str = ""


FIELDS: list[Field] = [
    Field("smtp.host", "CHOREBOARD_SMTP_HOST", "SMTP server", default="smtp.gmail.com"),
    Field("smtp.port", "CHOREBOARD_SMTP_PORT", "SMTP port", default="587", help="587 (STARTTLS)"),
    Field("smtp.user", "CHOREBOARD_SMTP_USER", "SMTP user", help="the Gmail address that sends"),
    Field("smtp.password", "CHOREBOARD_SMTP_PASSWORD", "SMTP password", secret=True,
          help="Gmail: an app password (myaccount.google.com/apppasswords), not the account password"),
    Field("smtp.from", "CHOREBOARD_SMTP_FROM", "From", help='default: "Choreboard <SMTP user>"'),
    Field("discord.token", "CHOREBOARD_DISCORD_TOKEN", "Discord bot token", secret=True,
          help="discord.com/developers -> your application -> Bot -> Reset Token"),
    Field("discord.invite", "CHOREBOARD_DISCORD_INVITE", "Server invite link",
          help="the bot can only DM people in a server with it: right-click the server -> Invite People -> "
               "Edit invite link -> never expires. Shown to everyone on their notification settings."),
]
BY_KEY = {f.key: f for f in FIELDS}


class SettingsError(ValueError):
    pass


def _fernet() -> Fernet | None:
    raw = os.environ.get(KEY_ENV, "")
    if len(raw) < MIN_KEY_LEN:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def can_store_secrets() -> bool:
    return _fernet() is not None


def _stored(db: sqlite3.Connection, key: str) -> tuple[str, bool] | None:
    row = db.execute("SELECT value, secret FROM settings WHERE key = ?", (key,)).fetchone()
    return (row[0], bool(row[1])) if row else None


def get(db: sqlite3.Connection, key: str) -> str:
    """The effective value: environment, else stored (decrypted), else the
    default. A stored secret that can't be decrypted (the key changed) reads
    as unset rather than crashing the sender."""
    f = BY_KEY[key]
    if os.environ.get(f.env):
        return os.environ[f.env]
    got = _stored(db, key)
    if got is None:
        return f.default
    value, secret = got
    if not secret:
        return value
    fernet = _fernet()
    if fernet is None:
        return ""
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def describe(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """For the settings page: each field's source and, for non-secrets, its
    value. Secrets report only whether they're set."""
    out = []
    for f in FIELDS:
        env = bool(os.environ.get(f.env))
        stored = _stored(db, f.key)
        source = "env" if env else "app" if stored else "default"
        item: dict[str, Any] = {"key": f.key, "label": f.label, "secret": f.secret, "help": f.help,
                                "source": source, "env": f.env}
        if f.secret:
            item["set"] = bool(get(db, f.key))
            if stored and not env and not item["set"]:
                item["problem"] = "stored, but it can't be decrypted with the current CHOREBOARD_SECRET_KEY"
        else:
            item["value"] = get(db, f.key)
            item["default"] = f.default
        out.append(item)
    return out


def put(db: sqlite3.Connection, changes: dict[str, str | None], by: int | None) -> None:
    """Store values; None or "" clears one (back to env/default). Secrets
    are encrypted, and refused when there's no key to encrypt with."""
    for key, value in changes.items():
        f = BY_KEY.get(key)
        if f is None:
            raise SettingsError(f"no setting {key!r}")
        if value is None or value == "":
            db.execute("DELETE FROM settings WHERE key = ?", (key,))
            continue
        value = value.strip()
        if key == "smtp.port" and not (value.isdigit() and 0 < int(value) < 65536):
            raise SettingsError("the SMTP port is a number, usually 587")
        if key == "discord.invite" and not value.startswith(("https://discord.gg/", "https://discord.com/invite/")):
            raise SettingsError("an invite link looks like https://discord.gg/abc123")
        if f.secret:
            fernet = _fernet()
            if fernet is None:
                raise SettingsError(f"set {KEY_ENV} (at least {MIN_KEY_LEN} characters) on the server to store secrets here")
            value = fernet.encrypt(value.encode()).decode()
        db.execute("INSERT INTO settings (key, value, secret, updated_by) VALUES (?, ?, ?, ?)"
                   " ON CONFLICT (key) DO UPDATE SET value = excluded.value, secret = excluded.secret,"
                   " updated_by = excluded.updated_by, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
                   (key, value, 1 if f.secret else 0, by))
