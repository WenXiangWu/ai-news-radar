from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo


SUPPORTED_PROTOCOL = "way-to-agentic-radar"
SUPPORTED_REGISTRY_SCHEMA = "way-content-registry/v1"
LEGACY_REGISTRY_SCHEMA = "radar-registry/v1"
SUPPORTED_REGISTRY_SCHEMAS = {SUPPORTED_REGISTRY_SCHEMA, LEGACY_REGISTRY_SCHEMA}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_WAY_MANIFEST_SCHEMAS = {
    "sources": "radar-content-contract/v1/source",
    "entities": "radar-content-contract/v1/entity",
    "surfaces": "radar-content-contract/v1/surface",
    "editorial": "way-content-registry/v1/editorial",
}


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


def _sqlite_path(value: Any, default: str = "var/radar/state.sqlite3") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    path = Path(raw)
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return path.as_posix()
    if path.suffix:
        return path.with_suffix(".sqlite3").as_posix()
    return Path(f"{path}.sqlite3").as_posix()


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
        "contract_root",
    ):
        if key in normalized:
            normalized[key] = _safe_relative(normalized[key], f"protocol.{key}")
    if "registry_index" not in normalized:
        raise ValueError("protocol.registry_index is required")
    if "state_path" in normalized:
        state_path = _safe_relative(normalized["state_path"], "protocol.state_path")
        if Path(state_path).suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            normalized["way_state_path"] = state_path
            normalized["state_path"] = _sqlite_path(None)
        else:
            normalized["state_path"] = state_path
    else:
        normalized["state_path"] = _sqlite_path(None)
    return normalized


def load_protocol(target_root: Path) -> Dict[str, Any]:
    """Load and validate the Way protocol declaration."""

    root = Path(target_root)
    return _normalize_protocol(_read_json(root / "radar/protocol.json"))


def _cron_number(value: str, minimum: int, maximum: int) -> bool:
    if not value.isdigit():
        return False
    return minimum <= int(value) <= maximum


def _valid_cron(value: Any) -> bool:
    fields = str(value or "").split()
    if len(fields) != 5:
        return False
    for field, (minimum, maximum) in zip(fields, _CRON_RANGES):
        for item in field.split(","):
            if not item:
                return False
            base, separator, raw_step = item.partition("/")
            if separator and (not raw_step.isdigit() or int(raw_step) <= 0):
                return False
            if base == "*":
                continue
            if "-" in base:
                parts = base.split("-")
                if len(parts) != 2 or not all(
                    _cron_number(part, minimum, maximum) for part in parts
                ):
                    return False
                if int(parts[0]) > int(parts[1]):
                    return False
                continue
            if not _cron_number(base, minimum, maximum):
                return False
    return True


def _validate_path(errors: List[str], value: Any, label: str) -> None:
    try:
        _safe_relative(value, label)
    except ValueError as exc:
        errors.append(f"unsafe path: {exc}")


def _validate_schedule(errors: List[str], schedule: Any, label: str) -> None:
    if not isinstance(schedule, dict):
        errors.append(f"{label} must be an object")
        return
    if not _valid_cron(schedule.get("cron")):
        errors.append(f"{label}.cron is invalid")
    timezone = str(schedule.get("timezone") or "Asia/Shanghai")
    try:
        ZoneInfo(timezone)
    except Exception:  # noqa: BLE001
        errors.append(f"{label}.timezone is invalid")


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
                    _validate_path(errors, path, f"{task_id}.output.paths[{index}]")
    _validate_schedule(errors, task.get("schedule") or {}, f"{task_id or module_id}.schedule")


