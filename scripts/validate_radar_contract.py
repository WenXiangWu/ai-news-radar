"""Validate Radar v1 contracts and load Way-owned registry declarations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker


CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "radar" / "contracts" / "v1"
SCHEMA_FILES = {
    "source": "source.schema.json",
    "entity": "entity.schema.json",
    "surface": "surface.schema.json",
    "content-item": "content-item.schema.json",
    "revision": "revision.schema.json",
    "translation": "translation.schema.json",
    "export": "export.schema.json",
    "run-report": "run-report.schema.json",
    "source-validation": "source-validation.schema.json",
    "update-report": "update-report.schema.json",
    "monitor": "monitor.schema.json",
    "report-index": "report-index.schema.json",
    "editorial": "editorial.schema.json",
    "module": "module.schema.json",
}
PATH_FIELDS = {"output_root", "content_root", "path", "raw_path"}
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


@dataclass(frozen=True)
class WayRegistry:
    sources: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    surfaces: list[dict[str, Any]]
    editorial: list[dict[str, Any]]
    modules: list[dict[str, Any]]
    content_ids: list[str]


def _schema_name(schema_name: str) -> str:
    normalized = schema_name.removesuffix(".schema.json")
    if normalized in SCHEMA_FILES:
        return normalized
    for name, filename in SCHEMA_FILES.items():
        if schema_name == filename:
            return name
    raise ValueError(f"unknown contract schema: {schema_name}")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"manifest does not exist: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg}") from exc


def _format_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    return f"{location or '<root>'}: {error.message}"


def _cron_field_errors(expression: str, position: int) -> list[str]:
    minimum, maximum = CRON_RANGES[position]
    errors: list[str] = []
    for token in expression.split(","):
        if not token:
            errors.append("schedule.cron contains an empty field")
            continue
        base, separator, step_text = token.partition("/")
        if separator:
            if not step_text.isdigit() or int(step_text) <= 0:
                errors.append(f"schedule.cron step must be > 0: {token}")
                continue
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            bounds = base.split("-")
            if len(bounds) != 2 or not all(value.isdigit() for value in bounds):
                errors.append(f"schedule.cron has invalid range: {token}")
                continue
            start, end = (int(value) for value in bounds)
            if start > end:
                errors.append(f"schedule.cron range is reversed: {token}")
                continue
        elif base.isdigit():
            start = end = int(base)
        else:
            errors.append(f"schedule.cron has invalid token: {token}")
            continue
        if start < minimum or end > maximum:
            errors.append(
                f"schedule.cron value outside {minimum}-{maximum}: {token}"
            )
    return errors


def _schedule_errors(schedule: Any) -> list[str]:
    if not isinstance(schedule, dict):
        return ["schedule must be an object"]
    errors: list[str] = []
    timezone = schedule.get("timezone")
    if not isinstance(timezone, str) or not timezone:
        errors.append("schedule.timezone must be a valid IANA timezone")
    else:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"schedule.timezone is unknown: {timezone}")
    cron = schedule.get("cron")
    if not isinstance(cron, str):
        errors.append("schedule.cron must contain five fields")
    else:
        fields = cron.split()
        if len(fields) != 5:
            errors.append("schedule.cron must contain exactly five fields")
        else:
            for position, expression in enumerate(fields):
                errors.extend(_cron_field_errors(expression, position))
    return errors


def _is_under(path: str, parent: str) -> bool:
    path_parts = Path(path).as_posix().strip("/").split("/")
    parent_parts = Path(parent).as_posix().strip("/").split("/")
    return bool(path_parts and path_parts[: len(parent_parts)] == parent_parts)


def _is_strictly_under(path: str, parent: str) -> bool:
    if not _is_under(path, parent):
        return False
    path_parts = Path(path).as_posix().strip("/").split("/")
    parent_parts = Path(parent).as_posix().strip("/").split("/")
    return len(path_parts) > len(parent_parts)


def _semantic_errors(payload: Any, schema_name: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    errors: list[str] = []
    if schema_name == "source":
        errors.extend(f"{error}" for error in _schedule_errors(payload.get("schedule")))
    elif schema_name == "module":
        for index, task in enumerate(payload.get("tasks", [])):
            if isinstance(task, dict):
                for error in _schedule_errors(task.get("schedule")):
                    errors.append(f"tasks.{index}.{error}")
    elif schema_name == "export":
        bundle_root = payload.get("bundle_root")
        content_root = payload.get("content_root")
        if isinstance(bundle_root, str) and isinstance(content_root, str):
            if _is_under(bundle_root, content_root) or _is_under(content_root, bundle_root):
                errors.append("bundle_root and content_root must be separate owned roots")
        if isinstance(bundle_root, str):
            checksums_path = payload.get("checksums_path")
            if isinstance(checksums_path, str) and not _is_strictly_under(checksums_path, bundle_root):
                errors.append("checksums_path must be inside bundle_root")
            item_ids = set(payload.get("items", []))
            revision_ids = set(payload.get("revisions", []))
            translation_ids = set(payload.get("translations", []))
            artifact_ids: set[str] = set()
            artifact_paths: set[str] = set()
            for index, artifact in enumerate(payload.get("artifacts", [])):
                if isinstance(artifact, dict):
                    artifact_path = artifact.get("path")
                    if isinstance(artifact_path, str) and not _is_strictly_under(artifact_path, bundle_root):
                        errors.append(f"artifacts.{index}.path must be inside bundle_root")
                    if artifact.get("artifact_id") in artifact_ids:
                        errors.append(f"artifacts.{index}.artifact_id must be unique")
                    artifact_ids.add(artifact.get("artifact_id"))
                    if artifact_path in artifact_paths:
                        errors.append(f"artifacts.{index}.path must be unique")
                    artifact_paths.add(artifact_path)
                    if artifact.get("content_id") not in item_ids:
                        errors.append(f"artifacts.{index}.content_id must be declared in items")
                    if artifact.get("revision_id") not in revision_ids:
                        errors.append(f"artifacts.{index}.revision_id must be declared in revisions")
                    translation_id = artifact.get("translation_id")
                    if artifact.get("kind") == "translation" and translation_id is None:
                        errors.append(f"artifacts.{index}.translation_id is required for translation artifacts")
                    if translation_id is not None and translation_id not in translation_ids:
                        errors.append(f"artifacts.{index}.translation_id must be declared in translations")
    return errors


def validate_contract_file(path: Path, schema_name: str) -> list[str]:
    """Return schema errors for a contract JSON file; an empty list is valid."""

    try:
        normalized_name = _schema_name(schema_name)
        schema_path = CONTRACT_ROOT / SCHEMA_FILES[normalized_name]
        if not schema_path.is_file():
            return [f"missing contract schema: {schema_path}"]
        payload = _load_json(path)
        schema = _load_json(schema_path)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = [_format_error(error) for error in validator.iter_errors(payload)]
        errors.extend(_semantic_errors(payload, normalized_name))
        return sorted(errors)
    except (OSError, TypeError, ValueError) as exc:
        return [str(exc)]


def _safe_relative_path(value: str, base: Path) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or Path(value).is_absolute()
    ):
        raise ValueError(f"unsafe manifest path: {value!r}")
    candidate = (base / value).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"manifest path escapes registry root: {value!r}") from exc
    return candidate


def _safe_manifest_file(path: Path, registry_root: Path) -> Path:
    path = Path(path)
    if "\\" in str(path) or "\x00" in str(path):
        raise ValueError(f"unsafe manifest path: {path}")
    if path.is_absolute():
        candidate = path
    else:
        candidate = registry_root / path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(registry_root.resolve())
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"manifest path escapes registry root or does not exist: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"manifest is not a file: {path}")
    return resolved


def _load_manifest(path: Path, schema_name: str, registry_root: Path) -> dict[str, Any]:
    safe_path = _safe_manifest_file(path, registry_root)
    payload = _load_json(safe_path)
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be an object: {safe_path}")
    errors = validate_contract_file(safe_path, schema_name)
    if errors:
        raise ValueError(f"invalid {schema_name} manifest {safe_path}: {'; '.join(errors)}")
    return payload


def _load_directory(registry_root: Path, relative_root: str, schema_name: str) -> list[dict[str, Any]]:
    directory = _safe_relative_path(relative_root, registry_root)
    if not directory.is_dir():
        raise ValueError(f"manifest directory does not exist: {relative_root}")
    manifests: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        manifests.append(_load_manifest(path, schema_name, registry_root))
    return manifests


def _validate_module_paths(value: Any, repo_root: Path, location: str) -> None:
    """Validate repository-relative paths nested in a module declaration."""

    path_fields = {
        "content_root",
        "output_root",
        "path",
        "paths",
        "raw_path",
        "source_root",
        "stale_manifest",
        "target_path",
    }
    if not isinstance(value, dict):
        return
    for field, field_value in value.items():
        field_location = f"{location}.{field}"
        if field not in path_fields:
            if isinstance(field_value, dict):
                _validate_module_paths(field_value, repo_root, field_location)
            elif isinstance(field_value, list):
                for index, item in enumerate(field_value):
                    if isinstance(item, dict):
                        _validate_module_paths(item, repo_root, f"{field_location}[{index}]")
            continue
        values = field_value if field == "paths" else [field_value]
        if not isinstance(values, list):
            raise ValueError(f"{field_location} must contain repository-relative paths")
        for index, path_value in enumerate(values):
            if not isinstance(path_value, str):
                raise ValueError(f"{field_location}[{index}] must be a repository-relative path")
            try:
                _safe_relative_path(path_value, repo_root)
            except ValueError as exc:
                raise ValueError(f"{field_location}: {exc}") from exc


def _ensure_unique(items: list[dict[str, Any]], category: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item["id"]
        if item_id in indexed:
            raise ValueError(f"duplicate {category} id: {item_id}")
        indexed[item_id] = item
    return indexed


def _ensure_id_parts(item_id: str, prefix: str, category: str) -> None:
    parts = item_id.split(".", 1)
    if len(parts) != 2 or parts[0] != prefix or not ID_RE.fullmatch(parts[1]):
        raise ValueError(f"invalid {category} identity: {item_id}")


def _validate_protocol_state(root: Path, repo_root: Path) -> None:
    candidates = [root / "protocol.json", repo_root / "radar" / "protocol.json"]
    protocol_path = next((path for path in candidates if path.is_file()), None)
    if protocol_path is None:
        return
    protocol = _load_json(protocol_path)
    state_path = protocol.get("state_path") if isinstance(protocol, dict) else None
    if not isinstance(state_path, str) or not state_path.endswith(".sqlite3"):
        raise ValueError("Radar durable state_path must be a SQLite .sqlite3 path")
    state_resolved = _safe_relative_path(state_path, repo_root)
    for field in (
        "job_report_path",
        "export_manifest_path",
        "source_validation_path",
        "update_report_path",
        "monitor_path",
        "report_history_root",
        "report_index_path",
        "radar_content_root",
    ):
        other_path = protocol.get(field)
        if not isinstance(other_path, str):
            continue
        if state_resolved == _safe_relative_path(other_path, repo_root):
            raise ValueError(f"Radar state_path must be separate from {field}")


def load_way_registry(root: Path) -> WayRegistry:
    """Load and validate the Way-owned source/entity/surface/editorial registry."""

    root = root.resolve()
    registry_root = root / "radar" / "registry"
    repo_root = root
    if not registry_root.is_dir() and (root / "registry").is_dir():
        registry_root = root / "registry"
        repo_root = root
    registry_root = registry_root.resolve()
    _validate_protocol_state(root, repo_root)
    index_path = registry_root / "index.json"
    index = _load_json(index_path)
    if not isinstance(index, dict) or index.get("schema") != "way-content-registry/v1":
        raise ValueError("registry index must declare way-content-registry/v1")
    if index.get("version") != "v1":
        raise ValueError("registry index must declare version v1")
    if "legacy_imports" in index:
        raise ValueError("legacy imports are not allowed in way-content-registry/v1")
    roots = index.get("manifest_roots")
    expected = {"sources", "entities", "surfaces", "editorial"}
    if not isinstance(roots, dict) or set(roots) != expected:
        raise ValueError("registry index must declare sources, entities, surfaces, and editorial roots")
    if any(not isinstance(value, str) for value in roots.values()):
        raise ValueError("registry manifest roots must be relative strings")
    knowledge_registry = index.get("knowledge_registry")
    if knowledge_registry is not None:
        if not isinstance(knowledge_registry, str):
            raise ValueError("knowledge_registry must be a repository-relative path")
        _safe_manifest_file(knowledge_registry, registry_root)

    sources = _load_directory(registry_root, roots["sources"], "source")
    entities = _load_directory(registry_root, roots["entities"], "entity")
    surfaces = _load_directory(registry_root, roots["surfaces"], "surface")
    editorial = _load_directory(registry_root, roots["editorial"], "editorial")
    if "content_references" not in index:
        raise ValueError("registry index must declare content_references")
    content_ids = index.get("content_references", [])
    if not isinstance(content_ids, list) or not all(isinstance(item, str) for item in content_ids):
        raise ValueError("content_references must be a list of content IDs")
    if len(content_ids) != len(set(content_ids)):
        raise ValueError("content_references contain duplicate IDs")

    module_refs = index.get("modules", [])
    if not isinstance(module_refs, list):
        raise ValueError("registry modules must be a list")
    modules: list[dict[str, Any]] = []
    module_ids: set[str] = set()
    module_paths: set[Path] = set()
    for reference in module_refs:
        if not isinstance(reference, dict) or not isinstance(reference.get("id"), str) or not isinstance(reference.get("manifest"), str):
            raise ValueError("each module reference must contain id and manifest")
        reference_id = reference["id"]
        if reference_id in module_ids:
            raise ValueError(f"duplicate module id: {reference_id}")
        module_ids.add(reference_id)
        manifest_reference = reference["manifest"]
        if Path(manifest_reference).is_absolute():
            raise ValueError(f"module manifest must be repository-relative: {manifest_reference}")
        module_path = _safe_manifest_file(manifest_reference, registry_root)
        if module_path in module_paths:
            raise ValueError(f"duplicate module manifest: {manifest_reference}")
        module_paths.add(module_path)
        module = _load_manifest(module_path, "module", registry_root)
        if module["id"] != reference["id"]:
            raise ValueError(f"module reference ID does not match manifest: {reference['manifest']}")
        modules.append(module)

    source_by_id = _ensure_unique(sources, "source")
    entity_by_id = _ensure_unique(entities, "entity")
    surface_by_id = _ensure_unique(surfaces, "surface")
    _ensure_unique(editorial, "editorial")
    for item in sources:
        _ensure_id_parts(item["id"], "source", "source")
        for field in PATH_FIELDS:
            if field in item:
                _safe_relative_path(item[field], root)
    for item in entities:
        _ensure_id_parts(item["id"], "framework", "entity")
        if item["default_surface_id"] not in surface_by_id:
            raise ValueError(f"entity references unknown surface: {item['default_surface_id']}")
        if any(source_id not in source_by_id for source_id in item["source_ids"]):
            raise ValueError(f"entity references unknown source: {item['id']}")
        _safe_relative_path(item["content_root"], root)
    for item in surfaces:
        _ensure_id_parts(item["id"], "surface", "surface")
        if item["entity_id"] not in entity_by_id:
            raise ValueError(f"surface references unknown entity: {item['entity_id']}")
        _safe_relative_path(item["path"], root)
    for item in editorial:
        _ensure_id_parts(item["id"], "editorial", "editorial")
        if item["entity_id"] not in entity_by_id:
            raise ValueError(f"editorial references unknown entity: {item['entity_id']}")
        if item["surface_id"] not in surface_by_id:
            raise ValueError(f"editorial references unknown surface: {item['surface_id']}")
        unknown_content_ids = [content_id for content_id in item["content_ids"] if content_id not in content_ids]
        if unknown_content_ids:
            raise ValueError(f"editorial references unknown content: {unknown_content_ids[0]}")
    for module in modules:
        module_prefix = module["id"].split(".", 1)[0]
        if module_prefix not in {"framework", "source", "knowledge", "outlook"}:
            raise ValueError(f"invalid module identity: {module['id']}")
        if module["kind"] != module_prefix:
            raise ValueError(f"module kind does not match identity: {module['id']}")
        _ensure_id_parts(module["id"], module_prefix, "module")
        _safe_relative_path(module["display"]["target_path"], repo_root)
        task_ids: set[str] = set()
        for task in module["tasks"]:
            if task["id"] in task_ids:
                raise ValueError(f"duplicate task id in module {module['id']}: {task['id']}")
            task_ids.add(task["id"])
            _validate_module_paths(task, repo_root, f"module.{module['id']}.task.{task['id']}")
            output = task["output"]
            output_paths = [output["path"]] if "path" in output else output["paths"]
            for output_path in output_paths:
                _safe_relative_path(output_path, repo_root)

    return WayRegistry(
        sources=sources,
        entities=entities,
        surfaces=surfaces,
        editorial=editorial,
        modules=modules,
        content_ids=content_ids,
    )
