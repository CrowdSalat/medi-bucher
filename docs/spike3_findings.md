# Spike 3 Findings: Booking Mechanism & API Behavior

---

## 1. Payload Structure (confirmed from bundle + live probing)

### Book
```
POST https://calendar.mywellness.com/v2/enduser/class/Book
Content-Type: application/json
Authorization: Bearer <token>
```
```json
{
  "partitionDate": 20260915,  // int YYYYMMDD (from class/Search partitionDate)
  "userId":    "uuid",       // from GET services.../Me → user.id
  "classId":   "uuid",       // instance id from class/Search (.id field)
  "station":   0             // omittable — see station semantics below
}
```

### Unbook
```
POST https://calendar.mywellness.com/v2/enduser/class/Unbook
Content-Type: application/json
```
```json
{
  "partitionDate": 20260915,
  "userId":  "uuid",
  "classId": "uuid"
}
```
Response enum verified live (see §2.2).

### Remove from waiting list
```
POST https://core.mywellness.com/core/calendarevent/{calendarEventId}/RemoveFromWaitingList
```
```json
{"partitionDate": "YYYYMMDD", "userId": "uuid"}
```

---

## 2. Response Models (extracted from JS bundle)

### Book response — `{result, message}` (all HTTP 200)

| `result` value | Meaning | Verified live |
|---|---|---|
| `Booked` | Spot claimed successfully | ✅ A1 |
| `UserAlreadyBooked` | Already booked for this class — idempotent, no-op | ✅ B |
| `PlaceNotAvailable` | Place/station not bookable (see station semantics) OR taken during race | ✅ |
| `ToMuchParticipants` | Class at hard capacity (note: literal spelling from server) | (bundle only) |
| `UserAddedToWaitingList` | Class full, user placed on waiting list | (bundle only) |
| `Failed` | Generic failure | ✅ (bad payload, today) |
| `EventNotExists` | Class instance not found (Book returns the Unbook enum value too) | ✅ probe |
| `NoPermissionsForUserException` | User lacks required subscription/group; may also appear in `errors[0].field` as `"BookingApiException.NoPermissionsForUserException"` | (bundle only) |

### Unbook response — `{result, message}` (all HTTP 200)

| `result` | Meaning | Verified live |
|---|---|---|
| `UnBooked` | Successful cancel | ✅ A2 |
| `EventNotExists` | Not booked / class instance gone — safe no-op | ✅ C |
| `UserNotExists` | User not found | (bundle only) |
| `TooLate` | Past cancellation deadline (cancellationMinutesInAdvance) | (bundle only) |
| `BookingNotAvailable` | Booking context gone | (bundle only) |
| `Failed` | Generic failure | (bundle only) |
| `CanUnbook` | Allowed to cancel | (bundle only) |

### Waiting-list removal — single string value

| Value | Meaning |
|---|---|
| `Removed` | Removed from waiting list |
| `UserNotInWaitingList` | User wasn't on it |
| `Failed` | Generic failure |

---

## 3. Latency (measured from Europe, `curl -w`)

| Scenario | RTT | Notes |
|----------|-----|-------|
| Cold TCP + TLS handshake | ~160-180 ms | first request to new connection |
| Warm connection (keep-alive) | **~36-40 ms** | TCP+TLS reused, server processing only |
| Search (class/S) warm | ~160-180 ms | larger payload, but connection reused |

**Key insight for burst mode:** once a persistent `httpx.AsyncClient` with cookie-jar keep-alive is established, each Book request costs only **~35-40 ms**. Burst of 5 parallel Book requests ≈ 35-40 ms wall-clock (not 5×35).

---

## 4. Live Probes (authenticated Bearer token, 2026-09-15)

### 4.1 Validation-layer (HTTP 400) — happens BEFORE the result enum

| Request | Response |
|---|---|
| `Book` with `classId` = all-zero UUID | `400` `[{"field":"ClassId","errorMessage":"The ClassId field must have valid value"}]` |
| `Book` with `partitionDate` as string `"20260916"` | `400` `[{"field":"","errorMessage":"The supplied value is invalid."}]` (must be int) |
| `Book` on class whose window is not open | `400` `[{"field":"Error","errorMessage":"Booking has not opened, yet."}]` |
| `Book` with `userId` missing/garbage | `400` `[{"field":"$.userId","errorMessage":"The JSON value could not be converted to System.Guid..."}]` |

