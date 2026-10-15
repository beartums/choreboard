"""Who may do what (policy.py), end to end through the service: members mark
their own chores inside the window and propose swaps; admins do the rest."""
import datetime as dt

import pytest

from choreboard import policy, schema
from choreboard.calendar import Pattern

from choreboard.service import Conflict, Forbidden, PendingSwap, Service, key_str

D = dt.date
MON, WED, SUN = D(2026, 10, 19), D(2026, 10, 21), D(2026, 10, 25)


@pytest.fixture
def h():
    svc = Service(schema.connect(":memory:"))
    eric = svc.bootstrap("Eric", "eric@example.com")
    tucker = svc.create_person(eric, "Tucker", email="t@example.com")
    mom = svc.create_person(eric, "Mom", email="m@example.com")
    dog = svc.create_chore(eric, "Walk the dog", points=3)
    svc.create_rule(eric, dog["id"], tucker["id"], Pattern(weekdays=frozenset({1, 3})), available=dt.time(16), due=dt.time(18))
    svc.create_rule(eric, dog["id"], mom["id"], Pattern(weekdays=frozenset({4, 5})))
    svc.create_rule(eric, dog["id"], eric["id"], Pattern(weekdays=frozenset({7})), due=dt.time(18))
    svc.db.execute("UPDATE rules SET created_at = '2026-01-01T12:00:00Z'")

    class H:
        pass
    x = H()
    x.svc, x.eric, x.tucker, x.mom, x.dog = svc, eric, tucker, mom, dog
    return x


def key(h, d):
    return h.svc.calendar(d, d)[0].occurrence.key


def at(h, *args):
    h.svc.clock = lambda: dt.datetime(*args)


def test_the_window_runs_from_start_time_to_an_hour_after_due(h):
    k = key(h, MON)                                   # Tucker, 4-6 PM
    at(h, 2026, 10, 19, 15, 59)
    with pytest.raises(Forbidden, match="opens at 4 PM"):
        h.svc.record(h.tucker, k, "done")
    at(h, 2026, 10, 19, 19, 1)
    with pytest.raises(Forbidden, match="passed"):
        h.svc.record(h.tucker, k, "done")
    at(h, 2026, 10, 19, 18, 59)                       # within the hour's grace
    assert h.svc.record(h.tucker, k, "done")["status"] == "done"


def test_no_times_means_that_whole_day_plus_an_hour(h):
    k = key(h, D(2026, 10, 22))                       # Mom, Thursday, no times
    at(h, 2026, 10, 21, 23, 59)
    with pytest.raises(Forbidden, match="opens at Thu Oct 22, 12 AM"):
        h.svc.record(h.mom, k, "done")
    at(h, 2026, 10, 23, 0, 59)                        # 12:59 AM Friday: still allowed
    assert h.svc.record(h.mom, k, "done")


def test_members_only_mark_their_own_and_only_done(h):
    at(h, 2026, 10, 19, 17)
    k = key(h, MON)
    with pytest.raises(Forbidden, match="only Tucker can mark this"):
        h.svc.record(h.mom, k, "done")
    for status, kw in (("failed", {"deduct": 0}), ("skipped", {}), ("covered", {"done_by": 3})):
        with pytest.raises(Forbidden, match="only an admin"):
            h.svc.record(h.tucker, k, status, note="x", **kw)
    # An admin can, any time.
    at(h, 2027, 1, 1, 0)
    assert h.svc.record(h.eric, k, "failed", note="late", deduct=1)["status"] == "failed"


def test_members_undo_their_own_done_inside_the_window_only(h):
    at(h, 2026, 10, 19, 17)
    out = h.svc.record(h.tucker, key(h, MON), "done")
    assert h.svc.undo(h.tucker, out["id"], "mis-tap")["undone_at"]
    out = h.svc.record(h.tucker, key(h, MON), "done")
    at(h, 2026, 10, 20, 9)
    with pytest.raises(Forbidden, match="passed"):
        h.svc.undo(h.tucker, out["id"], "too late")
    assert h.svc.undo(h.eric, out["id"], "admin fix")["undone_at"]


