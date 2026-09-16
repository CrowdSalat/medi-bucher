# Task Breakdown: MyWellness Course Booker (v1)

Implementation plan in ralph-loop-sized tasks. Each task states **what** to build and **how to verify** it standalone. v1 = phases 1–5 (skeleton/config → discovery → auth → scheduling → burst+state). Deferred to v2: `--once` polish, signal-heavy daemon niceties, web UI, waiting lists.

**Verification rule:** tasks must not touch the live `Book` endpoint unless the user gives a heads-up and explicit OK. `--dry-run` cleanliness is the default gate.

**Status legend:** ✅ done · 🟡 partial · ⬜ open

---

## Phase 1 — Skeleton & Config

### T1: Repo skeleton & entrypoint — ✅ done

**What:** Create `booker/` package, `pyproject.toml` (deps: `httpx`, `pyyaml`), and `python -m booker` entrypoint that parses `--help`, `--dry-run`, `config` path. Stub modules: `config.py`, `client.py`, `auth.py`, `discovery.py`, `scheduler.py`, `book.py`, `state.py`.

**How to verify:**
- `python -m booker --help` exits 0 and lists the flags.
- `python -m booker --dry-run config.yaml` runs and exits cleanly (no-op stub output).
- `python -c "import booker"` imports without error.

### T2: Config loader & validation — ✅ done (superseded in part)

**What:** Parse `config.yaml` (facility, accounts, targets) into validated dataclasses. Validate: `day` ∈ 1..7, `time` matches `HH:MM`, `priority` int, no duplicate `(name, day, time)` per account, account names match `MEDI_CREDS_<name>_NAME/_PW` vars. Missing required secrets → account flagged skipped, daemon continues.

**How to verify:**
- Valid config → `--dry-run` prints the resolved model (accounts, targets, timezone).
- Invalid configs each fail with a specific error: `day: 9`, missing `time`, duplicate triple, missing `MEDI_CREDS_*` for an account.
- `config.example.yaml` committed next to it.

**Note (2026-09, superseded):** facility is now hardcoded in `booker/config.py` (`MEDITERANA_ID/NAME`); creds may be inline (`username`/`password` per account, env fallback kept). Covered by `test_config.py`.

---

## Phase 2 — Schedule Client & Discovery

### T3: Schedule client (`class/Search` wrapper) — ✅ done

**What:** Thin `httpx` client for `GET calendar.mywellness.com/v2/enduser/class/Search` (facilityId, fromDate/toDate, `eventTypes=Class`, channel header). Parses the JSON list into instance dataclasses (id, name, eventTypeId, startDate, partitionDate, hasLayout, availablePlaces, bookingInfo.bookingOpensOn).

**How to verify:**
- Fetch 2026-09-16..22 → prints parsed instances count and one sample entry (name, eventTypeId, bookingOpensOn) matching `docs/course_catalog.md`.
- Malformed/error HTTP → raises a typed `ScheduleApiError`, not a crash.

### T4: Target resolver & catalog — ✅ done

**What:** Match configured targets `(name, day, time)` to instances: resolve `eventTypeId`, then list matching instances with their `bookingOpensOn`. Ambiguous names (multiple series like Body Workout) require matching day+time; no match → log info. Optional `event_type_id` pin → warn if resolved differs.

**How to verify:**
- `--dry-run` with a real target (e.g. "Wirbelsäulengym", day 1, time 09:00) resolves to `eventTypeId` `a3c6181c-7a77-434d-ac5c-96c58282288c` and lists instances with correct `bookingOpensOn`.
- A deliberately wrong time for an ambiguous name → no match + "no matching occurrence" log, exit 0.

---

## Phase 3 — Auth & Token Lifecycle

### T5: Login & user resolution — ✅ done

**What:** Per account: read `MEDI_CREDS_<name>_NAME/_PW`, `POST core.../authentication/login`, capture Bearer token + resolve `userId` via `Me`. In-memory per-account token storage. Accept an optional `MEDI_CREDS_<name>_TOKEN` override (skip login).

