# MyWellness API Reference (Researchers' Findings)

Compiled from Spike 1 + Spike 2 analysis: JS bundle `index-WQuZRR7E.js` reverse-engineering, live HTTP probing, and headless-browser network capture (2026-09-15).

**Verification legend:**
- ✅ **live** = successfully called and response inspected (via `curl` or Playwright)
- 🔍 **bundle** = found in the widget's JS bundle, not yet called directly
- ⚠️ **auth** = requires authentication (Bearer token, scheme `TechnogymBearer`; verified live 2026-09-15)

---

## 1. Base URLs & Fixed IDs

| Constant | Value |
|----------|-------|
| `apiUrl` (services) | `https://services.mywellness.com` |
| `apiUrlCore` | `https://core.mywellness.com` |
| `apiUrlCalendar` | `https://calendar.mywellness.com` |
| `apiUrlBooking` | `https://booking.mywellness.com` |
| `apiUrlCms` | `https://cms.mywellness.com` |
| `apiUrlPay` | `https://pay.mywellness.com` |
| `apiUrlWorkout` | `https://workout.mywellness.com` |
| `apiUrl` others | `ud`, `assessment`, `achievement`, `run`, `meet`, `ioteam`, `ai-api` |
| Mediterana `facilityId` | `0273e18b-52bf-404e-afa6-8bfb2eeccbad` |
| Mediterana `facilityUrl` | `mediterana` |
| Widget `apiAppId` | `EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9` |
| Channel ID constant | `5bf51e0109ee93b5aef82c77` |
| API version header | `api-supported-versions: 2.0` |

### Common request headers (observed)
```
x-mwapps-appid: EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9
x-mwapps-client: enduserweb
x-mwapps-clientversion: 1.18.1-2105,enduserweb
content-type: application/json            (on POST)
origin / referer: https://widgets.mywellness.com/
```
Optional / circumstantial: `X-MWAPPS-CHANNELID`, `x-mwapps-tz-olson` (`Europe/Berlin`), `x-mwapps-facilityid`. Schedule GET worked *without* the channel ID.

---

## 2. Schedule & Calendar (`calendar.mywellness.com`)

### 2.1 Class schedule search — ✅ live, public
```
GET /v2/enduser/class/Search
```
| Param | Example | Notes |
|-------|---------|-------|
| `facilityId` | `0273e18b-...` | required (`"FacilityId cannot be null"` if missing) |
| `fromDate` | `2026-09-15` | ISO `YYYY-MM-DD` (browser) or `YYYYMMDD` also accepted |
| `toDate` | `2026-09-15` | same format |
| `eventTypes` | `Class` | filter to classes (browser sends this) |

**Response:** JSON array, one object per class instance. Key fields:
```json
{
  "id": "e96a4fc8-...",                 // class instance (changes weekly)
  "name": "Wirbelsäulengym",
  "startDate": "2026-09-15T08:30:00",
  "endDate": "2026-09-15T09:30:00",
  "partitionDate": 20260915,            // int YYYYMMDD
  "recurrenceStartDate": "2026-08-26T08:30:00",
  "recurrenceEndDate": "2028-07-31T09:30:00",
  "eventTypeId": "a3c6181c-...",        // STABLE template ID -> course targeting
  "room": "Kursraum 1", "roomId": "59b92ac5...",
  "staffId", "staffUserId", "assignedTo", "facilityId",
  "maxParticipants": 35,
  "availablePlaces": 16,                // free seats now
  "numberOfParticipants": 19,
  "isParticipant": false,               // true if logged-in user booked
  "isInWaitingList": false,
  "waitingListPosition": 0,
  "bookingInfo": {
    "bookingOpensOn": "2026-09-13T21:00:00+02:00",  // exact release time
    "bookingOpensOnMinutesInAdvance": 2130,
    "cancellationMinutesInAdvance": 120,
    "bookingHasWaitingList": true,
    "bookingUserStatus": "CanBook",      // CanBook / full / waiting-list states
    "bookingAvailable": true,
    "dayInAdvanceStartHour": 21,  "dayInAdvanceStartMinutes": 0
  },
  "actualizedStartDateTime": "2026-09-15T08:30:00"
}
```

