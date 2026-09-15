# Task Breakdown: MyWellness Course Booker (v1)

Implementation plan in ralph-loop-sized tasks. Each task states **what** to build and **how to verify** it standalone. v1 = phases 1–5 (skeleton/config → discovery → auth → scheduling → burst+state). Deferred to v2: `--once` polish, signal-heavy daemon niceties, web UI, waiting lists.

**Verification rule:** tasks must not touch the live `Book` endpoint unless the user gives a heads-up and explicit OK. `--dry-run` cleanliness is the default gate.

---

## Phase 1 — Skeleton & Config

### T1: Repo skeleton & entrypoint

**What:** Create `booker/` package, `pyproject.toml` (deps: `httpx`, `pyyaml`), and `python -m booker` entrypoint that parses `--help`, `--dry-run`, `config` path. Stub modules: `config.py`, `client.py`, `auth.py`, `discovery.py`, `scheduler.py`, `book.py`, `state.py`.

**How to verify:**
- `python -m booker --help` exits 0 and lists the flags.
- `python -m booker --dry-run config.yaml` runs and exits cleanly (no-op stub output).
- `python -c "import booker"` imports without error.

### T2: Config loader & validation

**What:** Parse `config.yaml` (facility, accounts, targets) into validated dataclasses. Validate: `day` ∈ 1..7, `time` matches `HH:MM`, `priority` int, no duplicate `(name, day, time)` per account, account names match `MEDI_CREDS_<name>_NAME/_PW` vars. Missing required secrets → account flagged skipped, daemon continues.

**How to verify:**
- Valid config → `--dry-run` prints the resolved model (accounts, targets, timezone).
- Invalid configs each fail with a specific error: `day: 9`, missing `time`, duplicate triple, missing `MEDI_CREDS_*` for an account.
- `config.example.yaml` committed next to it.

---

## Phase 2 — Schedule Client & Discovery

### T3: Schedule client (`class/Search` wrapper)

**What:** Thin `httpx` client for `GET calendar.mywellness.com/v2/enduser/class/Search` (facilityId, fromDate/toDate, `eventTypes=Class`, channel header). Parses the JSON list into instance dataclasses (id, name, eventTypeId, startDate, partitionDate, hasLayout, availablePlaces, bookingInfo.bookingOpensOn).

**How to verify:**
- Fetch 2026-09-16..22 → prints parsed instances count and one sample entry (name, eventTypeId, bookingOpensOn) matching `docs/course_catalog.md`.
- Malformed/error HTTP → raises a typed `ScheduleApiError`, not a crash.

### T4: Target resolver & catalog

**What:** Match configured targets `(name, day, time)` to instances: resolve `eventTypeId`, then list matching instances with their `bookingOpensOn`. Ambiguous names (multiple series like Body Workout) require matching day+time; no match → log info. Optional `event_type_id` pin → warn if resolved differs.

**How to verify:**
- `--dry-run` with a real target (e.g. "Wirbelsäulengym", day 1, time 09:00) resolves to `eventTypeId` `a3c6181c-7a77-434d-ac5c-96c58282288c` and lists instances with correct `bookingOpensOn`.
- A deliberately wrong time for an ambiguous name → no match + "no matching occurrence" log, exit 0.

---

## Phase 3 — Auth & Token Lifecycle

### T5: Login & user resolution

**What:** Per account: read `MEDI_CREDS_<name>_NAME/_PW`, `POST core.../authentication/login`, capture Bearer token + resolve `userId` via `Me`. In-memory per-account token storage. Accept an optional `MEDI_CREDS_<name>_TOKEN` override (skip login).

**How to verify:**
- With real env creds (or token override): `--dry-run` prints account `jan` with resolved user id `b9ba037a-a59d-4d7d-a9e6-6e0ffe5f3977`.
- Wrong password for a second account → that account flagged suspended, first account still usable; daemon continues.

### T6: Expiry detection & re-login

**What:** Treat `401` (protected endpoints) and `Me`-200-with-`TokenNotValid` as logged-out. Re-login that account, retry the failed call once. Login failure → suspend account until next discovery cycle.

**How to verify:**
- Unit test: mocked client returns `TokenNotValid` then success → asserts one extra login and successful retry.
- Live: set a stale `MEDI_CREDS_<name>_TOKEN` override → `--dry-run` performs fresh login, continues; a bad override → account suspended, others fine.

---

## Phase 4 — Burst Scheduling

### T7: Scheduler core & dry-run

**What:** Async event loop: discovery every 6h over 14-day horizon; plan a burst per matched instance at its `bookingOpensOn`; wake at that instant; `--dry-run` computes and prints the plan without executing Book. Graceful shutdown on SIGINT/SIGTERM.