**How to verify:**
- With real env creds (or token override): `--dry-run` prints account `jan` with resolved user id `b9ba037a-a59d-4d7d-a9e6-6e0ffe5f3977`.
- Wrong password for a second account → that account flagged suspended, first account still usable; daemon continues.

### T6: Expiry detection & re-login — ✅ done

**What:** Treat `401` (protected endpoints) and `Me`-200-with-`TokenNotValid` as logged-out. Re-login that account, retry the failed call once. Login failure → suspend account until next discovery cycle.

**How to verify:**
- Unit test: mocked client returns `TokenNotValid` then success → asserts one extra login and successful retry.
- Live: set a stale `MEDI_CREDS_<name>_TOKEN` override → `--dry-run` performs fresh login, continues; a bad override → account suspended, others fine.

---

## Phase 4 — Burst Scheduling

### T7: Scheduler core & dry-run — ✅ done

**What:** Async event loop: discovery every 6h over 14-day horizon; plan a burst per matched instance at its `bookingOpensOn`; wake at that instant; `--dry-run` computes and prints the plan without executing Book. Graceful shutdown on SIGINT/SIGTERM.

**How to verify:**
- `--dry-run` prints a plan like `ACCOUNT=jan TARGET=Wirbelsäulengym CLASSID=... OPENS=2026-09-17T21:00:00+02:00` for real instances.
- Inject test: shorten discovery interval / use a fake clock → plan appears, daemon wakes at the right moment (assert via log timestamps), no `Book` ever called in dry-run.

### T8: Pre-burst re-verification — ✅ done

**What:** ~10 min before each burst: re-resolve `classId`, confirm `bookingOpensOn` unchanged, instance not in `booked_history`, schedule-change warning check (actual start vs configured day/time → warn + skip booking that instance).

**How to verify:**
- Dry-run shows re-verification rows (classId current, opens-on unchanged, eligible=yes/no + reason).
- Simulate a shifted instance (test fixture) → warning emitted + eligible=no (skipped).

---

## Phase 5 — Burst Execution & State

### T9: `booked_history.json` state — ✅ done

**What:** Load/save flat map keyed `<account>::<partitionDate>::<classId>` → `{booked_at}`. Prune entries with past `partitionDate` on startup. Dedupe = presence check.

**How to verify:**
- Unit tests: write → reload, key shape, prune removes past-dated entries, presence blocks a planned burst (dry-run shows eligible=no, history hit).

### T10: Burst execution & result handling — ✅ done (+ live round-trip verified)

