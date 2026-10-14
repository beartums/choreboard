"""The calendar engine, on the household's own example: the dog is walked by
Tucker Mon/Wed, Cass Tue/Sat, Mom Thu/Fri and Eric on Sunday."""
import datetime as dt

import pytest

from choreboard.calendar import Override, Pattern, Rule, calendar, plan_swap

D = dt.date
TUCKER, CASS, MOM, ERIC = 1, 2, 3, 4
DOG = 10
EVENING = dt.time(18, 0)

RULES = [
    Rule(1, DOG, TUCKER, Pattern(weekdays=frozenset({1, 3})), due=EVENING),
    Rule(2, DOG, CASS, Pattern(weekdays=frozenset({2, 6})), due=EVENING),
    Rule(3, DOG, MOM, Pattern(weekdays=frozenset({4, 5})), due=EVENING),
    Rule(4, DOG, ERIC, Pattern(weekdays=frozenset({7})), due=EVENING),
]
# Week of Monday 2026-10-19 .. Sunday 2026-10-25.
MON, WED, SUN = D(2026, 10, 19), D(2026, 10, 21), D(2026, 10, 25)


def who(occs):
    return [(o.date.isoweekday(), o.person_id) for o in occs]


def test_the_schedule_is_the_default():
    week = calendar(RULES, [], MON, SUN)
    assert who(week) == [(1, TUCKER), (2, CASS), (3, TUCKER), (4, MOM), (5, MOM), (6, CASS), (7, ERIC)]
    assert all(o.source == "rule" and o.scheduled_person_id is None for o in week)


def test_a_swap_changes_only_those_dates_and_then_reverts():
    week = calendar(RULES, [], MON, SUN)
    eric_sun = next(o for o in week if o.date == SUN)
    tucker_wed = next(o for o in week if o.date == WED)
    plan = plan_swap(eric_sun, tucker_wed, group_id="g1", reason="Eric away Sunday")
    overrides = [Override(**{**plan.a.__dict__, "id": 1}), Override(**{**plan.b.__dict__, "id": 2})]

    swapped = calendar(RULES, overrides, MON, SUN)
    assert who(swapped) == [(1, TUCKER), (2, CASS), (3, ERIC), (4, MOM), (5, MOM), (6, CASS), (7, TUCKER)]
    wed = next(o for o in swapped if o.date == WED)
    assert (wed.source, wed.scheduled_person_id, wed.group_id, wed.reason) == ("override", TUCKER, "g1", "Eric away Sunday")
    # Same occurrence identity before and after: outcomes stay attached.
    assert wed.key == tucker_wed.key

    next_week = calendar(RULES, overrides, MON + dt.timedelta(7), SUN + dt.timedelta(7))
    assert who(next_week) == who(calendar(RULES, [], MON, SUN)), "the following week is back to the schedule"


def test_skip_and_add():
    overrides = [Override(1, DOG, WED, "skip", rule_id=1, reason="vet visit"),
                 Override(2, DOG, WED, "add", person_id=MOM, due=dt.time(8, 0), reason="extra morning walk")]
    wed = calendar(RULES, overrides, WED, WED)
    assert [(o.person_id, o.skipped, o.due) for o in wed] == [(MOM, False, dt.time(8, 0)), (TUCKER, True, EVENING)]
    assert wed[0].key == (DOG, WED, "add", 2)
    assert calendar(RULES, overrides, WED, WED, include_skipped=False)[0].person_id == MOM


def test_latest_override_wins_and_misaimed_ones_are_ignored():
    overrides = [Override(1, DOG, SUN, "reassign", rule_id=4, person_id=MOM),
                 Override(2, DOG, SUN, "reassign", rule_id=4, person_id=CASS),
                 Override(3, DOG, MON, "reassign", rule_id=4, person_id=MOM)]  # Eric's rule isn't on Monday
    week = calendar(RULES, overrides, MON, SUN)
    assert next(o for o in week if o.date == SUN).person_id == CASS
    assert next(o for o in week if o.date == MON).person_id == TUCKER


def test_person_filter_is_after_overrides():
    overrides = [Override(1, DOG, SUN, "reassign", rule_id=4, person_id=MOM)]
    moms = calendar(RULES, overrides, MON, SUN, person_id=MOM)
    assert [o.date.isoweekday() for o in moms] == [4, 5, 7]
    assert calendar(RULES, overrides, MON, SUN, person_id=ERIC) == []


def test_every_n_days_and_bounds():
    r = Rule(9, 11, CASS, Pattern(every_days=3, anchor=D(2026, 10, 20)), start=D(2026, 10, 23), end=D(2026, 10, 29))
    got = [o.date for o in calendar([r], [], D(2026, 10, 15), D(2026, 11, 5))]
    assert got == [D(2026, 10, 23), D(2026, 10, 26), D(2026, 10, 29)]
    once = Rule(8, 12, ERIC, Pattern(on=D(2026, 10, 22)))
    assert [o.date for o in calendar([once], [], MON, SUN)] == [D(2026, 10, 22)]


def test_order_is_date_then_due_time():
    rules = [Rule(1, 20, ERIC, Pattern(weekdays=frozenset({1})), due=dt.time(19, 0)),
             Rule(2, 21, ERIC, Pattern(weekdays=frozenset({1})), due=dt.time(7, 0)),
             Rule(3, 22, ERIC, Pattern(weekdays=frozenset({1})))]
    assert [o.chore_id for o in calendar(rules, [], MON, MON)] == [21, 20, 22], "no time sorts last"


@pytest.mark.parametrize("kwargs", [{}, {"weekdays": frozenset()}, {"weekdays": frozenset({0})},
                                    {"every_days": 0, "anchor": MON}, {"every_days": 2},
                                    {"weekdays": frozenset({1}), "on": MON}])
def test_bad_patterns(kwargs):
    with pytest.raises(ValueError):
        Pattern(**kwargs)


def test_bad_overrides_and_swaps():
    with pytest.raises(ValueError):
        Override(1, DOG, SUN, "reassign", person_id=MOM)          # no rule
    with pytest.raises(ValueError):
        Override(1, DOG, SUN, "add")                              # no person
    week = calendar(RULES, [], MON, SUN)
    mon, wed = week[0], week[2]
    with pytest.raises(ValueError, match="same person"):
        plan_swap(mon, wed, "g", "")                              # both Tucker's
    with pytest.raises(ValueError):
        calendar(RULES, [], SUN, MON)