**Note:** early-bird racing → 400 `"Booking has not opened, yet."` — so burst mode must NOT hammer before `bookingOpensOn`, it's a hard server-side gate, not a passive race.

### 4.2 `station` semantics — CRITICAL

Same class (layout-less, `hasLayout:false`, 24 free places, 11 participants):

| `station` sent | Result |
|---|---|
| `0` (int) | `PlaceNotAvailable` — server interprets as "that place/station does not exist" |
| omitted | **`Booked`** ← correct for layout-less classes |
| `-1` or `1` (after booking) | `UserAlreadyBooked` |

- **Layout-less classes (`hasLayout:false`): omit `station` entirely.**
- Layout'd classes (cycling/rowing stations, `hasLayout:true`) need a valid station id → select from the class's `layout`/`places` data.
- Daemon rule: for targets with `hasLayout:false` → Book without `station`; for `hasLayout:true` → resolve a free station first (Spike item).

### 4.3 Round-trip verification (A/B/C, cleaned up — zero residue)

```
Book   → 200 {"result":"Booked"}
Book   → 200 {"result":"UserAlreadyBooked"}   (duplicate, idempotent)
Search → bookingUserStatus="CannotBook", isParticipant=true   (while booked)
Unbook → 200 {"result":"UnBooked"}
Unbook → 200 {"result":"EventNotExists"}      (not booked → safe no-op)
Search → bookingUserStatus="CanBook", isParticipant=false     (restored)
```

---

## 5. Authentication & Session Lifespan (verified live)

- Auth scheme: **Bearer token**. Protected endpoints return `WWW-Authenticate: TechnogymBearer` on 401; token sent as `Authorization: Bearer <token>` (a Cookie `token=...` also works — header is preferred).
- The widget stores it in **sessionStorage** under key `token` at `widgets.mywellness.com`.
- **Token format:** `base64(pipe-separated claims) + "." + 64-char hex (HMAC)`. Claims decoded (20 fields):
  `0 issued (YYYYMMDDHHMMSS local TZ) | 1 credentialId | 2 appId (no dashes) | 3 version "3" | 4 timezone "W. Europe Standard Time" | 5 culture "de-DE" | 6 userId (no dashes) | 7-9 "" | 10-16 1/1/0/1/""/""/0 | 17 opaque (changed between sessions: 7187→8182) | 18 "0" | 19 "com.mywellness5"`.
- **TTL/expiry (important):** the token dies within a few hours of issue. An expired token:
  - `POST services.../Me` → **HTTP 200** with body `{"errors":[{"field":"TokenNotValid",...}]}` — NOT a 401! The daemon must treat a 200-with-TokenNotValid as "logged out", never as success.
  - `POST class/Book`, `GET getssoauthtoken` → 401 (no renew possible with dead token).
- **Renewal path:** `GET /v2/enduser/account/getssoauthtoken` only works with a *live* token (401 otherwise; POST → 405). The only reliable renewal is a fresh **username+password login** → new token. This mandates the production config: email in config, password from env/secret, and a re-login-on-invalid-token loop in the daemon.
- **User ids resolved via `Me`:** userId/`user.id` = `b9ba037a-...`; `credentialId` = `497faaa8-...` (test account).

---

## 6. `class/Search` staleness — DO NOT gate on its status

Observed inconsistencies on the same day:
- 17–21 Sep classes reported `bookingUserStatus:CanBook` in one scan, yet `Book` answered `400 "Booking has not opened, yet."` minutes later, and a fresh scan showed `WaitingBookingOpens` again.
- Classes reported `CanBook` with 23–35 `availablePlaces` while `Book` returned `PlaceNotAvailable` for every attempt — root cause was the `station:0` payload (see §4.2), not availability.

**Rule for the daemon:**
- `bookingInfo.bookingOpensOn` is the authoritative, stable per-class release moment (class date − 2 days, 21:00 local); e.g. 17 Sep class → `2026-09-15T21:00:00+02:00`. Use it to schedule the burst.
- `class/Search` is for *discovery* (ids, names, opens-on, `hasLayout`). The **Book response is the only truth** for whether a spot was secured.
- Re-verify target ids right before the burst; treat `Search` status changes as advisory only.

---

## 7. HTTP Protocol Behaviors (probed live)

