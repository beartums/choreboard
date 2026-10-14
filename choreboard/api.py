"""The HTTP API: a thin skin over service.py. Bodies are validated here;
every decision is made there. Interactive docs at /docs.

Run: uvicorn --factory choreboard.api:create_app   (settings: CHOREBOARD_* env, auth.py)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from contextlib import asynccontextmanager

from . import notify, schema, settings as server_settings
from .auth import Authenticator, Caller, PomeriumVerifier, Settings, Unauthenticated
from .calendar import Pattern
from .service import Entry, Error, Invalid, Service, entries_json, parse_key

Weekday = int


class PersonIn(BaseModel):
    name: str
    email: str | None = None
    color: str = ""
    role: Literal["admin", "member"] = "member"


class PersonPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    color: str | None = None
    role: Literal["admin", "member"] | None = None
    active: bool | None = None


class ChoreIn(BaseModel):
    title: str
    points: int = Field(0, ge=0)
    description: str = ""
    icon: str = ""


class ChorePatch(BaseModel):
    title: str | None = None
    points: int | None = Field(None, ge=0)
    description: str | None = None
    icon: str | None = None
    active: bool | None = None


class RuleIn(BaseModel):
    """Exactly one of: weekdays (ISO, 1 = Monday ... 7 = Sunday);
    every_days + anchor; on."""
    chore_id: int
    person_id: int
    weekdays: list[Weekday] | None = None
    every_days: int | None = None
    anchor: dt.date | None = None
    on: dt.date | None = None
    available: dt.time | None = Field(None, description="start of the daily window: not before")
    due: dt.time | None = Field(None, description="end of the daily window: done by")
    start: dt.date | None = None
    end: dt.date | None = None

    def pattern(self) -> Pattern:
        try:
            return Pattern(weekdays=frozenset(self.weekdays) if self.weekdays is not None else None,
                           every_days=self.every_days, anchor=self.anchor, on=self.on)
        except ValueError as e:
            raise Invalid(str(e)) from None


class Reason(BaseModel):
    reason: str = ""


class ReassignIn(BaseModel):
    person_id: int
    reason: str = ""


class AddIn(BaseModel):
    chore_id: int
    date: dt.date
    person_id: int
    available: dt.time | None = None
    due: dt.time | None = None
    reason: str = ""


class SwapIn(BaseModel):
    a: str = Field(description="occurrence id, chore:YYYY-MM-DD:rule|add:id")
    b: str
    reason: str


class OutcomeIn(BaseModel):
    status: Literal["done", "skipped", "failed", "covered", "failed_covered"]
    note: str = ""
    done_by: int | None = None
    deduct: int | None = Field(None, ge=0)


class Note(BaseModel):
    note: str


class AdjustIn(BaseModel):
    person_id: int
    delta: int
    note: str


class TokenIn(BaseModel):
    name: str
    scopes: list[Literal["read", "write", "act_as"]] = ["read"]
    person_id: int | None = None


class SetupIn(BaseModel):
    name: str


class ReadIn(BaseModel):
    ids: list[int] | None = Field(None, description="omit to mark all")
    read: bool = True


class ChannelIn(BaseModel):
    address: str | None = Field(None, description="email address, or numeric Discord user id; empty clears")
    enabled: bool = True


class SubscriptionIn(BaseModel):
    event: str
    channel: Literal["app", "email", "discord"]
    chore_id: int | None = Field(None, description="one chore; omit for all of my chores")


def build_senders(db: Any) -> dict[str, notify.Sender]:
    """The channels this server can send on, from settings (environment
    first, then what an admin stored in the app). Rebuilt on every use, so a
    change on the settings page applies on the next delivery."""
    get = lambda k: server_settings.get(db, k)  # noqa: E731
    out: dict[str, notify.Sender] = {}
    user, password = get("smtp.user"), get("smtp.password")
    if user and password:
        port = get("smtp.port")
        out["email"] = notify.SmtpSender(host=get("smtp.host") or "smtp.gmail.com",
                                         port=int(port) if port.isdigit() else 587, user=user, password=password,
                                         sender=get("smtp.from") or f"Choreboard <{user}>")
    if get("discord.token"):
        out["discord"] = notify.DiscordSender(get("discord.token"))
    return out


class SettingsIn(BaseModel):
    """Keys to change; a value of null or "" clears one (back to the
    environment or the default). Omitted keys are left alone."""
    values: dict[str, str | None]


def _rule_json(r: Any) -> dict[str, Any]:
    p = r.pattern
    return {"id": r.id, "chore_id": r.chore_id, "person_id": r.person_id,
            "weekdays": sorted(p.weekdays) if p.weekdays else None, "every_days": p.every_days,
            "anchor": p.anchor, "on": p.on,
            "available": r.available.strftime("%H:%M") if r.available else None,
            "due": r.due.strftime("%H:%M") if r.due else None,
            "start": r.start, "end": r.end,
            # The first day the rule applies: `start`, or the day it was made.
            "first_day": r.first_day}


def create_app(settings: Settings | None = None, verifier: PomeriumVerifier | None = None,
               conn: Any = None, static_dir: str | None = None,
               senders: dict[str, notify.Sender] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    svc = Service(conn if conn is not None else schema.connect(settings.db_path))
    authn = Authenticator(settings, svc, verifier)
    fixed_senders = senders                       # tests pass their own

    def current_senders() -> dict[str, notify.Sender]:
        return fixed_senders if fixed_senders is not None else build_senders(svc.db)
    link = os.environ.get("CHOREBOARD_PUBLIC_URL", "").rstrip("/")
    link = f"{link}/calendar" if link else ""

    # One SQLite connection, one request at a time. A household makes a few
    # requests a minute; serialising them is simpler than a connection pool
    # and keeps transactions from interleaving on the shared connection.
    lock = asyncio.Lock()

    async def deliver_now() -> int:
        """One pass of the outbox: claim what's due (under the lock), send it
        (outside the lock, so a slow mail server never holds up requests),
        record each result (under the lock)."""
        async with lock:
            batch = notify.claim_due(svc.db)
            senders = current_senders()
        for d in batch:
            err, retry = await asyncio.to_thread(notify.send_one, d, senders, link)
            async with lock:
                notify.finish(svc.db, d, err, retry)
        return len(batch)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        async def worker() -> None:
            # A delivery left 'sending' by a restart mid-send goes back in the queue.
            async with lock:
                svc.db.execute("UPDATE deliveries SET status = 'queued' WHERE status = 'sending'")
            while True:
                try:
                    await deliver_now()
                except Exception:  # noqa: BLE001 -- keep the worker alive; each delivery logs its own failure
                    notify.log.exception("notification worker pass failed")
                await asyncio.sleep(15)
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="choreboard", version="0.1.0", lifespan=lifespan,
                  description="Household chores as a calendar: rules give the defaults, date overrides win.")
    app.state.deliver_now = deliver_now

    @app.middleware("http")
    async def _one_at_a_time(request: Request, call_next: Any) -> Any:
        async with lock:
            return await call_next(request)

    @app.exception_handler(Error)
    async def _service_error(_: Request, e: Error) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.exception_handler(Unauthenticated)
    async def _unauth(_: Request, e: Unauthenticated) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=401)

    def caller(request: Request) -> Caller:
        return authn.resolve(request.method, dict(request.headers))

    def actor(c: Caller = Depends(caller)) -> dict[str, Any]:
        if c.person is None:
            raise Invalid("nobody is set up yet: POST /api/setup first")
        return c.person

    # --- health, identity, setup ------------------------------------------------

    # /healthz is for the kubelet's probes, inside the cluster. Through the
    # public URL Pomerium answers /healthz itself (a bare "ok"), so the same
    # check is also at /api/health, which reaches the app.
    @app.get("/healthz", include_in_schema=False)
    @app.get("/api/health", include_in_schema=False)
    def healthz() -> dict[str, str]:
        svc.db.execute("SELECT 1")
        return {"status": "ok", "version": os.environ.get("CHOREBOARD_VERSION", "dev")}

    @app.get("/api/me")
    def me(c: Caller = Depends(caller)) -> dict[str, Any]:
        return {"email": c.email, "person": c.person, "via": c.via, "scopes": sorted(c.scopes),
                "needs_setup": c.person is None}

    @app.post("/api/setup", status_code=201)
    def setup(body: SetupIn, c: Caller = Depends(caller)) -> dict[str, Any]:
        """The first person: the signed-in caller, as an admin."""
        if c.via == "token":
            raise Invalid("setup is done by a person signing in, not a token")
        return svc.bootstrap(body.name, c.email)

    # --- people and chores ------------------------------------------------------

    @app.get("/api/people")
    def people(_: Caller = Depends(caller)) -> list[dict[str, Any]]:
        return svc.people()

    @app.post("/api/people", status_code=201)
    def create_person(body: PersonIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.create_person(a, body.name, email=body.email, color=body.color, role=body.role)

    @app.patch("/api/people/{pid}")
    def update_person(pid: int, body: PersonPatch, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.update_person(a, pid, **body.model_dump(exclude_unset=True))

    @app.get("/api/chores")
    def chores(_: Caller = Depends(caller)) -> list[dict[str, Any]]:
        return svc.chores()

    @app.post("/api/chores", status_code=201)
    def create_chore(body: ChoreIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.create_chore(a, body.title, points=body.points, description=body.description, icon=body.icon)

    @app.patch("/api/chores/{cid}")
    def update_chore(cid: int, body: ChorePatch, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.update_chore(a, cid, **body.model_dump(exclude_unset=True))

    # --- rules ------------------------------------------------------------------

    @app.get("/api/rules")
    def rules(chore_id: int | None = None, _: Caller = Depends(caller)) -> list[dict[str, Any]]:
        return [_rule_json(r) for r in svc.rules(chore_id)]

    @app.post("/api/rules", status_code=201)
    def create_rule(body: RuleIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return _rule_json(svc.create_rule(a, body.chore_id, body.person_id, body.pattern(), due=body.due,
                                          start=body.start, end=body.end, available=body.available))

    @app.put("/api/rules/{rid}")
    def update_rule(rid: int, body: RuleIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return _rule_json(svc.update_rule(a, rid, body.chore_id, body.person_id, body.pattern(), due=body.due,
                                          start=body.start, end=body.end, available=body.available))

    @app.delete("/api/rules/{rid}", status_code=204)
    def delete_rule(rid: int, a: dict = Depends(actor)) -> None:
        svc.delete_rule(a, rid)

    # --- the calendar and what changes it ---------------------------------------

    @app.get("/api/calendar")
    def calendar(start: dt.date = Query(alias="from"), end: dt.date = Query(alias="to"),
                 person_id: int | None = None, include_skipped: bool = True,
                 _: Caller = Depends(caller)) -> list[dict[str, Any]]:
        """Every occurrence from `from` to `to` (inclusive, at most a year),
        overrides applied, with any outcome recorded against it."""
        return list(entries_json(svc.calendar(start, end, person_id, include_skipped)))

    def _occ(o: Any) -> dict[str, Any]:
        return next(entries_json([Entry(o, None)]))

    @app.post("/api/occurrences/{key}/reassign")
    def reassign(key: str, body: ReassignIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return _occ(svc.reassign(a, parse_key(key), body.person_id, body.reason))

    @app.post("/api/occurrences/{key}/skip")
    def skip(key: str, body: Reason, a: dict = Depends(actor)) -> dict[str, Any]:
        return _occ(svc.skip(a, parse_key(key), body.reason))

    @app.post("/api/occurrences", status_code=201)
    def add(body: AddIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return _occ(svc.add(a, body.chore_id, body.date, body.person_id, body.reason, body.due, body.available))

    @app.post("/api/swaps", status_code=201)
    def swap(body: SwapIn, a: dict = Depends(actor)) -> dict[str, Any]:
        group = svc.swap(a, parse_key(body.a), parse_key(body.b), body.reason)
        return {"group_id": group, "a": _occ(svc.occurrence(parse_key(body.a))),
                "b": _occ(svc.occurrence(parse_key(body.b)))}

    @app.delete("/api/overrides/{group_or_id}")
    def remove_override(group_or_id: str, a: dict = Depends(actor)) -> dict[str, int]:
        """Undo a swap (its group id) or one override (its numeric id)."""
        return {"removed": svc.remove_override_group(a, group_or_id)}

    # --- outcomes and points ----------------------------------------------------

    @app.post("/api/occurrences/{key}/outcome", status_code=201)
    def record(key: str, body: OutcomeIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.record(a, parse_key(key), body.status, note=body.note, done_by=body.done_by, deduct=body.deduct)

    @app.post("/api/outcomes/{oid}/undo")
    def undo(oid: int, body: Note, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.undo(a, oid, body.note)

    @app.get("/api/people/{pid}/points")
    def points(pid: int, limit: int = Query(100, ge=1, le=1000), _: Caller = Depends(caller)) -> dict[str, Any]:
        return {"person_id": pid, "balance": svc.balance(pid), "entries": svc.ledger(pid, limit)}

    @app.post("/api/ledger", status_code=201)
    def adjust(body: AdjustIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.adjust(a, body.person_id, body.delta, body.note)

    # --- tokens -----------------------------------------------------------------

    @app.get("/api/tokens")
    def tokens(a: dict = Depends(actor)) -> list[dict[str, Any]]:
        """Admins only. Never the tokens themselves -- only their first characters."""
        return svc.tokens(a)

    @app.post("/api/tokens", status_code=201)
    def create_token(body: TokenIn, a: dict = Depends(actor), c: Caller = Depends(caller)) -> dict[str, Any]:
        """The token is in this response ONLY; keep it."""
        if c.via == "token":
            raise Invalid("tokens are created by a person signing in, not by another token")
        raw, rec = svc.create_token(a, body.name, list(body.scopes), body.person_id)
        return {**rec, "token": raw}

    @app.delete("/api/tokens/{tid}")
    def revoke_token(tid: int, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.revoke_token(a, tid)

    # --- notifications ---------------------------------------------------------

    @app.get("/api/notifications/catalog")
    def catalog(_: Caller = Depends(caller)) -> dict[str, Any]:
        """The events you can subscribe to, and which channels this server
        can actually send on."""
        return {"events": [{"kind": k, "label": lbl, "scope": sc} for k, (lbl, sc) in notify.EVENTS.items()],
                "channels": [{"channel": "app", "configured": True}]
                + [{"channel": c, "configured": c in current_senders()} for c in notify.CHANNELS]}

    @app.get("/api/me/notifications")
    def my_notifications(a: dict = Depends(actor)) -> dict[str, Any]:
        return {"channels": svc.channels(a["id"]), "subscriptions": svc.subscriptions(a["id"])}

    @app.put("/api/me/channels/{channel}")
    def set_channel(channel: str, body: ChannelIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.set_channel(a, a["id"], channel, body.address, body.enabled)

    @app.post("/api/me/channels/{channel}/test", status_code=202)
    def test_channel(channel: str, a: dict = Depends(actor)) -> dict[str, str]:
        svc.test_notification(a, a["id"], channel)
        return {"status": "queued; see /api/me/deliveries in a few seconds"}

    @app.post("/api/me/subscriptions", status_code=201)
    def subscribe(body: SubscriptionIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return svc.subscribe(a, a["id"], body.event, body.channel, body.chore_id)

    @app.delete("/api/me/subscriptions/{sid}", status_code=204)
    def unsubscribe(sid: int, a: dict = Depends(actor)) -> None:
        svc.unsubscribe(a, a["id"], sid)

    @app.get("/api/me/inbox")
    def inbox(unread: bool = False, limit: int = Query(50, ge=1, le=200), a: dict = Depends(actor)) -> dict[str, Any]:
        """Your notifications (everything you haven't ignored), newest first."""
        return {"unread": svc.unread(a["id"]), "items": svc.inbox(a["id"], unread, limit)}

    @app.post("/api/me/inbox/read")
    def mark_read(body: ReadIn, a: dict = Depends(actor)) -> dict[str, int]:
        return {"changed": svc.mark_read(a, a["id"], body.ids, body.read), "unread": svc.unread(a["id"])}

    @app.get("/api/me/deliveries")
    def my_deliveries(limit: int = Query(50, ge=1, le=200), a: dict = Depends(actor)) -> list[dict[str, Any]]:
        return svc.deliveries(a["id"], limit)

    # --- server settings (admins) ---------------------------------------------

    @app.get("/api/settings")
    def get_settings(a: dict = Depends(actor)) -> dict[str, Any]:
        """Notification credentials and where each comes from. Secrets say only
        whether they're set."""
        return {**svc.server_settings(a), "configured": sorted(current_senders())}

    @app.put("/api/settings")
    def put_settings(body: SettingsIn, a: dict = Depends(actor)) -> dict[str, Any]:
        return {**svc.update_server_settings(a, body.values), "configured": sorted(current_senders())}

    # --- the web UI (web/, built into choreboard/static) -------------------------
    # Registered last, so every API route above wins. A browser route such as
    # /chores/3 gets index.html and the React router takes it from there;
    # unknown /api paths still 404 as JSON.
    static = Path(static_dir) if static_dir else Path(__file__).parent / "static"
    if (static / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def ui(path: str) -> Any:
            if path.startswith(("api/", "assets/")) or path in ("api", "docs", "redoc", "openapi.json"):
                return JSONResponse({"error": "not found"}, status_code=404)
            f = static / path
            if path and f.is_file() and static in f.resolve().parents:
                return FileResponse(f)                          # favicon and friends
            return FileResponse(static / "index.html", headers={"cache-control": "no-cache"})

    app.state.svc = svc
    return app