def test_editing_is_for_admins(h):
    for fn in (lambda a: h.svc.create_chore(a, "x"), lambda a: h.svc.adjust(a, 1, 5, "x"),
               lambda a: h.svc.reassign(a, key(h, MON), h.mom["id"], "x"),
               lambda a: h.svc.skip(a, key(h, MON), "x"),
               lambda a: h.svc.swap(a, key(h, MON), key(h, SUN), "x")):
        with pytest.raises(Forbidden, match="only an admin"):
            fn(h.tucker)


def test_a_member_proposes_and_the_other_side_approves(h):
    a, b = key(h, WED), key(h, SUN)                    # Tucker's Wed for Eric's Sun
    req = h.svc.propose_swap(h.tucker, a, b, "practice Wednesday")
    assert (req["status"], req["from_person"], req["to_person"]) == ("pending", h.tucker["id"], h.eric["id"])
    # Tentative: nothing moved yet, and both days say so.
    rows = {r["id"]: r for r in h.svc.calendar_json(h.tucker, WED, SUN)}
    assert rows[f"1:{WED}:rule:1"]["person_id"] == h.tucker["id"]
    assert rows[f"1:{WED}:rule:1"]["pending_swap"]["side"] == "from"
    assert rows[f"1:{SUN}:rule:3"]["pending_swap"]["side"] == "to"
    # Only the other side answers; the asker can cancel.
    with pytest.raises(Forbidden, match="only the person asked"):
        h.svc.answer_swap(h.tucker, req["id"], True)
    with pytest.raises(Conflict, match="pending swap"):
        h.svc.propose_swap(h.tucker, a, key(h, D(2026, 10, 22)), "again")
    done = h.svc.answer_swap(h.eric, req["id"], True)
    assert done["status"] == "approved" and done["group_id"].startswith("sw_")
    assert h.svc.occurrence(a).person_id == h.eric["id"] and h.svc.occurrence(b).person_id == h.tucker["id"]
    assert [n["kind"] for n in h.svc.inbox(h.eric["id"])] == ["swap_requested"], "requests reach the inbox unasked"


def test_decline_cancel_and_expiry(h):
    req = h.svc.propose_swap(h.tucker, key(h, WED), key(h, SUN), "x")
    assert h.svc.answer_swap(h.eric, req["id"], False, "I'm away")["status"] == "declined"
    assert h.svc.occurrence(key(h, WED)).person_id == h.tucker["id"]
    req = h.svc.propose_swap(h.tucker, key(h, WED), key(h, SUN), "x")
    with pytest.raises(Forbidden):
        h.svc.cancel_swap(h.eric, req["id"]) if not policy.is_admin(h.eric) else h.svc.cancel_swap(h.mom, req["id"])
    assert h.svc.cancel_swap(h.tucker, req["id"])["status"] == "cancelled"
    req = h.svc.propose_swap(h.tucker, key(h, WED), key(h, SUN), "x")
    at(h, 2026, 10, 22, 9)                            # Wednesday has passed
    assert h.svc.request(req["id"])["status"] == "expired"


def test_approval_rechecks_that_nothing_changed(h):
    req = h.svc.propose_swap(h.tucker, key(h, WED), key(h, SUN), "x")
    h.svc.db.execute("UPDATE swap_requests SET status = 'cancelled' WHERE id = ?", (req["id"],))
    h.svc.reassign(h.eric, key(h, SUN), h.mom["id"], "Mom has Sunday now")
    req = h.svc.propose_swap(h.tucker, key(h, WED), key(h, D(2026, 10, 22)), "y")   # Tucker's Wed for Mom's Thu
    h.svc.db.execute("UPDATE swap_requests SET to_person = ? WHERE id = ?", (h.eric["id"], req["id"]))  # stale
    with pytest.raises(Conflict, match="changed hands"):
        h.svc.answer_swap(h.eric, req["id"], True)
    assert h.svc.request(req["id"])["status"] == "expired"


