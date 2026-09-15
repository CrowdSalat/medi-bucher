# Requirements: MyWellness Course Booker

This document captures the functional goals, system behavior, and technical constraints for the automated course booking agent. The focus is on operational intent and flexibility rather than hardcoded execution details.

---

## 1. System Intent & Overview
The primary goal is to build a simple, automated Python daemon that monitors the Mediterana schedule on MyWellness and books targeted courses as soon as spots open, providing a speed advantage when competitive slots drop.

**Target Platform:** `https://widgets.mywellness.com/facility/mediterana/schedule/mediterana?joinAllowed=true&`

---

## 2. Core Functional Requirements

### FR-1: Flexible Target Course Matching
* **Intent:** Allow users to define target classes easily in a configuration file without breaking when course instance IDs change week-to-week.
* **Behavior:** The agent matches classes by `(Name + Day of Week + Time Slot)`. It resolves that triple to the persistent course-template id (`eventTypeId`, stable across weeks) during schedule discovery and uses the per-instance id only at booking time.
* **Schedule-change warning:** if a matched occurrence's actual weekday or time differs from the configured triple, the agent logs a warning and **skips booking that occurrence** rather than booking the wrong slot.

### FR-2: Deterministic Booking Window
* **Intent:** Maximize the chances of securing a spot at the known release moment while keeping the runtime simple.
* **Behavior:**
  * **Discovery Scan:** Periodically query the schedule (e.g., a few times daily) to resolve target course instances and their exact release times.
  * **Spike Mode:** Each target class reports its own authoritative release moment (`bookingInfo.bookingOpensOn`, i.e. class date − 2 days at 21:00 local). The agent schedules a parallel `Book` burst at exactly that instant, preceded by a single re-verification pass (~10 min before) to re-resolve the target's `classId` and detect schedule changes.

### FR-3: Single-Path Architecture
* **Intent:** Keep the codebase simple and maintainable by avoiding complex, dynamic fallback logic at runtime.
* **Behavior:** The agent executes through a single, deterministic pipeline (**Option A — API Engine**): direct HTTP requests using `httpx` with Bearer-token auth and keep-alive connections. Confirmed viable by spikes; no browser automation is needed.

### FR-4: Local State & Deduplication
* **Intent:** Ensure the agent never attempts to re-book a slot it has already secured or spam the platform with redundant request loops.
* **Behavior:** Maintain a simple local state file (`booked_history.json`) recording successfully booked dates and class instances.

---

## 3. Configuration & Operational Constraints

Configuration and runtime design live in [`03_design.md`](03_design.md).

---

## 4. Explicitly Out of Scope (First Iteration)
* External push notifications (e.g., WhatsApp, Telegram, or Email alerts).
* Automated warning alerts if an expected recurring course is missing after its launch window.
* **Cancellation monitoring:** freeing of spots via cancellations is handled outside the agent (facility emails the user); the daemon only books at known release windows.
