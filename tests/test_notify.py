"""Notifications: who hears about what, in which words, and how delivery
behaves when a channel fails."""
import datetime as dt

import pytest

from choreboard import notify, schema
from choreboard.calendar import Pattern
from choreboard.service import Invalid, Service

D = dt.date
MON, WED, SUN = D(2026, 10, 19), D(2026, 10, 21), D(2026, 10, 25)


class FakeSender:
    def __init__(self, fail_times=0, error=RuntimeError("smtp timeout")):
        self.sent, self.fail_times, self.error = [], fail_times, error

    def send(self, address, title, body):
        if self.fail_times:
            self.fail_times -= 1
            raise self.error
        self.sent.append((address, title, body))


@pytest.fixture
def h():
    svc = Service(schema.connect(":memory:"))
    eric = svc.bootstrap("Eric", "eric@example.com")
    tucker = svc.create_person(eric, "Tucker", email="tucker@example.com")
    mom = svc.create_person(eric, "Mom", email="mom@example.com")
    dog = svc.create_chore(eric, "Walk the dog", points=3, icon="🐕")
    for person, days in ((tucker, {1, 3}), (mom, {4, 5}), (eric, {7})):
        svc.create_rule(eric, dog["id"], person["id"], Pattern(weekdays=frozenset(days)), due=dt.time(18))
    svc.db.execute("UPDATE rules SET created_at = '2026-01-01T12:00:00Z'")
    svc.db.execute("DELETE FROM deliveries")                     # setup noise

    class H:
        pass
    x = H()
    x.svc, x.eric, x.tucker, x.mom, x.dog = svc, eric, tucker, mom, dog
    return x


def key(h, date):
    return h.svc.calendar(date, date)[0].occurrence.key


def queued(h, person):
    return [d for d in h.svc.deliveries(person["id"]) if d["status"] == "queued"]


def drain(h, senders):
    """What the worker does, without its timer."""
    for d in notify.claim_due(h.svc.db):
        err, retry = notify.send_one(d, senders)
        notify.finish(h.svc.db, d, err, retry)


def test_nothing_is_sent_without_a_subscription(h):
    h.svc.swap(h.eric, key(h, SUN), key(h, WED), "Eric away")
    assert queued(h, h.tucker) == []


def test_a_swap_tells_the_other_person_in_plain_words(h):
    h.svc.subscribe(h.tucker, h.tucker["id"], "swapped", "email")
    h.svc.subscribe(h.eric, h.eric["id"], "swapped", "email")
    h.svc.swap(h.eric, key(h, SUN), key(h, WED), "Eric away Sunday")
    [d] = queued(h, h.tucker)
    assert d["address"] == "tucker@example.com", "email defaults to the sign-in address"
    assert d["title"] == "Swapped: you now do 🐕 Walk the dog on Sun Oct 25, by 6 PM"
    assert "you now do 🐕 Walk the dog on Sun Oct 25" in d["body"] and "Eric away Sunday" in d["body"]
    assert queued(h, h.eric) == [], "never about your own action"


def test_when_the_other_person_swaps_it_says_so(h):
    h.svc.update_person(h.eric, h.tucker["id"], role="admin")    # an admin's swap is immediate
    h.tucker["role"] = "admin"
    h.svc.subscribe(h.eric, h.eric["id"], "swapped", "email")
    h.svc.swap(h.tucker, key(h, WED), key(h, SUN), "practice")
    [d] = queued(h, h.eric)
    assert d["body"].startswith("Tucker swapped with you: you now do 🐕 Walk the dog on Wed Oct 21")


def test_missed_and_excused_and_covered(h):
    for ev in ("missed", "excused", "covered", "done_by_other"):
        h.svc.subscribe(h.tucker, h.tucker["id"], ev, "email")
    h.svc.record(h.eric, key(h, MON), "failed", note="forgot", deduct=2)
    h.svc.skip(h.eric, key(h, WED), "vet visit")
    titles = [d["title"] for d in queued(h, h.tucker)]
    assert "Missed: 🐕 Walk the dog on Mon Oct 19, by 6 PM" in titles
    assert "Skipped: 🐕 Walk the dog on Wed Oct 21, by 6 PM" in titles
    missed = next(d for d in queued(h, h.tucker) if d["kind"] == "missed")
    assert "−2 points" in missed["body"] and "forgot" in missed["body"]
    h.svc.record(h.eric, key(h, D(2026, 10, 26)), "covered", done_by=h.mom["id"], note="practice")
    assert any(d["kind"] == "covered" and "Mom did your" in d["body"] for d in queued(h, h.tucker))


