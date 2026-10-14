# choreboard

Household chores as a calendar: schedule rules give the defaults, and an
override for a specific date wins on that date and nowhere else. Points live
in an append-only ledger with a reason on every entry.

Status: **step 3 of 6.** Done: the calendar engine (`calendar.py`, pure),
the service over SQLite (`service.py`), and the HTTP API (`api.py`) with
Pomerium sign-in and API tokens (`auth.py`). Next: container, CI and
deployment, then a setup UI.

## Run it locally

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
CHOREBOARD_DB=./dev.db CHOREBOARD_DEV_EMAIL=you@example.com \
  .venv/bin/uvicorn --factory choreboard.api:create_app --reload
```

Then open <http://localhost:8000/docs>: `POST /api/setup` makes you the first
admin, and every other route is there to try.

## Configuration

| Variable | Default | |
|---|---|---|
| `CHOREBOARD_DB` | `/data/choreboard.db` | SQLite file |
| `CHOREBOARD_JWKS_URL` | — | Pomerium's keys, e.g. `https://<host>/.well-known/pomerium/jwks.json` |
| `CHOREBOARD_AUDIENCE` | — | the route's host; the assertion's `aud` must match |
| `CHOREBOARD_AUTO_JOIN` | `1` | a signed-in stranger joins as a member |
| `CHOREBOARD_DEV_EMAIL` | — | **development only**: trust this identity with no sign-in |

API tokens: `POST /api/tokens` (signed in as a person) returns a `cb_…` token
once. Send it as `Authorization: Bearer`. Scopes: `read`, `write`, and
`act_as` (name another person per request with `X-Choreboard-As: <email>`).