### 2.2 Public calendar events (campaigns/RunX) — 🔍 bundle
```
GET /v2/public/calendarevents?campaignId=&facilityId=&campaignType=&fromDate=&toDate=
```
`fromDate`/`toDate` format `YYYYMMDD`. Returns lightweight items (`id`, `name`, `startDate`, `endDate`, `recurrenceStart/EndDate`, `eventTypeId`, `calendarEventType`, `facilityId`). Used by the app for qualification-day checks — **not** the main schedule.

### 2.3 Participants — ⚠️ auth, 🔍 bundle
```
GET /v2/enduser/class/GetParticipants?date=YYYY-MM-DD&classId={calendarId}
```
Yes: `GET` with `date` + `classId` query params and JSON body `{date, classId}`. Returns participant list (`user`, `joinedOn`, `station`, `bookedEquipment`). Returns `401` unauthenticated.

---

## 3. Authentication (`core.mywellness.com` + `services.mywellness.com`)

### 3.1 Login — ✅ live (dummy creds, 401 on invalid), anonymous
```
POST /v2/enduser/authentication/login?_c=de-DE
Body: {"username":"<email>","password":"<password>","keepMeLoggedIn":false}
```
- No CAPTCHA, no reCAPTCHA token required (verified in live browser capture).
- Valid creds → auth token (Bearer style). Login returns user context; the app stores the signed token in sessionStorage. The service sets no readable session cookie by itself.
- `_c=de-DE` culture param optional.

### 3.2 Session restore / user context — ✅ live, ⚠️ auth
```
POST /application/{apiAppId}/Me          (services.mywellness.com)
Body: {}
```
- Returns user context incl. `id` (needed for Book payload) — **directly `{data:{...}}`** (no `userContext` wrapper).
- **Auth is a Bearer token** (`Authorization: Bearer <token>`; scheme advertised as `WWW-Authenticate: TechnogymBearer`). A Cookie `token=<value>` is also accepted, but the header is canonical.
- Token lives in the widget's **sessionStorage** under key `token` (signed custom format: `base64(pipe-claims).hex-HMAC`). Claims incl. issued-timestamp, credentialId, appId, tz, culture, userId.
- **Expired vs valid:** expired token → **HTTP 200** with `{"errors":[{"field":"TokenNotValid","type":"Security",...}]}` — treat a 200-with-TokenNotValid as logged out. Protected class endpoints return plain 401 in that case.
- **Renewal:** `GET /v2/enduser/account/getssoauthtoken` needs a *live* token (401 when dead); daemon must re-login with username+password instead.

### 3.3 Login status — 🔍 bundle
```
POST /application/{apiAppId}/GetLoginStatus
```

### 3.4 Access-code login (OAuth) — 🔍 bundle
```
POST /application/{apiAppId}/LoginByAccessCode
Body: {"apiKey":"...","accessToken":"..."}
GET  /application/{apiAppId}/LoginByAccessCode?accessToken=...&req-x-mwapps-client=&apiKey=110768EB-63B3-441B-9962-17FF624BB88D&_retUrl=...
```

### 3.5 SSO token — 🔍 bundle
```
GET /v2/enduser/account/getssoauthtoken
```

### 3.6 Password validation (strength check) — 🔍 bundle, anonymous
```
POST /v2/enduser/authentication/ValidatePassword
Body: {"password":"..."}
Response: {"isValid":bool,"message","shortMessage","strength"}
```

### 3.7 Forgot / change password — 🔍 bundle (NOT part of agent flow)
```
POST /application/{apiAppId}/ForgotPassword
Body: {"username","captchaCode","captchaText","recaptchaToken"}   <- reCAPTCHA v3 here only
POST /application/{apiAppId}/ChangePassword
Body: {"email","oldPassword","newPassword"}
GET  /application/{apiAppId}/CaptchaImage
```

---

## 4. Booking (`calendar.mywellness.com`) — require Bearer token

### 4.1 Book a class — ✅ live (round-trip verified, cleaned up)
```
POST /v2/enduser/class/Book
Content-Type: application/json
Authorization: Bearer <token>
```
```json
{
  "partitionDate": 20260915,  // int YYYYMMDD (from class/Search → partitionDate)
  "userId":    "uuid",       // from services.../Me → data.id
  "classId":   "uuid",       // instance id from class/Search → .id (changes weekly)
  "station":   ...           // OPTIONAL — see rule below
}
```
**`station` rule (verified live):** for classes with `hasLayout:false` **omit `station`** — sending `0` makes the server return `PlaceNotAvailable` ("place does not exist"). For `hasLayout:true` (cycling etc.) a valid station/place id must be resolved first (open item).
**Response (HTTP 200):**
```json
{"result": "<enum>", "message": "optional context"}
```

