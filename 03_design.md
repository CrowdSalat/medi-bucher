# Design: MyWellness Course Booker

## 1. Runtime & Operational Constraints

* **Runtime:** Python 3.10+.
* **Environment:** long-lived daemon process with an internal async scheduler (`asyncio` event loop; no external cron dependency).
  * Holds per-account auth tokens and warm `httpx` connection pools in memory.
  * Reschedules pre-burst re-verification windows internally; wakes only when work is due.
  * Containerizable later (systemd unit today, K8s pod/CronJob when needed).
* **Deployment model:** single daemon serves multiple user accounts in one process; each account has its own credentials, token, targets, and booking history. One instance per person is no longer needed.
* **Multi-account isolation:** failures or expiry in one account never affect the others (per-account token lifecycle, §3).

---

## 2. Configuration Model

Two separate concerns with different storage models:

### 2.1 Course targets — config file (`config.yaml`)

YAML on disk; committed (no secrets). Defines what to watch and when.

```yaml
facility:
  id: "0273e18b-52bf-404e-afa6-8bfb2eeccbad"
  name: mediterana

accounts:
  - name: jan                        # maps to medi_creds_jan_name / medi_creds_jan_pw
    timezone: Europe/Berlin
    targets:                         # list of classes to monitor
      - name: "Wirbelsäulengym"      # schedule display name
        day: 1                       # 1=Mon..7=Sun (matches facility series)
        time: "19:00"                # HH:MM; the (name, day, time) triple is the identity
        priority: 1                  # lower = attempt first at release
      - name: "Aqua Fitness"
        day: 2
        time: "17:00"
```

**Target identity & matching:**
- A target is identified by `(name, day, time)`. The daemon resolves it to the persistent `eventTypeId` (course template id) at discovery. `eventTypeId` is stable across weeks; the per-instance `classId` changes weekly.
- Caveat: one display name can map to several `eventTypeId`s (facility runs separate series) — the `day` + `time` disambiguate. Reference catalog: [`docs/course_catalog.md`](docs/course_catalog.md).
- Optional `event_type_id:` pin in config to verify the resolved template differs (warn if changed/facility recreated the series).

**Schedule-change warning:**
- At each discovery pass the daemon compares the matched occurrence's actual start (weekday + time) against the configured `(day, time)`. Mismatch → log a warning and **skip booking that occurrence** (fixed courses stay fixed; never silently book the wrong slot). See §7 for where warnings land.

### 2.2 Credentials — prefixed env vars

One `<name>` per account in the config file; credentials injected via env vars at runtime.

| Env var | Purpose |
|---|---|
| `MEDI_CREDS_<name>_NAME` | Login email (username) |
| `MEDI_CREDS_<name>_PW` | Login password |

Example for `name: jan`:
```
MEDI_CREDS_JAN_NAME=jan.weyrich@protonmail.com
MEDI_CREDS_JAN_PW=s3cret
```

K8s: mount `MEDI_CREDS_*` as a `Secret` → envFrom; local dev: `.env` file (gitignored) or direct export.

---

## 3. Auth & Token Lifecycle (per account)

- **Login:** at startup, for each account: read `MEDI_CREDS_<name>_NAME/_PW` → `POST core.../authentication/login` → capture Bearer token. Store token + user id in memory per account.
- **Usage:** `Authorization: Bearer <token>` on all protected calls (`Me`, `Book`, `Unbook`, participant checks).
- **Invalidation detection:**
  - `401` on any protected endpoint, **or**
  - `Me` returns HTTP 200 with `{"errors":[{"field":"TokenNotValid",...}]}`.
- **Refresh:** re-login that account only (fresh token), retry the failed call once. If re-login fails (wrong password, lockout) → log error and suspend the account until next discovery cycle (don't crash the daemon, don't affect other accounts).
- **No persistence:** tokens live in memory only; a daemon restart re-logs-in. This keeps the daemon stateless w.r.t. secrets.

---

## 4. Discovery & Scheduling

- **Cadence:** full schedule scan every 6h. Horizon: next **14 days** (covers the 2-days-ahead release window with margin).
- **Per pass, per account:**
  1. Fetch `class/Search` for the horizon window.
  2. For each configured target, resolve the matching series (`eventTypeId`) by `(name, day, time)`.
  3. Read each matched instance's authoritative `bookingInfo.bookingOpensOn`.
  4. Run the schedule-change warning check (§2.1).
