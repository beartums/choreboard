"""Server settings: notification credentials stored from the app, encrypted
with a key from the environment, admin-only, write-only."""
import pytest
from fastapi.testclient import TestClient

from choreboard import schema, settings
from choreboard.api import build_senders, create_app
from choreboard.auth import PomeriumVerifier, Settings
from choreboard.service import Forbidden, Invalid, Service

KEY = "k" * 40


@pytest.fixture
def svc():
    s = Service(schema.connect(":memory:"))
    s.eric = s.bootstrap("Eric", "eric@example.com")
    s.kid = s.create_person(s.eric, "Tucker", email="t@example.com")
    return s


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(settings.KEY_ENV, KEY)
    for f in settings.FIELDS:
        monkeypatch.delenv(f.env, raising=False)


def test_secrets_are_encrypted_at_rest_and_never_returned(svc, key):
    svc.update_server_settings(svc.eric, {"smtp.user": "bot@gmail.com", "smtp.password": "abcd efgh ijkl mnop"})
    raw = svc.db.execute("SELECT value, secret FROM settings WHERE key = 'smtp.password'").fetchone()
    assert raw[1] == 1 and "abcd" not in raw[0], "ciphertext in the database"
    assert settings.get(svc.db, "smtp.password") == "abcd efgh ijkl mnop"
    view = {f["key"]: f for f in svc.server_settings(svc.eric)["fields"]}
    assert view["smtp.password"]["set"] is True and "value" not in view["smtp.password"]
    assert view["smtp.user"]["value"] == "bot@gmail.com" and view["smtp.user"]["source"] == "app"
    assert view["smtp.host"]["value"] == "smtp.gmail.com" and view["smtp.host"]["source"] == "default"


def test_no_key_no_secrets(svc, monkeypatch):
    monkeypatch.delenv(settings.KEY_ENV, raising=False)
    with pytest.raises(Invalid, match="CHOREBOARD_SECRET_KEY"):
        svc.update_server_settings(svc.eric, {"discord.token": "tok"})
    svc.update_server_settings(svc.eric, {"smtp.user": "bot@gmail.com"})      # non-secrets are fine
    assert svc.server_settings(svc.eric)["can_store_secrets"] is False
    monkeypatch.setenv(settings.KEY_ENV, "too-short")
    assert settings.can_store_secrets() is False


def test_a_changed_key_reads_as_unset_and_says_why(svc, key, monkeypatch):
    svc.update_server_settings(svc.eric, {"discord.token": "tok"})
    monkeypatch.setenv(settings.KEY_ENV, "z" * 40)
    view = {f["key"]: f for f in svc.server_settings(svc.eric)["fields"]}
    assert view["discord.token"]["set"] is False and "can't be decrypted" in view["discord.token"]["problem"]
    assert "discord" not in build_senders(svc.db)


def test_environment_wins(svc, key, monkeypatch):
    svc.update_server_settings(svc.eric, {"smtp.user": "app@gmail.com"})
    monkeypatch.setenv("CHOREBOARD_SMTP_USER", "env@gmail.com")
    view = {f["key"]: f for f in svc.server_settings(svc.eric)["fields"]}
    assert (view["smtp.user"]["value"], view["smtp.user"]["source"]) == ("env@gmail.com", "env")


def test_admins_only(svc, key):
    with pytest.raises(Forbidden):
        svc.server_settings(svc.kid)
    with pytest.raises(Forbidden):
        svc.update_server_settings(svc.kid, {"smtp.user": "x@gmail.com"})


def test_clearing_and_validation(svc, key):
    svc.update_server_settings(svc.eric, {"smtp.port": "465"})
    assert settings.get(svc.db, "smtp.port") == "465"
    svc.update_server_settings(svc.eric, {"smtp.port": None})
    assert settings.get(svc.db, "smtp.port") == "587", "cleared: back to the default"
    for bad in ({"smtp.port": "abc"}, {"nope": "x"}):
        with pytest.raises(Invalid):
            svc.update_server_settings(svc.eric, bad)


def test_senders_follow_settings_live(svc, key):
    assert build_senders(svc.db) == {}
    svc.update_server_settings(svc.eric, {"smtp.user": "bot@gmail.com", "smtp.password": "pw", "discord.token": "t"})
    s = build_senders(svc.db)
    assert set(s) == {"email", "discord"}
    assert (s["email"].user, s["email"].password, s["email"].sender, s["email"].port) == (
        "bot@gmail.com", "pw", "Choreboard <bot@gmail.com>", 587)


def test_settings_over_http(key):
    import time
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    aud = "cb.example.com"

    def as_(email):
        return {"X-Pomerium-Jwt-Assertion": jwt.encode({"email": email, "aud": aud, "exp": int(time.time()) + 300},
                                                        k, algorithm="ES256")}
    app = create_app(Settings(db_path=":memory:", audience=aud),
                     verifier=PomeriumVerifier("", aud, keys=lambda _t: k.public_key()), conn=schema.connect(":memory:"))
    c = TestClient(app)
    E, T = as_("eric@example.com"), as_("tucker@example.com")
    c.post("/api/setup", json={"name": "Eric"}, headers=E)
    c.get("/api/me", headers=T)
    assert c.get("/api/settings", headers=T).status_code == 403
    r = c.put("/api/settings", json={"values": {"smtp.user": "bot@gmail.com", "smtp.password": "pw"}}, headers=E)
    assert r.status_code == 200 and r.json()["configured"] == ["email"]
    assert "pw" not in r.text
    cat = c.get("/api/notifications/catalog", headers=T).json()
    assert {"channel": "email", "configured": True} in cat["channels"], "the catalog sees it at once"