| `result` value | Meaning | Verified | Agent action |
|---|---|---|---|
| `Booked` | Spot claimed | ✅ | Done (record in booked_history.json) |
| `UserAlreadyBooked` | Already booked — idempotent no-op | ✅ | Treat as success (dedup) |
| `PlaceNotAvailable` | Place/station not bookable (wrong payload) OR taken by another user | ✅ | Retry next target / accept waiting list |
| `ToMuchParticipants` | Class at hard capacity (server spelling) | bundle | Same as PlaceNotAvailable |
| `UserAddedToWaitingList` | Class full, placed on waiting list | bundle | Success with delay |
| `Failed` | Generic failure | ✅ | Retry / log error |
| `EventNotExists` | Class instance not on schedule | ✅ | Skip/re-trigger discovery |
| `NoPermissionsForUserException` | User lacks subscription/group access | bundle | Fatal for this class type |

`NoPermissionsForUserException` may also arrive as a structured error:
```json
{"errors":[{"field":"BookingApiException.NoPermissionsForUserException",...}], "result": "..." }
```
**Hard gate:** before `bookingOpensOn` the server returns `400 {"field":"Error","errorMessage":"Booking has not opened, yet."}` — schedule burst AT the gate, don't pre-hammer.

### 4.2 Unbook — ✅ live (round-trip verified)
```
POST /v2/enduser/class/Unbook
Content-Type: application/json
```
```json
{
  "partitionDate": 20260915,  // int YYYYMMDD
  "userId":  "uuid",
  "classId": "uuid"
}
```
**Response (HTTP 200):** `{result, message}`

| `result` | Meaning | Verified |
|---|---|---|
| `UnBooked` | Successful cancel | ✅ |
| `EventNotExists` | Not booked / instance gone — safe no-op | ✅ |
| `UserNotExists` | User not found | bundle |
| `TooLate` | Past cancellation deadline (cancellationMinutesInAdvance) | bundle |
| `BookingNotAvailable` | Booking context invalid | bundle |
| `Failed` | Generic failure | bundle |
| `CanUnbook` | Confirmed cancel allowed | bundle |

### 4.3 Remove from waiting list — 🔍 bundle
```
POST https://core.mywellness.com/core/calendarevent/{calendarEventId}/RemoveFromWaitingList
Content-Type: application/json
```
```json
{"partitionDate": "YYYYMMDD", "userId": "uuid"}
```
Response: plain string `"Removed"` | `"UserNotInWaitingList"` | `"Failed"`.

### 4.4 Promotion board (room placement) — 🔍 bundle
```
GET {apiUrlBooking}/public/calendarevents/{calendarId}/bookings/{userId}/room/{roomId}
```

---

## 5. Facility (`core.mywellness.com`) — public

| Endpoint | Verified | Notes |
|----------|----------|-------|
| `GET /v2/enduser/facility/detail?facilityUrl=mediterana` | ✅ | full facility profile incl. `mwc_class_settings` (booking policy), `extendedSettings` |
| `GET /v2/enduser/facility/search?domain=com.mywellness&to=250` | ✅ | facility listings (add `lat`/`lng`, `chainId`, `licenceId`) |
| `GET /v2/enduser/facility/GetPrivacyPolicy?id={facilityId}` | ✅ | privacy text |
| `GET /v2/enduser/facility/GetLiabilityDisclaimer` | 🔍 | |
| `GET /v2/enduser/facility/GetPictureUri` | 🔍 | |
| `GET /v2/enduser/facilityuser/Detail?FacilityId={id}` | 🔍 ⚠️ | |
| `GET /v2/enduser/staff/SearchStaff?facilityId=&roleIds=` | 🔍 | |
| `GET /v2/enduser/user/HasLiabilitySigned` | 🔍 ⚠️ | |
| `POST /v2/enduser/user/SignLiability` | 🔍 ⚠️ | |

---

