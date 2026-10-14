"""The calendar: schedule rules give the defaults, date overrides win.

Pure functions over plain values -- no database, no clock, no I/O. What is due
on a date is the rules' occurrences on that date with that date's overrides
applied. Nothing here is stored per day; see DESIGN.md.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Iterable, Literal

Date = dt.date
OverrideKind = Literal["reassign", "skip", "add"]


@dataclass(frozen=True)
class Pattern:
    """When a rule recurs. Exactly one of: `weekdays` (ISO: 1 = Monday ...
    7 = Sunday), `every_days` from `anchor`, or a single date `on`."""
    weekdays: frozenset[int] | None = None
    every_days: int | None = None
    anchor: Date | None = None
    on: Date | None = None

    def __post_init__(self) -> None:
        kinds = sum(x is not None for x in (self.weekdays, self.every_days, self.on))
        if kinds != 1:
            raise ValueError("a pattern is exactly one of weekdays, every_days or on")
        if self.weekdays is not None:
            if not self.weekdays or not all(1 <= d <= 7 for d in self.weekdays):
                raise ValueError("weekdays are ISO 1 (Monday) to 7 (Sunday), at least one")
        if self.every_days is not None:
            if self.every_days < 1:
                raise ValueError("every_days must be at least 1")
            if self.anchor is None:
                raise ValueError("every_days needs an anchor date")

    def matches(self, d: Date) -> bool:
        if self.weekdays is not None:
            return d.isoweekday() in self.weekdays
        if self.every_days is not None:
            assert self.anchor is not None
            delta = (d - self.anchor).days
            return delta >= 0 and delta % self.every_days == 0
        return d == self.on


@dataclass(frozen=True)
class Rule:
    """A chore for a person on a pattern, optionally bounded by start/end
    (inclusive). Editable: history is keyed by occurrence, not by rule."""
    id: int
    chore_id: int
    person_id: int
    pattern: Pattern
    due: dt.time | None = None              # end of the day's window: done by
    start: Date | None = None
    end: Date | None = None
    available: dt.time | None = None        # start of the day's window: not before

    def __post_init__(self) -> None:
        if self.available and self.due and self.available >= self.due:
            raise ValueError("the start time must be before the due time")

    def occurs(self, d: Date) -> bool:
        if self.start is not None and d < self.start:
            return False
        if self.end is not None and d > self.end:
            return False
        return self.pattern.matches(d)


@dataclass(frozen=True)
class Override:
    """A change to one date.

    reassign: rule `rule_id`'s occurrence on `date` goes to `person_id`.
    skip:     rule `rule_id`'s occurrence on `date` does not happen.
    add:      an extra occurrence of `chore_id` for `person_id` on `date`.

    Overrides sharing a `group_id` were made together (a swap) and are undone
    together."""
    id: int
    chore_id: int
    date: Date
    kind: OverrideKind
    rule_id: int | None = None
    person_id: int | None = None
    due: dt.time | None = None
    group_id: str | None = None
    reason: str = ""
    available: dt.time | None = None

    def __post_init__(self) -> None:
        if self.available and self.due and self.available >= self.due:
            raise ValueError("the start time must be before the due time")
        if self.kind in ("reassign", "skip") and self.rule_id is None:
            raise ValueError(f"a {self.kind} override needs the rule it changes")
        if self.kind in ("reassign", "add") and self.person_id is None:
            raise ValueError(f"a {self.kind} override needs a person")
        if self.kind == "add" and self.rule_id is not None:
            raise ValueError("an add override is not tied to a rule")


# (chore_id, date, source, source_id). Not the person: reassigning an
# occurrence keeps its identity, so an outcome recorded against it stays put.
OccurrenceKey = tuple[int, Date, str, int]


@dataclass(frozen=True)
class Occurrence:
    key: OccurrenceKey
    chore_id: int
    date: Date
    person_id: int
    due: dt.time | None
    rule_id: int | None
    # The person the schedule says, when an override changed it; else None.
    scheduled_person_id: int | None = None
    override_id: int | None = None
    skipped: bool = False
    reason: str = ""
    group_id: str | None = None
    available: dt.time | None = None

    @property
    def source(self) -> str:
        """'rule' when the schedule decided this outright, 'override' when a
        date override changed or created it."""
        return "override" if self.override_id is not None else "rule"


def dates(start: Date, end: Date) -> Iterable[Date]:
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def calendar(rules: Iterable[Rule], overrides: Iterable[Override], start: Date, end: Date,
             person_id: int | None = None, include_skipped: bool = True) -> list[Occurrence]:
    """Every occurrence from `start` to `end` (inclusive), overrides applied.

    Several overrides on the same rule and date: the highest id (the latest)
    wins. An override aimed at a date its rule does not occur on is ignored --
    the API refuses to create one, so this only matters after a rule edit.
    `person_id` narrows to whoever is on the hook after overrides; skipped
    occurrences are kept (flagged) unless `include_skipped` is False.
    """
    if end < start:
        raise ValueError("end is before start")
    rules = list(rules)
    by_rule_date: dict[tuple[int, Date], Override] = {}
    adds: list[Override] = []
    for o in sorted(overrides, key=lambda o: o.id):
        if not start <= o.date <= end:
            continue
        if o.kind == "add":
            adds.append(o)
        else:
            assert o.rule_id is not None
            by_rule_date[(o.rule_id, o.date)] = o

    out: list[Occurrence] = []
    for d in dates(start, end):
        for r in rules:
            if not r.occurs(d):
                continue
            occ = Occurrence(key=(r.chore_id, d, "rule", r.id), chore_id=r.chore_id, date=d,
                             person_id=r.person_id, due=r.due, rule_id=r.id, available=r.available)
            o = by_rule_date.get((r.id, d))
            if o is not None and o.chore_id == r.chore_id:
                common = dict(override_id=o.id, reason=o.reason, group_id=o.group_id)
                if o.kind == "skip":
                    occ = replace(occ, skipped=True, **common)
                else:
                    assert o.person_id is not None
                    moved = o.person_id != r.person_id
                    occ = replace(occ, person_id=o.person_id,
                                  scheduled_person_id=r.person_id if moved else None, **common)
            out.append(occ)
    for o in adds:
        assert o.person_id is not None
        out.append(Occurrence(key=(o.chore_id, o.date, "add", o.id), chore_id=o.chore_id,
                              date=o.date, person_id=o.person_id, due=o.due, available=o.available, rule_id=None,
                              override_id=o.id, reason=o.reason, group_id=o.group_id))

    if person_id is not None:
        out = [x for x in out if x.person_id == person_id]
    if not include_skipped:
        out = [x for x in out if not x.skipped]
    # By due time, then start time; anything untimed last.
    out.sort(key=lambda x: (x.date, x.due or dt.time.max, x.available or dt.time.max, x.chore_id, x.key[2], x.key[3]))
    return out


@dataclass(frozen=True)
class SwapPlan:
    """The two reassign overrides a swap creates (ids assigned on save)."""
    a: Override
    b: Override
    group_id: str = field(default="")


def plan_swap(a: Occurrence, b: Occurrence, group_id: str, reason: str) -> SwapPlan:
    """Swap who is on the hook for two rule occurrences: a's goes to b's
    person and b's to a's, on those dates only. Both must be rule
    occurrences (an `add` has no rule to override -- reassign it directly),
    and must be different people."""
    if a.rule_id is None or b.rule_id is None:
        raise ValueError("only scheduled occurrences can be swapped")
    if a.key == b.key:
        raise ValueError("an occurrence can't be swapped with itself")
    if a.person_id == b.person_id:
        raise ValueError("both occurrences are already the same person's")
    if a.skipped or b.skipped:
        raise ValueError("a skipped occurrence can't be swapped")
    return SwapPlan(
        a=Override(id=0, chore_id=a.chore_id, date=a.date, kind="reassign", rule_id=a.rule_id,
                   person_id=b.person_id, group_id=group_id, reason=reason),
        b=Override(id=0, chore_id=b.chore_id, date=b.date, kind="reassign", rule_id=b.rule_id,
                   person_id=a.person_id, group_id=group_id, reason=reason),
        group_id=group_id)
