# choreboard — design

Household chores as a **calendar**. Schedule rules supply the defaults; an
override for a specific date wins on that date and nowhere else. When the
date passes, the rules are in charge again — there is nothing to undo.

> "Next week, swap my Sunday dog walk for Mom's Wednesday" puts each of us on
> the other's day for those two dates. Every other Sunday is still mine.

Most chore apps store only the rule, so a one-off change means editing the
rule (and remembering to put it back) or deleting and re-creating it (and
losing history). Here the calendar is **computed, never stored**: what is due
on a date is `rules(date)` with `overrides(date)` applied on top. Nothing is
written per day until something *happens* on it.

## Principles

1. **The calendar is a pure function** of rules, overrides and a date range
   (`choreboard/calendar.py`). No database, no clock, no I/O — fully tested.
2. **History never hangs off a rule.** Outcomes and ledger entries are keyed
   by the occurrence (chore, date, source), so editing or deleting a rule can
   never orphan or erase what already happened.
3. **Points are an append-only ledger** with a reason on every entry.
   Balances are sums. Nothing is deducted automatically: a deduction is
   always a person's explicit decision with a note.
4. **API first.** The API is the product; the web UI and nav are clients.
5. **Everyone signs in as themselves.** Kids participate as adults. Roles
   exist in the data; permissions are one policy function, permissive for
   now and tightened later in one place.

## Model

| Entity | Holds |
|---|---|
| person | name, colour, identity (email from the signed-in token), role |
| chore | title, description, points, icon |
| rule | chore, person, pattern, due time, start/end date. Editable. |
| override | chore, date, kind, reason, who, `group_id` |
| outcome | occurrence key, status, who did it, note, when |
| ledger | person, ±points, kind, note, outcome link |

**Patterns:** a set of weekdays; every *N* days from an anchor date; or one
date. Optional start and end dates bound any pattern.

**Override kinds**, each for one date:

- `reassign` — a rule's occurrence on that date goes to another person.
- `skip` — a rule's occurrence on that date doesn't happen.
- `add` — an extra occurrence of a chore for a person on that date.

A **swap** is two `reassign` overrides sharing a `group_id`; it is created and
undone as one unit, and carries one reason.

**Occurrence key.** A rule-derived occurrence is `(chore, date, "rule", rule_id)`;
an added one is `(chore, date, "add", override_id)`. The key does not include
the person, so reassigning an occurrence keeps its identity — an outcome
recorded against it stays attached.

## Outcomes (the household's model)

| Outcome | Meaning | Points |
|---|---|---|
| done | the assignee did it | + assignee |
| skipped | didn't need doing | none |
| failed | the assignee didn't do it | − assignee, explicit amount, reason required |
| covered | someone else did it, no fault | + the person who did it |
| failed & covered | someone else did it instead | + coverer, − assignee (explicit) |

**Points always go to whoever did the chore** — the assignee for `done`, the
person named in `done_by` for `covered` and `failed_covered` — whether or not
the schedule or an override had them on it.

A *swap* is planned ahead (overrides); *covered* is what happened on the day
(an outcome). Both exist because both happen.

**Swaps are any two occurrences, any distance apart** — tomorrow's for one
months away; nothing in between changes. Each side is independent once made:
when one side is done, the other (still undone) side can be swapped again with
yet another occurrence. Later overrides win, so undoing that second swap puts
the occurrence back to what the first swap said. A swap can only be undone as a
whole while neither of its sides has an outcome.

## Sign-in

- **Browser:** behind Pomerium. The app verifies Pomerium's signed assertion
  (`X-Pomerium-Jwt-Assertion`, against Pomerium's JWKS), not a plain header.
- **Machines** (nav, automations): scoped API tokens.
- **Later, if ever public:** its own OIDC against Authentik.

## Build order

1. Calendar engine + tests. ✅
2. Storage (SQLite), outcomes, ledger, swaps. ✅
3. HTTP API (FastAPI), CRUD, tokens, Pomerium verification. ← next
4. Container, CI (tests + multi-arch image), deploy in griflab `tools`.
5. Setup UI.
6. nav widget via a token.

Out of the first version: rewards, streaks, photos, notifications.

## Decisions made in step 2

- **One live outcome per occurrence.** Recording a second is a conflict; undo
  the first. An undone outcome stays in the table (`undone_at`, `undo_note`).
- **Undo appends.** Each ledger entry of an undone outcome gets a `reversal`
  entry; the ledger is never updated or deleted.
- **Planned vs recorded.** A `skip` *override* (planned ahead) makes the
  occurrence not happen; a `skipped` *outcome* (recorded on the day) says it
  didn't need doing. Overrides and swaps are refused on an occurrence that
  already has an outcome, and a swap can't be undone while either side has one.
- **Notes are required** for failed, covered, failed_covered, swaps, undos and
  adjustments. `failed` requires `deduct` stated explicitly (0 is allowed).
- **Points are copied** into the ledger when earned, so changing a chore's
  points later doesn't rewrite history.
- **Deleting a rule** removes its overrides (they have nothing left to
  change) but never its outcomes or points.
- **The first person** is created by `bootstrap` (no actor), as an admin, and
  only while nobody exists.
