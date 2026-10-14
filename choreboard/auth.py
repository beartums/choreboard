"""Who is calling. Three ways in, tried in this order:

1. `Authorization: Bearer cb_...` -- an API token (machines: nav,
   automations). Acts in its owner's name; a token with the `act_as` scope may
   name another person per request with `X-Choreboard-As: <email>`, which is
   how nav acts for whoever is looking at it. Writes need the `write` scope.
2. `X-Pomerium-Jwt-Assertion` -- the browser, behind Pomerium. The JWT is
   VERIFIED against Pomerium's published keys and its audience checked; a
   plain identity header would be trivially forgeable from inside the cluster.
3. `CHOREBOARD_DEV_EMAIL` -- local development only, and only when set.

A signed-in email nobody has yet joins as a member when auto-join is on (the
default: Pomerium has already decided they belong in the household). With
nobody at all, the caller gets an identity but no person, and may only run
setup.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import jwt

from .service import Forbidden, Service

READ_METHODS = {"GET", "HEAD", "OPTIONS"}
# Pomerium stamps `iat` (and `exp`) from ITS clock; this pod's clock is
# another node's. With no leeway, a token stamped a fraction of a second
# "in the future" by a node running slightly ahead is rejected -- an
# intermittent 401 that a reload clears. A minute is the usual allowance.
CLOCK_LEEWAY_S = 60
log = logging.getLogger("choreboard.auth")


class Unauthenticated(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: str = "/data/choreboard.db"
    jwks_url: str = ""
    audience: str = ""
    dev_email: str = ""
    auto_join: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(db_path=os.environ.get("CHOREBOARD_DB", cls.db_path),
                   jwks_url=os.environ.get("CHOREBOARD_JWKS_URL", ""),
                   audience=os.environ.get("CHOREBOARD_AUDIENCE", ""),
                   dev_email=os.environ.get("CHOREBOARD_DEV_EMAIL", ""),
                   auto_join=os.environ.get("CHOREBOARD_AUTO_JOIN", "1") not in ("0", "false", "no"))


@dataclass
class Caller:
    """The resolved caller. `person` is None only before setup."""
    email: str
    person: dict[str, Any] | None
    via: str                               # pomerium | token | dev
    scopes: frozenset[str] = field(default_factory=lambda: frozenset({"read", "write", "act_as"}))
    token_id: int | None = None


class PomeriumVerifier:
    """Verifies Pomerium's assertion. `keys` maps a JWT to its signing key;
    by default Pomerium's JWKS, fetched and cached."""

    def __init__(self, jwks_url: str, audience: str, keys: Callable[[str], Any] | None = None):
        if not audience:
            raise ValueError("CHOREBOARD_AUDIENCE is required with Pomerium (the route's host)")
        self.audience = audience
        if keys is None:
            client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
            keys = lambda token: client.get_signing_key_from_jwt(token).key  # noqa: E731
        self.keys = keys

    def email(self, token: str) -> tuple[str, str]:
        """(email, display name) from a valid assertion; Unauthenticated
        otherwise."""
        try:
            claims = jwt.decode(token, self.keys(token), algorithms=["ES256", "RS256"],
                                audience=self.audience, leeway=CLOCK_LEEWAY_S,
                                options={"require": ["exp", "aud"]})
        except Exception as e:  # noqa: BLE001 -- every failure is the same answer
            log.warning("Pomerium assertion rejected: %s: %s", type(e).__name__, e)
            raise Unauthenticated(f"Pomerium assertion rejected: {e}") from None
        email = str(claims.get("email") or "").strip().lower()
        if not email:
            raise Unauthenticated("the assertion carries no email claim")
        return email, str(claims.get("name") or "")


class Authenticator:
    def __init__(self, settings: Settings, svc: Service, verifier: PomeriumVerifier | None = None):
        self.settings = settings
        self.svc = svc
        if settings.dev_email and (settings.jwks_url or verifier is not None):
            # With both, any request WITHOUT an assertion would be trusted as
            # the dev identity -- an open door next to a locked one.
            raise ValueError("CHOREBOARD_DEV_EMAIL is for development only; unset it when Pomerium is configured")
        if verifier is None and settings.jwks_url:
            verifier = PomeriumVerifier(settings.jwks_url, settings.audience)
        self.verifier = verifier

    def resolve(self, method: str, headers: dict[str, str]) -> Caller:
        h = {k.lower(): v for k, v in headers.items()}
        auth = h.get("authorization", "")
        if auth.lower().startswith("bearer "):
            caller = self._token(auth[7:].strip(), h.get("x-choreboard-as", ""))
        elif h.get("x-pomerium-jwt-assertion") and self.verifier is not None:
            email, name = self.verifier.email(h["x-pomerium-jwt-assertion"])
            caller = Caller(email, self._person(email, name), "pomerium")
        elif self.settings.dev_email:
            email = self.settings.dev_email.strip().lower()
            caller = Caller(email, self._person(email, ""), "dev")
        else:
            raise Unauthenticated("sign in through Pomerium, or send an API token")
        if method.upper() not in READ_METHODS and "write" not in caller.scopes:
            raise Forbidden("this token is read-only")
        return caller

    def _token(self, raw: str, act_as: str) -> Caller:
        tok = self.svc.token_lookup(raw)
        if tok is None:
            raise Unauthenticated("unknown or revoked token")
        scopes = frozenset(tok["scopes"])
        person = self.svc.person(tok["person_id"])
        if act_as:
            if "act_as" not in scopes:
                raise Forbidden("this token can't act as someone else (needs the act_as scope)")
            person = self.svc.person_by_email(act_as)
            if person is None:
                raise Forbidden(f"nobody here signs in as {act_as}")
        if not person.get("active", 1):
            raise Forbidden("that person is deactivated")
        return Caller(person.get("email") or "", person, "token", scopes, tok["id"])

    def _person(self, email: str, name: str) -> dict[str, Any] | None:
        person = self.svc.person_by_email(email, include_inactive=True)
        if person is not None:
            if not person["active"]:
                raise Forbidden(f"{email} has been deactivated here")
            return person
        if not self.svc.people():
            return None                                   # before setup
        if not self.settings.auto_join:
            raise Forbidden(f"{email} isn't in this household; ask someone to add you")
        joined = self.svc.create_person({"id": None, "active": 1}, name.strip() or email.split("@")[0],
                                        email=email)
        return joined
