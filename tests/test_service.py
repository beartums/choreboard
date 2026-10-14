"""The service on a real (in-memory) database: the household's dog-walking
rotation, outcomes and points, swaps and their undo."""
import datetime as dt

import pytest

from choreboard import schema
from choreboard.calendar import Pattern
from choreboard.service import Conflict, Forbidden, Invalid, NotFound, Service, entries_json, key_str, parse_key

D = dt.date
MON, WED, SUN = D(2026, 10, 19), D(2026, 10, 21), D(2026, 10, 25)


@pytest.fixture
def h():
    """A household: Eric (admin) bootstraps, adds Tucker, Cass and Mom, and
    the dog's rules. Returns a namespace of handles."""
    svc = Service(schema.connect(":memory:"))
    eric = svc.bootstrap("Eric", "Eric@Example.com")
    tucker = svc.create_person(eric, "Tucker", email="tucker@example.com")
    cass = svc.create_person(eric, "Cassandra", email="cass@example.com")
    mom = svc.create_person(eric, "Mom", email="mom@example.com")
    dog = svc.create_chore(eric, "Walk the dog", points=3, icon="🐕")
    evening = dt.time(18)
    for person, days in ((tucker, {1, 3}), (cass, {2, 6}), (mom, {4, 5}), (eric, {7})):
        svc.create_rule(eric, dog["id"], person["id"], Pattern(weekdays=frozenset(days)), due=evening)
    # Rules begin the day they're made; these tests use fixed dates, so make
    # the rules old enough to cover them whatever day the suite runs.
    svc.db.execute("UPDATE rules SET created_at = '2026-01-01T12:00:00Z'")

    class H:
        pass
    x = H()
    x.svc, x.eric, x.tucker, x.cass, x.mom, x.dog = svc, eric, tucker, cass, mom, dog
    return x


def key_on(h, date, person=None):
    for e in h.svc.calendar(date, date):
        if person is None or e.occurrence.person_id == person["id"]:
            return e.occurrence.key
    raise AssertionError(f"nothing on {date}")


def who(h, start=MON, end=SUN):
    return [(e.occurrence.date.isoweekday(), e.occurrence.person_id) for e in h.svc.calendar(start, end)]


def test_bootstrap_once_and_email_is_identity(h):
    assert h.eric["role"] == "admin" and h.eric["email"] == "eric@example.com"
    assert h.svc.person_by_email("ERIC@example.com")["id"] == h.eric["id"]
    with pytest.raises(Conflict):
        h.svc.bootstrap("Again", "x@example.com")
    with pytest.raises(Conflict):
        h.svc.create_person(h.eric, "Dup", email="mom@example.com")


def test_nobody_anonymous_can_write(h):
    with pytest.raises(Forbidden):
        h.svc.create_chore(None, "Sneaky")


def test_done_earns_the_chores_points(h):
    k = key_on(h, MON)
    out = h.svc.record(h.eric, k, "done")
    assert (out["status"], out["assignee_id"], out["done_by_id"]) == ("done", h.tucker["id"], h.tucker["id"])
    assert h.svc.balance(h.tucker["id"]) == 3
    with pytest.raises(Conflict):
        h.svc.record(h.eric, k, "done")


def test_failed_deducts_only_what_is_said_and_needs_a_reason(h):
    k = key_on(h, MON)
    with pytest.raises(Invalid, match="note"):
        h.svc.record(h.eric, k, "failed", deduct=2)
    with pytest.raises(Invalid, match="explicitly"):
        h.svc.record(h.eric, k, "failed", note="forgot")
    h.svc.record(h.eric, k, "failed", note="forgot again", deduct=2)
    assert h.svc.balance(h.tucker["id"]) == -2
    entry = h.svc.ledger(h.tucker["id"])[0]
    assert (entry["kind"], entry["delta"]) == ("deduction", -2)
    assert "forgot again" in entry["note"]