def test_done_by_yourself_is_not_news(h):
    h.svc.subscribe(h.tucker, h.tucker["id"], "done_by_other", "email")
    h.svc.clock = lambda: dt.datetime(2026, 10, 19, 17, 30)      # Monday, before the 6 PM due time
    h.svc.record(h.tucker, key(h, MON), "done")
    assert queued(h, h.tucker) == []
    h.svc.record(h.eric, key(h, WED), "done")
    assert [d["kind"] for d in queued(h, h.tucker)] == ["done_by_other"]


def test_a_subscription_can_be_for_one_chore(h):
    bins = h.svc.create_chore(h.eric, "Bins", points=1)
    h.svc.create_rule(h.eric, bins["id"], h.tucker["id"], Pattern(weekdays=frozenset({1})))
    h.svc.db.execute("UPDATE rules SET created_at = '2026-01-01T12:00:00Z'")
    h.svc.subscribe(h.tucker, h.tucker["id"], "missed", "email", chore_id=bins["id"])
    day = h.svc.calendar(MON, MON)
    dog_mon = next(e.occurrence.key for e in day if e.occurrence.chore_id == h.dog["id"])
    bins_mon = next(e.occurrence.key for e in day if e.occurrence.chore_id == bins["id"])
    h.svc.record(h.eric, dog_mon, "failed", note="x", deduct=0)
    assert queued(h, h.tucker) == [], "the dog isn't the chore he asked about"
    h.svc.record(h.eric, bins_mon, "failed", note="x", deduct=0)
    assert [d["title"].split(":")[0] for d in queued(h, h.tucker)] == ["Missed"]


def test_new_chore_reaches_everyone_but_whoever_added_it(h):
    for p in (h.tucker, h.mom, h.eric):
        h.svc.subscribe(p, p["id"], "new_chore", "email")
    h.svc.update_person(h.eric, h.mom["id"], role="admin")       # adding chores is an admin's job
    h.mom["role"] = "admin"
    h.svc.create_chore(h.mom, "Water plants", points=1, icon="🪴")
    assert [d["title"] for d in queued(h, h.tucker)] == ["New chore: 🪴 Water plants"]
    assert queued(h, h.eric) and not queued(h, h.mom)
    with pytest.raises(Invalid, match="isn.t about one chore"):
        h.svc.subscribe(h.tucker, h.tucker["id"], "new_chore", "email", chore_id=h.dog["id"])


def test_reassign_tells_both_sides(h):
    for ev in ("assigned", "unassigned"):
        h.svc.subscribe(h.tucker, h.tucker["id"], ev, "email")
        h.svc.subscribe(h.mom, h.mom["id"], ev, "email")
    h.svc.reassign(h.eric, key(h, MON), h.mom["id"], "sleepover")
    assert [d["kind"] for d in queued(h, h.mom)] == ["assigned"]
    assert [d["kind"] for d in queued(h, h.tucker)] == ["unassigned"]


def test_delivery_retries_then_succeeds_and_logs(h):
    h.svc.subscribe(h.tucker, h.tucker["id"], "points", "email")
    h.svc.adjust(h.eric, h.tucker["id"], 5, "birthday")
    flaky = FakeSender(fail_times=1)
    drain(h, {"email": flaky})
    [d] = h.svc.deliveries(h.tucker["id"])
    assert (d["status"], d["attempts"]) == ("queued", 1) and "smtp timeout" in d["last_error"]
    h.svc.db.execute("UPDATE deliveries SET next_at = '2000-01-01T00:00:00Z'")      # skip the backoff wait
    drain(h, {"email": flaky})
    [d] = h.svc.deliveries(h.tucker["id"])
    assert (d["status"], d["attempts"], d["last_error"]) == ("sent", 2, None)
    assert flaky.sent[0][1] == "Points +5" and "birthday" in flaky.sent[0][2]


def test_unconfigured_channel_or_missing_address_fails_at_once(h):
    h.svc.subscribe(h.tucker, h.tucker["id"], "points", "discord")
    h.svc.adjust(h.eric, h.tucker["id"], 1, "x")
    drain(h, {})                                                   # no discord sender on this server
    [d] = h.svc.deliveries(h.tucker["id"])
    assert d["status"] == "failed" and "isn't set up" in d["last_error"]
    h.svc.adjust(h.eric, h.tucker["id"], 1, "y")
    drain(h, {"discord": FakeSender()})                            # set up, but Tucker has no Discord id
    assert h.svc.deliveries(h.tucker["id"])[0]["last_error"] == "no discord address for this person"


