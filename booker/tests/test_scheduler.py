import asyncio
import datetime as dt
import os
import tempfile
import unittest
from zoneinfo import ZoneInfo

from booker.client import BookingInfo, ScheduleInstance
from booker.config import Target
from booker.discovery import resolve_targets
from booker.scheduler import (
    STATUS_ELIGIBLE,
    STATUS_SKIP_ALREADY_BOOKED,
    STATUS_SKIP_AUTH,
    STATUS_SKIP_HISTORY,
    STATUS_SKIP_INSTANCE_GONE,
    STATUS_SKIP_OPENS_SHIFTED,
    STATUS_SKIP_SCHEDULE_CHANGE,
    PlannedBurst,
    Scheduler,
    format_burst_line,
    format_verify_line,
    verify_burst,
    verify_bursts,
)
from booker.state import AuthStatus, BookedHistory

UTC = ZoneInfo("UTC")


def make_instance(
    class_id: str,
    name: str,
    start: dt.datetime,
    partition: int,
    opens: dt.datetime,
    *,
    is_participant: bool = False,
    status: str = "CanBook",
    event_type_id: str = "evt-1",
) -> ScheduleInstance:
    return ScheduleInstance(
        id=class_id,
        name=name,
        event_type_id=event_type_id,
        start_date=start,
        partition_date=partition,
        has_layout=False,
        booking_info=BookingInfo(booking_opens_on=opens, booking_user_status=status),
        is_participant=is_participant,
    )


class FakeClock:
    def __init__(self, start: dt.datetime) -> None:
        self.val = start

    def now(self) -> dt.datetime:
        return self.val

    async def sleep(self, seconds: float) -> None:
        self.val += dt.timedelta(seconds=seconds)
        await asyncio.sleep(0)


class FakeSchedule:
    def __init__(self, instances: list[ScheduleInstance]) -> None:
        self.instances = instances
        self.calls = 0

    def fetch(self, facility_id: str, from_date: dt.date, to_date: dt.date):
        self.calls += 1
        return self.instances

    def close(self) -> None:
        pass


class FakeAuth:
    def __init__(self, name: str) -> None:
        self.name = name
        self.user_id = f"u-{name}"
        self.refreshes = 0

    def authenticate(self) -> None:
        pass

    def refresh(self) -> None:
        self.refreshes += 1

    def close(self) -> None:
        pass


FUTURE_OPENS = dt.datetime(2026, 9, 19, 21, 0, tzinfo=UTC)
PAST_OPENS = dt.datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def wsg_target(priority: int = 1) -> Target:
    return Target(name="WSG", day=1, time="09:00", priority=priority)


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = Scheduler(
            facility_id="f",
            account_targets={"a": [wsg_target()]},
            account_tz={"a": "UTC"},
            now_fn=lambda: NOW,
        )

    def _resolved(self, targets, instances):
        return resolve_targets(instances, targets, UTC)

    def test_plan_keeps_only_future_opens_and_sorts(self):
        inst_future = make_instance(
            "id-future", "WSG",
            dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC), 20260921, FUTURE_OPENS,
        )
        inst_past = make_instance(
            "id-past", "WSG",
            dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC), 20260928, PAST_OPENS,
        )
        plan = self.scheduler.plan({"a": self._resolved([wsg_target()], [inst_future, inst_past])})
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].instance_id, "id-future")
        self.assertEqual(plan[0].trigger_at, FUTURE_OPENS)

        later_opens = dt.datetime(2026, 9, 26, 21, 0, tzinfo=UTC)
        inst_later = make_instance(
            "id-later", "WSG",
            dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC), 20260928, later_opens,
        )
        plan = self.scheduler.plan(
            {"a": self._resolved([wsg_target()], [inst_future, inst_later])}
        )
        self.assertEqual([b.trigger_at for b in plan], [FUTURE_OPENS, later_opens])

    def test_plan_dedupes_same_instance_keeping_higher_priority(self):
        inst_future = make_instance(
            "id-future", "WSG",
            dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC), 20260921, FUTURE_OPENS,
        )
        targets = [wsg_target(priority=2), wsg_target(priority=1)]
        plan = self.scheduler.plan({"a": self._resolved(targets, [inst_future])})
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].target.priority, 1)
        self.assertEqual(plan[0].instance_id, "id-future")

    def test_plan_empty_when_all_triggers_past(self):
        sched = Scheduler(
            facility_id="f",
            account_targets={"a": [wsg_target()]},
            account_tz={"a": "UTC"},
            now_fn=lambda: NOW,
        )
        inst_past = make_instance(
            "id-past", "WSG",
            dt.datetime(2026, 9, 28, 9, 0, tzinfo=UTC), 20260928, PAST_OPENS,
        )
        self.assertEqual(sched.plan({"a": self._resolved([wsg_target()], [inst_past])}), [])

    def test_plan_skips_missing_booking_opens(self):
        inst_no_opens = make_instance(
            "id-noopens", "WSG",
            dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC), 20260921, None,
        )
        self.assertEqual(self.scheduler.plan({"a": self._resolved([wsg_target()], [inst_no_opens])}), [])

    def test_resolve_matches_naive_start_date_in_account_tz(self):
        berlin = ZoneInfo("Europe/Berlin")
        target = Target(name="WSG", day=1, time="19:00")
        inst = make_instance(
            "id-19", "WSG",
            dt.datetime(2026, 9, 21, 19, 0),  # naive, facility-local wall time
            20260921, FUTURE_OPENS,
        )
        plan = self.scheduler.plan(
            {"a": resolve_targets([inst], [target], berlin)}
        )
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].instance_id, "id-19")


class VerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.burst = PlannedBurst(
            account_name="a",
            target=wsg_target(),
            instance_id="id-future",
            partition_date=20260921,
            trigger_at=FUTURE_OPENS,
            fire_toward=dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
        )

    def _inst(self, **overrides) -> ScheduleInstance:
        kwargs = dict(
            class_id="id-future",
            name="WSG",
            start=dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
            partition=20260921,
            opens=FUTURE_OPENS,
        )
        kwargs.update(overrides)
        return make_instance(
            kwargs["class_id"],
            kwargs["name"],
            kwargs["start"],
            kwargs["partition"],
            kwargs["opens"],
            is_participant=kwargs.get("is_participant", False),
            status=kwargs.get("status", "CanBook"),
        )

    def test_eligible(self):
        verify_burst(self.burst, [self._inst()], UTC)
        self.assertEqual(self.burst.status, STATUS_ELIGIBLE)
        self.assertEqual(self.burst.reason, "")

    def test_instance_gone(self):
        verify_burst(self.burst, [], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_INSTANCE_GONE)
        self.assertIn("no longer present", self.burst.reason)

    def test_classid_changed(self):
        verify_burst(self.burst, [self._inst(class_id="replaced")], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_INSTANCE_GONE)
        self.assertIn("classId changed", self.burst.reason)

    def test_opens_shifted(self):
        shifted = dt.datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
        verify_burst(self.burst, [self._inst(opens=shifted)], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_OPENS_SHIFTED)
        self.assertIn("bookingOpensOn changed", self.burst.reason)

    def test_opens_no_longer_available(self):
        verify_burst(self.burst, [self._inst(opens=None)], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_OPENS_SHIFTED)

    def test_already_participant(self):
        verify_burst(self.burst, [self._inst(is_participant=True)], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_ALREADY_BOOKED)

    def test_already_booked_by_status(self):
        verify_burst(self.burst, [self._inst(status="Booked")], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_ALREADY_BOOKED)

    def test_schedule_change_time_moved(self):
        moved = self._inst(start=dt.datetime(2026, 9, 21, 10, 0, tzinfo=UTC))
        verify_burst(self.burst, [moved], UTC)
        self.assertEqual(self.burst.status, STATUS_SKIP_SCHEDULE_CHANGE)
        self.assertIn("schedule change", self.burst.reason)

    def test_history_presence_blocks_burst(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = BookedHistory(os.path.join(tmp, "booked_history.json"))
            history.add("a", 20260921, "id-future")
            verify_burst(self.burst, [self._inst()], UTC, history=history)
        self.assertEqual(self.burst.status, STATUS_SKIP_HISTORY)
        self.assertIn("booked_history", self.burst.reason)

    def test_verify_bursts_with_history(self):
        history = BookedHistory("/nonexistent/x.json")
        history.add("a", 20260921, "id-future")
        self._inst()
        verify_bursts([self.burst], {"a": [self._inst()]}, {"a": "UTC"}, history)
        self.assertEqual(self.burst.status, STATUS_SKIP_HISTORY)

    def test_verify_bursts_mixed(self):
        b1 = PlannedBurst(
            account_name="a", target=wsg_target(), instance_id="id-ok",
            partition_date=20260921, trigger_at=FUTURE_OPENS,
            fire_toward=dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
        )
        b2 = PlannedBurst(
            account_name="b", target=wsg_target(), instance_id="id-gone",
            partition_date=20260921, trigger_at=FUTURE_OPENS,
            fire_toward=dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
        )
        instances_by_account = {
            "a": [make_instance("id-ok", "WSG", dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC), 20260921, FUTURE_OPENS)],
            "b": [],
        }
        verify_bursts([b1, b2], instances_by_account, {"a": "UTC", "b": "UTC"})
        self.assertEqual(b1.status, STATUS_ELIGIBLE)
        self.assertEqual(b2.status, STATUS_SKIP_INSTANCE_GONE)

    def test_format_lines(self):
        self.burst.status = STATUS_ELIGIBLE
        self.assertIn("ACCOUNT=a", format_burst_line(self.burst))
        self.assertIn("STATUS=eligible", format_burst_line(self.burst))
        self.assertIn("AUTH=unknown", format_burst_line(self.burst))
        self.assertEqual(
            format_verify_line(self.burst),
            f"VERIFY {self.burst.instance_id} OK AUTH=unknown",
        )

        self.burst.status = STATUS_SKIP_INSTANCE_GONE
        self.burst.reason = "class instance no longer present in schedule data"
        self.assertIn("STATUS=skip_instance_gone", format_burst_line(self.burst))
        self.assertIn("AUTH=unknown", format_burst_line(self.burst))
        self.assertEqual(
            format_verify_line(self.burst),
            f"VERIFY {self.burst.instance_id} FAIL: skip_instance_gone "
            "AUTH=unknown (class instance no longer present in schedule data)",
        )