def test_failed_with_zero_is_allowed_and_costs_nothing(h):
    h.svc.record(h.eric, key_on(h, MON), "failed", note="sick", deduct=0)
    assert h.svc.balance(h.tucker["id"]) == 0


def test_covered_and_failed_covered(h):
    h.svc.record(h.eric, key_on(h, MON), "covered", done_by=h.mom["id"], note="Tucker at practice")
    assert (h.svc.balance(h.mom["id"]), h.svc.balance(h.tucker["id"])) == (3, 0)
    h.svc.record(h.eric, key_on(h, WED), "failed_covered", done_by=h.cass["id"], note="ignored it", deduct=3)
    assert (h.svc.balance(h.cass["id"]), h.svc.balance(h.tucker["id"])) == (3, -3)
    with pytest.raises(Invalid, match="done_by"):
        h.svc.record(h.eric, key_on(h, D(2026, 10, 26)), "covered", note="x")
    with pytest.raises(Invalid, match="assignee"):
        h.svc.record(h.eric, key_on(h, D(2026, 10, 26)), "covered", done_by=h.tucker["id"], note="x")


def test_skipped_outcome_has_no_points_or_extras(h):
    h.svc.record(h.eric, key_on(h, MON), "skipped")
    assert h.svc.balance(h.tucker["id"]) == 0
    with pytest.raises(Invalid):
        h.svc.record(h.eric, key_on(h, WED), "skipped", deduct=1)


def test_undo_keeps_the_record_and_reverses_the_points(h):
    k = key_on(h, WED)
    out = h.svc.record(h.eric, k, "failed_covered", done_by=h.cass["id"], note="ignored", deduct=2)
    undone = h.svc.undo(h.eric, out["id"], "it was actually done")
    assert undone["undone_at"] and undone["undo_note"] == "it was actually done"
    assert (h.svc.balance(h.cass["id"]), h.svc.balance(h.tucker["id"])) == (0, 0)
    kinds = [e["kind"] for e in h.svc.ledger(h.tucker["id"])]
    assert kinds == ["reversal", "deduction"], "the ledger is appended to, never edited"
    with pytest.raises(Conflict):
        h.svc.undo(h.eric, out["id"], "again")
    # The occurrence is free again.
    assert h.svc.record(h.eric, k, "done")["status"] == "done"


def test_swap_this_week_only_and_undo(h):
    sun, wed = key_on(h, SUN), key_on(h, WED)
    with pytest.raises(Invalid, match="reason"):
        h.svc.swap(h.eric, sun, wed, "")
    group = h.svc.swap(h.eric, sun, wed, "Eric away Sunday")
    T, C, M, E = (p["id"] for p in (h.tucker, h.cass, h.mom, h.eric))
    assert who(h) == [(1, T), (2, C), (3, E), (4, M), (5, M), (6, C), (7, T)]
    assert who(h, MON + dt.timedelta(7), SUN + dt.timedelta(7)) == [(1, T), (2, C), (3, T), (4, M), (5, M), (6, C), (7, E)]

    # Tucker walks on Sunday: credited to Tucker, the swap's assignee.
    out = h.svc.record(h.eric, sun, "done")
    assert out["assignee_id"] == T and h.svc.balance(T) == 3
    with pytest.raises(Conflict, match="outcome"):
        h.svc.remove_override_group(h.eric, group)
    h.svc.undo(h.eric, out["id"], "swap cancelled")
    assert h.svc.remove_override_group(h.eric, group) == 2
    assert who(h)[2] == (3, T) and who(h)[6] == (7, E)


def test_reassign_skip_and_add(h):
    T, M = h.tucker["id"], h.mom["id"]
    occ = h.svc.reassign(h.eric, key_on(h, MON), M, "Tucker sleepover")
    assert (occ.person_id, occ.scheduled_person_id, occ.source) == (M, T, "override")
    skipped = h.svc.skip(h.eric, key_on(h, WED), "dog at the vet")
    assert skipped.skipped
    with pytest.raises(Conflict, match="skipped"):
        h.svc.record(h.eric, skipped.key, "done")
    extra = h.svc.add(h.eric, h.dog["id"], WED, M, "extra walk", due=dt.time(8))
    assert extra.key[2] == "add"
    h.svc.record(h.eric, extra.key, "done")
    assert h.svc.balance(M) == 3