def test_gives_up_after_the_backoff_runs_out(h):
    h.svc.subscribe(h.tucker, h.tucker["id"], "points", "email")
    h.svc.adjust(h.eric, h.tucker["id"], 1, "x")
    dead = FakeSender(fail_times=99)
    for _ in range(len(notify.BACKOFF) + 1):
        h.svc.db.execute("UPDATE deliveries SET next_at = '2000-01-01T00:00:00Z' WHERE status = 'queued'")
        drain(h, {"email": dead})
    [d] = h.svc.deliveries(h.tucker["id"])
    assert (d["status"], d["attempts"]) == ("failed", len(notify.BACKOFF) + 1)


def test_channels_and_validation(h):
    ch = h.svc.channels(h.tucker["id"])
    assert ch["email"] == {"address": "tucker@example.com", "enabled": True, "default": True}
    assert ch["discord"]["address"] is None
    with pytest.raises(Invalid, match="user id"):
        h.svc.set_channel(h.tucker, h.tucker["id"], "discord", "tucker#1234")
    h.svc.set_channel(h.tucker, h.tucker["id"], "discord", "123456789012345678")
    h.svc.set_channel(h.tucker, h.tucker["id"], "email", "t.other@example.com")
    assert h.svc.channels(h.tucker["id"])["email"]["address"] == "t.other@example.com"
    h.svc.set_channel(h.tucker, h.tucker["id"], "email", None)
    assert h.svc.channels(h.tucker["id"])["email"]["default"] is True


def test_subscribe_is_idempotent_and_unsubscribe_works(h):
    a = h.svc.subscribe(h.tucker, h.tucker["id"], "missed", "email")
    b = h.svc.subscribe(h.tucker, h.tucker["id"], "missed", "email")
    assert a["id"] == b["id"] and len(h.svc.subscriptions(h.tucker["id"])) == 1
    h.svc.unsubscribe(h.tucker, h.tucker["id"], a["id"])
    assert h.svc.subscriptions(h.tucker["id"]) == []


def test_a_broken_notification_never_breaks_the_action(h, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(notify, "emit", boom)
    assert h.svc.record(h.eric, key(h, MON), "done")["status"] == "done"
    assert h.svc.balance(h.tucker["id"]) == 3


def test_smtp_sender_uses_tls_and_logs_in(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            calls.append(("quit",))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, pw):
            calls.append(("login", user))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"], msg["From"], msg.get_content().strip()))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    notify.SmtpSender("smtp.gmail.com", 587, "bot@gmail.com", "app-pw", "Choreboard <bot@gmail.com>").send(
        "t@example.com", "Missed: dog", "You missed it.")
    assert calls == [("connect", "smtp.gmail.com", 587), ("starttls",), ("login", "bot@gmail.com"),
                     ("send", "t@example.com", "Missed: dog", "Choreboard <bot@gmail.com>", "You missed it."), ("quit",)]


def test_discord_sender_opens_a_dm_then_posts(monkeypatch):
    import io
    import json as _json
    seen = []

    def fake_urlopen(req, timeout):
        seen.append((req.get_method(), req.full_url, req.headers.get("Authorization"), _json.loads(req.data)))
        body = {"id": "999"} if req.full_url.endswith("/users/@me/channels") else {}
        return io.BytesIO(_json.dumps(body).encode())

    class Ctx(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        lambda req, timeout: Ctx(fake_urlopen(req, timeout).getvalue()))
    notify.DiscordSender("tok").send("123456789012345678", "Swapped", "you now do Sunday")
    assert seen[0] == ("POST", "https://discord.com/api/v10/users/@me/channels", "Bot tok",
                       {"recipient_id": "123456789012345678"})
    assert seen[1][:2] == ("POST", "https://discord.com/api/v10/channels/999/messages")
    assert seen[1][3] == {"content": "**Swapped**\nyou now do Sunday"}


def test_anything_not_ignored_lands_in_the_inbox(h):
    # Ignored by default: nothing.
    h.svc.adjust(h.eric, h.tucker["id"], 1, "x")
    assert h.svc.inbox(h.tucker["id"]) == []
    # Inbox only: no delivery, an unread notification.
    h.svc.subscribe(h.tucker, h.tucker["id"], "points", "app")
    h.svc.adjust(h.eric, h.tucker["id"], 2, "y")
    [n] = h.svc.inbox(h.tucker["id"])
    assert (n["title"], n["read_at"]) == ("Points +2", None) and h.svc.unread(h.tucker["id"]) == 1
    assert h.svc.deliveries(h.tucker["id"]) == []
    # Email AND app: one inbox row (not two), one delivery.
    h.svc.subscribe(h.tucker, h.tucker["id"], "points", "email")
    h.svc.adjust(h.eric, h.tucker["id"], 3, "z")
    assert len(h.svc.inbox(h.tucker["id"])) == 2 and len(h.svc.deliveries(h.tucker["id"])) == 1
    # Email alone still counts as "not ignored".
    h.svc.subscribe(h.mom, h.mom["id"], "new_chore", "email")
    h.svc.create_chore(h.eric, "Bins")
    assert [n["kind"] for n in h.svc.inbox(h.mom["id"])] == ["new_chore"]


