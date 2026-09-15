# Spike 1 Findings: Schedule Data & Course Identification

## 1. Data Delivery: Client-Side SPA, Clean REST API

MyWellness is a Vite-built React SPA. The widget HTML loads an `index-WQuZRR7E.js` bundle. All schedule data is delivered via **clean JSON REST APIs** — no SSR, no GraphQL, no scraping needed.

---

## 2. Schedule API — Fully Public (No Auth Required)

**Endpoint:** `GET calendar.mywellness.com/v2/enduser/class/Search`

### Query Parameters
| Parameter   | Example                       | Notes                                       |
|-------------|-------------------------------|---------------------------------------------|
| `facilityId`| `0273e18b-52bf-...` (required)| Mediterana's UUID                           |
| `fromDate`  | `20260915`                   | `YYYYMMDD` format                           |
| `toDate`    | `20260921`                   | `YYYYMMDD` format                           |

### Required Headers
| Header                  | Value                                     |
|-------------------------|-------------------------------------------|
| `X-MWAPPS-CHANNELID`   | `5bf51e0109ee93b5aef82c77` (static)      |
| `x-mwapps-tz-olson`    | `Europe/Berlin`                           |

### Sample Raw Response (per class item)
```json
{
  "id": "e96a4fc8-a5be-48a8-90ab-c35d770c0417",
  "name": "Wirbelsäulengym",
  "startDate": "2026-09-15T08:30:00",
  "endDate": "2026-09-15T09:30:00",
  "partitionDate": 20260915,
  "recurrenceStartDate": "2026-08-26T08:30:00",
  "recurrenceEndDate": "2028-07-31T09:30:00",
  "eventTypeId": "a3c6181c-7a77-434d-ac5c-96c58282288c",
  "maxParticipants": 35,
  "availablePlaces": 16,
  "numberOfParticipants": 19,
  "bookingInfo": {
    "bookingOpensOn": "2026-09-13T21:00:00+02:00",
    "bookingAvailable": true,
    "bookingUserStatus": "CanBook",
    "dayInAdvanceStartHour": 21,
    "dayInAdvanceStartMinutes": 0
  }
}
```

**No anti-bot protection observed** on schedule endpoint — direct `curl` calls succeed with only the channel ID header.

---

## 3. Course Identification Strategy (Answers Spike 1 Questions)

### Persistent Template ID Found: `eventTypeId`

Recurring classes share an `eventTypeId` across all instances. This is the ideal identifier for `config.yaml`:

```yaml
target_courses:
  - eventTypeId: "a3c6181c-7a77-434d-ac5c-96c58282288c"
    name: "Wirbelsäulengym"
```

### Fallback Composite Key
If `eventTypeId` is unavailable, match on: `name` + `startDate` time-of-day + `partitionDate` weekday.

### Instance-Level IDs
- `id` — changes every recurrence instance (do NOT use as stable identifier)
- `eventTypeId` — stable template ID (USE for targeting)
- `partitionDate` — date partition (YYYYMMDD integer)

---

## 4. Booking Policy (from `extendedSettings.mwc_class_settings`)

Mediterana class booking policy:
- **Booking window opens:** 2 days before class at 21:00
- **Cancellation deadline:** 2 hours before class
- **Penalties:** enabled — 3 strikes within 30 days → penalty
- **Waiting list:** enabled, `NotifyOnFreePlace` mode

---

## 5. Spike 1 Verdict

| Question | Answer |
|----------|--------|
| Is data API or HTML-rendered? | **JSON REST API**, no scraping needed |
| Persistent parent/template ID available? | **Yes — `eventTypeId`** |
| Recommended config strategy | Use `eventTypeId` as primary key; `name + day + time` as human-readable fallback |

### Mediterana Static Values
- `facilityId`: `0273e18b-52bf-404e-afa6-8bfb2eeccbad`
- `facilityUrl`: `mediterana`
- Channel ID: `5bf51e0109ee93b5aef82c77`

---

## 6. Implications for Spike 2 (Auth & Anti-Bot)

**Schedule access:** No auth required, no anti-bot detected on API.

**Booking:** Returns `401 Unauthorized` — authentication required.

**Auth flow (from JS bundle):**
- `core.mywellness.com/v2/enduser/authentication/ValidatePassword` — username/password auth
- `core.mywellness.com/v2/enduser/account/getssoauthtoken` — SSO token refresh
- Google reCAPTCHA v3 detected in bundle (`recaptcha/api.js?render=...`)

**Preliminary Spike 2 verdict:** A **hybrid approach** may be optimal:
- Schedule polling: **pure API** (`httpx`)
- Booking: likely needs reCAPTCHA solved → may require **Playwright** for the login/booking step only

This remains to be confirmed in Spike 2 testing (whether reCAPTCHA is enforced on the auth endpoint, or only on the web login form).
