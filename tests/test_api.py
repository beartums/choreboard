"""The HTTP API end to end, signed in the way production is: an ES256 JWT
in X-Pomerium-Jwt-Assertion, verified against a key -- here our own test key
standing in for Pomerium's JWKS."""
import datetime as dt
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from choreboard import schema
from choreboard.api import create_app
from choreboard.auth import PomeriumVerifier, Settings

AUD = "choreboard.example.com"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER = ec.generate_private_key(ec.SECP256R1())


def assertion(email, name="", key=KEY, aud=AUD, exp_in=300):
    return jwt.encode({"email": email, "name": name, "aud": aud, "exp": int(time.time()) + exp_in},
                      key, algorithm="ES256")


def as_(email, name=""):
    return {"X-Pomerium-Jwt-Assertion": assertion(email, name)}


@pytest.fixture
def client():
    app = create_app(Settings(db_path=":memory:", audience=AUD),
                     verifier=PomeriumVerifier("", AUD, keys=lambda _t: KEY.public_key()),
                     conn=schema.connect(":memory:"))
    return TestClient(app)


@pytest.fixture
def home(client):
    """Eric sets up; Tucker, Cass and Mom join by signing in; the dog's rules."""
    E = as_("eric@example.com")
    assert client.get("/api/me", headers=E).json()["needs_setup"] is True
    eric = client.post("/api/setup", json={"name": "Eric"}, headers=E).json()
    ids = {"eric": eric["id"]}
    for who, email in (("tucker", "tucker@example.com"), ("cass", "cass@example.com"), ("mom", "mom@example.com")):
        ids[who] = client.get("/api/me", headers=as_(email, who.title())).json()["person"]["id"]
    dog = client.post("/api/chores", json={"title": "Walk the dog", "points": 3}, headers=E).json()
    for who, days in (("tucker", [1, 3]), ("cass", [2, 6]), ("mom", [4, 5]), ("eric", [7])):
        r = client.post("/api/rules", json={"chore_id": dog["id"], "person_id": ids[who], "weekdays": days,
                                            "due": "18:00"}, headers=E)
        assert r.status_code == 201, r.text
    # Rules begin the day they're made; backdate so the fixed dates below are covered.
    client.app.state.svc.db.execute("UPDATE rules SET created_at = '2026-01-01T12:00:00Z'")
    return {"E": E, "ids": ids, "dog": dog}


def day(client, h, date):
    return client.get(f"/api/calendar?from={date}&to={date}", headers=h).json()


def test_unsigned_forged_and_expired_are_refused(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"X-Pomerium-Jwt-Assertion": "not-a-jwt"}).status_code == 401
    forged = {"X-Pomerium-Jwt-Assertion": assertion("eric@example.com", key=OTHER)}
    assert client.get("/api/me", headers=forged).status_code == 401
    wrong_aud = {"X-Pomerium-Jwt-Assertion": assertion("eric@example.com", aud="other.example.com")}
    assert client.get("/api/me", headers=wrong_aud).status_code == 401
    expired = {"X-Pomerium-Jwt-Assertion": assertion("eric@example.com", exp_in=-120)}   # beyond the leeway
    assert client.get("/api/me", headers=expired).status_code == 401
    # A plain identity header means nothing.
    assert client.get("/api/me", headers={"X-Authentik-Email": "eric@example.com"}).status_code == 401


def test_setup_once_then_people_join_by_signing_in(client, home):
    people = client.get("/api/people", headers=home["E"]).json()
    assert [(p["name"], p["role"]) for p in people] == [("Eric", "admin"), ("Tucker", "member"),
                                                        ("Cass", "member"), ("Mom", "member")]
    again = client.post("/api/setup", json={"name": "x"}, headers=as_("new@example.com"))
    assert again.status_code == 409