def test_read_state(h):
    h.svc.subscribe(h.tucker, h.tucker["id"], "points", "app")
    for i in range(3):
        h.svc.adjust(h.eric, h.tucker["id"], 1, f"n{i}")
    items = h.svc.inbox(h.tucker["id"])
    assert h.svc.mark_read(h.tucker, h.tucker["id"], [items[0]["id"]]) == 1
    assert h.svc.unread(h.tucker["id"]) == 2
    assert len(h.svc.inbox(h.tucker["id"], unread_only=True)) == 2
    assert h.svc.mark_read(h.tucker, h.tucker["id"]) == 2 and h.svc.unread(h.tucker["id"]) == 0
    assert h.svc.mark_read(h.tucker, h.tucker["id"], [items[1]["id"]], read=False) == 1
    assert h.svc.unread(h.tucker["id"]) == 1
    assert h.svc.mark_read(h.tucker, h.tucker["id"], []) == 0
    # Someone else's ids don't touch Tucker's, nor theirs.
    assert h.svc.mark_read(h.mom, h.mom["id"], [items[1]["id"]]) == 0


def test_discord_user_agent_is_the_required_form(monkeypatch):
    import io
    got = {}

    class Ctx(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        got["ua"] = req.get_header("User-agent")
        return Ctx(b'{"id": "1"}')
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    notify.DiscordSender("t").send("123456789012345678", "x", "y")
    assert got["ua"].startswith("DiscordBot (https://") and got["ua"].endswith(")")


def test_discord_errors_in_words():
    assert "share a server" in notify.discord_reason(403, '{"message": "Cannot send messages to this user", "code": 50007}')
    assert "token is wrong" in notify.discord_reason(401, '{"message": "401: Unauthorized", "code": 0}')
    assert "HTML page" in notify.discord_reason(403, "<!DOCTYPE html><title>Access denied</title>")
    assert notify.discord_reason(500, '{"message": "boom"}').startswith("Discord 500: {")


def test_discord_refusals_fail_at_once_rate_limits_retry(monkeypatch):
    import io
    import urllib.error

    def refuse(status, body):
        def fake(req, timeout):
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(body))
        return fake
    d = {"id": 1, "channel": "discord", "address": "123456789012345678", "title": "t", "body": "b", "attempts": 0}
    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        refuse(403, b'{"message": "no mutual guilds", "code": 50278}'))
    err, retry = notify.send_one(d, {"discord": notify.DiscordSender("tok")})
    assert retry is False and "invite it to a server" in err
    monkeypatch.setattr(notify.urllib.request, "urlopen", refuse(429, b'{"message": "rate limited", "retry_after": 1}'))
    err, retry = notify.send_one(d, {"discord": notify.DiscordSender("tok")})
    assert retry is True


def test_asking_to_join_the_discord_server_reaches_every_admin(h):
    from choreboard.service import Conflict
    h.svc.update_person(h.eric, h.mom["id"], role="admin")
    h.svc.set_channel(h.tucker, h.tucker["id"], "discord", "123456789012345678")
    got = h.svc.request_discord(h.tucker)
    assert got["asked"] == 2
    for admin in (h.eric, h.mom):
        [n] = h.svc.inbox(admin["id"])                  # no subscription needed
        assert n["title"] == "Tucker wants to join the Discord server"
        assert "123456789012345678" in n["body"]
    assert h.svc.inbox(h.tucker["id"]) == []
    with pytest.raises(Conflict, match="already asked today"):
        h.svc.request_discord(h.tucker)
    # An admin who subscribed by email gets an email too.
    h.svc.subscribe(h.eric, h.eric["id"], "discord_request", "email")
    h.svc.request_discord(h.mom)
    assert [d["kind"] for d in queued(h, h.eric)] == ["discord_request"]


def test_the_only_admin_is_told_to_invite_themselves(h):
    with pytest.raises(Invalid, match="only admin"):
        h.svc.request_discord(h.eric)


def test_invite_link_setting(h, monkeypatch):
    from choreboard import settings as st
    monkeypatch.delenv("CHOREBOARD_DISCORD_INVITE", raising=False)
    with pytest.raises(Invalid, match="discord.gg"):
        h.svc.update_server_settings(h.eric, {"discord.invite": "join us!"})
    h.svc.update_server_settings(h.eric, {"discord.invite": "https://discord.gg/abc123"})
    assert h.svc.discord_invite() == "https://discord.gg/abc123"
    assert st.get(h.svc.db, "discord.invite") == "https://discord.gg/abc123"
