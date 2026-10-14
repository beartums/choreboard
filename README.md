# choreboard

Household chores as a calendar: schedule rules give the defaults, and an
override for a specific date wins on that date and nowhere else. Points live
in an append-only ledger with a reason on every entry.

Status: **step 5 of 6.** Done: the calendar engine (`calendar.py`), the
service over SQLite (`service.py`), the HTTP API (`api.py`, `auth.py`), the
image and deployment, and the web UI (`web/`): setup (people, chores, weekly
rotations, other schedules) and the calendar (record what happened; give,
skip or swap a date; undo). Next: the nav widget.

## Run it locally

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
CHOREBOARD_DB=./dev.db CHOREBOARD_DEV_EMAIL=you@example.com \
  .venv/bin/uvicorn --factory choreboard.api:create_app --reload
```

Then open <http://localhost:8000/docs>: `POST /api/setup` makes you the first
admin, and every other route is there to try.

The UI (`web/`, React + TypeScript + Vite) in another terminal:

```sh
cd web && npm install
npm run dev        # http://localhost:5173, /api proxied to :8000
npm test           # vitest + Testing Library
npm run build      # into choreboard/static, which the API serves at /
```

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