def test_editing_a_rule_keeps_history(h):
    k = key_on(h, MON)
    h.svc.record(h.eric, k, "done")
    rule = h.svc.rule(k[3])
    h.svc.update_rule(h.eric, rule.id, rule.chore_id, h.cass["id"], rule.pattern, due=rule.due)
    e = h.svc.calendar(MON, MON)[0]
    assert e.occurrence.person_id == h.cass["id"], "the rule moved"
    assert e.outcome["assignee_id"] == h.tucker["id"], "what happened did not"
    assert h.svc.balance(h.tucker["id"]) == 3


def test_deleting_a_rule_keeps_outcomes_and_points(h):
    k = key_on(h, MON)
    h.svc.record(h.eric, k, "done")
    h.svc.delete_rule(h.eric, k[3])
    assert h.svc.balance(h.tucker["id"]) == 3
    assert h.svc.outcome(1)["status"] == "done"
    with pytest.raises(NotFound):
        h.svc.occurrence(k)


def test_adjustments_need_a_reason(h):
    with pytest.raises(Invalid):
        h.svc.adjust(h.eric, h.cass["id"], 5, " ")
    h.svc.adjust(h.eric, h.cass["id"], 5, "birthday bonus")
    assert h.svc.balance(h.cass["id"]) == 5


def test_keys_round_trip_and_json(h):
    k = key_on(h, MON)
    assert parse_key(key_str(k)) == k
    with pytest.raises(Invalid):
        parse_key("1:2026-10-19:nope:4")
    rows = list(entries_json(h.svc.calendar(MON, MON)))
    assert rows[0]["id"] == key_str(k) and rows[0]["due"] == "18:00" and rows[0]["outcome"] is None


def test_migrations_are_idempotent(tmp_path):
    p = str(tmp_path / "x.db")
    schema.connect(p).close()
    conn = schema.connect(p)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(schema.MIGRATIONS)


def test_swaps_are_not_limited_to_a_week(h):
    # Tucker's Monday tomorrow for Eric's Sunday three months out.
    far = D(2027, 1, 24)                                   # a Sunday
    a, b = key_on(h, MON), key_on(h, far)
    h.svc.swap(h.eric, a, b, "Tucker away; pays it back in January")
    assert h.svc.occurrence(a).person_id == h.eric["id"]
    assert h.svc.occurrence(b).person_id == h.tucker["id"]
    # Everything in between is untouched.
    assert h.svc.occurrence(key_on(h, D(2026, 11, 1))).person_id == h.eric["id"]


def test_the_undone_side_of_a_swap_can_be_swapped_again(h):
    T, C, E = h.tucker["id"], h.cass["id"], h.eric["id"]
    sun, wed = key_on(h, SUN), key_on(h, WED)
    first = h.svc.swap(h.eric, sun, wed, "Eric away Sunday")
    h.svc.record(h.eric, wed, "done")                      # Eric did his half
    # Tucker's half (Sunday) isn't done yet: he trades it on to Cass's
    # Saturday a month later.
    sat = key_on(h, D(2026, 11, 21))
    second = h.svc.swap(h.eric, sun, sat, "Tucker busy Sunday too")
    assert h.svc.occurrence(sun).person_id == C
    assert h.svc.occurrence(sat).person_id == T
    # The first swap can't be undone now (Eric's half is recorded)...
    with pytest.raises(Conflict):
        h.svc.remove_override_group(h.eric, first)
    # ...but undoing the second puts Sunday back to the first swap's answer.
    h.svc.remove_override_group(h.eric, second)
    assert h.svc.occurrence(sun).person_id == T
    assert h.svc.occurrence(sat).person_id == C
    assert h.svc.occurrence(wed).person_id == E


