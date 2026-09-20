from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo


SUPPORTED_PROTOCOL = "way-to-agentic-radar"
SUPPORTED_REGISTRY_SCHEMA = "radar-registry/v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing contract document: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON contract: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"contract document must be an object: {path}")
    return payload


def _safe_relative(value: Any, label: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a safe relative path")
    return path.as_posix()


def _normalize_protocol(protocol: Dict[str, Any]) -> Dict[str, Any]:
    if protocol.get("protocol") != SUPPORTED_PROTOCOL:
        raise ValueError(f"unsupported protocol: {protocol.get('protocol')!r}")
    version = str(protocol.get("version") or "")
    if not version.startswith("1."):
        raise ValueError(f"unsupported protocol version: {version!r}")
    normalized = dict(protocol)
    for key in (
        "registry_root",
        "registry_index",
        "job_report_path",
        "export_manifest_path",
        "state_path",
    ):
        if key in normalized:
            normalized[key] = _safe_relative(normalized[key], f"protocol.{key}")
    if "registry_index" not in normalized:
        raise ValueError("protocol.registry_index is required")
    return normalized


def load_protocol(target_root: Path) -> Dict[str, Any]:
    """Load and validate the Way protocol declaration."""

    root = Path(target_root)
    return _normalize_protocol(_read_json(root / "radar/protocol.json"))


def load_registry_document(target_root: Path) -> Dict[str, Any]:
    """Load and validate the registry index referenced by the protocol."""

    root = Path(target_root)
    protocol = load_protocol(root)
    registry = _read_json(root / protocol["registry_index"])
    errors = validate_contract_document(registry, SUPPORTED_REGISTRY_SCHEMA)
    if errors:
        raise ValueError("invalid Radar registry: " + "; ".join(errors))
    return registry


def _valid_cron(value: Any) -> bool:
    fields = str(value or "").split()
    if len(fields) != 5:
        return False
    allowed = re.compile(r"^[0-9*/?,\-\s]+$")
    return all(bool(allowed.fullmatch(field)) for field in fields)


def _validate_path(errors: List[str], value: Any, label: str) -> None:
    try:
        _safe_relative(value, label)
    except ValueError as exc:
        errors.append(f"unsafe path: {exc}")


def _validate_task(
    errors: List[str],
    task: Any,
    *,
    module_id: str,
    task_ids: set[str],
) -> None:
    if not isinstance(task, dict):
        errors.append(f"{module_id}.task must be an object")
        return
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        errors.append(f"{module_id}.task missing id")
    elif task_id in task_ids:
        errors.append(f"duplicate task id: {task_id}")
    else:
        task_ids.add(task_id)
    adapter = str(task.get("adapter") or "").strip()
    if not adapter:
        errors.append(f"{task_id or module_id}.adapter is required")
    output = task.get("output")
    if not isinstance(output, dict) or not (output.get("path") or output.get("paths")):
        errors.append(f"{task_id or module_id}.output is required")
    elif isinstance(output, dict):
        if output.get("path"):
            _validate_path(errors, output["path"], f"{task_id}.output.path")
        if output.get("paths"):
            if not isinstance(output["paths"], list):
                errors.append(f"{task_id}.output.paths must be a list")
            else:
                for index, path in enumerate(output["paths"]):
                    _validate_path(
                        errors,
                        path,
                        f"{task_id}.output.paths[{index}]",
                    )
    schedule = task.get("schedule") or {}
    if not isinstance(schedule, dict):
        errors.append(f"{task_id or module_id}.schedule must be an object")
        return
    if not _valid_cron(schedule.get("cron")):
        errors.append(f"{task_id or module_id}.schedule.cron is invalid")
    timezone = str(schedule.get("timezone") or "Asia/Shanghai")
    try:
        ZoneInfo(timezone)
    except Exception:  # noqa: BLE001
        errors.append(f"{task_id or module_id}.schedule.timezone is invalid")


def validate_contract_document(
    document: Dict[str, Any],
    schema_name: str,
) -> List[str]:
    """Return structural contract errors without executing any adapter."""

    errors: List[str] = []
    if schema_name != SUPPORTED_REGISTRY_SCHEMA:
        return [f"unsupported contract schema: {schema_name}"]
    if not isinstance(document, dict):
        return ["registry must be an object"]
    if document.get("schema") != SUPPORTED_REGISTRY_SCHEMA:
        errors.append("registry.schema is unsupported")
    managed_roots = document.get("managed_roots") or []
    if not isinstance(managed_roots, list):
        errors.append("managed_roots must be a list")
    else:
        for index, path in enumerate(managed_roots):
            _validate_path(errors, path, f"managed_roots[{index}]")

    modules = document.get("modules") or []
    if not isinstance(modules, list):
        errors.append("modules must be a list")
        return errors
    module_ids: set[str] = set()
    task_ids: set[str] = set()
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            errors.append(f"modules[{index}] must be an object")
            continue
        module_id = str(module.get("id") or "").strip()
        if not module_id:
            errors.append(f"modules[{index}] missing id")
        elif not _ID_RE.fullmatch(module_id):
            errors.append(f"invalid module id: {module_id}")
        elif module_id in module_ids:
            errors.append(f"duplicate module id: {module_id}")
        else:
            module_ids.add(module_id)
        if module.get("manifest"):
            _validate_path(errors, module["manifest"], f"{module_id}.manifest")
        display = module.get("display") or {}
        if isinstance(display, dict) and display.get("target_path"):
            _validate_path(errors, display["target_path"], f"{module_id}.display.target_path")
        if module.get("manifest") and "tasks" not in module:
            _validate_path(errors, module["manifest"], f"{module_id}.manifest")
            continue
        tasks = module.get("tasks") or []
        if not isinstance(tasks, list) or not tasks:
            errors.append(f"{module_id or index}.tasks is required")
            continue
        for task in tasks:
            _validate_task(errors, task, module_id=module_id or str(index), task_ids=task_ids)
    return errors
