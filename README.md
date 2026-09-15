# booker

Automated course-booking daemon for **Mediterana / MyWellness**. It watches configured classes, discovers their exact release moment, and fires a parallel booking burst at `bookingOpensOn` — the moment the API opens a booking — to grab a spot before it fills.

Works for one or many accounts from a single process, purely against the MyWellness API (no browser).

## Features

- Target-based matching: configure classes as `(name, day, time)` — exactly how you'd read the weekly schedule; the daemon translates this to the recurring MyWellness series (`eventTypeId`) and disambiguates via weekday + time.
- Deterministic booking window: schedules a burst at the server-reported `bookingOpensOn` (class − 2 days, 21:00 local) instead of polling.
- Pre-burst re-verification ~10 min before release: re-confirms the class still exists, the schedule didn't shift, and you aren't already booked.
- Per-account auth lifecycle: login at startup, automatic re-login on token expiry, an account that fails to log in is suspended without affecting the others.
- **No re-booking**: a `booked_history.json` records every successful burst; once booked, a class is never booked again — even if it later shows free (e.g. other people cancelling).
- Dry-run / one-shot modes for safe inspection.

## Installation

Python ≥ 3.10.

```bash
python3 -m pip install httpx pyyaml
python3 -m pip install -e .
```

## Configuration

Runtime, read by the daemon (not committed): a [`.env`](`.env`) you source in the shell before starting booker — or export by hand. One account is keyed by `MEDI_CREDS_<NAME>`:

```
export MEDI_CREDS_JAN_NAME=jan.weyrich@protonmail.com
export MEDI_CREDS_JAN_PW=...
```

Missing credentials for an account are a warning, not an error — that account is just skipped.

Config file — start from [`config.example.yaml`](config.example.yaml):

```yaml
facility:
  id: "0273e18b-52bf-404e-afa6-8bfb2eeccbad"   # Mediterana
  name: mediterana

accounts:
  - name: jan                    # -> MEDI_CREDS_JAN_NAME / MEDI_CREDS_JAN_PW
    timezone: Europe/Berlin      # used to map (day, time) onto the schedule
    targets:
      - name: "Wirbelsäulengym"  # schedule display name
        day: 1                   # 1=Mon..7=Sun
        time: "19:00"            # HH:MM
        priority: 1              # lower = attempted first when several bursts collide
      - name: "Aqua Fitness"
        day: 2
        time: "17:00"
```

Target `day`/`time` must match an actual occurrence in the schedule (double-check against `docs/course_catalog.md` or `--dry-run`); a target that resolves to nothing is reported rather than silently ignored.

## Usage

```bash
# What would happen? No Book call is ever sent in dry-run.
source ./.env
python3 -m booker --dry-run config.yaml

# Run one discovery + booking pass for bursts due right now, then exit.
python3 -m booker --once config.yaml

# Long-lived daemon: discovery every 6h, bursts fire at release time.
python3 -m booker config.yaml
```

`--dry-run` prints, per planned burst:

```
ACCOUNT=jan TARGET=Wirbelsäulengym CLASSID=c5fef3af-… OPENS=2026-09-19T21:00:00+02:00 START=2026-09-21T09:00:00 STATUS=eligible
VERIFY c5fef3af-… OK
PAYLOAD would POST …/class/Book {"classId":…, "partitionDate":20260921, "userId":…}
```

### Booking semantics (v1)

- Bursts fire in parallel at `bookingOpensOn` (§5 of `03_design.md`).
- No retry: the API's verdict is final; a full/waitlist class is logged, never re-attempted, and the waiting list is deliberately **not** joined.
- Fixed booked courses only: a slot recorded in `booked_history.json` is never re-booked. If you want it again after cancelling, remove that entry from the file.
- Classes with equipment/station layouts (`hasLayout: true`) are a known v1 limitation — they need a station/gym-equipment choice the daemon doesn't make yet; such targets are reported, not booked.

## What lives where

| Path | Purpose |
|---|---|
| `booker/config.py` | config.yaml parsing + validation |
| `booker/client.py` | `ScheduleClient` (public schedule API) + `AuthClient` (login, expiry re-login) |
| `booker/discovery.py` | target → `eventTypeId` resolution, catalog, schedule-change detection |
| `booker/scheduler.py` | plan/discovery loop, pre-burst verification, burst fire |
| `booker/book.py` | `BurstExecutor` — builds Book payloads, maps API results |
| `booker/state.py` | `booked_history.json` (loaded, written, pruned) |
| `booker/auth.py` | login, user-id resolution, token-file override |
| `scripts/live_roundtrip.py` | live Book→Unbook probe (requires explicit `--confirm`) |
| `docs/` | API reference, spike findings, course catalog, requirements, design, task breakdown |

## Development

### Running the tests

```bash
python3 -m unittest booker.tests.test_auth booker.tests.test_book \
  booker.tests.test_scheduler booker.tests.test_state
```

All tests run against mocked HTTP (`httpx.MockTransport`) — no credentials, no network.

### Architecture notes

- The scheduler is clock- and network-injectable: constructor takes `now_fn`, `sleep_fn`, factory callables for the HTTP clients. Tests drive it with a fake clock; this is also how `--once`/`--dry-run` are implemented without scheduling hacks.
- `booker/book.py` result mapping is the security-relevant part of the write path: `dry_run=True` returns a simulated `Booked` without any HTTP, and the unit test `test_dry_run_books_nothing_over_http` would fail if a transport got touched.
- Auth reality (see `docs/spike3_findings.md`): bearer tokens last a few hours and an expired token can still return HTTP 200 with `{"errors":[{"field":"TokenNotValid"}]}` — `AuthClient.call()` treats that exactly like a 401: re-login once, retry once, then suspend the account.

### Gotchas / known issues

- **`classId` is stable across weekly instances** (verified: same id for consecutive Wirbelsäulengym weeks). Booking is disambiguated by `partitionDate`; `booked_history` keys are `${account}::${partitionDate}::${classId}` so they stay unique, but don't assume instance ids rotate weekly.
- `class/Search`'s `bookingUserStatus` fields can be stale/flapping; only `bookingInfo.bookingOpensOn` is authoritative.
- The widget UI reads the schedule via an *authenticated* search endpoint; the daemon uses the *public* search endpoint — same data, no login needed for discovery.
- `live_roundtrip.py` sends real bookings; it has a two-step safety model: without `--confirm` it only prints the plan (class, payload), with `--confirm` it Books and Unbooks the same class immediately. Never run it unattended.

## Git hygiene

`secrets/` and `.env` are gitignored — credentials never enter the repository. `config.yaml` is local; commit `config.example.yaml` instead.

See [`03_design.md`](03_design.md) for the full design and [`04_task.md`](04_task.md) for the v1 task breakdown.