class DaemonTests(unittest.TestCase):
    def test_run_daemon_wakes_and_fires_at_lead(self):
        opens = dt.datetime(2026, 9, 19, 21, 0, tzinfo=UTC)
        start = dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
        clock = FakeClock(dt.datetime(2026, 9, 19, 20, 0, tzinfo=UTC))
        inst = make_instance("id-future", "WSG", start, 20260921, opens)
        fired = []

        async def on_burst(account_name, burst):
            fired.append((account_name, burst.instance_id, burst.trigger_at))

        scheduler = Scheduler(
            facility_id="f",
            account_targets={"a": [wsg_target()]},
            account_tz={"a": "UTC"},
            schedule_client_factory=lambda: FakeSchedule([inst]),
            auth_client_factory=lambda name: FakeAuth(name),
            now_fn=clock.now,
            sleep_fn=clock.sleep,
            on_burst=on_burst,
            discovery_cadence_s=6 * 3600,
            burst_lead_s=30,
            preverify_window_s=600,
        )

        async def run() -> None:
            task = asyncio.ensure_future(scheduler.run_daemon())

            async def wait_fired() -> None:
                for _ in range(10000):
                    if fired:
                        return
                    await asyncio.sleep(0)
                raise AssertionError("burst never fired")

            await asyncio.wait_for(wait_fired(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        scheduler.close()
        self.assertEqual(len(fired), 1)
        account, class_id, trigger = fired[0]
        self.assertEqual(account, "a")
        self.assertEqual(class_id, "id-future")
        self.assertEqual(trigger, opens)
        self.assertEqual(clock.now(), opens - dt.timedelta(seconds=30))


class AuthStateTests(unittest.TestCase):
    def _scheduler(self, auth_factory, clock=None):
        return Scheduler(
            facility_id="f",
            account_targets={"a": [wsg_target()]},
            account_tz={"a": "UTC"},
            auth_client_factory=auth_factory,
            now_fn=clock.now if clock else lambda: NOW,
        )

    def _status(self, tmp) -> AuthStatus:
        return AuthStatus(os.path.join(tmp, "auth_status.json"))

    def test_verify_auth_refreshes_and_records_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(tmp)
            sched = self._scheduler(FakeAuth)
            sched.auth_status = status
            asyncio.run(sched.authenticate_all())
            client = sched.auth_client("a")
            self.assertEqual(client.refreshes, 0)
            self.assertTrue(sched.verify_auth("a"))
            self.assertEqual(client.refreshes, 1)
            self.assertNotIn("a", sched._suspended)
            self.assertEqual(status.get("a")["status"], "ok")
            self.assertEqual(status.get("a")["user_id"], "u-a")
            self.assertTrue(os.path.exists(status.path))

    def test_verify_auth_failure_suspends_and_records(self):
        class FailingAuth(FakeAuth):
            def refresh(self):
                self.refreshes += 1
                raise RuntimeError("login refused")

        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(tmp)
            sched = self._scheduler(FailingAuth)
            sched.auth_status = status
            asyncio.run(sched.authenticate_all())
            self.assertTrue(sched._auth_ok("a"))
            self.assertFalse(sched.verify_auth("a"))
            self.assertIn("a", sched._suspended)
            self.assertFalse(sched._auth_ok("a"))
            self.assertEqual(status.get("a")["status"], "failed")

    def test_verify_auth_missing_client_is_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(tmp)
            sched = self._scheduler(FakeAuth)
            sched.auth_status = status
            self.assertFalse(sched.verify_auth("a"))
            self.assertEqual(status.get("a")["status"], "failed")

    def test_authenticate_all_recovers_suspended_account(self):
        attempts = {"n": 0}

        class FlakyAuth(FakeAuth):
            def authenticate(self):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("first login broke")

        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(tmp)
            sched = self._scheduler(FlakyAuth)
            sched.auth_status = status
            asyncio.run(sched.authenticate_all())
            self.assertIn("a", sched._suspended)
            self.assertEqual(status.get("a")["status"], "failed")
            asyncio.run(sched.authenticate_all())
            self.assertNotIn("a", sched._suspended)
            self.assertEqual(status.get("a")["status"], "ok")
            self.assertEqual(status.get("a")["user_id"], "u-a")

    def test_discovery_reauthenticates_and_stamps_auth(self):
        clock = FakeClock(dt.datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
        inst = make_instance(
            "id-future", "WSG",
            dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC), 20260921, FUTURE_OPENS,
        )

        class CountingAuth(FakeAuth):
            def __init__(self, name):
                super().__init__(name)
                self.logins = 0

            def authenticate(self):
                self.logins += 1

        sched = Scheduler(
            facility_id="f",
            account_targets={"a": [wsg_target()]},
            account_tz={"a": "UTC"},
            schedule_client_factory=lambda: FakeSchedule([inst]),
            auth_client_factory=CountingAuth,
            now_fn=clock.now,
            sleep_fn=clock.sleep,
        )

        async def run() -> None:
            plan = await sched.discovery_pass(verify=False)
            auth = sched.auth_client("a")
            self.assertEqual(auth.logins, 1)
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].auth_status, "ok")

        asyncio.run(run())
        sched.close()

    def test_run_daemon_skips_burst_when_auth_refresh_fails(self):
        opens = dt.datetime(2026, 9, 19, 21, 0, tzinfo=UTC)
        start = dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
        clock = FakeClock(opens - dt.timedelta(minutes=5))
        inst = make_instance("id-future", "WSG", start, 20260921, opens)
        fired = []

        async def stuck_sleep(seconds):
            await asyncio.Future()  # never resolves; freeze the daemon loop

        class FailingAuth(FakeAuth):
            def refresh(self):
                self.refreshes += 1
                raise RuntimeError("login refused")

        async def on_burst(account_name, burst):
            fired.append(burst.instance_id)

        with tempfile.TemporaryDirectory() as tmp:
            status = AuthStatus(os.path.join(tmp, "auth_status.json"))
            scheduler = Scheduler(
                facility_id="f",
                account_targets={"a": [wsg_target()]},
                account_tz={"a": "UTC"},
                schedule_client_factory=lambda: FakeSchedule([inst]),
                auth_client_factory=lambda name: FailingAuth(name),
                now_fn=clock.now,
                sleep_fn=stuck_sleep,
                on_burst=on_burst,
                discovery_cadence_s=6 * 3600,
                burst_lead_s=30,
                preverify_window_s=600,
            )
            scheduler.auth_status = status

            async def run() -> None:
                task = asyncio.ensure_future(scheduler.run_daemon())
                for _ in range(10000):
                    if "a" in scheduler._suspended:
                        await asyncio.sleep(0)
                        break
                    await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            asyncio.run(run())
            scheduler.close()
            self.assertEqual(status.get("a")["status"], "failed")
            self.assertTrue(os.path.exists(status.path))
            self.assertEqual(scheduler._suspended, {"a"})
        self.assertEqual(fired, [])


if __name__ == "__main__":
    unittest.main()