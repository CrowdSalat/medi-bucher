# Spike 2 Findings: Authentication & Anti-Bot Measures

> ⚠️ **Partially superseded by live auth testing (Spike 3, 2026-09-15):** the session is a **signed Bearer token** (`WWW-Authenticate: TechnogymBearer`) stored in sessionStorage under key `token`, sent as `Authorization: Bearer <token>`. No user-visible session cookie is required. Token expires within hours; renewal = fresh username+password login. See `spike3_findings.md` §5 and `api_reference.md` §3.2 for the corrected model.

## 1. Authentication Mechanism — Cookie-Based Session (original finding)

Authentication is **not** embedded-token based. The SPA uses `fetch(..., {credentials:"include"})`, i.e. server-set **cookies** carry the session across API calls.

### Login Endpoint (the one that matters)

**`POST https://core.mywellness.com/v2/enduser/authentication/login?_c=de-DE`**

```json
Header  Content-Type: application/json
Body    {"username":"<email>","password":"<password>","keepMeLoggedIn":false}
```

Observed request headers from a real browser login submit:
```
x-mwapps-appid: EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9
x-mwapps-client: enduserweb
x-mwapps-clientversion: 1.18.1-2105,enduserweb
origin: https://widgets.mywellness.com
referer: https://widgets.mywellness.com/
```

The `_c=de-DE` query param is the culture/locale and is optional.

### Session Restoration

**`POST https://services.mywellness.com/application/EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9/Me`**
- Returns the logged-in user context (`user.id` is required for the Book payload).
- Unauthenticated/expired → `{"errors":[{"field":"TokenNotValid","type":"Security","..."]}` — **this is the failure signal the agent must detect.**

### Session Lifespan & Refresh
Not measurable without live credentials. Observed mechanism:
- Login success → server sets session cookie.
- Expired/invalid → subsequent calls fail with `TokenNotValid` (or `401`).
- No explicit refresh endpoint observed; the agent should **re-login on `401`/`TokenNotValid`**.
- `keepMeLoggedIn:true` is available for longer-lived sessions.

> ⚠️ **Pending live validation:** exact cookie TTL requires a real account. The re-login-on-401 pattern keeps this dependency harmless.

---

## 2. Anti-Bot Assessment — Minimal Resistance

| Check | Result |
|-------|--------|
| Cloudflare / Turnstile | **None.** No `cf-ray`, no challenge pages. TLS cert from Amazon (AWS-hosted). |
| reCAPTCHA on login | **NOT enforced.** Browser capture shows login POST body contains only `username`/`password`/`keepMeLoggedIn`. Plain `curl` login returns clean `401` for bad creds — no challenge. |
| reCAPTCHA elsewhere | v3 (`6LeM5KQZ...`) is loaded, but **only on the Forgot Password page** and it submits a `recaptchaToken` parametire — not part of the booking flow. Badge is hidden via CSS. |
| Custom headers required | `X-MWAPPS-CHANNELID` not even sent by browser on schedule/search calls. `x-mwapps-appid`/`x-mwapps-client` are sent on auth calls (safe to replicate). |
| Rate limiting | None encountered during repeated probing. |

**Conclusion: no bot protection blocks a lightweight HTTP client.**

---

## 3. Auth Boundary Map (what requires session vs not)

| Endpoint | Auth |
|----------|------|
| `facility/detail`, `facility/search` | Public |
| `calendar/.../class/Search` (schedule) | Public |
| `core/.../authentication/login` | Anonymous POST (submits creds) |
| `calendar/.../class/Book`, `Unbook`, `GetParticipants` | **Cookie (session)** |
| `services/.../Me` (session restore) | **Cookie (session)** |

---

## 4. Architectural Decision: Option A — Pure API Client

| Factor | Finding |
|--------|---------|
| Schedule polling | Public JSON API, zero auth → **`httpx` GET** |
| Login | Single JSON POST, no CAPTCHA → **`httpx` POST** |
| Booking | POST with cookie session (httpx CookieJar persists it automatically) |
| Re-login handling | Detect `401`/`TokenNotValid` → re-auth with stored creds |

**Verdict: build the agent as a pure Python API client (`httpx`) with a cookie jar.**

Playwright is **not required** for operation. A headless browser may still be useful as a non-recommended fallback if the facility ever enables reCAPTCHA on login — but that is out of scope now.

### Recommended Auth Sequence for the Agent
1. `POST /v2/enduser/authentication/login` → server sets cookie in jar.
2. `POST services.../application/{appId}/Me` to verify session + get `userId`.
3. On any `401`/`TokenNotValid` during polling/booking → repeat step 1 (throttled).

---

## 5. Notes for Spike 3

- Book endpoint shape (pre-confirmed in Spike 1 bundle): `POST calendar/v2/enduser/class/Book` with `{"partitionDate":<int YYYYMMDD>,"userId":<uuid>,"classId":<calendar id>,"station":<int>}`.
- Booking failure modes to map in Spike 3: overlap rules, timing (before release), full slot, waiting-list takeover.
- `_c` (culture) query param is optional; `x-mwapps-appid`/`x-mwapps-client` headers should be replicated on auth + booking calls.