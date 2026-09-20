#!/usr/bin/env python3
"""Load, validate, and reconcile the way-to-agentic Radar registry."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROTOCOL = "way-to-agentic-radar"
SUPPORTED_TASK_KINDS = {
    "news_sync",
    "knowledge_sync",
    "docs_sync",
    "wiki_sync",
    "translation",
    "index_sync",
    "outlook_sync",
}


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def _safe_relative(value: str, *, label: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a safe relative path")
    return path.as_posix()


def _normalize_schedule(schedule: dict[str, Any] | None, defaults: dict[str, Any]) -> dict[str, Any]:
    raw = dict(defaults)
    raw.update(schedule or {})
    raw["enabled"] = bool(raw.get("enabled", True))
    raw["timezone"] = str(raw.get("timezone") or "Asia/Shanghai")
    raw["cron"] = str(raw.get("cron") or "17 * * * *").strip()
    raw["retry_count"] = max(0, int(raw.get("retry_count") or 0))
    raw["max_runtime_minutes"] = max(1, int(raw.get("max_runtime_minutes") or 30))
    raw["max_new_items"] = max(0, int(raw.get("max_new_items") or 0))
    return raw


def _normalize_task(
    task: dict[str, Any],
    *,
    module_id: str,
    defaults: dict[str, Any],
    fallback_output: str = "",
) -> dict[str, Any]:
    out = dict(task)
    out["id"] = str(out.get("id") or f"task.{module_id}.{out.get('kind', 'run')}")
    out["module_id"] = module_id
    out["kind"] = str(out.get("kind") or "index_sync")
    out["adapter"] = str(out.get("adapter") or "")
    out["schedule"] = _normalize_schedule(out.get("schedule"), defaults)
    output = out.get("output")
    if not isinstance(output, dict):
        output = {"path": fallback_output}
    else:
        output = dict(output)
    if output.get("path"):
        output["path"] = _safe_relative(str(output["path"]), label=f"{out['id']}.output.path")
    if output.get("paths"):
        raw_paths = output.get("paths")
        if not isinstance(raw_paths, list):
            raise ValueError(f"{out['id']}.output.paths must be a list")
        output["paths"] = [
            _safe_relative(str(path), label=f"{out['id']}.output.paths")
            for path in raw_paths
        ]
    out["output"] = output
    depends_on = out.get("depends_on")
    out["depends_on"] = [str(x) for x in depends_on] if isinstance(depends_on, list) else []
    return out


def _legacy_module(item: dict[str, Any], spec: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    item_id = str(item.get("id") or "").strip()
    module_id = f"{spec.get('kind') or 'framework'}.{item_id}"
    root = _safe_relative(str(item.get("root") or ""), label=f"{module_id}.root")
    adapter = str(item.get(spec.get("adapter_field") or "adapter") or spec.get("adapter") or "")
    if spec.get("task_kind") == "wiki_sync":
        source = {
            "legacy_id": item_id,
            "github": str(item.get("github") or ""),
        }
    else:
        source = {
            "legacy_id": item_id,
            "official": str(item.get("official") or ""),
        }
    task = _normalize_task(
        {
            "id": f"task.{module_id}.{'wiki' if spec.get('task_kind') == 'wiki_sync' else 'docs'}",
            "kind": spec.get("task_kind") or "index_sync",
            "adapter": adapter,
            "source": source,
            "output": {"path": root},
            "schedule": spec.get("schedule") or {},
        },
        module_id=module_id,
        defaults=defaults,
        fallback_output=root,
    )
    translation_task = _normalize_task(
        {
            "id": f"task.{module_id}.translation",
            "kind": "translation",
            "adapter": "markdown_google",
            "depends_on": [task["id"]],
            "source": {
                "stale_manifest": f"{root}/_sync/stale.json",
                "source_root": root,
            },
            "output": {"path": f"{root}/zh"},
            "schedule": spec.get("translation_schedule") or {},
        },
        module_id=module_id,
        defaults=defaults,
        fallback_output=f"{root}/zh",
    )
    return {
        "id": module_id,
        "kind": str(spec.get("kind") or "framework"),
        "enabled": True,
        "display": {
            "name": str(item.get("name") or item_id),
            "target_path": root,
        },
        "source": source,
        "legacy": {
            "registry": str(spec.get("path") or ""),
            "id": item_id,
        },
        "tasks": [task, translation_task],
    }


def _load_legacy_modules(target_root: Path, index: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for spec in index.get("legacy_imports") or []:
        if not isinstance(spec, dict):
            continue
        path = target_root / _safe_relative(str(spec.get("path") or ""), label="legacy_import.path")
        payload = _read_json(path, {})
        items = payload.get(str(spec.get("items_key") or "items")) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                modules.append(_legacy_module(item, spec, defaults))
    return modules


def _load_explicit_modules(target_root: Path, index: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    base = target_root / "radar" / "registry"
    for ref in index.get("modules") or []:
        if isinstance(ref, str):
            manifest_path = base / _safe_relative(ref, label="module.manifest")
        elif isinstance(ref, dict):
            manifest_path = base / _safe_relative(
                str(ref.get("manifest") or ""), label="module.manifest"
            )
        else:
            continue
        module = dict(_read_json(manifest_path, {}))
        module_id = str(module.get("id") or "").strip()
        if not module_id:
            continue
        module["enabled"] = bool(module.get("enabled", True))
        display = module.get("display")
        if not isinstance(display, dict):
            display = {}
        if display.get("target_path"):
            display["target_path"] = _safe_relative(
                str(display["target_path"]), label=f"{module_id}.display.target_path"
            )
        module["display"] = display
        tasks = module.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
        module["tasks"] = [
            _normalize_task(task, module_id=module_id, defaults=defaults)
            for task in tasks
            if isinstance(task, dict)
        ]
        modules.append(module)
    return modules


def _load_knowledge_modules(target_root: Path, index: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    rel = str(index.get("knowledge_registry") or "").strip()
    if not rel:
        return []
    payload = _read_json(
        target_root
        / "radar"
        / "registry"
        / _safe_relative(rel, label="knowledge_registry"),
        {},
    )
    modules: list[dict[str, Any]] = []
    for source in payload.get("sources") or []:
        if not isinstance(source, dict) or not source.get("module_id"):
            continue
        module_id = str(source["module_id"])
        output = _safe_relative(str(source.get("output") or ""), label=f"{module_id}.output")
        source_adapter = str(source.get("adapter") or "")
        tasks = [
            _normalize_task(
                {
                    **task,
                    "adapter": str(task.get("adapter") or source_adapter),
                },
                module_id=module_id,
                defaults=defaults,
                fallback_output=output,
            )
            for task in (source.get("tasks") or [])
            if isinstance(task, dict)
        ]
        modules.append(
            {
                "id": module_id,
                "kind": "knowledge" if source.get("fulltext") else "source",
                "enabled": bool(source.get("enabled", True)),
                "display": {
                    "name": str(source.get("label") or source.get("id")),
                    "target_path": output,
                },
                "source": {
                    "legacy_id": str(source.get("id") or ""),
                    "adapter": str(source.get("adapter") or ""),
                },
                "tasks": tasks,
            }
        )
    return modules


def _merge_modules(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for module in modules:
        module_id = str(module.get("id") or "")
        if not module_id:
            continue
        if module_id not in by_id:
            by_id[module_id] = dict(module)
            by_id[module_id]["tasks"] = list(module.get("tasks") or [])
            order.append(module_id)
            continue
        current = by_id[module_id]
        for key, value in module.items():
            if key == "tasks":
                continue
            if value not in (None, "", {}, []):
                current[key] = value
        seen = {str(task.get("id")) for task in current.get("tasks") or [] if isinstance(task, dict)}
        for task in module.get("tasks") or []:
            if not isinstance(task, dict) or str(task.get("id")) in seen:
                continue
            current.setdefault("tasks", []).append(task)
            seen.add(str(task.get("id")))
    return [by_id[module_id] for module_id in order]


def load_registry(target_root: Path) -> dict[str, Any]:
    """Load explicit, legacy-imported, and knowledge modules into one registry."""
    target_root = Path(target_root).resolve()
    protocol = _read_json(target_root / "radar/protocol.json", {})
    index = _read_json(target_root / "radar/registry/index.json", {})
    if protocol.get("protocol") != PROTOCOL:
        raise ValueError(f"unsupported Radar protocol: {protocol.get('protocol')!r}")
    defaults = dict(index.get("defaults") or {})
    modules = _load_explicit_modules(target_root, index, defaults)
    modules.extend(_load_legacy_modules(target_root, index, defaults))
    modules.extend(_load_knowledge_modules(target_root, index, defaults))
    registry = {
        "protocol": protocol,
        "schema": index.get("schema"),
        "defaults": defaults,
        "managed_roots": [
            _safe_relative(str(root), label="managed_root")
            for root in (index.get("managed_roots") or [])
        ],
        "modules": _merge_modules(modules),
    }
    errors = validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    return registry


def flatten_tasks(registry: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for module in registry.get("modules") or []:
        if not isinstance(module, dict):
            continue
        for task in module.get("tasks") or []:
            if isinstance(task, dict):
                tasks.append(task)
    return tasks


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if registry.get("schema") != "radar-registry/v1":
        errors.append("unsupported registry schema")
    protocol = registry.get("protocol") or {}
    if protocol.get("protocol") != PROTOCOL or str(protocol.get("version")) != "1.0":
        errors.append("unsupported protocol version")

    module_ids: set[str] = set()
    task_ids: set[str] = set()
    for module in registry.get("modules") or []:
        if not isinstance(module, dict):
            errors.append("module must be an object")
            continue
        module_id = str(module.get("id") or "")
        if not module_id:
            errors.append("module id is required")
        elif module_id in module_ids:
            errors.append(f"duplicate module id: {module_id}")
        module_ids.add(module_id)
        for task in module.get("tasks") or []:
            if not isinstance(task, dict):
                errors.append(f"{module_id} task must be an object")
                continue
            task_id = str(task.get("id") or "")
            if not task_id:
                errors.append(f"{module_id} task id is required")
            elif task_id in task_ids:
                errors.append(f"duplicate task id: {task_id}")
            task_ids.add(task_id)
            kind = str(task.get("kind") or "")
            if kind not in SUPPORTED_TASK_KINDS:
                errors.append(f"{task_id} unsupported task kind: {kind}")
            schedule = task.get("schedule") or {}
            try:
                ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
            except Exception:  # noqa: BLE001
                errors.append(f"{task_id} invalid timezone")
            cron = str(schedule.get("cron") or "").split()
            if len(cron) != 5:
                errors.append(f"{task_id} cron must contain five fields")
            output = task.get("output") or {}
            if not isinstance(output, dict):
                output = {}
            if output.get("path"):
                try:
                    _safe_relative(str(output["path"]), label=f"{task_id}.output.path")
                except ValueError as exc:
                    errors.append(str(exc))
            raw_paths = output.get("paths")
            if raw_paths is not None:
                if not isinstance(raw_paths, list):
                    errors.append(f"{task_id}.output.paths must be a list")
                else:
                    for path in raw_paths:
                        try:
                            _safe_relative(str(path), label=f"{task_id}.output.paths")
                        except ValueError as exc:
                            errors.append(str(exc))
        display = module.get("display") or {}
        if display.get("target_path"):
            try:
                _safe_relative(str(display["target_path"]), label=f"{module_id}.display.target_path")
            except ValueError as exc:
                errors.append(str(exc))
    return errors


def _tree_fingerprint(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {"sha256": "", "file_count": 0}
    digest = hashlib.sha256()
    count = 0
    paths = [path for path in root.rglob("*") if path.is_file()]
    for path in sorted(paths):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
        count += 1
    return {"sha256": digest.hexdigest(), "file_count": count}


def _module_output_paths(module: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    display = module.get("display") or {}
    if display.get("target_path"):
        paths.append(str(display["target_path"]))
    for task in module.get("tasks") or []:
        output = task.get("output") if isinstance(task, dict) else None
        if not isinstance(output, dict):
            continue
        if output.get("path"):
            paths.append(str(output["path"]))
        if isinstance(output.get("paths"), list):
            paths.extend(str(path) for path in output["paths"] if path)
    return list(dict.fromkeys(paths))


def _orphaned_outputs(target_root: Path, registry: dict[str, Any]) -> list[str]:
    declared = []
    for module in registry.get("modules") or []:
        declared.extend(_module_output_paths(module))
    managed_roots = registry.get("managed_roots") or []
    orphaned: list[str] = []
    for root_rel in managed_roots:
        root = target_root / root_rel
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name in {
                "sync",
                "wiki",
                "_shared",
                "00-outline",
                "_preview",
                "scripts",
                "logos",
                "jsmanifest",
            }:
                continue
            child_rel = child.relative_to(target_root).as_posix()
            if not any(
                path == child_rel
                or path.startswith(child_rel + "/")
                or child_rel.startswith(path + "/")
                for path in declared
            ):
                orphaned.append(child_rel)
    return orphaned


def reconcile_registry(target_root: Path, registry: dict[str, Any], state_path: Path) -> dict[str, Any]:
    """Compare desired registry state with target output and persist safe state."""
    previous = _read_json(state_path, {"modules": {}}) if state_path.is_file() else {"modules": {}}
    previous_modules = previous.get("modules") if isinstance(previous, dict) else {}
    if not isinstance(previous_modules, dict):
        previous_modules = {}
    now = datetime.now(timezone.utc).isoformat()
    current_state: dict[str, Any] = {}
    added: list[str] = []
    changed: list[str] = []
    blocked: list[str] = []

    for module in registry.get("modules") or []:
        module_id = str(module.get("id") or "")
        signature = hashlib.sha256(
            json.dumps(module, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        prior = previous_modules.get(module_id) if isinstance(previous_modules.get(module_id), dict) else {}
        if not prior:
            added.append(module_id)
        elif prior.get("signature") != signature:
            changed.append(module_id)
        paths = _module_output_paths(module)
        existing = [path for path in paths if (target_root / path).exists()]
        bootstrap = module.get("bootstrap") if isinstance(module.get("bootstrap"), dict) else {}
        if bootstrap.get("mode") == "adopt_existing" and existing and not prior.get("baseline"):
            status = "pending_bootstrap"
            baseline = _tree_fingerprint(target_root / existing[0])
        elif not module.get("enabled", True):
            status = "disabled"
            baseline = prior.get("baseline")
        elif not existing:
            status = "new"
            baseline = prior.get("baseline")
        else:
            status = "managed"
            baseline = prior.get("baseline")
        if not module_id or not (module.get("tasks") or []):
            blocked.append(module_id or "<missing>")
        current_state[module_id] = {
            "signature": signature,
            "status": status,
            "checked_at": now,
            "baseline": baseline,
            "output_paths": paths,
        }

    state = {"schema": "radar-registry-state/v1", "updated_at": now, "modules": current_state}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "added_modules": added,
        "changed_modules": changed,
        "orphaned_outputs": _orphaned_outputs(target_root, registry),
        "blocked_modules": blocked,
        "modules": current_state,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate and reconcile a way-to-agentic Radar registry")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    target_root = Path(args.target_root).resolve()
    registry = load_registry(target_root)
    reconciliation = reconcile_registry(target_root, registry, Path(args.state))
    output = {
        "schema": "radar-registry-report/v1",
        "registry": registry,
        "reconciliation": reconciliation,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