def test_capabilities_match_the_rules(h):
    at(h, 2026, 10, 19, 15)                            # Monday 3 PM: Tucker's opens at 4
    mon = {r["id"]: r for r in h.svc.calendar_json(h.tucker, MON, MON)}[f"1:{MON}:rule:1"]
    assert "done" not in mon["can"] and mon["cannot"]["done"] == "opens at 4 PM"
    assert "propose_swap" in mon["can"] and "failed" not in mon["can"]
    at(h, 2026, 10, 19, 16, 30)
    mon = h.svc.calendar_json(h.tucker, MON, MON)[0]
    assert "done" in mon["can"]
    other = h.svc.calendar_json(h.mom, MON, MON)[0]
    assert other["cannot"]["done"] == "only Tucker can mark this"
    admin = h.svc.calendar_json(h.eric, MON, MON)[0]
    assert {"done", "failed", "covered", "skip", "give", "swap"} <= set(admin["can"])
    assert "propose_swap" not in admin["can"], "admins swap outright"


def test_swaps_undo_newest_first(h):
    """Swap on swap: an undo peels back one at a time."""
    wed, sun, thu = key(h, WED), key(h, SUN), key(h, D(2026, 10, 22))
    first = h.svc.swap(h.eric, wed, sun, "first")            # Wed: Eric, Sun: Tucker
    second = h.svc.swap(h.eric, sun, thu, "second")          # Sun: Mom, Thu: Tucker
    from choreboard.service import Conflict as C
    with pytest.raises(C, match="later swap on Sun Oct 25 has to be undone first"):
        h.svc.remove_override_group(h.eric, first)
    admin_sun = {r["id"]: r for r in h.svc.calendar_json(h.eric, WED, SUN)}
    assert "undo_swap" in admin_sun[f"1:{SUN}:rule:3"]["can"], "the newest is undoable"
    assert "later swap" in admin_sun[f"1:{WED}:rule:1"]["cannot"]["undo_swap"], "the older says why not"
    h.svc.remove_override_group(h.eric, second)
    assert h.svc.occurrence(sun).person_id == h.tucker["id"], "back to the first swap's answer"
    h.svc.remove_override_group(h.eric, first)
    assert h.svc.occurrence(sun).person_id == h.eric["id"], "back to the schedule"


def test_a_member_asks_to_undo_and_the_other_side_approves(h):
    wed, sun = key(h, WED), key(h, SUN)
    h.svc.swap(h.eric, wed, sun, "first")                     # Tucker now has Sunday
    with pytest.raises(Forbidden):
        h.svc.request_undo(h.mom, sun, "not mine")
    req = h.svc.request_undo(h.tucker, sun, "plans changed")
    assert (req["kind"], req["to_person"]) == ("undo", h.eric["id"])
    row = {r["id"]: r for r in h.svc.calendar_json(h.tucker, WED, SUN)}[f"1:{SUN}:rule:3"]
    assert row["pending_swap"]["kind"] == "undo" and "request_undo" not in row["can"]
    assert h.svc.answer_swap(h.eric, req["id"], True)["status"] == "approved"
    assert h.svc.occurrence(sun).person_id == h.eric["id"] and h.svc.occurrence(wed).person_id == h.tucker["id"]


def test_a_declined_undo_leaves_the_swap(h):
    h.svc.swap(h.eric, key(h, WED), key(h, SUN), "first")
    req = h.svc.request_undo(h.tucker, key(h, SUN), "x")
    h.svc.answer_swap(h.eric, req["id"], False, "no")
    assert h.svc.occurrence(key(h, SUN)).person_id == h.tucker["id"]


def test_a_pending_swap_freezes_both_days_until_an_admin_says_how_to_settle_it(h):
    a, b = key(h, WED), key(h, SUN)
    h.svc.propose_swap(h.tucker, a, b, "x")
    for fn in (lambda: h.svc.swap(h.eric, b, key(h, D(2026, 10, 22)), "y"),
               lambda: h.svc.reassign(h.eric, a, h.mom["id"], "y"),
               lambda: h.svc.skip(h.eric, b, "y")):
        with pytest.raises(PendingSwap, match="pending swap") as e:
            fn()
        assert e.value.request["from_person"] == h.tucker["id"]
    admin = {r["id"]: r for r in h.svc.calendar_json(h.eric, WED, SUN)}[f"1:{SUN}:rule:3"]
    assert {"swap", "give", "skip"} <= set(admin["can"]) and "approve_swap" in admin["can"]
    # Members still can't touch it (they can't reassign at all; their swap is refused).
    with pytest.raises(Conflict, match="pending swap"):
        h.svc.propose_swap(h.mom, key(h, D(2026, 10, 22)), a, "z")


