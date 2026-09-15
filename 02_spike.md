# Technical Spikes: MyWellness Booking Agent

This document outlines the preliminary investigations (spikes) required to finalize the technical design of the booking agent. The goal is to answer critical unknowns regarding the MyWellness platform's architecture before committing to a specific codebase.

---

## Spike 1: Schedule Data & Course Identification
**Intent:** Understand how the platform delivers schedule data to the client and determine the most reliable way to identify and track target courses across different weeks.

* **Questions to Answer:**
  * Is the schedule data delivered via a structured, accessible API (e.g., JSON/GraphQL) or rendered server-side in the HTML?
  * Do recurring classes share a persistent "parent" or "template" ID, or must we identify them using a composite key (Name + Day + Time)?
* **Expected Output:** A sample of the schedule data payload and a finalized strategy for how users will define target courses in the `config.yaml`.

## Spike 2: Authentication & Anti-Bot Measures
**Intent:** Determine if a lightweight, API-only execution path is viable by analyzing how the platform handles user sessions and whether it actively blocks scripted requests.

* **Questions to Answer:**
  * How are user sessions authenticated and maintained (e.g., standard Bearer tokens, JWTs, or session cookies)?
  * What is the lifespan of an authenticated session before it expires or requires a refresh?
  * Are there active anti-bot mechanisms (like Cloudflare Turnstile, CAPTCHAs, or encrypted JavaScript handshakes) that block standard HTTP libraries?
* **Expected Output:** A final architectural decision: proceed with a pure API client (`httpx`/`requests`) OR fall back to headless browser automation (`Playwright`).

## Spike 3: Booking Mechanism & API Behavior
**Intent:** Uncover the exact technical requirements to successfully execute a booking action and map out how the platform responds to successes and failures.

* **Questions to Answer:**
  * What is the exact payload structure and HTTP method required to claim a spot in a course?
  * What is the typical network latency for a booking request, and can it be optimized for the burst/spike mode?
  * How does the system respond to common edge cases (e.g., attempting to book a second before the release time, or requesting a slot that just filled up)?
* **Expected Output:** Documentation of the required booking request payload and a mapped list of API error responses so the agent can handle them gracefully.