def test_calendar_swap_outcomes_and_points(client, home):
    E, ids = home["E"], home["ids"]
    sun, wed = day(client, E, "2026-10-25")[0], day(client, E, "2026-10-21")[0]
    assert (sun["person_id"], wed["person_id"]) == (ids["eric"], ids["tucker"])

    r = client.post("/api/swaps", json={"a": sun["id"], "b": wed["id"], "reason": "Eric away Sunday"}, headers=E)
    assert r.status_code == 201, r.text
    assert (r.json()["a"]["person_id"], r.json()["b"]["person_id"]) == (ids["tucker"], ids["eric"])
    assert day(client, E, "2026-11-01")[0]["person_id"] == ids["eric"], "the next Sunday is the schedule again"

    T = as_("tucker@example.com")
    client.app.state.svc.clock = lambda: dt.datetime(2026, 10, 25, 12)    # inside Sunday's window
    out = client.post(f"/api/occurrences/{sun['id']}/outcome", json={"status": "done"}, headers=T)
    assert out.status_code == 201, out.text
    assert client.get(f"/api/people/{ids['tucker']}/points", headers=T).json()["balance"] == 3
    assert day(client, E, "2026-10-25")[0]["outcome"]["status"] == "done"

    bad = client.post(f"/api/occurrences/{wed['id']}/outcome", json={"status": "failed", "note": "no"}, headers=E)
    assert bad.status_code == 400 and "explicitly" in bad.json()["error"]

    undo = client.post(f"/api/outcomes/{out.json()['id']}/undo", json={"note": "oops"}, headers=E)
    assert undo.status_code == 200 and undo.json()["undone_at"]
    assert client.get(f"/api/people/{ids['tucker']}/points", headers=E).json()["balance"] == 0
    assert client.delete(f"/api/overrides/{r.json()['group_id']}", headers=E).json() == {"removed": 2}


def test_reassign_skip_add_and_adjust(client, home):
    E, ids = home["E"], home["ids"]
    mon = day(client, E, "2026-10-19")[0]
    r = client.post(f"/api/occurrences/{mon['id']}/reassign", json={"person_id": ids["mom"], "reason": "x"}, headers=E)
    assert r.json()["person_id"] == ids["mom"] and r.json()["scheduled_person_id"] == ids["tucker"]
    tue = day(client, E, "2026-10-20")[0]
    assert client.post(f"/api/occurrences/{tue['id']}/skip", json={"reason": "vet"}, headers=E).json()["skipped"]
    added = client.post("/api/occurrences", json={"chore_id": home["dog"]["id"], "date": "2026-10-20",
                                                  "person_id": ids["cass"], "reason": "extra"}, headers=E)
    assert added.status_code == 201 and added.json()["source"] == "override"
    adj = client.post("/api/ledger", json={"person_id": ids["cass"], "delta": 5, "note": "bonus"}, headers=E)
    assert adj.status_code == 201
    assert client.get(f"/api/people/{ids['cass']}/points", headers=E).json()["balance"] == 5


def test_rules_crud(client, home):
    E = home["E"]
    rules = client.get("/api/rules", headers=E).json()
    first = rules[0]
    body = {**{k: first[k] for k in ("chore_id", "person_id", "due")}, "weekdays": [1]}
    assert client.put(f"/api/rules/{first['id']}", json=body, headers=E).json()["weekdays"] == [1]
    bad = client.post("/api/rules", json={"chore_id": first["chore_id"], "person_id": first["person_id"]}, headers=E)
    assert bad.status_code == 400
    assert client.delete(f"/api/rules/{first['id']}", headers=E).status_code == 204