## 6. User & Account (`services.mywellness.com`) — 🔍, mostly ⚠️ auth

| Endpoint | Notes |
|----------|-------|
| `POST /core/user/{userId}/GetFacilityUserData` | body `{"facilityId"}` |
| `POST /core/user/{userId}/PenaltyStatus` | body `{"facilityId"}`; returns `{eventBooking:{status,availableStrikes}}` |
| `POST /core/user/{userId}/GetUsersThatCanBeMySelf` | |
| `POST /core/user/{userId}/MergeUserByUser` | |
| `POST /core/user/{userId}/JoinFacility` / `UnJoinFacility` | |
| `POST /core/user/{userId}/SaveFacilityUserPrivacySettings` | |
| `POST /application/{apiAppId}/Lists` | |
| `POST /application/{apiAppId}/FacilityPublicProfile` | |

---

## 7. Booking-Policy Data (from facility `extendedSettings.mwc_class_settings`)

Mediterana live values:
```json
{
  "booking": {
    "timeInAdvanceType": "Days", "timeInAdvanceValue": "2",
    "dayInAdvanceStartHour": 21, "dayInAdvanceStartMinutes": 0,
    "cancellationTimeInAdvanceType": "Hours", "cancellationTimeInAdvanceValue": "2",
    "checkUserOverlappingBookings": "false",
    "earlyBooking": {"enabled": false},
    "waitingList": true, "waitingListMode": "NotifyOnFreePlace"
  },
  "penalties": {"enabled": true, "strikesToGetPenalty": 3,
                "strikeDaysEvaluation": 30, "automaticExpiration": true}
}
```
Practical meaning: bookings open **2 days ahead at 21:00** local; cancel up to **2h** before; no-shows accrue strikes.

---

## 8. HTTP Protocol & Error Semantics (all verified live)

### Auth-gated endpoints (Book/Unbook/etc.) — observed 401 behavior
| Test | HTTP Code | Latency (cold) |
|------|-----------|----------------|
| Empty body | 401 | 130 ms |
| Malformed JSON | 401 | 125 ms |
| Valid shape, no auth | 401 | 128 ms |
| `GET`/`PUT`/`PATCH`/`DELETE` on `/Book` | **405** | ~128 ms |
| `OPTIONS` on `/Book` | **405** | 126 ms |

Auth check fires **before** body validation: no/invalid auth → 401 regardless of payload.

### Other HTTP codes
| Code | Meaning | Source |
|------|---------|--------|
| `400` | Missing/validation (e.g. `"FacilityId cannot be null"`, `"Booking has not opened, yet."`) | live |
| `401` | Unauthenticated/expired token → triggers re-login | live |
| `403` | Rejected on some public endpoints (empty body; retry with channel header) | live |
| `{field:"TokenNotValid", type:"Security"}` | Expired token on `/Me` — still HTTP 200, treat as logged-out | live |

### Latency (from Europe, measured with `curl -w`)
| Scenario | RTT | Notes |
|----------|-----|-------|
| Cold TCP+TLS (first request) | ~160-180 ms | new connection to `calendar.mywellness.com` |
| Warm connection (keep-alive) | **~36-40 ms** | same TCP+TLS session reused |
| class/Search (warm) | ~160-180 ms | larger payload; connection reused |

Keep-alive is the key optimization for burst mode: once `httpx.AsyncClient` opens a connection, each subsequent Book request costs only ~35-40 ms.

---

## 9+. Official Partner API (future reference only)

<https://apidocs.mywellness.com/#restful-api>

Technogym's formal developer docs (Server-to-Server / OAuth2 / Asset API + webhooks). **No user-booking endpoint exists there** — partners only receive Booked/Unbooked/Changed webhooks. Requires partner onboarding (ApiKey + license). `calendar_event_id` + `calendar_event_partition_date` payload there confirm the `classId`/`partitionDate` semantics of our `Book` call.

---

## 9. Not Yet Mapped (Spike 3 leftovers)
- Exact waiting-list join (`UserAddedToWaitingList`) + `RemoveFromWaitingList` round trip (needs a genuinely full class).
- `station` resolution for `hasLayout:true` classes (equipment/cycling stations).
- Overlap-booking and penalty-lockout response shapes.
- Exact Bearer token TTL (observed expiry between ~1.5h and ~4h post-issue).