def test_points_go_to_whoever_does_it(h):
    T, M = h.tucker["id"], h.mom["id"]
    sun, wed = key_on(h, SUN), key_on(h, WED)
    h.svc.swap(h.eric, sun, wed, "Eric away Sunday")
    h.svc.record(h.eric, sun, "done")                    # assigned to Tucker by the swap
    h.svc.record(h.eric, wed, "covered", done_by=M, note="Eric stuck at work")   # not assigned at all
    assert h.svc.balance(T) == 3 and h.svc.balance(M) == 3 and h.svc.balance(h.eric["id"]) == 0


def test_editing_a_chore(h):
    k = key_on(h, MON)
    h.svc.record(h.eric, k, "done")                                   # earns 3
    c = h.svc.update_chore(h.eric, h.dog["id"], title="Walk Biscuit", points=5, icon="🦮", description="leash")
    assert (c["title"], c["points"], c["icon"], c["description"]) == ("Walk Biscuit", 5, "🦮", "leash")
    assert h.svc.balance(h.tucker["id"]) == 3, "points already earned don't change"
    h.svc.record(h.eric, key_on(h, WED), "done")
    assert h.svc.balance(h.tucker["id"]) == 8, "new completions earn the new value"
    for bad in ({"points": -1}, {"title": "  "}, {"points": "5"}, {"owner": 2}):
        with pytest.raises(Invalid):
            h.svc.update_chore(h.eric, h.dog["id"], **bad)
    with pytest.raises(NotFound):
        h.svc.update_chore(h.eric, 999, title="x")
    with pytest.raises(Forbidden):
        h.svc.update_chore(None, h.dog["id"], title="x")


def test_retiring_a_chore_hides_it_but_keeps_its_rules_and_history(h):
    h.svc.record(h.eric, key_on(h, MON), "done")
    h.svc.update_chore(h.eric, h.dog["id"], active=False)
    assert h.svc.chores() == []
    assert h.svc.chore(h.dog["id"])["active"] == 0
    assert h.svc.balance(h.tucker["id"]) == 3


def test_editing_a_person(h):
    p = h.svc.update_person(h.eric, h.cass["id"], name="Cass", color="mint", email="CASS2@Example.com", role="admin")
    assert (p["name"], p["color"], p["email"], p["role"]) == ("Cass", "mint", "cass2@example.com", "admin")
    assert h.svc.person_by_email("cass2@example.com")["id"] == h.cass["id"]
    assert h.svc.person_by_email("cass@example.com") is None
    with pytest.raises(Conflict):
        h.svc.update_person(h.eric, h.cass["id"], email="mom@example.com")
    for bad in ({"role": "owner"}, {"name": ""}, {"id": 9}):
        with pytest.raises(Invalid):
            h.svc.update_person(h.eric, h.cass["id"], **bad)
    with pytest.raises(NotFound):
        h.svc.update_person(h.eric, 999, name="x")
    # Deactivating keeps their history but takes them off the list.
    h.svc.update_person(h.eric, h.cass["id"], active=False)
    assert h.cass["id"] not in [x["id"] for x in h.svc.people()]
    assert h.svc.person_by_email("cass2@example.com") is None
    assert h.svc.person_by_email("cass2@example.com", include_inactive=True)["active"] == 0


def test_swap_group_ids_never_look_like_override_ids(h, monkeypatch):
    """Regression: a bare hex group id can be all digits, which the undo
    route reads as a single override's id."""
    import uuid as _uuid
    monkeypatch.setattr(_uuid, "uuid4", lambda: _uuid.UUID("16268710-6237-4000-8000-000000000000"))
    group = h.svc.swap(h.eric, key_on(h, SUN), key_on(h, WED), "x")
    assert not group.isdigit()
    assert h.svc.remove_override_group(h.eric, group) == 2


def test_undo_a_single_override_by_id(h):
    occ = h.svc.reassign(h.eric, key_on(h, MON), h.mom["id"], "sleepover")
    assert h.svc.remove_override_group(h.eric, str(occ.override_id)) == 1
    assert h.svc.occurrence(occ.key).person_id == h.tucker["id"]