def test_tokens_read_only_and_act_as(client, home):
    E, ids = home["E"], home["ids"]
    ro = client.post("/api/tokens", json={"name": "wall", "scopes": ["read"]}, headers=E).json()
    nav = client.post("/api/tokens", json={"name": "nav", "scopes": ["read", "write", "act_as"]}, headers=E).json()
    assert ro["token"].startswith("cb_") and "token" not in client.get("/api/tokens", headers=E).json()[0]
    # Members can't mint or list tokens (act_as can act as anyone).
    T = as_("tucker@example.com")
    assert client.post("/api/tokens", json={"name": "mine", "scopes": ["read"]}, headers=T).status_code == 403
    assert client.get("/api/tokens", headers=T).status_code == 403
    listed = client.get("/api/tokens", headers=E).json()
    assert listed[0]["name"] == "nav" and listed[0]["person_name"] == "Eric", "newest first, with the owner's name"

    R = {"Authorization": f"Bearer {ro['token']}"}
    assert client.get("/api/calendar?from=2026-10-19&to=2026-10-19", headers=R).status_code == 200
    mon = day(client, E, "2026-10-19")[0]
    assert client.post(f"/api/occurrences/{mon['id']}/outcome", json={"status": "done"}, headers=R).status_code == 403
    assert client.get("/api/me", headers={**R, "X-Choreboard-As": "tucker@example.com"}).status_code == 403

    N = {"Authorization": f"Bearer {nav['token']}", "X-Choreboard-As": "tucker@example.com"}
    client.app.state.svc.clock = lambda: dt.datetime(2026, 10, 19, 12)    # Tucker's Monday
    assert client.get("/api/me", headers=N).json()["person"]["id"] == ids["tucker"]
    out = client.post(f"/api/occurrences/{mon['id']}/outcome", json={"status": "done"}, headers=N).json()
    assert out["recorded_by"] == ids["tucker"]
    # Tokens can't mint tokens or run setup.
    assert client.post("/api/tokens", json={"name": "x"}, headers=N).status_code == 400

    # An admin acting as Tucker (nav's "view as"): Tucker's permissions, the admin on record.
    V = {**N, "X-Choreboard-Via": "eric@example.com"}
    me = client.get("/api/me", headers=V).json()["person"]
    assert me["id"] == ids["tucker"] and me["via_name"] == "Eric"
    assert client.post(f"/api/outcomes/{out['id']}/undo", json={"note": "oops"}, headers=V).status_code == 200
    again = client.post(f"/api/occurrences/{mon['id']}/outcome", json={"status": "done"}, headers=V).json()
    assert again["via_id"] == ids["eric"]
    # Only an admin may be named as the one acting; and only with an act_as token.
    assert client.get("/api/me", headers={**N, "X-Choreboard-Via": "cass@example.com"}).status_code == 403
    assert client.get("/api/me", headers={**R, "X-Choreboard-As": "tucker@example.com",
                                          "X-Choreboard-Via": "eric@example.com"}).status_code == 403

    client.delete(f"/api/tokens/{nav['id']}", headers=E)
    assert client.get("/api/me", headers=N).status_code == 401


def test_deactivated_people_are_refused(client, home):
    E, ids = home["E"], home["ids"]
    client.patch(f"/api/people/{ids['cass']}", json={"active": False}, headers=E)
    assert client.get("/api/me", headers=as_("cass@example.com")).status_code == 403


def test_auto_join_off():
    app = create_app(Settings(db_path=":memory:", audience=AUD, auto_join=False),
                     verifier=PomeriumVerifier("", AUD, keys=lambda _t: KEY.public_key()),
                     conn=schema.connect(":memory:"))
    c = TestClient(app)
    c.post("/api/setup", json={"name": "Eric"}, headers=as_("eric@example.com"))
    assert c.get("/api/me", headers=as_("stranger@example.com")).status_code == 403


