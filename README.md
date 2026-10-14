# choreboard

Household chores as a calendar: schedule rules give the defaults, and an
override for a specific date wins on that date and nowhere else. Points live
in an append-only ledger with a reason on every entry.

Status: **step 1 of 6** — the calendar engine (`choreboard/calendar.py`),
pure and tested. Storage, the HTTP API, deployment and a setup UI follow.
Read [DESIGN.md](DESIGN.md) first.

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```