| Test | HTTP Code | Time |
|------|-----------|------|
| `POST /Book` empty body (no auth) | 401 | 130 ms |
| `POST /Book` malformed JSON (no auth) | 401 | 125 ms |
| `POST /Book` valid shape, no auth | 401 | 128 ms |
| `GET` / `PUT` / `PATCH` / `DELETE` on `/Book` | **405** | 128 ms |
| `OPTIONS` | 405 | 126 ms |
| Wrong HTTP method (PUT/PATCH) | 405 | 132 ms |

**Important:** the auth check (401) fires **before** body validation. This means:
- With a valid auth, malformed body → 400 (validation error)
- No/invalid auth → 401 always, regardless of payload

---

## 8. Edge-Case Behaviors (from bundle error-handling logic)

**The app's `onBook` handler reveals the real response mapping:**

```
Booked → success, publish + joinFacility
PlaceNotAvailable | ToMuchParticipants → error message "S102" (generic "full")
UserAlreadyBooked → treated as already bookable state
UserAddedToWaitingList → success (waiting list accepted)
Failed → error "S45" (generic error)
NoPermissionsForUserException → dedicated error (needs subscription)
```

**The app's `unbookClass` handler validates the cancellation window client-side:** if `cancellationMinutesInAdvance` is set it computes class-start − advance and refuses/errors before even calling the API. Server would return `TooLate` if it slipped through. Default observed: `cancellationMinutesInAdvance: 120` (2h before start).

### Remaining unknown edge cases (needs real conditions)

- **Overlap booking** (user already has a class at the same time): likely `Failed` with a specific `message`
- **Class just started / is over**: handled client-side, unknown server code
- **Penalty lockout** (3 strikes): server-side group-based block → may surface as `NoPermissionsForUserException`
- **Waiting-list join** (`UserAddedToWaitingList`) + `RemoveFromWaitingList` round trip: not yet tested live
- **`hasLayout:true` classes**: how to resolve a valid `station` id (layout endpoint, free-place listing)

---

## 9. Architectural Implications for the Daemon

### Auth/session design (updated)

1. **Token injection:** accept token via secret file/env (`Authorization: Bearer`), for spikes; production uses email+password → login → token.
2. **Re-login loop:** on `401` or `Me`-200-with-`TokenNotValid` → re-authenticate with username+password, retry once. Never cache cookies as auth source of truth.
3. **`Me` response check:** a **200 with `errors[].field == "TokenNotValid"` means logged out** — do not treat as success.

### Burst/spike strategy

1. **Pre-warm connection:** open `httpx.AsyncClient` with keep-alive pool; establish TLS once ~60-90 ms before burst time
2. **Pre-fetch target class IDs** from `class/Search` 5-10 minutes before `bookingOpensOn`
3. **At release time (21:00:00):** send parallel Book requests with the same payload (station omitted for layout-less)
4. **Race handling:** first `Booked` = win; `PlaceNotAvailable` = spot gone, retry next target or accept waiting list
5. **Warm RTT per request:** ~35-40 ms → effective burst rate limited by parallelism, not RTT
6. **Early-bird safety:** 400 `"Booking has not opened, yet."` if fired before the gate — schedule at gate, don't pre-hammer

### `bookingOpensOn` reliability

From `class/Search` response:
```json
"bookingInfo": {
  "bookingOpensOn": "2026-09-15T21:00:00+02:00",
  "bookingOpensOnMinutesInAdvance": 2190,
  "cancellationMinutesInAdvance": 120,
  "bookingHasWaitingList": true,
  "bookingUserStatus": "CanBook"   // advisory only — see §6
}
```
The datetime is explicit per class instance and is the authoritative release trigger (class date − 2 days @ 21:00 local). The daemon computes the exact trigger moment per target and schedules sub-second polling around it.

### Polling strategy (routine vs spike)

| Mode | Interval | Purpose |
|------|----------|---------|
| Routine | 10-15 min | Catch cancellations / unscheduled drops |
| Pre-spike | 30-60s starting 10 min before `bookingOpensOn` | Confirm schedule not changed |
| Burst | Parallel Book at release time | Claim spot |

---

## 10. Open Items

- Confirm waiting-list join/removal round trip live
- Confirm penalty-lockout error shape
- Resolve `station` for `hasLayout:true` classes (equipment/cycling)
- Confirm overlap-booking response shape
- Token TTL exact value (measured expiry between ~1.5h and ~4h post-issue)