def test_a_daily_time_window(h):
    rule = h.svc.rules(h.dog["id"])[0]
    w = h.svc.update_rule(h.eric, rule.id, rule.chore_id, rule.person_id, rule.pattern,
                          available=dt.time(16), due=dt.time(18))
    assert (w.available, w.due) == (dt.time(16), dt.time(18))
    occ = h.svc.calendar(MON, MON)[0].occurrence
    assert (occ.available, occ.due) == (dt.time(16), dt.time(18))
    assert list(entries_json(h.svc.calendar(MON, MON)))[0]["available"] == "16:00"
    with pytest.raises(Invalid, match="before the due"):
        h.svc.update_rule(h.eric, rule.id, rule.chore_id, rule.person_id, rule.pattern,
                          available=dt.time(18), due=dt.time(17))
    # A start time alone is fine ("any time after 4").
    h.svc.update_rule(h.eric, rule.id, rule.chore_id, rule.person_id, rule.pattern, available=dt.time(16))
    extra = h.svc.add(h.eric, h.dog["id"], WED, h.mom["id"], "extra", due=dt.time(9), available=dt.time(7))
    assert (extra.available, extra.due) == (dt.time(7), dt.time(9))
    with pytest.raises(Invalid):
        h.svc.add(h.eric, h.dog["id"], WED, h.mom["id"], "x", due=dt.time(7), available=dt.time(9))


def test_new_rules_do_not_fill_in_the_past(h):
    """A rule made today has nothing before today unless it says it started
    earlier."""
    bins = h.svc.create_chore(h.eric, "Bins")
    rule = h.svc.create_rule(h.eric, bins["id"], h.cass["id"], Pattern(weekdays=frozenset(range(1, 8))))
    today = rule.created                       # the database's today, in local time
    past = today - dt.timedelta(days=14)
    mine = lambda a, b: [e for e in h.svc.calendar(a, b) if e.occurrence.chore_id == bins["id"]]
    assert mine(past, today - dt.timedelta(days=1)) == []
    assert [e.occurrence.date for e in mine(today, today)] == [today]
    h.svc.update_rule(h.eric, rule.id, rule.chore_id, rule.person_id, rule.pattern, start=past)
    assert len(mine(past, today - dt.timedelta(days=1))) == 14, "an explicit earlier start reaches back"
    # Editing keeps the original creation day; it isn't reset to the edit.
    assert h.svc.rule(rule.id).created == today


def test_recorded_outcomes_stay_visible_when_the_schedule_moves_on(h):
    """Outcomes on days the rules no longer produce -- the rule now begins
    later, was edited, or deleted -- still show, flagged unscheduled."""
    k = key_on(h, MON)
    h.svc.record(h.eric, k, "done")
    rule = h.svc.rule(k[3])
    # The rule now begins after Monday...
    h.svc.update_rule(h.eric, rule.id, rule.chore_id, rule.person_id, rule.pattern, due=rule.due,
                      start=MON + dt.timedelta(days=1))
    day = h.svc.calendar(MON, MON)
    assert len(day) == 1 and day[0].occurrence.unscheduled and day[0].outcome["status"] == "done"
    assert day[0].occurrence.person_id == h.tucker["id"]
    assert list(entries_json(day))[0]["unscheduled"] is True
    # ...shown in Tucker's own view too, not in someone else's...
    assert h.svc.calendar(MON, MON, person_id=h.tucker["id"])
    assert h.svc.calendar(MON, MON, person_id=h.cass["id"]) == []
    # ...and still undoable, after which the day is empty.
    h.svc.undo(h.eric, day[0].outcome["id"], "cleanup")
    assert h.svc.calendar(MON, MON) == []
    # Deleting a rule leaves its recorded days visible the same way.
    k2 = key_on(h, WED)
    h.svc.record(h.eric, k2, "done")
    h.svc.delete_rule(h.eric, k2[3])
    assert h.svc.calendar(WED, WED)[0].occurrence.unscheduled
