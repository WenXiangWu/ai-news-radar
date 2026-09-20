from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .registry import Registry, SourceSpec
from .storage import StateStore


@dataclass(frozen=True)
class Operation:
    source_id: str
    source: SourceSpec
    scheduled_at: datetime
    next_run_at: str | None
    cursor: dict[str, Any]
    entity_ids: tuple[str, ...] = ()
    surface_ids: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.source_id

    @property
    def schedule(self) -> dict[str, Any]:
        return dict(self.source.schedule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "scheduled_at": self.scheduled_at.isoformat(),
            "next_run_at": self.next_run_at,
            "schedule": self.schedule,
            "entity_ids": list(self.entity_ids),
            "surface_ids": list(self.surface_ids),
        }


def _field_matches(
    expression: str,
    value: int,
    minimum: int,
    maximum: int,
) -> bool:
    for raw_part in str(expression or "*").split(","):
        part = raw_part.strip()
        if not part:
            return False
        base, separator, raw_step = part.partition("/")
        try:
            step = int(raw_step) if separator else 1
        except ValueError:
            return False
        if step <= 0:
            return False
        if base in ("", "*"):
            start, end = minimum, maximum
        elif "-" in base:
            parts = base.split("-", 1)
            if len(parts) != 2:
                return False
            try:
                start, end = int(parts[0]), int(parts[1])
            except ValueError:
                return False
        else:
            try:
                start = end = int(base)
            except ValueError:
                return False
        if start < minimum or end > maximum or start > end:
            return False
        if start <= value <= end and (value - start) % step == 0:
            return True
        if maximum == 7 and value == 0 and end == 7 and (0 - start) % step == 0:
            return True
    return False


def _cron_matches(expression: str, value: datetime) -> bool:
    fields = str(expression or "").split()
    if len(fields) != 5:
        return False
    minute, hour, day_of_month, month, day_of_week = fields
    day_of_week_value = (value.weekday() + 1) % 7
    return (
        _field_matches(minute, value.minute, 0, 59)
        and _field_matches(hour, value.hour, 0, 23)
        and _field_matches(day_of_month, value.day, 1, 31)
        and _field_matches(month, value.month, 1, 12)
        and (
            _field_matches(day_of_week, day_of_week_value, 0, 7)
            or (
                day_of_week_value == 0
                and _field_matches(day_of_week, 7, 0, 7)
            )
        )
    )


def _timezone(schedule: Mapping[str, Any]) -> ZoneInfo | None:
    try:
        return ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
    except Exception:  # noqa: BLE001
        return None


def _localize(value: datetime, timezone_value: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone_value)
    return value.astimezone(timezone_value)


def _last_scheduled(
    expression: str,
    value: datetime,
) -> datetime | None:
    current = value.replace(second=0, microsecond=0)
    for _ in range(366 * 24 * 60):
        if _cron_matches(expression, current):
            return current
        current -= timedelta(minutes=1)
    return None


def _next_scheduled(
    expression: str,
    value: datetime,
) -> datetime | None:
    current = value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if _cron_matches(expression, current):
            return current
        current += timedelta(minutes=1)
    return None