def _validate_legacy_registry(document: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if document.get("schema") != LEGACY_REGISTRY_SCHEMA:
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
        tasks = module.get("tasks")
        if tasks is None and module.get("manifest"):
            continue
        if not isinstance(tasks, list) or not tasks:
            errors.append(f"{module_id or index}.tasks is required")
            continue
        for task in tasks:
            _validate_task(errors, task, module_id=module_id or str(index), task_ids=task_ids)
    return errors


def _validate_way_index(document: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    contract_keys = {
        "sources": "source",
        "entities": "entity",
        "surfaces": "surface",
        "editorial": "editorial",
    }
    if document.get("schema") != SUPPORTED_REGISTRY_SCHEMA:
        errors.append("registry.schema is unsupported")
    if document.get("version") != "v1":
        errors.append("registry.version is unsupported")
    roots = document.get("manifest_roots")
    if not isinstance(roots, dict):
        errors.append("manifest_roots must be an object")
    else:
        for kind in _WAY_MANIFEST_SCHEMAS:
            if kind not in roots:
                errors.append(f"manifest_roots.{kind} is required")
            else:
                try:
                    _safe_relative(roots[kind], f"manifest_roots.{kind}")
                except ValueError as exc:
                    errors.append(str(exc))
    contracts = document.get("contracts")
    if not isinstance(contracts, dict):
        errors.append("contracts must be an object")
    else:
        for kind in _WAY_MANIFEST_SCHEMAS:
            contract_key = contract_keys[kind]
            if contract_key not in contracts:
                errors.append(f"contracts.{contract_key} is required")
            else:
                _validate_path(errors, contracts[contract_key], f"contracts.{contract_key}")
    if document.get("knowledge_registry") is not None:
        _validate_path(
            errors,
            document["knowledge_registry"],
            "knowledge_registry",
        )
    modules = document.get("modules", [])
    if not isinstance(modules, list):
        errors.append("modules must be a list")
    else:
        module_ids: set[str] = set()
        manifest_paths: set[str] = set()
        for index, reference in enumerate(modules):
            if not isinstance(reference, dict):
                errors.append(f"modules[{index}] must be an object")
                continue
            module_id = str(reference.get("id") or "").strip()
            if not module_id or not _ID_RE.fullmatch(module_id):
                errors.append(f"modules[{index}].id is invalid")
            elif module_id in module_ids:
                errors.append(f"duplicate module id: {module_id}")
            else:
                module_ids.add(module_id)
            manifest = reference.get("manifest")
            try:
                normalized_manifest = _safe_relative(manifest, f"modules[{index}].manifest")
            except ValueError as exc:
                errors.append(str(exc))
            else:
                if normalized_manifest in manifest_paths:
                    errors.append(f"duplicate module manifest: {normalized_manifest}")
                manifest_paths.add(normalized_manifest)
    return errors


def _validate_id(errors: List[str], value: Any, prefix: str, label: str) -> str:
    identifier = str(value or "").strip()
    if not identifier or not identifier.startswith(prefix) or not _ID_RE.fullmatch(identifier):
        errors.append(f"{label} is invalid")
    return identifier


def _validate_way_manifest(payload: Dict[str, Any], kind: str) -> List[str]:
    errors: List[str] = []
    expected_schema = _WAY_MANIFEST_SCHEMAS[kind]
    if payload.get("schema") != expected_schema:
        errors.append(f"{kind}.schema is invalid")
    if kind == "sources":
        identifier = _validate_id(errors, payload.get("id"), "source.", "source.id")
        if payload.get("kind") != "source":
            errors.append("source.kind is invalid")
        for key in ("source_type", "name", "locator", "output_root"):
            if not str(payload.get(key) or "").strip():
                errors.append(f"{identifier or 'source'}.{key} is required")
        _validate_path(errors, payload.get("output_root"), f"{identifier}.output_root")
        _validate_schedule(errors, payload.get("schedule"), f"{identifier}.schedule")
    elif kind == "entities":
        identifier = _validate_id(errors, payload.get("id"), "framework.", "entity.id")
        if payload.get("kind") != "framework":
            errors.append("entity.kind is invalid")
        if not str(payload.get("name") or "").strip():
            errors.append(f"{identifier or 'entity'}.name is required")
        source_ids = payload.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            errors.append(f"{identifier or 'entity'}.source_ids is required")
        else:
            for source_id in source_ids:
                _validate_id(errors, source_id, "source.", f"{identifier}.source_ids")
        _validate_id(errors, payload.get("default_surface_id"), "surface.", f"{identifier}.default_surface_id")
        _validate_path(errors, payload.get("content_root"), f"{identifier}.content_root")
    elif kind == "surfaces":
        identifier = _validate_id(errors, payload.get("id"), "surface.", "surface.id")
        if payload.get("kind") != "surface":
            errors.append("surface.kind is invalid")
        if not str(payload.get("surface_type") or "").strip():
            errors.append(f"{identifier or 'surface'}.surface_type is required")
        _validate_id(errors, payload.get("entity_id"), "framework.", f"{identifier}.entity_id")
        _validate_path(errors, payload.get("path"), f"{identifier}.path")
        if not str(payload.get("locale") or "").strip():
            errors.append(f"{identifier or 'surface'}.locale is required")
    else:
        identifier = _validate_id(errors, payload.get("id"), "editorial.", "editorial.id")
        if payload.get("kind") != "editorial":
            errors.append("editorial.kind is invalid")
        _validate_id(errors, payload.get("entity_id"), "framework.", f"{identifier}.entity_id")
        _validate_id(errors, payload.get("surface_id"), "surface.", f"{identifier}.surface_id")
        if not str(payload.get("title") or "").strip():
            errors.append(f"{identifier or 'editorial'}.title is required")
        route = str(payload.get("route") or "")
        if not route.startswith("/") or ".." in Path(route).parts:
            errors.append(f"{identifier or 'editorial'}.route is invalid")
    return errors


def _validate_way_module(
    payload: Dict[str, Any],
    *,
    expected_id: str,
    task_ids: set[str],
) -> List[str]:
    errors: List[str] = []
    if payload.get("schema") != "way-content-registry/v1/module":
        errors.append("module.schema is invalid")
    module_id = str(payload.get("id") or "").strip()
    if not module_id or not _ID_RE.fullmatch(module_id):
        errors.append("module.id is invalid")
    elif module_id != expected_id:
        errors.append(f"module.id does not match index reference: {module_id} != {expected_id}")
    if payload.get("kind") not in {"framework", "source"}:
        errors.append(f"{module_id or expected_id}.kind is invalid")
    display = payload.get("display")
    if not isinstance(display, dict):
        errors.append(f"{module_id or expected_id}.display must be an object")
    elif display.get("target_path"):
        _validate_path(
            errors,
            display["target_path"],
            f"{module_id or expected_id}.display.target_path",
        )
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append(f"{module_id or expected_id}.tasks is required")
    else:
        for task in tasks:
            _validate_task(
                errors,
                task,
                module_id=module_id or expected_id,
                task_ids=task_ids,
            )
    return errors


def validate_contract_document(
    document: Dict[str, Any],
    schema_name: str,
) -> List[str]:
    """Return structural contract errors without executing any adapter."""

    if schema_name == LEGACY_REGISTRY_SCHEMA:
        return _validate_legacy_registry(document)
    if schema_name == SUPPORTED_REGISTRY_SCHEMA:
        return _validate_way_index(document)
    return [f"unsupported contract schema: {schema_name}"]


def _resolve_under(root: Path, relative: str, label: str) -> Path:
    root_path = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside registry root: {relative}") from exc
    return candidate


def _iter_manifest_files(root: Path) -> Iterable[Path]:
    root_path = root.resolve()
    paths = sorted(path for path in root.rglob("*.json") if path.is_file())
    for path in paths:
        try:
            path.resolve().relative_to(root_path)
        except ValueError as exc:
            raise ValueError(f"manifest path resolves outside registry root: {path}") from exc
    return paths


def _load_way_registry(root: Path, index_path: Path, index: Dict[str, Any]) -> Dict[str, Any]:
    errors = _validate_way_index(index)
    if errors:
        raise ValueError("invalid Way registry index: " + "; ".join(errors))
    registry_root = index_path.parent
    manifests: Dict[str, List[Dict[str, Any]]] = {kind: [] for kind in _WAY_MANIFEST_SCHEMAS}
    by_id: Dict[str, Dict[str, Any]] = {}
    for kind in _WAY_MANIFEST_SCHEMAS:
        relative_root = _safe_relative(index["manifest_roots"][kind], f"manifest_roots.{kind}")
        manifest_root = _resolve_under(registry_root, relative_root, f"manifest_roots.{kind}")
        if not manifest_root.is_dir():
            raise ValueError(f"missing manifest root: {manifest_root}")
        for manifest_path in _iter_manifest_files(manifest_root):
            payload = _read_json(manifest_path)
            manifest_errors = _validate_way_manifest(payload, kind)
            if manifest_errors:
                raise ValueError(
                    f"invalid {kind} manifest {manifest_path}: " + "; ".join(manifest_errors)
                )
            identifier = str(payload["id"])
            if identifier in by_id:
                raise ValueError(f"duplicate manifest id: {identifier}")
            by_id[identifier] = payload
            manifests[kind].append(payload)

    source_ids = {item["id"] for item in manifests["sources"]}
    entity_ids = {item["id"] for item in manifests["entities"]}
    surface_ids = {item["id"] for item in manifests["surfaces"]}
    cross_errors: List[str] = []
    for entity in manifests["entities"]:
        for source_id in entity.get("source_ids", []):
            if source_id not in source_ids:
                cross_errors.append(f"{entity['id']} references missing source {source_id}")
        if entity["default_surface_id"] not in surface_ids:
            cross_errors.append(
                f"{entity['id']} references missing surface {entity['default_surface_id']}"
            )
    for surface in manifests["surfaces"]:
        if surface["entity_id"] not in entity_ids:
            cross_errors.append(f"{surface['id']} references missing entity {surface['entity_id']}")
        for source_id in surface.get("source_ids", []):
            if source_id not in source_ids:
                cross_errors.append(f"{surface['id']} references missing source {source_id}")
    for editorial in manifests["editorial"]:
        if editorial["entity_id"] not in entity_ids:
            cross_errors.append(f"{editorial['id']} references missing entity {editorial['entity_id']}")
        if editorial["surface_id"] not in surface_ids:
            cross_errors.append(f"{editorial['id']} references missing surface {editorial['surface_id']}")
    if cross_errors:
        raise ValueError("invalid Way registry references: " + "; ".join(cross_errors))

    entities_by_source = {
        source["id"]: [entity for entity in manifests["entities"] if source["id"] in entity["source_ids"]]
        for source in manifests["sources"]
    }
    declarations: List[Dict[str, Any]] = []
    for source in manifests["sources"]:
        entities = entities_by_source[source["id"]] or [None]
        for entity in entities:
            surfaces = [
                surface
                for surface in manifests["surfaces"]
                if entity
                and surface["entity_id"] == entity["id"]
                and (not surface.get("source_ids") or source["id"] in surface["source_ids"])
            ] or [None]
            for surface in surfaces:
                output_path = (
                    surface.get("path")
                    if surface
                    else entity.get("content_root") if entity else source["output_root"]
                )
                declarations.append(
                    {
                        "id": ".".join(
                            part
                            for part in (
                                source["id"],
                                entity["id"] if entity else "unassigned",
                                surface["id"] if surface else "default",
                            )
                            if part
                        ),
                        "source_id": source["id"],
                        "entity_id": entity["id"] if entity else None,
                        "surface_id": surface["id"] if surface else None,
                        "schedule": dict(source["schedule"]),
                        "translation_profile": str(source.get("translation_profile") or "prose/v1"),
                        "output": {"path": output_path},
                        "source": source,
                        "entity": entity,
                        "surface": surface,
                    }
                )
    explicit_modules: List[Dict[str, Any]] = []
    explicit_task_ids: set[str] = set()
    for reference in index.get("modules") or []:
        relative_manifest = _safe_relative(reference["manifest"], "module.manifest")
        manifest_path = _resolve_under(registry_root, relative_manifest, "module.manifest")
        module = _read_json(manifest_path)
        module_errors = _validate_way_module(
            module,
            expected_id=str(reference["id"]),
            task_ids=explicit_task_ids,
        )
        if module_errors:
            raise ValueError(
                f"invalid module manifest {manifest_path}: " + "; ".join(module_errors)
            )
        loaded = dict(module)
        loaded["manifest"] = relative_manifest
        explicit_modules.append(loaded)

    normalized = dict(index)
    normalized["manifests"] = manifests
    normalized["declarations"] = declarations
    normalized["modules"] = explicit_modules
    return normalized


def _load_legacy_registry(root: Path, index_path: Path, index: Dict[str, Any]) -> Dict[str, Any]:
    index_errors = _validate_legacy_registry(index)
    if index_errors:
        raise ValueError("invalid Radar registry index: " + "; ".join(index_errors))
    modules: List[Dict[str, Any]] = []
    for reference in index.get("modules") or []:
        if not isinstance(reference, dict):
            raise ValueError("registry.modules entries must be objects")
        raw_manifest = reference.get("manifest")
        try:
            relative_manifest = _safe_relative(raw_manifest, "module.manifest")
        except ValueError as exc:
            raise ValueError(f"invalid module manifest reference: {exc}") from exc
        manifest_path = index_path.parent / relative_manifest
        module = _read_json(manifest_path)
        module_errors = _validate_legacy_registry(
            {"schema": LEGACY_REGISTRY_SCHEMA, "modules": [module], "managed_roots": []}
        )
        if module_errors:
            raise ValueError(
                f"invalid module manifest {manifest_path}: " + "; ".join(module_errors)
            )
        loaded = dict(module)
        loaded["manifest"] = relative_manifest
        modules.append(loaded)
    normalized = dict(index)
    normalized["modules"] = modules
    errors = _validate_legacy_registry(normalized)
    if errors:
        raise ValueError("invalid Radar registry: " + "; ".join(errors))
    return normalized


def load_registry_document(target_root: Path) -> Dict[str, Any]:
    """Load, validate, and normalize the registry declared by Way."""

    root = Path(target_root)
    protocol = load_protocol(root)
    registry_path = root / protocol["registry_index"]
    registry = _read_json(registry_path)
    schema = registry.get("schema")
    if schema == SUPPORTED_REGISTRY_SCHEMA:
        return _load_way_registry(root, registry_path, registry)
    if schema == LEGACY_REGISTRY_SCHEMA:
        return _load_legacy_registry(root, registry_path, registry)
    errors = validate_contract_document(registry, str(schema or ""))
    raise ValueError("invalid Radar registry: " + "; ".join(errors))
