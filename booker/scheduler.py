from __future__ import annotations

import asyncio
import datetime as _dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .book import BurstExecutor, SUCCESS_RESULTS
from .client import AuthClient, ScheduleClient, ScheduleInstance
from .config import Target
from .discovery import ResolvedTarget, resolve_targets
from .state import AuthStatus, BookedHistory

DEFAULT_DISCOVERY_CADENCE_S = 6 * 3600
DEFAULT_HORIZON_DAYS = 14
DEFAULT_BURST_LEAD_S = 30
DEFAULT_PREVERIFY_WINDOW_S = 10 * 60

STATUS_ELIGIBLE = "eligible"
STATUS_SKIP_AUTH = "skip_auth_failed"
STATUS_SKIP_INSTANCE_GONE = "skip_instance_gone"
STATUS_SKIP_OPENS_SHIFTED = "skip_opens_shifted"
STATUS_SKIP_ALREADY_BOOKED = "skip_already_booked"
STATUS_SKIP_SCHEDULE_CHANGE = "skip_schedule_change"
STATUS_SKIP_HISTORY = "skip_booked_history"

_BOOKED_USER_STATUSES = ("Booked",)


@dataclass
class PlannedBurst:
    account_name: str
    target: Target
    instance_id: str
    partition_date: int
    trigger_at: _dt.datetime
    fire_toward: _dt.datetime
    status: str = STATUS_ELIGIBLE
    reason: str = ""
    auth_status: str = "unknown"


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _to_tz(dt: _dt.datetime | None, tz: ZoneInfo) -> _dt.datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _applies_tz(dt: _dt.datetime | None, tz: ZoneInfo) -> _dt.datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt


