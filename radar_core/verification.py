from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping

from .discovery import FETCH_ADAPTERS
from .registry import Registry, SourceSpec
from .storage import StateStore


COUNT_KEYS = (
    "discovered",
    "fetched",
    "normalized",
    "revisions_new",
    "revisions_reused",
    "translated",
    "translation_reused",
    "quality_failed",
    "failed",
)


def build_baseline_verification(
    registry: Registry,
    state: StateStore,
    *,
    operations: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the post-run baseline and update status for every source.

    A source is considered verified only after its registry fingerprint has
    been committed and its cursor has advanced. A source that is not due is
    still included so the daily report can distinguish "verified but idle"
    from "never successfully synchronized".
    """

    operation_rows = [
        dict(row) for row in (operations or []) if isinstance(row, Mapping)
    ]
    sources: list[dict[str, Any]] = []
    for declaration in _source_declarations(registry):
        source = declaration["source"]
        source_id = source.id
        registration = state.get_source_registration(source_id)
        cursor = state.get_cursor(source_id)
        matching = [
            row
            for row in operation_rows
            if str(row.get("source_id") or "") == source_id
            or str(row.get("task_id") or "") in declaration["task_ids"]
        ]
        counts = {
            key: sum(int(row.get(key) or 0) for row in matching)
            for key in COUNT_KEYS
        }
        status = _operation_status(matching)
        baseline_status = _baseline_status(source, registration)
        cursor_status = "present" if cursor is not None else "missing"
        errors = _errors(matching)
        if not source.enabled:
            status = "disabled"
        elif status in {"failed", "partial"}:
            pass
        elif baseline_status == "verified" and cursor is not None:
            status = "success" if matching else "not_due"
        elif matching and status == "success":
            status = "failed"
            errors.append("successful operation did not commit a verified baseline")
        else:
            status = "pending"

        sources.append(
            {
                "source_id": source_id,
                "module_id": declaration["module_id"],
                "module_name": declaration["module_name"],
                "task_ids": list(declaration["task_ids"]),
                "adapter": declaration["adapter"],
                "locator": source.locator,
                "enabled": source.enabled,
                "schedule": dict(source.schedule),
                "status": status,
                "baseline_status": baseline_status,
                "baseline_expected": source.registry_fingerprint,
                "baseline_actual": (
                    registration.get("baseline_fingerprint")
                    if isinstance(registration, Mapping)
                    else None
                ),
                "cursor_status": cursor_status,
                "cursor_run_id": (
                    cursor.get("run_id") if isinstance(cursor, Mapping) else None
                ),
                "updated": counts["revisions_new"],
                "reused": counts["revisions_reused"],
                "translated": counts["translated"],
                "translation_reused": counts["translation_reused"],
                "errors": errors,
                "counts": counts,
            }
        )

    baseline_verified = sum(
        row["baseline_status"] == "verified" for row in sources
    )
    baseline_pending = sum(
        row["baseline_status"] in {"pending", "missing", "staged"}
        for row in sources
        if row["enabled"]
    )
    baseline_stale = sum(
        row["baseline_status"] == "stale" for row in sources if row["enabled"]
    )
    failed = sum(row["status"] in {"failed", "partial"} for row in sources)
    if failed or baseline_stale:
        status = "failed"
    elif baseline_pending:
        status = "pending"
    else:
        status = "ok"

    return {
        "schema": "radar-source-verification/v1",
        "status": status,
        "counts": {
            "sources": len(sources),
            "enabled": sum(bool(row["enabled"]) for row in sources),
            "baseline_verified": baseline_verified,
            "baseline_pending": baseline_pending,
            "baseline_stale": baseline_stale,
            "updated": sum(int(row["updated"]) for row in sources),
            "translated": sum(int(row["translated"]) for row in sources),
            "failed": failed,
        },
        "sources": sources,
    }


def _source_declarations(registry: Registry) -> list[dict[str, Any]]:
    declarations: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    for source in sorted(registry.sources, key=lambda item: item.id):
        payload = source.payload
        module_id = str(payload.get("module_id") or source.id).strip()
        declarations[source.id] = {
            "source": source,
            "task_ids": [],
            "module_id": module_id,
            "module_name": str(
                payload.get("module_name")
                or payload.get("name")
                or source.name
                or module_id
            ),
            "adapter": str(
                payload.get("adapter") or payload.get("connector") or ""
            ),
        }

    for task in sorted(registry.tasks, key=lambda item: item.id):
        if not task.enabled or task.adapter not in FETCH_ADAPTERS:
            continue
        source = task.to_source_spec()
        declaration = declarations.get(source.id)
        if declaration is None:
            declaration = {
                "source": source,
                "task_ids": [],
                "module_id": task.module_id,
                "module_name": task.module_name or task.module_id,
                "adapter": task.adapter,
            }
            declarations[source.id] = declaration
        declaration["task_ids"].append(task.id)
        declaration["module_id"] = task.module_id
        declaration["module_name"] = task.module_name or task.module_id
        declaration["adapter"] = task.adapter

    return list(declarations.values())


def _baseline_status(
    source: SourceSpec,
    registration: Mapping[str, Any] | None,
) -> str:
    if not source.enabled:
        return "disabled"
    if registration is None:
        return "missing"
    if registration.get("baseline_fingerprint") == source.registry_fingerprint:
        return "verified"
    if registration.get("staged_fingerprint") == source.registry_fingerprint:
        return "staged"
    if registration.get("baseline_fingerprint"):
        return "stale"
    return "pending"


def _operation_status(rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    statuses = {str(row.get("status") or "") for row in rows}
    if "failed" in statuses or "blocked" in statuses:
        return "failed"
    if "partial" in statuses:
        return "partial"
    if statuses <= {"success", "ok"}:
        return "success"
    if "dry_run" in statuses:
        return "dry_run"
    return "pending"


def _errors(rows: list[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        raw_errors = row.get("errors")
        if not isinstance(raw_errors, list):
            raw_errors = [row.get("error")] if row.get("error") else []
        for error in raw_errors:
            value = str(error).strip()
            if value and value not in errors:
                errors.append(value[:500])
    return errors


__all__ = ["build_baseline_verification"]
