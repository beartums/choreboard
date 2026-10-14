"""Property tests: thousands of random households instead of hand-picked
cases. Each test states an invariant the calendar must keep for ANY rules and
overrides; hypothesis looks for a counterexample and shrinks it if it finds
one."""
import datetime as dt

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from choreboard import schema
from choreboard.calendar import Override, Pattern, Rule, calendar, plan_swap
from choreboard.service import Conflict, Service

BASE = dt.date(2026, 1, 1)
SPAN = 60
WINDOW = (BASE, BASE + dt.timedelta(days=SPAN - 1))
PEOPLE = [1, 2, 3, 4]
CHORES = [10, 11, 12]

dates = st.integers(0, SPAN - 1).map(lambda n: BASE + dt.timedelta(days=n))
weekday_sets = st.frozensets(st.integers(1, 7), min_size=1)
patterns = st.one_of(
    st.builds(lambda w: Pattern(weekdays=w), weekday_sets),
    st.builds(lambda n, a: Pattern(every_days=n, anchor=a), st.integers(1, 10), dates),
    st.builds(lambda d: Pattern(on=d), dates),
)
times = st.one_of(st.none(), st.integers(0, 23).map(lambda h: dt.time(h)))


@st.composite
def rule_lists(draw, max_size=6):
    n = draw(st.integers(0, max_size))
    out = []
    for i in range(n):
        start = draw(st.one_of(st.none(), dates))
        end = draw(st.one_of(st.none(), dates))
        if start and end and end < start:
            start, end = end, start
        out.append(Rule(i + 1, draw(st.sampled_from(CHORES)), draw(st.sampled_from(PEOPLE)), draw(patterns),
                        due=draw(times), start=start, end=end))
    return out


@st.composite
def override_lists(draw, rules, max_size=8):
    """Overrides aimed at random (rule, date) pairs -- some land on real
    occurrences, some on dates the rule doesn't occur (which must be inert)."""
    out = []
    for i in range(draw(st.integers(0, max_size))):
        kind = draw(st.sampled_from(["reassign", "skip", "add"]))
        d = draw(dates)
        if kind == "add" or not rules:
            out.append(Override(100 + i, draw(st.sampled_from(CHORES)), d, "add",
                                person_id=draw(st.sampled_from(PEOPLE)), due=draw(times)))
            continue
        r = draw(st.sampled_from(rules))
        out.append(Override(100 + i, r.chore_id, d, kind, rule_id=r.id,
                            person_id=draw(st.sampled_from(PEOPLE)) if kind == "reassign" else None))
    return out


@st.composite
def households(draw):
    rules = draw(rule_lists())
    return rules, draw(override_lists(rules))


@given(rule_lists())
@settings(max_examples=300)
def test_without_overrides_each_rule_gives_exactly_its_dates(rules):
    occs = calendar(rules, [], *WINDOW)
    got = {(o.rule_id, o.date) for o in occs}
    want = {(r.id, d) for r in rules for d in (BASE + dt.timedelta(days=n) for n in range(SPAN)) if r.occurs(d)}
    assert got == want
    assert len(occs) == len(want), "no occurrence twice"
    assert all(o.person_id == next(r for r in rules if r.id == o.rule_id).person_id for o in occs)
    assert all(o.source == "rule" and not o.skipped for o in occs)


@given(households())
@settings(max_examples=300)
def test_keys_are_unique_and_order_is_by_date(h):
    occs = calendar(*h, *WINDOW)
    assert len({o.key for o in occs}) == len(occs)
    assert [o.date for o in occs] == sorted(o.date for o in occs)


@given(households())
@settings(max_examples=300)
def test_an_override_changes_only_its_own_date(h):
    rules, overrides = h
    base = calendar(rules, [], *WINDOW)
    with_o = calendar(rules, overrides, *WINDOW)
    touched = {o.date for o in overrides}
    assert [o for o in base if o.date not in touched] == [o for o in with_o if o.date not in touched]


@given(households())
@settings(max_examples=300)
def test_reassign_and_skip_keep_every_scheduled_occurrence(h):
    """Overrides never make a scheduled occurrence vanish or appear twice:
    the rule keys are the same set; only `add`s are extra."""
    rules, overrides = h
    base_keys = {o.key for o in calendar(rules, [], *WINDOW)}
    occs = calendar(rules, overrides, *WINDOW)
    assert {o.key for o in occs if o.rule_id is not None} == base_keys
    adds = {o.id for o in overrides if o.kind == "add"}
    assert {o.override_id for o in occs if o.rule_id is None} == adds


@given(households())
@settings(max_examples=300)
def test_the_latest_override_for_an_occurrence_wins(h):
    rules, overrides = h
    occs = {o.key: o for o in calendar(rules, overrides, *WINDOW)}
    for o in occs.values():
        if o.rule_id is None:
            continue
        aimed = [x for x in overrides if x.kind != "add" and x.rule_id == o.rule_id and x.date == o.date]
        if not aimed:
            assert o.override_id is None
            continue
        last = max(aimed, key=lambda x: x.id)
        assert o.override_id == last.id
        assert o.skipped == (last.kind == "skip")
        if last.kind == "reassign":
            assert o.person_id == last.person_id


