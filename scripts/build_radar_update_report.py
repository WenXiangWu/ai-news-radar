#!/usr/bin/env python3
"""Build the Way-owned daily Radar module update report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validate_radar_contract import validate_contract_file


def build_update_report(
    target_root: Path,
    content_registry_path: Path,
    run_report_path: Path,
    source_validation_path: Path | None = None,
) -> dict[str, Any]:
    content_registry = _load_json(content_registry_path)
    run_report = _load_json(run_report_path)
    validation = (
        _load_json(source_validation_path)
        if source_validation_path is not None
        else {}
    )
    modules = _load_modules(Path(target_root))
    operations = _list_of_mappings(
        run_report.get("operations") or run_report.get("tasks")
    )
    verification = run_report.get("verification")
    verification_sources = {
        str(row.get("source_id")): dict(row)
        for row in _list_of_mappings(
            verification.get("sources") if isinstance(verification, Mapping) else []
        )
        if row.get("source_id")
    }
    validation_sources = {
        str(row.get("source_id")): dict(row)
        for row in _list_of_mappings(validation.get("sources"))
        if row.get("source_id")
    }
    content = _list_of_mappings(content_registry.get("content"))

    rows: list[dict[str, Any]] = []
    for module in modules:
        module_id = str(module["module_id"])
        source_ids = set(module["source_ids"])
        task_ids = set(module["task_ids"])
        module_operations = [
            operation
            for operation in operations
            if str(operation.get("task_id") or "") in task_ids
            or str(operation.get("module_id") or "") == module_id
            or str(operation.get("source_id") or "") in source_ids
        ]
        module_sources = [
            _merge_source_rows(
                verification_sources.get(source_id),
                validation_sources.get(source_id),
            )
            for source_id in sorted(source_ids)
            if source_id in verification_sources or source_id in validation_sources
        ]
        updated_ids = {
            str(content_id)
            for operation in module_operations
            for content_id in operation.get("updated_content_ids") or []
            if str(content_id).strip()
        }
        updated_content = [
            _content_row(item, Path(target_root))
            for item in content
            if str(item.get("source_id") or "") in source_ids
            and str(item.get("content_id") or "") in updated_ids
        ]
        translation_links = _translation_links(updated_content)
        task_rows = [_task_row(operation) for operation in module_operations]
        status = _module_status(
            module,
            task_rows,
            module_sources,
        )
        module_errors = [
            *_errors(module_operations, module_sources),
            *[
                f"translation file missing: {link['path']}"
                for link in translation_links
                if link.get("exists") is False
            ],
        ]
        if module_errors and status in {"success", "not_due"}:
            status = "failed"
        rows.append(
            {
                "module_id": module_id,
                "name": module["name"],
                "kind": module["kind"],
                "enabled": module["enabled"],
                "target_path": module.get("target_path"),
                "status": status,
                "task_ids": sorted(task_ids),
                "source_ids": sorted(source_ids),
                "tasks": task_rows,
                "sources": module_sources,
                "updated": sum(
                    int(operation.get("revisions_new") or 0)
                    for operation in module_operations
                ),
                "translated": sum(
                    int(operation.get("translated") or 0)
                    for operation in module_operations
                ),
                "translation_reused": sum(
                    int(operation.get("translation_reused") or 0)
                    for operation in module_operations
                ),
                "updated_content": updated_content,
                "translation_links": translation_links,
                "errors": module_errors,
            }
        )

    verification_status = (
        str(verification.get("status") or "")
        if isinstance(verification, Mapping)
        else ""
    )
    run_status = str(run_report.get("status") or "unknown")
    status = _overall_status(run_status, verification_status, rows)
    updated = sum(int(row["updated"]) for row in rows)
    translated = sum(int(row["translated"]) for row in rows)
    return {
        "schema": "radar-update-report/v1",
        "run_id": str(run_report.get("run_id") or ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "summary": {
            "modules": len(rows),
            "updated_modules": sum(bool(row["updated"]) for row in rows),
            "updated": updated,
            "translated": translated,
            "failed_modules": sum(row["status"] == "failed" for row in rows),
            "pending_modules": sum(row["status"] == "pending" for row in rows),
        },
        "verification": verification
        if isinstance(verification, Mapping)
        else {"status": "unknown", "sources": []},
        "source_validation": validation
        if isinstance(validation, Mapping)
        else {"status": "unknown", "sources": []},
        "modules": rows,
    }


def _load_modules(target_root: Path) -> list[dict[str, Any]]:
    registry_root = Path(target_root) / "radar" / "registry"
    modules: dict[str, dict[str, Any]] = {}
    index = _load_json(registry_root / "index.json")
    for reference in index.get("modules") or []:
        if not isinstance(reference, Mapping):
            continue
        path = registry_root / str(reference.get("manifest") or "")
        module = _load_json(path)
        if module:
            _add_module(modules, module)

    knowledge = _load_json(registry_root / "knowledge.json")
    for source in knowledge.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        module = {
            "id": source.get("module_id") or source.get("id"),
            "kind": "knowledge"
            if str(source.get("module_id") or "").startswith("knowledge.")
            else "source",
            "enabled": source.get("enabled", True),
            "display": {
                "name": source.get("label")
                or source.get("name")
                or source.get("module_id")
                or source.get("id")
            },
            "source": dict(source),
            "tasks": source.get("tasks") or [],
        }
        _add_module(modules, module)

    source_root = registry_root / "sources"
    for path in sorted(source_root.glob("*.json")):
        source = _load_json(path)
        if not source:
            continue
        module = {
            "id": source.get("module_id") or source.get("id"),
            "kind": "source",
            "enabled": source.get("enabled", True),
            "display": {"name": source.get("name") or source.get("id")},
            "source": dict(source),
            "tasks": [],
        }
        _add_module(modules, module)

    return [modules[module_id] for module_id in sorted(modules)]


def _add_module(target: dict[str, dict[str, Any]], module: Mapping[str, Any]) -> None:
    module_id = str(module.get("id") or "").strip()
    if not module_id:
        return
    display = module.get("display") if isinstance(module.get("display"), Mapping) else {}
    source = module.get("source") if isinstance(module.get("source"), Mapping) else {}
    existing = target.setdefault(
        module_id,
        {
            "module_id": module_id,
            "name": str(display.get("name") or module_id),
            "kind": str(module.get("kind") or "source"),
            "enabled": bool(module.get("enabled", True)),
            "target_path": display.get("target_path"),
            "task_ids": set(),
            "source_ids": set(),
        },
    )
    existing["name"] = str(display.get("name") or existing["name"])
    existing["kind"] = str(module.get("kind") or existing["kind"])
    existing["enabled"] = bool(module.get("enabled", True))
    existing["target_path"] = display.get("target_path") or existing.get("target_path")
    module_source_id = _source_id(module_id)
    existing["source_ids"].add(module_source_id)
    for task in module.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id") or "").strip()
        if task_id:
            existing["task_ids"].add(task_id)
        adapter = str(task.get("adapter") or source.get("adapter") or "")
        if adapter:
            existing["source_ids"].add(module_source_id)


def _source_id(module_id: str) -> str:
    return module_id if module_id.startswith("source.") else f"source.{module_id}"


def _task_row(operation: Mapping[str, Any]) -> dict[str, Any]:
    status = str(operation.get("status") or "unknown")
    if status == "success":
        status = "ok"
    return {
        "task_id": operation.get("task_id"),
        "status": status,
        "adapter": operation.get("adapter"),
        "source_id": operation.get("source_id"),
        "scheduled_at": operation.get("scheduled_at"),
        "next_run_at": operation.get("next_run_at"),
        "updated": int(operation.get("revisions_new") or 0),
        "translated": int(operation.get("translated") or 0),
        "summary": _summary(operation),
        "errors": list(operation.get("errors") or []),
    }


def _content_row(item: Mapping[str, Any], target_root: Path) -> dict[str, Any]:
    translations = [
        dict(translation)
        for translation in item.get("translations") or []
        if isinstance(translation, Mapping)
        and translation.get("status") == "translated"
    ]
    for translation in translations:
        if translation.get("path"):
            translation["url"] = _public_url(str(translation["path"]))
            translation["exists"] = (
                target_root / str(translation["path"])
            ).is_file()
    return {
        "content_id": item.get("content_id"),
        "title": item.get("title"),
        "canonical_url": item.get("canonical_url"),
        "revision_id": item.get("current_revision_id"),
        "translations": translations,
    }


def _translation_links(content: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    links: dict[str, dict[str, Any]] = {}
    for item in content:
        for translation in item.get("translations") or []:
            if not isinstance(translation, Mapping):
                continue
            path = str(translation.get("path") or "").strip()
            if not path:
                continue
            key = str(translation.get("translation_id") or path)
            links[key] = {
                "translation_id": translation.get("translation_id"),
                "content_id": item.get("content_id"),
                "language": translation.get("language"),
                "provider": translation.get("provider"),
                "path": path,
                "url": str(translation.get("url") or _public_url(path)),
                "artifact_path": translation.get("artifact_path"),
                "exists": translation.get("exists"),
            }
    return [links[key] for key in sorted(links)]


def _merge_source_rows(
    verification: Mapping[str, Any] | None,
    validation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    row = dict(verification or validation or {})
    if verification and validation:
        row["adapter_validation"] = {
            "status": validation.get("status"),
            "health": validation.get("health"),
            "errors": validation.get("errors") or [],
        }
    return row


def _module_status(
    module: Mapping[str, Any],
    tasks: list[Mapping[str, Any]],
    sources: list[Mapping[str, Any]],
) -> str:
    if not module.get("enabled", True):
        return "disabled"
    statuses = {str(task.get("status") or "") for task in tasks}
    if "failed" in statuses or any(
        str(source.get("status") or "") == "failed" for source in sources
    ):
        return "failed"
    if "partial" in statuses or any(
        str(source.get("status") or "") == "partial" for source in sources
    ):
        return "partial"
    if tasks:
        return "success" if statuses <= {"ok", "success", "skipped"} else "pending"
    if any(str(source.get("status") or "") == "pending" for source in sources):
        return "pending"
    return "not_due"


def _overall_status(
    run_status: str,
    verification_status: str,
    modules: list[Mapping[str, Any]],
) -> str:
    if run_status in {"failed", "partial"}:
        return run_status
    if verification_status == "failed":
        return "failed"
    if any(str(module.get("status") or "") == "failed" for module in modules):
        return "failed"
    if verification_status == "pending" or any(
        str(module.get("status") or "") == "pending" for module in modules
    ):
        return "pending"
    return "success"


def _summary(operation: Mapping[str, Any]) -> str:
    return (
        f"发现 {int(operation.get('discovered') or 0)} · "
        f"抓取 {int(operation.get('fetched') or 0)} · "
        f"更新 {int(operation.get('revisions_new') or 0)} · "
        f"翻译 {int(operation.get('translated') or 0)}"
    )


def _errors(
    operations: list[Mapping[str, Any]],
    sources: list[Mapping[str, Any]],
) -> list[str]:
    values: list[str] = []
    for row in [*operations, *sources]:
        raw = row.get("errors") or []
        if not isinstance(raw, list):
            raw = [raw]
        for error in raw:
            text = str(error).strip()
            if text and text not in values:
                values.append(text[:500])
    return values


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value or [] if isinstance(row, Mapping)]


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not Path(path).is_file():
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _public_url(path: str) -> str:
    normalized = path.replace("\\", "/").lstrip("/")
    if normalized.startswith("frontend/"):
        normalized = normalized.removeprefix("frontend/")
    return "/" + normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Radar module update report")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--content-registry", required=True)
    parser.add_argument("--run-report", required=True)
    parser.add_argument("--source-validation")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = build_update_report(
        Path(args.target_root),
        Path(args.content_registry),
        Path(args.run_report),
        Path(args.source_validation) if args.source_validation else None,
    )
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    errors = validate_contract_file(target, "update-report")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"wrote {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