- **What a scan drives:**
  - Schedule a burst for every matched instance at its `bookingOpensOn`.
  - Schedule a **pre-burst re-verification** pass ~10 min before each burst (re-resolve `classId`, `bookingOpensOn` unchanged, segment still eligible, not in `booked_history`).
- **Missing target:** a configured target with no matching occurrence during a scan is logged (info) — no alerting in v1.

---

## 5. Burst Semantics

- **Trigger:** at `bookingOpensOn` (server-authoritative, class date − 2 days @ 21:00 local). Do **not** fire earlier — server hard-rejects with `400 "Booking has not opened, yet."`.
- **Payload:** `Book` with `{partitionDate (int), userId, classId}` and **no `station`** for layout-less classes (`hasLayout: false`). `hasLayout: true` targets (equipment) require station resolution — treated as reserved/known limitation in v1.
- **Parallelism:** fire all due targets in parallel (one `Book` per target; `priority` only orders the sequence of *fallback* attempts where applicable). Reuse the account's warm `httpx` pool (~35–40 ms warm RTT).
- **Result handling (per target):**
  - `Booked` / `UserAlreadyBooked` → record instance in `booked_history.json` (§6); mark done.
  - `PlaceNotAvailable` / `Failed` / `EventNotExists` / `NoPermissionsForUserException` → log result; **no retry** at that instance (gate already past, cancellation-catching out of scope). `NoPermissionsForUserException` is fatal for that series — log a dedicated warning.
  - Unrecognized `result` → log raw body.
- **No waiting list:** v1 never sends `Book` expecting `UserAddedToWaitingList` (see §8); a full class at release = not booked, logged.

---

## 6. State & Deduplication (`booked_history.json`)

- Flat map keyed by `<account>::<partitionDate>::<classId>` → `{"booked_at": <ISO8601>}`.
- **Presence in the map = already handled → never book that instance again**, even if externally cancelled. Manual cancellation is respected implicitly (the instance stays recorded → no rebook).
- **Pruning:** on startup, drop entries whose `partitionDate` is in the past (they can never recur; `classId` is per-instance).
- Location: adjacent to config (working dir default), gitignored.

---

## 7. Logging & Observability

- **stdout:** structured, minimal (one line per event): discovery pass summary, scheduled bursts, burst outcome per target, auth re-login, schedule-change warnings, errors. Container/systemd-friendly.
- **Persistence:** `booked_history.json` is the durable success record; no separate log file in v1.
- **Flags:**
  - `--dry-run`: discovery + scheduling only, never calls `Book` (log what *would* be booked).
  - `--once`: run a single discovery+burst-trigger pass, then exit (cron-style).

---

## 8. Waiting-List Policy

- **v1:** never join a waiting list. A full course at release is simply not booked and logged (`PlaceNotAvailable`).
- Rationale: stakeholder expects fixed courses at release; the competing-slot scenario is covered by burst parallelism. A `join_waiting_list: true` per-target flag can be added later if needed.

---

## 9. Alternative UX Flows (brainstorm)

> *Ideas for consideration, not yet decided.*

**A. Interactive login mask (stretch goal)**
First-run: if `MEDI_CREDS_<name>_PW` is not set, prompt for email + password, perform login, store the resulting session token locally (encrypted or plain in a local `.tokens/` dir). Subsequent runs skip login. Good for interactive local dev; not suitable for headless K8s.

**B. Single-token override**
For quick one-off runs or debugging, accept a pre-existing token via env var (`MEDI_CREDS_<name>_TOKEN`) and skip login entirely. Useful for ad-hoc use though it must be refreshed manually when expired.

**C. Config-file-only (no env)**
Put credentials in a separate YAML/JSON file (e.g. `secrets/<name>.yaml`, gitignored) and mount/decrypt it at runtime. Simpler mental model but needs secret management; env vars are more K8s-native.

**D. Web UI / Telegram bot (not in scope v1)**
A minimal web frontend or bot where users register and manage their targets interactively, with the daemon reading from a shared backend store.