@given(households())
@settings(max_examples=200)
def test_person_views_partition_the_calendar(h):
    occs = calendar(*h, *WINDOW)
    per_person = [o for p in PEOPLE for o in calendar(*h, *WINDOW, person_id=p)]
    assert sorted(o.key for o in per_person) == sorted(o.key for o in occs)
    visible = calendar(*h, *WINDOW, include_skipped=False)
    assert [o for o in occs if not o.skipped] == visible


@given(rule_lists(), st.data())
@settings(max_examples=300)
def test_a_swap_trades_exactly_two_people_on_exactly_two_dates(rules, data):
    base = [o for o in calendar(rules, [], *WINDOW)]
    pairs = [(a, b) for a in base for b in base if a.key != b.key and a.person_id != b.person_id]
    assume(pairs)
    a, b = data.draw(st.sampled_from(pairs))
    plan = plan_swap(a, b, "g", "r")
    after = {o.key: o for o in calendar(rules, [Override(**{**plan.a.__dict__, "id": 1}),
                                                Override(**{**plan.b.__dict__, "id": 2})], *WINDOW)}
    assert after[a.key].person_id == b.person_id and after[b.key].person_id == a.person_id
    for o in base:
        if o.key not in (a.key, b.key):
            assert after[o.key].person_id == o.person_id


# --- the service, against a real database ------------------------------------

def _household(rules):
    svc = Service(schema.connect(":memory:"))
    admin = svc.bootstrap("P1", "p1@example.com")
    for p in PEOPLE[1:]:
        svc.create_person(admin, f"P{p}", email=f"p{p}@example.com")
    for c in CHORES:
        svc.create_chore(admin, f"chore {c}", points=2)
    for r in rules:   # ids line up: people 1-4, chores 1-3 (CHORES map to 1..3)
        svc.create_rule(admin, CHORES.index(r.chore_id) + 1, r.person_id, r.pattern, due=r.due,
                        start=r.start, end=r.end)
    svc.db.execute("UPDATE rules SET created_at = '2025-12-01T12:00:00Z'")   # before WINDOW
    return svc, admin


def _snapshot(svc):
    return [(e.occurrence.key, e.occurrence.person_id, e.occurrence.skipped) for e in svc.calendar(*WINDOW)]


@given(rule_lists(max_size=4), st.data())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_any_sequence_of_swaps_undone_in_reverse_restores_the_schedule(rules, data):
    """Swap, swap again (including an already-swapped occurrence), then undo
    newest-first: each undo restores the previous state exactly, and the end
    is the plain schedule."""
    svc, admin = _household(rules)
    states, groups = [_snapshot(svc)], []
    for _ in range(data.draw(st.integers(1, 4))):
        occs = [e.occurrence for e in svc.calendar(*WINDOW) if not e.occurrence.skipped]
        pairs = [(a, b) for a in occs for b in occs if a.key != b.key and a.person_id != b.person_id]
        if not pairs:
            break
        a, b = data.draw(st.sampled_from(pairs))
        groups.append(svc.swap(admin, a.key, b.key, "test"))
        states.append(_snapshot(svc))
    for g in reversed(groups):
        states.pop()
        svc.remove_override_group(admin, g)
        assert _snapshot(svc) == states[-1]
    assert _snapshot(svc) == states[0]


@given(rule_lists(max_size=4), st.data())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_points_always_equal_the_live_outcomes(rules, data):
    """Record and undo outcomes at random: every balance equals the points of
    the live outcomes it should hold -- earned by whoever did it, minus stated
    deductions -- however the ledger got there."""
    svc, admin = _household(rules)
    occs = [e.occurrence for e in svc.calendar(*WINDOW)]
    assume(occs)
    live: dict[int, tuple] = {}
    for _ in range(data.draw(st.integers(1, 8))):
        if live and data.draw(st.booleans()):
            oid = data.draw(st.sampled_from(sorted(live)))
            svc.undo(admin, oid, "test")
            del live[oid]
            continue
        o = data.draw(st.sampled_from(occs))
        status = data.draw(st.sampled_from(["done", "skipped", "failed", "covered", "failed_covered"]))
        other = data.draw(st.sampled_from([p for p in PEOPLE if p != o.person_id]))
        kw = {"note": "n"}
        if status in ("covered", "failed_covered"):
            kw["done_by"] = other
        if status in ("failed", "failed_covered"):
            kw["deduct"] = data.draw(st.integers(0, 5))
        try:
            out = svc.record(admin, o.key, status, **kw)
        except Conflict:
            continue                                   # already recorded
        live[out["id"]] = (status, o.person_id, kw.get("done_by"), kw.get("deduct", 0))
    want = {p: 0 for p in PEOPLE}
    for status, assignee, done_by, deduct in live.values():
        if status == "done":
            want[assignee] += 2
        if status in ("covered", "failed_covered"):
            want[done_by] += 2
        if status in ("failed", "failed_covered"):
            want[assignee] -= deduct
    assert {p: svc.balance(p) for p in PEOPLE} == want