def test_an_admin_change_can_decline_the_pending_swap_first(h):
    a, b = key(h, WED), key(h, SUN)
    rid = h.svc.propose_swap(h.tucker, a, b, "x")["id"]
    h.svc.reassign(h.eric, a, h.mom["id"], "y", pending="decline")
    assert h.svc.request(rid)["status"] == "declined"
    assert h.svc.occurrence(a).person_id == h.mom["id"] and h.svc.occurrence(b).person_id == h.eric["id"]


def test_an_admin_change_can_approve_the_pending_swap_first(h):
    a, b = key(h, WED), key(h, SUN)
    rid = h.svc.propose_swap(h.tucker, a, b, "x")["id"]
    h.svc.skip(h.eric, b, "y", pending="approve")
    assert h.svc.request(rid)["status"] == "approved"
    assert h.svc.occurrence(a).person_id == h.eric["id"]          # the swap happened
    assert h.svc.occurrence(b, include_skipped=True).skipped                              # then the skip


def test_acting_as_someone_records_the_admin_too(h):
    k = key(h, MON)
    at(h, 2026, 10, 19, 17)
    as_tucker = {**h.tucker, "via_id": h.eric["id"]}
    h.svc.record(as_tucker, k, "done")
    row = h.svc.db.execute("SELECT recorded_by, via_id FROM outcomes").fetchone()
    assert tuple(row) == (h.tucker["id"], h.eric["id"])
    # Their permissions, not the admin's.
    with pytest.raises(Forbidden):
        h.svc.skip(as_tucker, key(h, WED), "y")
    rid = h.svc.propose_swap(as_tucker, key(h, WED), key(h, SUN), "x")["id"]
    ev = h.svc.db.execute("SELECT body, via_id FROM events WHERE kind = 'swap_requested'").fetchone()
    assert "Tucker (Eric, as Tucker)" in ev[0] and ev[1] == h.eric["id"]
    assert h.svc.db.execute("SELECT via_id FROM swap_requests WHERE id = ?", (rid,)).fetchone()[0] == h.eric["id"]


def test_a_member_can_ask_someone_to_take_their_chore(h):
    k = key(h, WED)                                     # Tucker's
    can = {r["id"]: r for r in h.svc.calendar_json(h.tucker, WED, WED)}[key_str(k)]["can"]
    assert "propose_give" in can
    with pytest.raises(Forbidden):
        h.svc.reassign(h.tucker, k, h.mom["id"], "busy")
    with pytest.raises(Forbidden):
        h.svc.propose_give(h.mom, k, h.mom["id"], "mine now")      # not hers to give
    rid = h.svc.propose_give(h.tucker, k, h.mom["id"], "practice")["id"]
    assert h.svc.occurrence(k).person_id == h.tucker["id"], "tentative until she accepts"
    entry = {r["id"]: r for r in h.svc.calendar_json(h.mom, WED, WED)}[key_str(k)]
    assert entry["pending_swap"]["kind"] == "give" and entry["pending_swap"]["side"] == "from"
    assert "approve_swap" in entry["can"]
    with pytest.raises(Conflict, match="pending"):
        h.svc.propose_swap(h.tucker, k, key(h, SUN), "x")
    with pytest.raises(Forbidden):
        h.svc.answer_swap(h.tucker, rid, True)
    h.svc.answer_swap(h.mom, rid, True)
    assert h.svc.occurrence(k).person_id == h.mom["id"]
    ev = h.svc.db.execute("SELECT title FROM events WHERE kind = 'swap_answered'").fetchone()[0]
    assert ev.startswith("Hand-off approved")


def test_a_declined_hand_off_changes_nothing(h):
    k = key(h, WED)
    rid = h.svc.propose_give(h.tucker, k, h.mom["id"], "practice")["id"]
    h.svc.answer_swap(h.mom, rid, False)
    assert h.svc.occurrence(k).person_id == h.tucker["id"]
    assert h.svc.request(rid)["status"] == "declined"