def _same_instant(a: _dt.datetime | None, b: _dt.datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    return _to_tz(a, ZoneInfo("UTC")) == _to_tz(b, ZoneInfo("UTC"))


def _weekday(inst: ScheduleInstance, tz: ZoneInfo) -> int:
    return _applies_tz(inst.start_date, tz).isoweekday()


def _time_str(inst: ScheduleInstance, tz: ZoneInfo) -> str:
    return _applies_tz(inst.start_date, tz).strftime("%H:%M")


def _describe_start(inst: ScheduleInstance, tz: ZoneInfo) -> str:
    return f"W{_weekday(inst, tz)}@{_time_str(inst, tz)}"


def verify_burst(
    burst: PlannedBurst,
    instances: list[ScheduleInstance],
    tz: ZoneInfo | str = "UTC",
    history: BookedHistory | None = None,
) -> PlannedBurst:
    if isinstance(tz, str):
        tz = ZoneInfo(tz)

    burst.status = STATUS_ELIGIBLE
    burst.reason = ""

    resolved = resolve_targets(instances, [burst.target], tz)[0]
    day_inst = next(
        (i for i in resolved.instances if i.partition_date == burst.partition_date),
        None,
    )

    if day_inst is None:
        shifted = next(
            (
                i
                for i in instances
                if i.partition_date == burst.partition_date
                and i.name.lower() == burst.target.name.lower()
            ),
            None,
        )
        if shifted is not None:
            burst.status = STATUS_SKIP_SCHEDULE_CHANGE
            burst.reason = (
                f"schedule change: class now at {_describe_start(shifted, tz)} "
                f"(configured W{burst.target.day}@{burst.target.time})"
            )
        else:
            burst.status = STATUS_SKIP_INSTANCE_GONE
            burst.reason = "class instance no longer present in schedule data"
        return burst

    if day_inst.id != burst.instance_id:
        burst.status = STATUS_SKIP_INSTANCE_GONE
        burst.reason = f"classId changed {burst.instance_id} -> {day_inst.id}"
        return burst

    if history is not None and history.has(
        burst.account_name, burst.partition_date, burst.instance_id
    ):
        burst.status = STATUS_SKIP_HISTORY
        burst.reason = "already recorded in booked_history"
        return burst

    opens = day_inst.booking_info.booking_opens_on
    if not _same_instant(opens, burst.trigger_at):
        burst.status = STATUS_SKIP_OPENS_SHIFTED
        if opens is None:
            burst.reason = "bookingOpensOn no longer available"
        else:
            burst.reason = (
                f"bookingOpensOn changed: was {burst.trigger_at.isoformat()}, "
                f"now {opens.isoformat()}"
            )
        return burst

    bi = day_inst.booking_info
    if day_inst.is_participant or bi.booking_user_status in _BOOKED_USER_STATUSES:
        burst.status = STATUS_SKIP_ALREADY_BOOKED
        burst.reason = (
            f"already booked (booking_user_status={bi.booking_user_status!r}, "
            f"is_participant={day_inst.is_participant})"
        )
        return burst

    if _weekday(day_inst, tz) != burst.target.day or _time_str(day_inst, tz) != burst.target.time:
        burst.status = STATUS_SKIP_SCHEDULE_CHANGE
        burst.reason = (
            f"schedule change: actual {_describe_start(day_inst, tz)} != "
            f"configured W{burst.target.day}@{burst.target.time}"
        )
        return burst

    return burst


def verify_bursts(
    plan: list[PlannedBurst],
    instances_by_account: dict[str, list[ScheduleInstance]],
    tz_by_account: dict[str, str] | None = None,
    history: BookedHistory | None = None,
) -> list[PlannedBurst]:
    for burst in plan:
        tz = (tz_by_account or {}).get(burst.account_name, "UTC")
        verify_burst(
            burst,
            instances_by_account.get(burst.account_name, []),
            tz,
            history,
        )
    return plan


def format_burst_line(burst: PlannedBurst) -> str:
    suffix = f" ({burst.reason})" if burst.reason else ""
    return (
        f"ACCOUNT={burst.account_name} TARGET={burst.target.name} "
        f"CLASSID={burst.instance_id} OPENS={burst.trigger_at.isoformat()} "
        f"START={burst.fire_toward.isoformat()} STATUS={burst.status} "
        f"AUTH={burst.auth_status}{suffix}"
    )


def format_verify_line(burst: PlannedBurst) -> str:
    if burst.status == STATUS_ELIGIBLE:
        return f"VERIFY {burst.instance_id} OK AUTH={burst.auth_status}"
    suffix = f" ({burst.reason})" if burst.reason else ""
    return (
        f"VERIFY {burst.instance_id} FAIL: {burst.status} "
        f"AUTH={burst.auth_status}{suffix}"
    )


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _default_on_burst(account_name: str, burst: PlannedBurst) -> None:
    await asyncio.sleep(0)
    print(f"BURST {account_name} {burst.instance_id} (dry: not bookable yet)")


class BurstFire:
    """on_burst glue: Book each due burst in parallel, persist successes."""

    def __init__(
        self,
        executor_factory,
        history: BookedHistory | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._executor_factory = executor_factory
        self._history = history
        self.dry_run = dry_run
        self._executors: dict[str, BurstExecutor] = {}

    def _executor(self, account_name: str) -> BurstExecutor:
        if account_name not in self._executors:
            self._executors[account_name] = self._executor_factory(account_name)
        return self._executors[account_name]

    def close(self) -> None:
        self._executors.clear()

    async def __call__(self, account_name: str, burst: PlannedBurst) -> None:
        executor = self._executor(account_name)
        result = await asyncio.to_thread(
            executor.book, burst.instance_id, burst.partition_date
        )
        if result in SUCCESS_RESULTS:
            if self.dry_run:
                print(
                    "DRY-RUN: would record "
                    f"{account_name}::{burst.partition_date}::{burst.instance_id} "
                    "as booked"
                )
            elif self._history is not None:
                self._history.add(
                    account_name, burst.partition_date, burst.instance_id
                )
                self._history.save()
        print(f"burst: {account_name} {burst.instance_id} -> {result}")


class Scheduler:
    def __init__(
        self,
        facility_id: str,
        account_targets: dict[str, list[Target]],
        account_tz: dict[str, str] | None = None,
        schedule_client_factory=None,
        auth_client_factory=None,
        now_fn=_utc_now,
        sleep_fn=_default_sleep,
        discovery_cadence_s: int = DEFAULT_DISCOVERY_CADENCE_S,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        burst_lead_s: int = DEFAULT_BURST_LEAD_S,
        preverify_window_s: int = DEFAULT_PREVERIFY_WINDOW_S,
        on_burst=None,
    ) -> None:
        self.facility_id = facility_id
        self.account_targets = dict(account_targets)
        self.account_tz = dict(account_tz) if account_tz else {n: "UTC" for n in self.account_targets}
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn if sleep_fn is not None else _default_sleep
        self.discovery_cadence_s = discovery_cadence_s
        self.horizon_days = horizon_days
        self.burst_lead_s = burst_lead_s
        self.preverify_window_s = preverify_window_s
        self.on_burst = on_burst if on_burst is not None else _default_on_burst

        self._schedule_factory = schedule_client_factory or _new_schedule_client
        self._auth_factory = auth_client_factory or _new_auth_client
        self._schedule_client: ScheduleClient | None = None
        self._auth_clients: dict[str, AuthClient] = {}
        self._suspended: set[str] = set()
        self._last_instances_by_account: dict[str, list[ScheduleInstance]] = {}
        self.history: BookedHistory | None = None
        self.auth_status: AuthStatus | None = None

    def auth_client(self, account_name: str) -> AuthClient | None:
        return self._auth_clients.get(account_name)

    def _auth_ok(self, account_name: str) -> bool:
        return (
            account_name in self._auth_clients
            and account_name not in self._suspended
        )

    def _record_auth(
        self, account_name: str, ok: bool, *, user_id: str | None = None
    ) -> None:
        if self.auth_status is not None:
            self.auth_status.set(account_name, ok, user_id=user_id)
            self.auth_status.save()

    def _drop_auth_client(self, account_name: str) -> None:
        client = self._auth_clients.pop(account_name, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def verify_auth(self, account_name: str) -> bool:
        """Refresh the account's session (fresh login + user-id resolve) so a
        queued burst fires on a known-good token; suspends on failure."""
        client = self._auth_clients.get(account_name)
        if client is None:
            self._record_auth(account_name, False)
            return False
        try:
            client.refresh()
        except Exception as exc:
            self._drop_auth_client(account_name)
            self._suspended.add(account_name)
            self._record_auth(account_name, False)
            print(
                f"error: account {account_name}: session refresh failed "
                f"({type(exc).__name__}); suspended"
            )
            return False
        self._record_auth(account_name, True, user_id=client.user_id)
        print(f"auth: account {account_name} status=ok user_id={client.user_id}")
        return True

    def plan(
        self,
        resolved_by_account: dict[str, list[ResolvedTarget]],
        now: _dt.datetime | None = None,
    ) -> list[PlannedBurst]:
        now = now or self.now_fn()
        candidates: list[tuple] = []
        for account_name, resolved_list in resolved_by_account.items():
            for resolved in resolved_list:
                for inst in resolved.instances:
                    opens = inst.booking_info.booking_opens_on
                    if opens is None or opens <= now:
                        continue
                    candidates.append(
                        (
                            resolved.target.priority,
                            opens,
                            account_name,
                            resolved.target,
                            inst,
                            resolved.event_type_id,
                        )
                    )
        candidates.sort(key=lambda c: (c[0], c[1]))

        seen_event: set[tuple] = set()
        seen_instance: set[tuple] = set()
        plan: list[PlannedBurst] = []
        for _priority, opens, account_name, target, inst, event_type_id in candidates:
            event_key = (account_name, inst.partition_date, event_type_id)
            instance_key = (account_name, inst.partition_date, inst.id)
            if event_key in seen_event or instance_key in seen_instance:
                continue
            seen_event.add(event_key)
            seen_instance.add(instance_key)
            plan.append(
                PlannedBurst(
                    account_name=account_name,
                    target=target,
                    instance_id=inst.id,
                    partition_date=inst.partition_date,
                    trigger_at=opens,
                    fire_toward=inst.start_date,
                )
            )
        plan.sort(key=lambda b: b.trigger_at)
        return plan

    async def authenticate_all(self) -> None:
        for name in self.account_targets:
            client = self._auth_clients.get(name)
            if client is None:
                client = self._auth_factory(name)
                self._auth_clients[name] = client
            try:
                client.authenticate()
            except Exception as exc:
                self._drop_auth_client(name)
                self._suspended.add(name)
                self._record_auth(name, False)
                print(
                    f"error: account {name}: authentication failed "
                    f"({type(exc).__name__}), suspended until next discovery"
                )
                continue
            self._suspended.discard(name)
            self._record_auth(name, True, user_id=client.user_id)
            print(f"auth: account {name} status=ok user_id={client.user_id}")

    def _schedule(self) -> ScheduleClient:
        if self._schedule_client is None:
            self._schedule_client = self._schedule_factory()
        return self._schedule_client

    async def discovery_pass(self, verify: bool = True) -> list[PlannedBurst]:
        await self.authenticate_all()
        now = self.now_fn()
        from_date = now.date()
        to_date = from_date + _dt.timedelta(days=self.horizon_days)
        instances = self._schedule().fetch(self.facility_id, from_date, to_date)

        resolved_by_account: dict[str, list[ResolvedTarget]] = {}
        for name, targets in self.account_targets.items():
            if name in self._suspended:
                print(f"discovery: skipping account {name} (suspended)")
                continue
            resolved_by_account[name] = resolve_targets(
                instances, targets, self.account_tz.get(name, "UTC")
            )
        self._last_instances_by_account = {name: instances for name in resolved_by_account}

        plan = self.plan(resolved_by_account)
        for burst in plan:
            burst.auth_status = (
                "ok" if self._auth_ok(burst.account_name) else "failed"
            )
        print(
            f"discovery: fetched {len(instances)} instances "
            f"({from_date}..{to_date}); {len(plan)} burst(s) scheduled"
        )
        if verify:
            verify_bursts(
                plan, self._last_instances_by_account, self.account_tz, self.history
            )
            for burst in plan:
                print(format_verify_line(burst))
        return plan

    async def run_daemon(self) -> None:
        await self.authenticate_all()
        next_discovery_at = self.now_fn()
        plan: list[PlannedBurst] = []
        fired: set[int] = set()
        verified: set[int] = set()
        try:
            while True:
                now = self.now_fn()
                if now >= next_discovery_at:
                    plan = await self.discovery_pass(verify=False)
                    fired = set()
                    verified = set()
                    next_discovery_at = now + _dt.timedelta(seconds=self.discovery_cadence_s)

                for burst in plan:
                    if burst.status != STATUS_ELIGIBLE or id(burst) in verified:
                        continue
                    if now >= burst.trigger_at - _dt.timedelta(seconds=self.preverify_window_s):
                        verify_burst(
                            burst,
                            self._last_instances_by_account.get(burst.account_name, []),
                            self.account_tz.get(burst.account_name, "UTC"),
                            self.history,
                        )
                        if burst.status == STATUS_ELIGIBLE:
                            if self.verify_auth(burst.account_name):
                                burst.auth_status = "ok"
                            else:
                                burst.auth_status = "failed"
                                burst.status = STATUS_SKIP_AUTH
                                burst.reason = (
                                    "account session refresh failed; "
                                    "burst cancelled"
                                )
                        verified.add(id(burst))
                        print(format_verify_line(burst))

                due: list[PlannedBurst] = []
                for burst in plan:
                    if burst.status != STATUS_ELIGIBLE or id(burst) in fired:
                        continue
                    if now >= burst.trigger_at - _dt.timedelta(seconds=self.burst_lead_s):
                        due.append(burst)
                if due:
                    await asyncio.gather(*(self.on_burst(b.account_name, b) for b in due))
                    for burst in due:
                        fired.add(id(burst))

                now = self.now_fn()
                wake_at = [next_discovery_at]
                for burst in plan:
                    if burst.status != STATUS_ELIGIBLE:
                        continue
                    if id(burst) not in verified:
                        wake_at.append(
                            burst.trigger_at - _dt.timedelta(seconds=self.preverify_window_s)
                        )
                    if id(burst) not in fired:
                        wake_at.append(
                            burst.trigger_at - _dt.timedelta(seconds=self.burst_lead_s)
                        )
                wake_at = min(wake_at)
                delay = (wake_at - now).total_seconds()
                if delay > 0:
                    await self.sleep_fn(delay)
        except asyncio.CancelledError:
            self.close()
            raise

    def close(self) -> None:
        if self._schedule_client is not None:
            try:
                self._schedule_client.close()
            except Exception:
                pass
            self._schedule_client = None
        for name, client in list(self._auth_clients.items()):
            try:
                client.close()
            except Exception:
                pass
        self._auth_clients.clear()


def _new_schedule_client() -> ScheduleClient:
    return ScheduleClient()


def _new_auth_client(account_name: str) -> AuthClient:
    return AuthClient(account_name)