**What:** At trigger: parallel `Book` (one per due target, no `station` for `hasLayout:false`, account's warm pool). Map results: `Booked`/`UserAlreadyBooked` → write history; `PlaceNotAvailable`/`Failed`/`EventNotExists`/`NoPermissionsForUserException` → log, no retry. Unknown → raw log. Never join waiting list.

**How to verify:**
- **Dry-run (default):** at simulated trigger time, asserts the exact payloads that would be sent; no HTTP write to `Book`.
- **Live (user approval required):** controlled Book→Unbook round-trip on an open slot per earlier A/B/C probes, with explicit heads-up before running.

**Note:** live round-trip was executed with approval — `docs/api_reference.md` §4.1/4.2 marked ✅ live.

---

## v1 Implementation Notes — ✅ all confirmed/fixed

- **classId is stable across weekly instances** (observed live: same id for the 2026-09-21 and 2026-09-28 Wirbelsäulengym). `Book` is disambiguated by `partitionDate`; `booked_history` keys (account::partition_date::class_id) stay unique. T8's "classId unchanged" check is weaker than originally assumed.
- Burst fires at `trigger_at − lead_s` (default 30s) — `DEFAULT_BURST_LEAD_S = 30`.
- **`startDate` from the API is naive (no offset).** Deviaties: fixed in commit `567b329` — naive `startDate` is now interpreted as target-tz-local (`_attach_or_convert` in `discovery.py`), mirroring `scheduler._applies_tz`. This was the root cause of `0 burst(s) scheduled` in the UTC-tz container.

## v1 Definition of Done — ✅ met

- `python -m booker --dry-run config.yaml` runs discovery → auth → plan without ever calling `Book`.
- Real target from `docs/course_catalog.md` is resolved and its burst scheduled at `bookingOpensOn`. ✅ (Wirbelsäulengym + Body Pump/Body Balance live on OpenShift)
- `booked_history.json` correctly blocks re-booking and prunes.
- Multi-account: one failed account never degrades the others. ✅ (per-account suspension in `Scheduler.authenticate_all`; covered by `test_auth.py`)
- Live round-trip Book→Unbook executed only with explicit user approval. ✅

---

## Phase 6 — Operationalize — ✅ implemented (originally a draft; all items done)

- **Containerize:** ✅ OCI image (`Containerfile`, non-root `USER 65534:0`, no hardcoded UID, restricted-v3/v2-compatible), multi-arch AMD64+ARM64. `PYTHONUNBUFFERED=1` added (log visibility in `oc logs`).
- **CI/CD:** ✅ build + push to **public GHCR** (`ghcr.io/crowdsalat/medi-bucher`).
  - **Registry:** GHCR, public.
  - **Tag scheme:** `latest` on each main commit; `sha-<git-sha>` per push; `<semver>` on git tags.
  - **Cleanup:** `sha-*` retained by `snok/container-retention-policy@v3.1.0` (2w/20 kept); `latest` + semver never cleaned.
  - **Image ref fix:** lowercase `ghcr.io/crowdsalat/medi-bucher` (uppercase `CrowdSalat` caused kubelet `InvalidImageName`).
- **OpenShift manifests:** ✅ Deployment (probes, resources, `runAsNonRoot`), PVC for history, SCC-restricted-v2-compatible.
  - **Config + creds:** single source of truth in **Infisical `MEDI_CONFIG`** (accounts, targets, creds inline) → `ExternalSecret` → Secret mounted at `/etc/booker/config.yaml`. No ConfigMap, no `MEDI_CREDS_*` env.
  - **Deployment model:** long-lived daemon (decided); `--once` supported for cron-style runs (documented in `deploy/README.md`).
  - **GitOps:** deployed via ArgoCD ApplicationSet `external-manifests` in `ocp-gitops`, namespace `app-medi-bucher`.
  - **Image pull:** public GHCR → anonymous pull.
- **"Maybe more":** leftover candidates from the original draft that remain **⬜ open**:
  - Standalone config-validation image/sidecar (not built).
  - Observability (metrics/log aggregation) beyond liveness probe (not built).

---

## Phase 6.5 — Post-deploy hardening (added after go-live) — ✅/⬜

- ✅ **Log visibility:** `PYTHONUNBUFFERED=1` in `Containerfile`.
- ✅ **Naive-`startDate` tz bug:** fixed (see v1 notes) — daemon now plans real bursts (`4 burst(s) scheduled` live).
- ✅ **SCC field pruning:** removed `fsGroup: 0`/seccomp/capabilities/`allowPrivilegeEscalation` — pod securityContext reduced to `runAsNonRoot: true`.
- ✅ **`MEDI_CONFIG` migration:** code + manifests merged; config/creds fully in Infisical, secret-mounted.
- ⬜ **Burst concurrency limiter:** parallel `Book` currently fans out one thread per due burst (via `asyncio.to_thread`, no cap). User concerned about hammering the API; proposed a global semaphore (~2–4 concurrent). **Not implemented.**
  - **Resolution (2026-09):** not needed — `Scheduler.plan` already dedupes to **one burst per account per course occurrence** (`(account, partitionDate, eventTypeId)` + `fired` set), so a release moment sends at most `accounts × courses-due` requests. Closed as "already bounded".
- ⬜ **`MEDI_CREDS_<name>_TOKEN` override:** exists only as an internal `token_file` param / `scripts/validate_session.py` (MW_TOKEN); never wired as an env override into the daemon path. Not needed operationally (Infisical supplies creds).

## v2 Feature Discussion (after Phase 6) — ⬜ open (nothing started)

Waiting-list join · `hasLayout:true` station resolution · web UI · cancellation monitoring · push notifications.