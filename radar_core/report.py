from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


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


def build_legacy_free_report(run: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(run)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        result = {}
    counts = {
        key: int(result.get(key) or payload.get(key) or 0)
        for key in COUNT_KEYS
    }
    errors = [
        str(error)[:500]
        for error in (result.get("errors") or payload.get("errors") or [])
        if error
    ]
    return {
        "schema": "radar-run-report/v1",
        "run_id": str(payload.get("run_id") or ""),
        "status": str(payload.get("status") or "unknown"),
        "updated_at": str(
            payload.get("updated_at")
            or datetime.now(timezone.utc).isoformat()
        ),
        "counts": counts,
        "errors": errors,
        "source_id": payload.get("source_id"),
        "task_id": payload.get("task_id"),
        "provider_counts": dict(
            result.get("provider_counts")
            or payload.get("provider_counts")
            or {}
        ),
    }


def write_run_report(run: Mapping[str, Any], out: Path) -> dict[str, Any]:
    report = build_legacy_free_report(run)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def build_contract_run_report(run: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(run)
    raw_operations = payload.get("operations") or payload.get("tasks") or []
    tasks: list[dict[str, Any]] = []
    for index, operation in enumerate(raw_operations):
        if not isinstance(operation, Mapping):
            continue
        raw_status = str(operation.get("status") or "skipped")
        status = {
            "success": "ok",
            "dry_run": "skipped",
            "running": "skipped",
        }.get(raw_status, raw_status)
        if status not in {"ok", "partial", "failed", "skipped"}:
            status = "failed"
        task_id = str(
            operation.get("task_id")
            or f"task.source.{operation.get('source_id') or index}.sync"
        )
        summary = str(operation.get("summary") or "").strip()
        if not summary:
            summary = (
                f"发现 {int(operation.get('discovered') or 0)} · "
                f"抓取 {int(operation.get('fetched') or 0)} · "
                f"翻译 {int(operation.get('translated') or 0)}"
            )
        row: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
            "summary": summary,
        }
        if operation.get("translated") is not None:
            row["translated"] = int(operation.get("translated") or 0)
        if operation.get("revisions_new") is not None:
            row["updated"] = int(operation.get("revisions_new") or 0)
        elif operation.get("updated") is not None:
            row["updated"] = int(operation.get("updated") or 0)
        if operation.get("failed"):
            row["error"] = "; ".join(
                str(error) for error in (operation.get("errors") or []) if error
            )[:500]
        tasks.append(row)
    status = str(payload.get("status") or "skipped")
    status = {"success": "ok", "dry_run": "skipped"}.get(status, status)
    if status not in {"ok", "partial", "failed", "skipped"}:
        status = "failed"
    started_at = str(
        payload.get("started_at")
        or payload.get("updated_at")
        or datetime.now(timezone.utc).isoformat()
    )
    finished_at = str(
        payload.get("finished_at")
        or payload.get("updated_at")
        or started_at
    )
    return {
        "schema": "radar-run-report/v1",
        "run_id": str(payload.get("run_id") or ""),
        "protocol_version": "v1",
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "tasks": tasks,
    }


def write_contract_run_report(run: Mapping[str, Any], out: Path) -> dict[str, Any]:
    report = build_contract_run_report(run)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "build_contract_run_report",
    "build_legacy_free_report",
    "write_contract_run_report",
    "write_run_report",
]
