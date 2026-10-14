# choreboard

Household chores as a calendar: schedule rules give the defaults, and an
override for a specific date wins on that date and nowhere else. Points live
in an append-only ledger with a reason on every entry.

Status: **step 2 of 6.** Done: the calendar engine (`choreboard/calendar.py`,
pure) and the service over SQLite (`choreboard/service.py`): people, chores,
rules, overrides and swaps, outcomes and the points ledger. Next: the HTTP
API, then deployment and a setup UI.
Read [DESIGN.md](DESIGN.md) first.

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```