def test_docs_and_health(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert "/api/calendar" in client.get("/openapi.json").json()["paths"]


def test_dev_identity_cannot_sit_beside_pomerium():
    with pytest.raises(ValueError, match="development only"):
        create_app(Settings(db_path=":memory:", audience=AUD, dev_email="x@example.com"),
                   verifier=PomeriumVerifier("", AUD, keys=lambda _t: KEY.public_key()),
                   conn=schema.connect(":memory:"))


def test_patch_chore_and_person(client, home):
    E, ids, dog = home["E"], home["ids"], home["dog"]
    r = client.patch(f"/api/chores/{dog['id']}", json={"points": 4, "title": "Walk Biscuit"}, headers=E)
    assert r.status_code == 200 and (r.json()["points"], r.json()["title"]) == (4, "Walk Biscuit")
    assert client.patch(f"/api/chores/{dog['id']}", json={"points": -2}, headers=E).status_code == 422
    assert client.patch("/api/chores/999", json={"title": "x"}, headers=E).status_code == 404
    p = client.patch(f"/api/people/{ids['mom']}", json={"name": "Mum", "color": "rose"}, headers=E)
    assert p.status_code == 200 and p.json()["name"] == "Mum"
    assert client.patch(f"/api/people/{ids['mom']}", json={"email": "eric@example.com"}, headers=E).status_code == 409
    assert client.patch(f"/api/people/{ids['mom']}", json={"role": "owner"}, headers=E).status_code == 422


def test_serves_the_ui_with_browser_routes(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    app = create_app(Settings(db_path=":memory:", audience=AUD),
                     verifier=PomeriumVerifier("", AUD, keys=lambda _t: KEY.public_key()),
                     conn=schema.connect(":memory:"), static_dir=str(tmp_path))
    c = TestClient(app)
    assert "root" in c.get("/").text
    assert "root" in c.get("/chores/3").text, "browser routes fall back to the app"
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/nope").status_code == 404 and c.get("/api/nope").json()["error"]
    assert c.get("/api/me").status_code == 401, "API routes still win"
    assert c.get("/../pyproject.toml").status_code in (200, 404) and "root" in c.get("/../pyproject.toml").text


def test_rule_time_window_over_http(client, home):
    E = home["E"]
    first = client.get("/api/rules", headers=E).json()[0]
    body = {k: first[k] for k in ("chore_id", "person_id", "weekdays")} | {"available": "16:00", "due": "18:00"}
    r = client.put(f"/api/rules/{first['id']}", json=body, headers=E)
    assert r.status_code == 200 and (r.json()["available"], r.json()["due"]) == ("16:00", "18:00")
    cal = client.get("/api/calendar?from=2026-10-19&to=2026-10-19", headers=E).json()
    assert (cal[0]["available"], cal[0]["due"]) == ("16:00", "18:00")
    bad = client.put(f"/api/rules/{first['id']}", json=body | {"available": "19:00"}, headers=E)
    assert bad.status_code == 400 and "before the due" in bad.json()["error"]


def test_clock_skew_is_tolerated_but_not_forever(client, home):
    """Pomerium's node may run a little ahead of this pod's: a token issued
    "30 seconds from now" is accepted; one issued two minutes ahead isn't."""
    def stamped(iat_offset):
        now = int(time.time())
        return {"X-Pomerium-Jwt-Assertion": jwt.encode(
            {"email": "eric@example.com", "aud": AUD, "iat": now + iat_offset, "nbf": now + iat_offset,
             "exp": now + 300}, KEY, algorithm="ES256")}
    assert client.get("/api/me", headers=stamped(30)).status_code == 200
    assert client.get("/api/me", headers=stamped(120)).status_code == 401
    r = client.get("/api/me", headers=stamped(120))
    assert "not yet valid" in r.json()["error"] or "iat" in r.json()["error"]


def test_health_is_reachable_under_api(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_notifications_over_http(home):
    """Subscribe, act, and the worker delivers -- through the API."""
    import asyncio
    from choreboard import notify as _n

    class Fake:
        def __init__(self):
            self.sent = []

        def send(self, address, title, body):
            self.sent.append((address, title, body))

    email = Fake()
    app = create_app(Settings(db_path=":memory:", audience=AUD),
                     verifier=PomeriumVerifier("", AUD, keys=lambda _t: KEY.public_key()),
                     conn=schema.connect(":memory:"), senders={"email": email})
    c = TestClient(app)
    E, T = as_("eric@example.com"), as_("tucker@example.com", "Tucker")
    c.post("/api/setup", json={"name": "Eric"}, headers=E)
    tid = c.get("/api/me", headers=T).json()["person"]["id"]
    cat = c.get("/api/notifications/catalog", headers=T).json()
    assert {"channel": "email", "configured": True} in cat["channels"]
    assert {"channel": "discord", "configured": False} in cat["channels"]
    assert any(e["kind"] == "swapped" for e in cat["events"])

    assert c.post("/api/me/subscriptions", json={"event": "points", "channel": "email"}, headers=T).status_code == 201
    assert c.post("/api/me/subscriptions", json={"event": "nope", "channel": "email"}, headers=T).status_code == 400
    assert c.put("/api/me/channels/discord", json={"address": "bob"}, headers=T).status_code == 400
    c.post("/api/ledger", json={"person_id": tid, "delta": 2, "note": "helping"}, headers=E)
    assert asyncio.run(app.state.deliver_now()) == 1
    assert email.sent == [("tucker@example.com", "Points +2", "Eric gave you 2 points: helping.")]
    log = c.get("/api/me/deliveries", headers=T).json()
    assert log[0]["status"] == "sent" and log[0]["kind"] == "points"
    assert c.post("/api/me/channels/email/test", headers=T).status_code == 202
    asyncio.run(app.state.deliver_now())
    assert email.sent[-1][1] == "Choreboard test"
    _ = _n


def test_inbox_over_http(client, home):
    E, ids = home["E"], home["ids"]
    T = as_("tucker@example.com")
    client.post("/api/me/subscriptions", json={"event": "points", "channel": "app"}, headers=T)
    client.post("/api/ledger", json={"person_id": ids["tucker"], "delta": 4, "note": "great week"}, headers=E)
    box = client.get("/api/me/inbox", headers=T).json()
    assert box["unread"] == 1 and box["items"][0]["title"] == "Points +4" and box["items"][0]["read_at"] is None
    r = client.post("/api/me/inbox/read", json={}, headers=T).json()
    assert r == {"changed": 1, "unread": 0}
    assert client.get("/api/me/inbox?unread=true", headers=T).json()["items"] == []
    assert client.get("/api/me/inbox", headers=E).json() == {"unread": 0, "items": []}, "Tucker's, not Eric's"


def test_member_swap_request_over_http(client, home):
    E, ids = home["E"], home["ids"]
    T = as_("tucker@example.com")
    wed, sun = day(client, T, "2026-10-21")[0], day(client, T, "2026-10-25")[0]
    assert "propose_swap" in wed["can"] and "swap" not in wed["can"]
    assert client.post("/api/swaps", json={"a": wed["id"], "b": sun["id"], "reason": "x"}, headers=T).status_code == 403
    r = client.post("/api/swap-requests", json={"a": wed["id"], "b": sun["id"], "reason": "practice"}, headers=T)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert day(client, E, "2026-10-25")[0]["pending_swap"]["id"] == rid
    assert [x["id"] for x in client.get("/api/swap-requests", headers=E).json()] == [rid]
    sun_e = day(client, E, "2026-10-25")[0]
    assert {"approve_swap", "decline_swap"} <= set(sun_e["can"])
    ok = client.post(f"/api/swap-requests/{rid}/approve", json={"reason": "sure"}, headers=E)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert day(client, E, "2026-10-21")[0]["person_id"] == ids["eric"]


def test_an_admin_signed_in_can_act_as_someone(client, home):
    E, ids = home["E"], home["ids"]
    A = {**E, "X-Choreboard-Act-As": str(ids["tucker"])}
    me = client.get("/api/me", headers=A).json()["person"]
    assert me["id"] == ids["tucker"] and me["via_name"] == "Eric" and me["role"] == "member"
    # Tucker's permissions apply: no admin pages.
    assert client.get("/api/tokens", headers=A).status_code == 403
    # Members can't act as anyone; nor can a bad id.
    T = {**as_("tucker@example.com"), "X-Choreboard-Act-As": str(ids["eric"])}
    assert client.get("/api/me", headers=T).status_code == 403
    assert client.get("/api/me", headers={**E, "X-Choreboard-Act-As": "999"}).status_code == 403
