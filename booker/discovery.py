from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .client import ScheduleInstance
from .config import Target


@dataclass
class ResolvedTarget:
    target: Target
    event_type_id: str
    instances: list[ScheduleInstance] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _instance_weekday(inst: ScheduleInstance, tz: ZoneInfo) -> int:
    local = inst.start_date.astimezone(tz)
    return local.isoweekday()


def _instance_time_str(inst: ScheduleInstance, tz: ZoneInfo) -> str:
    local = inst.start_date.astimezone(tz)
    return local.strftime("%H:%M")


def resolve_targets(
    instances: list[ScheduleInstance],
    targets: list[Target],
    tz: ZoneInfo | str = "UTC",
) -> list[ResolvedTarget]:
    if isinstance(tz, str):
        tz = ZoneInfo(tz)

    results: list[ResolvedTarget] = []
    for target in targets:
        matched: list[ScheduleInstance] = []
        resolved_type_id: str | None = None

        for inst in instances:
            name_match = inst.name.lower() == target.name.lower()
            if not name_match:
                continue

            wd = _instance_weekday(inst, tz)
            ts = _instance_time_str(inst, tz)

            if target.event_type_id and inst.event_type_id == target.event_type_id:
                if wd == target.day and ts == target.time:
                    matched.append(inst)
                    resolved_type_id = inst.event_type_id
                continue

            if wd == target.day and ts == target.time:
                matched.append(inst)
                resolved_type_id = inst.event_type_id

        if not matched:
            resolved_type_id = target.event_type_id

        if resolved_type_id is None and target.event_type_id:
            resolved_type_id = target.event_type_id

        event_type_id = resolved_type_id or ""

        if target.event_type_id and event_type_id and target.event_type_id != event_type_id:
            warnings = [f"event_type_id pin {target.event_type_id} differs from resolved {event_type_id}"]
        elif target.event_type_id and not matched:
            warnings = [f"event_type_id pin {target.event_type_id} not found in any matching instance"]
        else:
            warnings = []

        if target.event_type_id and not matched and target.event_type_id:
            event_type_id = target.event_type_id

        mismatches: list[str] = []
        for inst in matched:
            actual_wd = _instance_weekday(inst, tz)
            actual_time = _instance_time_str(inst, tz)
            if actual_wd != target.day or actual_time != target.time:
                mismatches.append(
                    f"{inst.name} {inst.id}: actual W{actual_wd}@{actual_time} "
                    f"!= configured W{target.day}@{target.time}"
                )

        results.append(
            ResolvedTarget(
                target=target,
                event_type_id=event_type_id,
                instances=matched,
                mismatches=mismatches,
                warnings=warnings,
            )
        )

    return results


def build_catalog(
    instances: list[ScheduleInstance],
    tz: ZoneInfo | str = "UTC",
) -> dict[str, list[str]]:
    if isinstance(tz, str):
        tz = ZoneInfo(tz)

    catalog: dict[str, list[str]] = {}
    for inst in instances:
        local = inst.start_date.astimezone(tz)
        wd = local.isoweekday()
        time_str = local.strftime("%H:%M")
        label = f"W{wd}@{time_str}"
        catalog.setdefault(inst.event_type_id, [])
        if label not in catalog[inst.event_type_id]:
            catalog[inst.event_type_id].append(label)
    return catalog