def _parse_scheduled(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _source_errors(source: SourceSpec) -> list[str]:
    errors: list[str] = []
    if not source.id:
        errors.append("source.id is required")
    if not source.source_type:
        errors.append("source.source_type is required")
    if not source.name:
        errors.append("source.name is required")
    if not source.locator:
        errors.append("source.locator is required")
    if not source.output_root:
        errors.append("source.output_root is required")
    schedule = source.schedule
    if not isinstance(schedule, Mapping):
        errors.append("source.schedule must be an object")
        return errors
    if len(str(schedule.get("cron") or "").split()) != 5:
        errors.append("source.schedule.cron is invalid")
    elif not _cron_matches(
        str(schedule.get("cron") or ""),
        datetime(2026, 1, 1, 0, 0),
    ) and not _cron_has_valid_fields(str(schedule.get("cron") or "")):
        errors.append("source.schedule.cron is invalid")
    if _timezone(schedule) is None:
        errors.append("source.schedule.timezone is invalid")
    return errors


def _cron_has_valid_fields(expression: str) -> bool:
    fields = str(expression or "").split()
    if len(fields) != 5:
        return False
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    return all(
        _field_is_valid(field, minimum, maximum)
        for field, (minimum, maximum) in zip(fields, ranges)
    )


def _field_is_valid(expression: str, minimum: int, maximum: int) -> bool:
    for raw_part in str(expression or "*").split(","):
        part = raw_part.strip()
        if not part:
            return False
        base, separator, raw_step = part.partition("/")
        if separator:
            if not raw_step.isdigit() or int(raw_step) <= 0:
                return False
        if base in ("", "*"):
            continue
        if "-" in base:
            parts = base.split("-", 1)
            if len(parts) != 2 or not all(item.isdigit() for item in parts):
                return False
            start, end = (int(item) for item in parts)
            if start < minimum or end > maximum or start > end:
                return False
            continue
        if not base.isdigit() or not minimum <= int(base) <= maximum:
            return False
    return True


def _invalid_entry(source: SourceSpec, errors: list[str]) -> dict[str, Any]:
    return {"source_id": source.id, "errors": errors}


def _cursor_payload(source: SourceSpec, state: StateStore) -> dict[str, Any] | None:
    row = state.get_cursor(source.id)
    if row is None:
        return None
    cursor = row.get("cursor")
    return dict(cursor) if isinstance(cursor, dict) else {}


def _reconcile_registry(registry: Registry, state: StateStore) -> None:
    registry.reset_diagnostics()
    for source in sorted(registry.sources, key=lambda item: item.id):
        registration = state.get_source_registration(source.id)
        baseline = (
            registration.get("baseline_fingerprint")
            if isinstance(registration, dict)
            else None
        )
        if registration is None:
            registry.diagnostics["added_sources"].append(source.id)
        elif baseline != source.registry_fingerprint:
            registry.diagnostics["changed_sources"].append(source.id)
        if not source.enabled or not bool(source.schedule.get("enabled", True)):
            registry.diagnostics["disabled_sources"].append(source.id)


def register_new_sources(
    registry: Registry,
    state: StateStore,
    run_id: str,
) -> list[str]:
    """Register source manifest fingerprints and create missing cursor rows."""

    _reconcile_registry(registry, state)
    added = list(registry.diagnostics["added_sources"])
    for source in sorted(registry.sources, key=lambda item: item.id):
        state.stage_source_registration(
            source.id,
            source.to_dict(),
            source.registry_fingerprint,
            run_id,
        )
    return added


def discover_due_operations(
    registry: Registry,
    now: datetime,
    state: StateStore,
) -> list[Operation]:
    """Return executable source operations whose registry schedule is due."""

    _reconcile_registry(registry, state)
    operations: list[Operation] = []

    for source in sorted(registry.sources, key=lambda item: item.id):
        schedule = source.schedule
        timezone_value = _timezone(schedule) if isinstance(schedule, Mapping) else None
        cron = str(schedule.get("cron") or "") if isinstance(schedule, Mapping) else ""
        errors = _source_errors(source)
        if errors:
            registry.diagnostics["invalid_declarations"].append(
                _invalid_entry(source, errors)
            )
            registry.diagnostics["next_run_at"][source.id] = None
            continue

        local_now = _localize(now, timezone_value)
        current_minute = local_now.replace(second=0, microsecond=0)
        scheduled_at = _last_scheduled(cron, current_minute)
        next_scheduled = _next_scheduled(cron, current_minute)
        registry.diagnostics["next_run_at"][source.id] = (
            next_scheduled.isoformat() if next_scheduled is not None else None
        )
        if not source.enabled or not bool(schedule.get("enabled", True)):
            continue
        if scheduled_at is None:
            continue

        cursor = _cursor_payload(source, state) or {}
        last_scheduled = _parse_scheduled(cursor.get("last_scheduled_at"))
        if last_scheduled is not None:
            last_scheduled = last_scheduled.astimezone(timezone_value)
        if last_scheduled is not None and last_scheduled >= scheduled_at:
            continue

        entities = registry.entities_for_source(source.id)
        surfaces = registry.surfaces_for_source(source.id)
        operations.append(
            Operation(
                source_id=source.id,
                source=source,
                scheduled_at=scheduled_at,
                next_run_at=(
                    next_scheduled.isoformat()
                    if next_scheduled is not None
                    else None
                ),
                cursor=cursor,
                entity_ids=tuple(entity.id for entity in entities),
                surface_ids=tuple(surface.id for surface in surfaces),
            )
        )
    return operations


__all__ = [
    "Operation",
    "discover_due_operations",
    "register_new_sources",
]
