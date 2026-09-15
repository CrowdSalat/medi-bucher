# Design: MyWellness Course Booker

## 1. Runtime & Operational Constraints

* **Runtime:** Python 3.10+.
* **Environment:** Designed to run continuously as a daemon process or scheduled service.
* **Deployment model:** single daemon can serve multiple user accounts if the added complexity is acceptable; otherwise deploy one instance per person.

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
- At each discovery pass the daemon compares the matched occurrence's actual start (weekday + time) against the configured `(day, time)`. Mismatch → log a warning to stderr/log and **skip booking that occurrence** (fixed courses stay fixed; never silently book the wrong slot).
```

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

## 3. Alternative UX Flows (brainstorm)

> *To be discussed — ideas below are for consideration, not yet decided.*

**A. Interactive login mask (stretch goal)**
First-run: if `MEDI_CREDS_<name>_PW` is not set, prompt for email + password, perform login, store the resulting session token locally (encrypted or plain in a local `.tokens/` dir). Subsequent runs skip login. Good for interactive local dev; not suitable for headless K8s.

**B. Single-token override**
For quick one-off runs or debugging, accept a pre-existing token via env var (`MEDI_CREDS_<name>_TOKEN`) and skip login entirely. Useful for ad-hoc use or to avoid re-entering password. Must be refreshed manually when expired.

**C. Config-file-only (no env)**
Put credentials in a separate YAML/JSON file (e.g. `secrets/<name>.yaml`, gitignored) and mount/decrypt it at runtime. Simpler mental model but needs secret management; env vars are more K8s-native.

**D. Web UI / Telegram bot**
Longer-term: a minimal web frontend or bot where users register and manage their targets interactively. The daemon reads from a shared backend store. Only if maintainability is justified.
