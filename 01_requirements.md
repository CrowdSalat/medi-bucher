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
* **Behavior:** The agent must match classes using a robust identifier strategy—preferring persistent parent/template IDs if available, or falling back to a combination of course attributes (e.g., Name + Day of Week + Time Slot).

### FR-2: Deterministic Booking Window
* **Intent:** Maximize the chances of securing a spot at the known release moment while keeping the runtime simple.
* **Behavior:**
  * **Discovery Scan:** Periodically query the schedule (e.g., a few times daily) to resolve target course instances and their exact release times.
  * **Spike Mode:** Each target class reports its own authoritative release moment (`bookingInfo.bookingOpensOn`, i.e. class date − 2 days at 21:00 local). The agent schedules a parallel `Book` burst at exactly that instant, preceded by a single re-verification pass (~10 min before) to re-resolve the target's `classId` and detect schedule changes.

### FR-3: Single-Path Architecture
* **Intent:** Keep the codebase simple and maintainable by avoiding complex, dynamic fallback logic at runtime.
* **Behavior:** The agent will execute through a single, deterministic pipeline chosen during technical spikes:
  * **Option A (API Engine):** Direct, lightweight HTTP requests if the REST API is accessible and unblocked.
  * **Option B (Browser Automation):** Headless browser execution (Playwright) if bot protection or session management requires a full browser context.

### FR-4: Local State & Deduplication
* **Intent:** Ensure the agent never attempts to re-book a slot it has already secured or spam the platform with redundant request loops.
* **Behavior:** Maintain a simple local state file (`booked_history.json`) recording successfully booked dates and class instances.

---

## 3. Configuration & Operational Constraints
* **Configuration:** All target preferences, user credentials, schedule timings, and mode settings must be declared in a simple `config.yaml`.
* **Runtime:** Python 3.10+.
* **Environment:** Designed to run continuously as a daemon process or scheduled service.

---

## 4. Explicitly Out of Scope (First Iteration)
* External push notifications (e.g., WhatsApp, Telegram, or Email alerts).
* Automated warning alerts if an expected recurring course is missing after its launch window.
* **Cancellation monitoring:** freeing of spots via cancellations is handled outside the agent (facility emails the user); the daemon only books at known release windows.
