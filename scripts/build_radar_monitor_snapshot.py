#!/usr/bin/env python3
"""Build the Radar-owned monitoring snapshot and report index."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_radar_update_report import _load_modules, _source_id
from scripts.validate_radar_contract import validate_contract_file


def build_monitor_snapshot(
    target_root: Path,
    run_report_path: Path,
    source_validation_path: Path | None = None,
    update_report_path: Path | None = None,
    *,
    generated_at: str | None = None,
    public_prefix: str = "/radar-content",
    reports_prefix: str | None = None,
) -> dict[str, Any]:
    target_root = Path(target_root)
    run_report = _load_json(run_report_path)
    validation = _load_json(source_validation_path)
    update = _load_json(update_report_path)
    modules = _load_modules(target_root)
    source_declarations = _load_source_declarations(target_root)

    operations = _mappings(run_report.get("operations") or run_report.get("tasks"))
    operation_by_source = {
        str(row.get("source_id")): row
        for row in operations
        if row.get("source_id")
    }
    validation_by_source = {
        str(row.get("source_id")): row
        for row in _mappings(validation.get("sources"))
        if row.get("source_id")
    }
    update_by_module = {
        str(row.get("module_id")): row
        for row in _mappings(update.get("modules"))
        if row.get("module_id")
    }

    module_rows: list[dict[str, Any]] = []
    for module in modules:
        module_id = str(module.get("module_id") or "")
        update_row = update_by_module.get(module_id, {})
        source_ids = sorted(
            set(str(source_id) for source_id in module.get("source_ids") or [])
        )
        source_rows = [
            _build_source_row(
                source_declarations.get(source_id, {}),
                module,
                operation_by_source.get(source_id),
                validation_by_source.get(source_id),
                update_row,
            )
            for source_id in source_ids
        ]
        status = str(update_row.get("status") or "").strip()
        if not status:
            status = _module_status(module, source_rows)
        module_rows.append(
            {
                "module_id": module_id,
                "name": str(
                    update_row.get("name")
                    or module.get("name")
                    or module_id
                ),
                "kind": str(update_row.get("kind") or module.get("kind") or "source"),
                "enabled": bool(module.get("enabled", True)),
                "target_path": module.get("target_path"),
                "status": status,
                "task_ids": sorted(
                    str(task_id) for task_id in module.get("task_ids") or []
                ),
                "source_ids": source_ids,
                "updated": int(update_row.get("updated") or sum(
                    int(row["metrics"].get("updated") or 0)
                    for row in source_rows
                )),
                "translated": int(update_row.get("translated") or sum(
                    int(row["metrics"].get("translated") or 0)
                    for row in source_rows
                )),
                "translation_links": list(update_row.get("translation_links") or []),
                "errors": list(update_row.get("errors") or []),
            }
        )

    # A module can be represented only by a validation report when a registry
    # was added after the previous Way snapshot. Keep it visible immediately.
    known_modules = {str(row["module_id"]) for row in module_rows}
    for row in validation_by_source.values():
        module_id = str(row.get("module_id") or "").strip()
        if module_id and module_id not in known_modules:
            module_rows.append(
                {
                    "module_id": module_id,
                    "name": str(row.get("module_name") or module_id),
                    "kind": "source",
                    "enabled": bool(row.get("enabled", True)),
                    "target_path": None,
                    "status": "failed" if row.get("status") == "failed" else "pending",
                    "task_ids": list(row.get("task_ids") or []),
                    "source_ids": [str(row.get("source_id") or "")],
                    "updated": 0,
                    "translated": 0,
                    "translation_links": [],
                    "errors": list(row.get("errors") or []),
                }
            )

    source_rows = [
        _build_source_row(
            declaration,
            next(
                (
                    module
                    for module in modules
                    if str(module.get("module_id") or "")
                    == str(declaration.get("module_id") or "")
                ),
                {},
            ),
            operation_by_source.get(str(declaration.get("source_id") or "")),
            validation_by_source.get(str(declaration.get("source_id") or "")),
            update_by_module.get(str(declaration.get("module_id") or ""), {}),
        )
        for declaration in source_declarations.values()
    ]
    source_rows.sort(key=lambda row: str(row["source_id"]))

    generated = generated_at or datetime.now(timezone.utc).isoformat()
    reports_prefix = reports_prefix or f"{public_prefix.rstrip('/')}/reports"
    providers = _provider_rows(run_report)
    summary = {
        "modules": len(module_rows),
        "sources": len(source_rows),
        "enabled_sources": sum(bool(row["enabled"]) for row in source_rows),
        "healthy_sources": sum(
            row["reachability"]["status"] in {"healthy", "degraded"}
            for row in source_rows
            if row["enabled"]
        ),
        "failed_sources": sum(row["status"] == "failed" for row in source_rows),
        "baseline_verified": sum(
            row["baseline"]["status"] == "verified"
            for row in source_rows
            if row["enabled"]
        ),
        "baseline_pending": sum(
            row["baseline"]["status"]
            in {"pending", "missing", "staged", "stale", "unknown"}
            for row in source_rows
            if row["enabled"]
        ),
        "updated": sum(int(row["metrics"].get("updated") or 0) for row in source_rows),
        "translated": sum(
            int(row["metrics"].get("translated") or 0) for row in source_rows
        ),
    }
    run_id = str(run_report.get("run_id") or "")
    return {
        "schema": "radar-monitor/v1",
        "generated_at": generated,
        "run": {
            "run_id": run_id,
            "status": str(run_report.get("status") or "unknown"),
            "started_at": run_report.get("started_at"),
            "finished_at": run_report.get("finished_at"),
            "duration_ms": int(run_report.get("duration_ms") or 0),
            "trigger_request_id": run_report.get("trigger_request_id"),
            "report_path": _public_path(public_prefix, "radar-run-report.json"),
        },
        "summary": summary,
        "providers": providers,
        "modules": sorted(module_rows, key=lambda row: str(row["module_id"])),
        "sources": source_rows,
        "reports": {
            "latest_run": _public_path(public_prefix, "radar-run-report.json"),
            "latest_update": _public_path(public_prefix, "radar-update-report.json"),
            "latest_validation": _public_path(public_prefix, "source-validation.json"),
            "history_index": _public_path(reports_prefix, "index.json"),
        },
    }


def build_report_index(
    reports_root: Path,
    *,
    generated_at: str | None = None,
    public_prefix: str = "/radar-content/reports",
) -> dict[str, Any]:
    root = Path(reports_root)
    candidates: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        payload = _load_json(path)
        if payload.get("schema") != "radar-update-report/v1":
            continue
        date = _report_date(path, payload)
        if not date:
            continue
        row = {
            "date": date,
            "status": str(payload.get("status") or "unknown"),
            "generated_at": str(payload.get("generated_at") or ""),
            "summary": dict(payload.get("summary") or {}),
            "path": _public_path(public_prefix, path.name),
        }
        existing = candidates.get(date)
        prefer_daily_name = path.stem == date
        existing_is_daily_name = (
            existing is not None
            and str(existing.get("path") or "").rsplit("/", 1)[-1] == f"{date}.json"
        )
        if (
            existing is None
            or row["generated_at"] > existing["generated_at"]
            or (
                row["generated_at"] == existing["generated_at"]
                and prefer_daily_name
                and not existing_is_daily_name
            )
        ):
            candidates[date] = row
    return {
        "schema": "radar-report-index/v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "reports": [
            candidates[date]
            for date in sorted(candidates, reverse=True)
        ],
    }


def _build_source_row(
    declaration: Mapping[str, Any],
    module: Mapping[str, Any],
    operation: Mapping[str, Any] | None,
    validation: Mapping[str, Any] | None,
    update: Mapping[str, Any],
) -> dict[str, Any]:
    declaration = dict(declaration or {})
    validation = dict(validation or {})
    operation = dict(operation or {})
    source_id = str(
        declaration.get("source_id")
        or validation.get("source_id")
        or operation.get("source_id")
        or ""
    )
    source_config = declaration.get("config")
    source_config = dict(source_config) if isinstance(source_config, Mapping) else {}
    enabled = bool(
        validation.get(
            "enabled",
            declaration.get("enabled", module.get("enabled", True)),
        )
    )
    if not enabled:
        status = "disabled"
    else:
        status = str(
            operation.get("status")
            or validation.get("status")
            or update.get("status")
            or "pending"
        )
        if status == "ok":
            status = "success"
    live = validation.get("live")
    live = dict(live) if isinstance(live, Mapping) else {}
    health = validation.get("health")
    health = dict(health) if isinstance(health, Mapping) else {}
    reachability_status = str(
        health.get("status")
        or ("failed" if validation.get("status") == "failed" else "unknown")
    )
    baseline = {
        "status": str(
            validation.get("baseline_status")
            or (operation.get("baseline_after") or {}).get("baseline_status")
            or "unknown"
        ),
        "fingerprint": validation.get("baseline_fingerprint")
        or (operation.get("baseline_after") or {}).get("baseline_fingerprint"),
        "cursor_status": validation.get("cursor_status") or "unknown",
        "cursor_run_id": validation.get("cursor_run_id")
        or (operation.get("baseline_after") or {}).get("cursor_run_id"),
    }
    translations = list(update.get("translation_links") or [])
    return {
        "source_id": source_id,
        "module_id": str(
            declaration.get("module_id")
            or validation.get("module_id")
            or operation.get("module_id")
            or module.get("module_id")
            or source_id
        ),
        "module_name": str(
            declaration.get("module_name")
            or validation.get("module_name")
            or module.get("name")
            or source_id
        ),
        "enabled": enabled,
        "adapter": str(
            declaration.get("adapter")
            or validation.get("adapter")
            or operation.get("adapter")
            or "unknown"
        ),
        "locator": str(
            declaration.get("locator")
            or validation.get("locator")
            or source_config.get("locator")
            or ""
        ),
        "schedule": dict(
            declaration.get("schedule")
            or validation.get("schedule")
            or source_config.get("schedule")
            or {}
        ),
        "scheduled_at": operation.get("scheduled_at"),
        "next_run_at": operation.get("next_run_at"),
        "last_run_at": operation.get("finished_at")
        or operation.get("updated_at")
        or validation.get("checked_at"),
        "status": status,
        "reachability": {
            "status": reachability_status,
            "message": health.get("message") or "",
            "checked_at": live.get("checked_at") or validation.get("checked_at"),
            "latency_ms": int(
                live.get("latency_ms")
                or validation.get("latency_ms")
                or 0
            ),
            "discovered": int(live.get("discovered") or 0),
            "fetched": int(live.get("fetched") or 0),
        },
        "baseline": baseline,
        "metrics": {
            "discovered": int(operation.get("discovered") or 0),
            "fetched": int(operation.get("fetched") or 0),
            "updated": int(
                operation.get("revisions_new")
                or update.get("updated")
                or 0
            ),
            "translated": int(
                operation.get("translated")
                or update.get("translated")
                or 0
            ),
            "translation_reused": int(operation.get("translation_reused") or 0),
        },
        "translations": translations,
        "errors": _unique_errors(
            list(validation.get("errors") or [])
            + list(operation.get("errors") or [])
            + list(update.get("errors") or [])
        ),
    }


def _load_source_declarations(target_root: Path) -> dict[str, dict[str, Any]]:
    registry_root = Path(target_root) / "radar" / "registry"
    declarations: dict[str, dict[str, Any]] = {}
    index = _load_json(registry_root / "index.json")
    for reference in _mappings(index.get("modules")):
        module = _load_json(registry_root / str(reference.get("manifest") or ""))
        _add_module_declarations(declarations, module)
    knowledge = _load_json(registry_root / "knowledge.json")
    for source in _mappings(knowledge.get("sources")):
        _add_source_declaration(
            declarations,
            source,
            module_id=str(source.get("module_id") or source.get("id") or ""),
            module_name=str(source.get("label") or source.get("name") or ""),
            tasks=_mappings(source.get("tasks")),
        )
    source_root = registry_root / "sources"
    for path in sorted(source_root.glob("*.json")):
        source = _load_json(path)
        if source:
            _add_source_declaration(
                declarations,
                source,
                module_id=str(source.get("module_id") or source.get("id") or ""),
                module_name=str(source.get("name") or source.get("id") or ""),
                tasks=[],
            )
    return declarations


def _add_module_declarations(
    declarations: dict[str, dict[str, Any]],
    module: Mapping[str, Any],
) -> None:
    module_id = str(module.get("id") or "").strip()
    if not module_id:
        return
    display = module.get("display")
    display = dict(display) if isinstance(display, Mapping) else {}
    module_source = module.get("source")
    module_source = dict(module_source) if isinstance(module_source, Mapping) else {}
    tasks = _mappings(module.get("tasks"))
    task_source = {}
    for task in tasks:
        nested = task.get("source")
        if isinstance(nested, Mapping):
            task_source.update(dict(nested))
    source = {**module_source, **task_source}
    _add_source_declaration(
        declarations,
        source,
        module_id=module_id,
        module_name=str(display.get("name") or module_id),
        tasks=tasks,
        enabled=bool(module.get("enabled", True)),
    )


def _add_source_declaration(
    declarations: dict[str, dict[str, Any]],
    source: Mapping[str, Any],
    *,
    module_id: str,
    module_name: str,
    tasks: list[Mapping[str, Any]],
    enabled: bool = True,
) -> None:
    source = dict(source)
    source_id = str(
        source.get("source_id")
        or source.get("id")
        or _source_id(module_id)
    ).strip()
    if not source_id:
        return
    if not source_id.startswith(("source.", "knowledge.")):
        source_id = _source_id(module_id)
    task = tasks[0] if tasks else {}
    schedule = source.get("schedule")
    if not isinstance(schedule, Mapping):
        schedule = task.get("schedule") if isinstance(task, Mapping) else {}
    task_enabled = bool(schedule.get("enabled", True)) if isinstance(schedule, Mapping) else True
    declarations[source_id] = {
        "source_id": source_id,
        "module_id": module_id,
        "module_name": module_name or module_id,
        "enabled": bool(source.get("enabled", enabled)) and task_enabled,
        "adapter": str(
            source.get("adapter")
            or task.get("adapter")
            or source.get("connector")
            or "unknown"
        ),
        "locator": _locator(source),
        "schedule": dict(schedule) if isinstance(schedule, Mapping) else {},
        "config": source,
    }


def _locator(source: Mapping[str, Any]) -> str:
    for key in (
        "locator",
        "url",
        "feed_url",
        "listing_url",
        "official",
        "deepwiki",
        "github",
    ):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("listing_urls",):
        values = source.get(key)
        if isinstance(values, list) and values:
            return str(values[0])
    values = source.get("sources")
    if isinstance(values, list) and values:
        first = values[0]
        if isinstance(first, Mapping):
            return _locator(first)
    pages = source.get("pages")
    if isinstance(pages, list) and pages and isinstance(pages[0], Mapping):
        return str(pages[0].get("url") or "")
    return ""


def _module_status(module: Mapping[str, Any], sources: list[Mapping[str, Any]]) -> str:
    if not module.get("enabled", True):
        return "disabled"
    statuses = {str(row.get("status") or "") for row in sources}
    if "failed" in statuses:
        return "failed"
    if statuses and statuses <= {"success", "ok", "not_due"}:
        return "success"
    return "pending"


def _provider_rows(run_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = _mappings(run_report.get("providers"))
    by_id = {str(row.get("id")): dict(row) for row in raw if row.get("id")}
    defaults = (
        ("deepseek", "DeepSeek", False, "deepseek-chat"),
        ("google", "Google Translate", True, "google-translate-gtx"),
    )
    counts = _int_map(run_report.get("provider_counts"))
    failures = _int_map(run_report.get("provider_failures"))
    fallbacks = _int_map(run_report.get("provider_fallbacks"))
    rows: list[dict[str, Any]] = []
    for provider_id, name, configured, model in defaults:
        row = by_id.get(
            provider_id,
            {
                "id": provider_id,
                "name": name,
                "configured": configured,
                "model": model,
                "status": "configured" if configured else "not_configured",
            },
        )
        row["success_count"] = counts.get(provider_id, int(row.get("success_count") or 0))
        row["failure_count"] = failures.get(provider_id, int(row.get("failure_count") or 0))
        row["fallback_count"] = fallbacks.get(provider_id, int(row.get("fallback_count") or 0))
        if row["success_count"]:
            row["last_used_at"] = run_report.get("finished_at")
        row.pop("api_key", None)
        row.pop("token", None)
        rows.append(row)
    return rows


def _report_date(path: Path, payload: Mapping[str, Any]) -> str:
    value = str(payload.get("date") or "").strip()
    if value:
        return value[:10]
    generated = str(payload.get("generated_at") or "")
    if len(generated) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", generated[:10]):
        return generated[:10]
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
    return match.group(1) if match else ""


def _mappings(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value or [] if isinstance(row, Mapping)]


def _int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    output: dict[str, int] = {}
    for key, raw in value.items():
        try:
            output[str(key)] = int(raw or 0)
        except (TypeError, ValueError):
            continue
    return output


def _unique_errors(values: list[Any]) -> list[str]:
    errors: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in errors:
            errors.append(text[:500])
    return errors


def _public_path(prefix: str, name: str) -> str:
    base = "/" + str(prefix or "").strip().strip("/")
    if base == "/":
        return f"/{str(name).lstrip('/')}"
    return f"{base}/{str(name).lstrip('/')}"


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not Path(path).is_file():
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Radar monitor snapshot")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--run-report", required=True)
    parser.add_argument("--source-validation")
    parser.add_argument("--update-report")
    parser.add_argument("--out", required=True)
    parser.add_argument("--reports-root")
    parser.add_argument("--report-index-out")
    parser.add_argument("--public-prefix", default="/radar-content")
    parser.add_argument("--reports-prefix", default="")
    args = parser.parse_args(argv)
    out = Path(args.out)
    snapshot = build_monitor_snapshot(
        Path(args.target_root),
        Path(args.run_report),
        Path(args.source_validation) if args.source_validation else None,
        Path(args.update_report) if args.update_report else None,
        public_prefix=args.public_prefix,
        reports_prefix=args.reports_prefix or None,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.reports_root and args.report_index_out:
        index = build_report_index(
            Path(args.reports_root),
            public_prefix=(
                args.reports_prefix
                or f"{args.public_prefix.rstrip('/')}/reports"
            ),
        )
        index_path = Path(args.report_index_out)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    errors = validate_contract_file(out, "monitor")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if args.report_index_out:
        errors = validate_contract_file(Path(args.report_index_out), "report-index")
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