**How to verify:**
- `--dry-run` prints a plan like `ACCOUNT=jan TARGET=Wirbelsäulengym CLASSID=... OPENS=2026-09-17T21:00:00+02:00` for real instances.
- Inject test: shorten discovery interval / use a fake clock → plan appears, daemon wakes at the right moment (assert via log timestamps), no `Book` ever called in dry-run.

### T8: Pre-burst re-verification

**What:** ~10 min before each burst: re-resolve `classId`, confirm `bookingOpensOn` unchanged, instance not in `booked_history`, schedule-change warning check (actual start vs configured day/time → warn + skip booking that instance).

**How to verify:**
- Dry-run shows re-verification rows (classId current, opens-on unchanged, eligible=yes/no + reason).
- Simulate a shifted instance (test fixture) → warning emitted + eligible=no (skipped).

---

## Phase 5 — Burst Execution & State

### T9: `booked_history.json` state

**What:** Load/save flat map keyed `<account>::<partitionDate>::<classId>` → `{booked_at}`. Prune entries with past `partitionDate` on startup. Dedupe = presence check.

**How to verify:**
- Unit tests: write → reload, key shape, prune removes past-dated entries, presence blocks a planned burst (dry-run shows eligible=no, history hit).

### T10: Burst execution & result handling

**What:** At trigger: parallel `Book` (one per due target, no `station` for `hasLayout:false`, account's warm pool). Map results: `Booked`/`UserAlreadyBooked` → write history; `PlaceNotAvailable`/`Failed`/`EventNotExists`/`NoPermissionsForUserException` → log, no retry. Unknown → raw log. Never join waiting list.

**How to verify:**
- **Dry-run (default):** at simulated trigger time, asserts the exact payloads that would be sent; no HTTP write to `Book`.
- **Live (user approval required):** controlled Book→Unbook round-trip on an open slot per earlier A/B/C probes, with explicit heads-up before running.

---

## v1 Implementation Notes
- **classId is stable across weekly instances** (observed live: same id for the 2026-09-21 and 2026-09-28 Wirbelsäulengym). `Book` is disambiguated by `partitionDate`; `booked_history` keys (account::partition_date::class_id) stay unique. T8's "classId unchanged" check is weaker than originally assumed.
- Burst fires at `trigger_at − lead_s` (default 30s) in the phase-4 loop but the API hard-rejects before `bookingOpensOn`; phase 5 frees the exact trigger moment — keep `burst_lead_s` at 0 or small and rely on the verified pre-burst timing.
- `startDate` from the API is naive (no offset); schedule-change weekday/time matching relies on account timezone (Europe/Berlin) being resolvable.

## v1 Definition of Done

- `python -m booker --dry-run config.yaml` runs discovery → auth → plan without ever calling `Book`.
- Real target from `docs/course_catalog.md` is resolved and its burst scheduled at `bookingOpensOn`.
- `booked_history.json` correctly blocks re-booking and prunes.
- Multi-account: one failed account never degrades the others.
- Live round-trip Book→Unbook executed only with explicit user approval.

---

## Phase 6 — Operationalize (draft, discuss before committing to tasks)

Planning in progress — items below are intentions, not finalized tasks. Each still needs What + How-to-verify.

- **Containerize:** OCI image (`Containerfile`, non-root USER, no hardcoded UID — OpenShift `restricted-v2` SCC compatible), multi-arch AMD64+ARM64.
- **CI/CD:** build the image and push it to a **public** container registry.
  - Registry + naming to confirm: **GitHub Container Registry (GHCR)** vs docker.io; image name/tag scheme (semver + git SHA?).
  - Credential strategy for the registry push.
- **OpenShift manifests:** Deployment/service or long-lived Pod; Secret wiring for `MEDI_CREDS_*` and `config.yaml` (ConfigMap vs Secret); persistent volume for `booked_history.json` (or reconsider — see v2); probes/liveness; resource limits.
- **"Maybe more" (candidates):** startup/shutdown behavior (SIGTERM handling in Pod lifecycle), standalone config validation image (sidecar/cron), observability, docs for operators.
- Open questions to nail down tomorrow: does the daemon keep running permanently (daemon) or run per-window (CronJob/`--once`)? Volume persistence for history vs in-memory + re-scan semantics.

## v2 Feature Discussion (after Phase 6)

Not yet scoped. Candidate themes from v1 notes: waiting-list join, `hasLayout:true` station resolution, web UI, cancellation monitoring, push notifications.