from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    SUPPORTED_REGISTRY_SCHEMA,
    _read_json,
    _resolve_under,
    _safe_relative,
    _validate_way_index,
    _validate_way_manifest,
    _validate_way_module,
    load_protocol,
    load_registry_document,
)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _diagnostics() -> dict[str, Any]:
    return {
        "added_sources": [],
        "changed_sources": [],
        "disabled_sources": [],
        "invalid_declarations": [],
        "next_run_at": {},
    }


def _source_type_for_adapter(adapter: str, fallback: str = "") -> str:
    normalized = str(adapter or "").strip()
    mapped = {
        "rss": "rss",
        "rss_article": "rss",
        "llms_txt": "llms_txt",
        "github_tree": "github",
        "deepwiki": "deepwiki",
        "local_import": "local",
        "knowledge_source": "local",
        "html_collection": "official_blog",
        "static_pages": "official_blog",
        "composite": "official_blog",
    }.get(normalized)
    if mapped:
        return mapped
    if fallback in {
        "official_blog",
        "rss",
        "llms_txt",
        "github",
        "deepwiki",
        "local",
    }:
        return fallback
    return "local"


@dataclass(frozen=True)
class SourceSpec:
    id: str
    source_type: str
    name: str
    locator: str
    output_root: str
    schedule: dict[str, Any]
    enabled: bool = True
    translation_profile: str = "prose/v1"
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SourceSpec":
        raw = dict(payload)
        source_id = str(raw.get("id") or "").strip()
        if not source_id:
            raise ValueError("source.id is required")
        schedule = raw.get("schedule")
        normalized_schedule = dict(schedule) if isinstance(schedule, Mapping) else {}
        return cls(
            id=source_id,
            source_type=str(raw.get("source_type") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            locator=str(raw.get("locator") or "").strip(),
            output_root=str(raw.get("output_root") or "").strip(),
            schedule=normalized_schedule,
            enabled=bool(raw.get("enabled", True)),
            translation_profile=str(raw.get("translation_profile") or "prose/v1"),
            payload=raw,
            fingerprint=_fingerprint(raw),
        )

    @property
    def source_id(self) -> str:
        return self.id

    @property
    def registry_fingerprint(self) -> str:
        return self.fingerprint

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class EntitySpec:
    id: str
    name: str
    source_ids: tuple[str, ...]
    default_surface_id: str
    content_root: str
    enabled: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EntitySpec":
        raw = dict(payload)
        entity_id = str(raw.get("id") or "").strip()
        if not entity_id:
            raise ValueError("entity.id is required")
        source_ids = raw.get("source_ids")
        normalized_source_ids = tuple(
            str(source_id).strip()
            for source_id in source_ids
            if str(source_id).strip()
        ) if isinstance(source_ids, list) else ()
        return cls(
            id=entity_id,
            name=str(raw.get("name") or "").strip(),
            source_ids=normalized_source_ids,
            default_surface_id=str(raw.get("default_surface_id") or "").strip(),
            content_root=str(raw.get("content_root") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
            payload=raw,
            fingerprint=_fingerprint(raw),
        )

    @property
    def entity_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class SurfaceSpec:
    id: str
    surface_type: str
    entity_id: str
    path: str
    locale: str
    source_ids: tuple[str, ...] = ()
    enabled: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SurfaceSpec":
        raw = dict(payload)
        surface_id = str(raw.get("id") or "").strip()
        if not surface_id:
            raise ValueError("surface.id is required")
        source_ids = raw.get("source_ids")
        normalized_source_ids = tuple(
            str(source_id).strip()
            for source_id in source_ids
            if str(source_id).strip()
        ) if isinstance(source_ids, list) else ()
        return cls(
            id=surface_id,
            surface_type=str(raw.get("surface_type") or "").strip(),
            entity_id=str(raw.get("entity_id") or "").strip(),
            path=str(raw.get("path") or "").strip(),
            locale=str(raw.get("locale") or "").strip(),
            source_ids=normalized_source_ids,
            enabled=bool(raw.get("enabled", True)),
            payload=raw,
            fingerprint=_fingerprint(raw),
        )

    @property
    def surface_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _merge_mappings(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mappings(merged[key], value)
        else:
            merged[key] = value
    return merged


def _task_locator(source: Mapping[str, Any], fallback: str) -> str:
    for key in (
        "locator",
        "url",
        "feed_url",
        "llms_url",
        "listing_url",
        "deepwiki",
        "deepwiki_url",
        "official",
        "github",
        "repo",
    ):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    pages = source.get("pages")
    if isinstance(pages, list) and pages:
        first = pages[0]
        if isinstance(first, Mapping):
            value = str(first.get("url") or first.get("fetch_url") or "").strip()
            if value:
                return value
    for key in ("listing_urls", "sources"):
        values = source.get(key)
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, Mapping):
                value = str(
                    first.get("locator")
                    or first.get("url")
                    or first.get("feed_url")
                    or first.get("llms_url")
                    or ""
                ).strip()
                if value:
                    return value
            value = str(first).strip()
            if value:
                return value
    return fallback


@dataclass(frozen=True)
class TaskSpec:
    id: str
    module_id: str
    kind: str
    adapter: str
    source: dict[str, Any]
    output: dict[str, Any]
    schedule: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    enabled: bool = True
    module_kind: str = ""
    module_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        module: Mapping[str, Any],
    ) -> "TaskSpec":
        raw = dict(payload)
        module_id = str(module.get("id") or "").strip()
        task_id = str(raw.get("id") or "").strip()
        if not task_id:
            raise ValueError(f"{module_id}.task.id is required")
        source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
        module_source = module.get("source")
        merged_source = _merge_mappings(
            module_source if isinstance(module_source, Mapping) else {},
            source,
        )
        adapter = str(
            raw.get("adapter")
            or merged_source.get("adapter")
            or ""
        ).strip()
        output = raw.get("output") if isinstance(raw.get("output"), Mapping) else {}
        schedule = raw.get("schedule") if isinstance(raw.get("schedule"), Mapping) else {}
        depends_on = raw.get("depends_on")
        normalized_depends = tuple(
            str(value).strip()
            for value in depends_on
            if str(value).strip()
        ) if isinstance(depends_on, list) else ()
        fingerprint = _fingerprint(raw)
        return cls(
            id=task_id,
            module_id=module_id,
            kind=str(raw.get("kind") or "").strip(),
            adapter=adapter,
            source=dict(merged_source),
            output=dict(output),
            schedule=dict(schedule),
            depends_on=normalized_depends,
            enabled=bool(module.get("enabled", True))
            and bool(raw.get("enabled", True))
            and bool(schedule.get("enabled", True)),
            module_kind=str(module.get("kind") or "").strip(),
            module_name=str(
                (module.get("display") or {}).get("name")
                if isinstance(module.get("display"), Mapping)
                else module_id
            ).strip(),
            payload=raw,
            fingerprint=fingerprint,
        )

    @property
    def source_id(self) -> str:
        if self.module_id.startswith("source."):
            return self.module_id
        return f"source.{self.module_id}"

    @property
    def registry_fingerprint(self) -> str:
        return self.fingerprint

    def to_source_spec(self) -> SourceSpec:
        output_root = str(
            self.output.get("path")
            or (
                self.output.get("paths")[0]
                if isinstance(self.output.get("paths"), list) and self.output.get("paths")
                else ""
            )
        ).strip()
        locator = _task_locator(self.source, self.module_id)
        payload = {
            "schema": "radar-content-contract/v1/source",
            "id": self.source_id,
            "kind": "source",
            "source_type": _source_type_for_adapter(self.adapter, self.kind),
            "adapter": self.adapter,
            "name": self.module_name or self.module_id,
            "locator": locator,
            "schedule": dict(self.schedule),
            "output_root": output_root or f"frontend/{self.module_id}",
            "translation_profile": str(
                self.payload.get("translation_profile") or "prose/v1"
            ),
            "enabled": self.enabled,
            "module_id": self.module_id,
            "task_id": self.id,
            "source": dict(self.source),
        }
        return SourceSpec.from_payload(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            **dict(self.payload),
            "id": self.id,
            "module_id": self.module_id,
            "source": dict(self.source),
            "output": dict(self.output),
            "schedule": dict(self.schedule),
            "depends_on": list(self.depends_on),
        }


@dataclass
class Registry:
    sources: list[SourceSpec]
    entities: list[EntitySpec] = field(default_factory=list)
    surfaces: list[SurfaceSpec] = field(default_factory=list)
    tasks: list[TaskSpec] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=_diagnostics)

    @classmethod
    def from_target_root(cls, target_root: Path) -> "Registry":
        return load_registry(target_root)

    def __post_init__(self) -> None:
        self.sources = [
            source
            if isinstance(source, SourceSpec)
            else SourceSpec.from_payload(source)
            for source in self.sources
        ]
        self.entities = [
            entity
            if isinstance(entity, EntitySpec)
            else EntitySpec.from_payload(entity)
            for entity in self.entities
        ]
        self.surfaces = [
            surface
            if isinstance(surface, SurfaceSpec)
            else SurfaceSpec.from_payload(surface)
            for surface in self.surfaces
        ]
        self.tasks = [
            task
            if isinstance(task, TaskSpec)
            else TaskSpec.from_payload(
                task,
                module=task.get("module") if isinstance(task, Mapping) else {},
            )
            for task in self.tasks
        ]
        defaults = _diagnostics()
        defaults.update(self.diagnostics or {})
        for key, value in defaults.items():
            if key not in self.diagnostics:
                self.diagnostics[key] = value

    def reset_diagnostics(self) -> None:
        self.diagnostics.clear()
        self.diagnostics.update(_diagnostics())

    def source(self, source_id: str) -> SourceSpec | None:
        return next((item for item in self.sources if item.id == source_id), None)

    def entities_for_source(self, source_id: str) -> list[EntitySpec]:
        return [
            entity
            for entity in self.entities
            if entity.enabled and source_id in entity.source_ids
        ]

    def surfaces_for_source(self, source_id: str) -> list[SurfaceSpec]:
        entity_ids = {
            entity.id
            for entity in self.entities_for_source(source_id)
        }
        return [
            surface
            for surface in self.surfaces
            if surface.enabled
            and (
                source_id in surface.source_ids
                or surface.entity_id in entity_ids
            )
        ]


def _manifest_payloads(document: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    manifests = document.get("manifests")
    if isinstance(manifests, Mapping):
        payloads = manifests.get(kind)
        if isinstance(payloads, list):
            return [dict(payload) for payload in payloads if isinstance(payload, Mapping)]
    return []


def _read_json_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing knowledge registry: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid knowledge registry: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"knowledge registry must be an object: {path}")
    return payload


def _knowledge_registry_payload(
    target_root: Path,
    document: Mapping[str, Any],
) -> dict[str, Any] | None:
    relative = str(document.get("knowledge_registry") or "").strip()
    if not relative:
        return None
    raw_path = Path(relative.replace("\\", "/"))
    if raw_path.is_absolute() or ".." in raw_path.parts:
        raise ValueError("knowledge_registry must be a safe relative path")
    registry_root = (Path(target_root) / "radar/registry").resolve()
    path = (registry_root / raw_path).resolve()
    try:
        path.relative_to(registry_root)
    except ValueError as exc:
        raise ValueError("knowledge_registry resolves outside registry root") from exc
    return _read_json_document(path)


def _knowledge_source_payloads(
    target_root: Path,
    document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    payload = _knowledge_registry_payload(target_root, document)
    if payload is None:
        return []
    entries = payload.get("sources")
    if not isinstance(entries, list):
        raise ValueError("knowledge registry sources must be a list")
    defaults = document.get("defaults")
    default_schedule = dict(defaults) if isinstance(defaults, Mapping) else {}
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("knowledge registry source must be an object")
        raw = dict(entry)
        legacy_id = str(raw.get("id") or "").strip()
        module_id = str(raw.get("module_id") or legacy_id).strip()
        if not module_id:
            raise ValueError("knowledge registry source requires id or module_id")
        source_id = (
            module_id
            if module_id.startswith("source.")
            else f"source.{module_id}"
        )
        tasks = raw.get("tasks")
        task_schedules = [
            task.get("schedule")
            for task in tasks
            if isinstance(task, Mapping) and isinstance(task.get("schedule"), Mapping)
        ] if isinstance(tasks, list) else []
        schedule = dict(task_schedules[0]) if task_schedules else default_schedule
        output_root = str(raw.get("output") or raw.get("output_root") or "").strip()
        locator = str(
            raw.get("locator")
            or raw.get("url")
            or raw.get("feed_url")
            or raw.get("official")
            or raw.get("github")
            or raw.get("listing_url")
            or (
                raw.get("listing_urls")[0]
                if isinstance(raw.get("listing_urls"), list)
                and raw.get("listing_urls")
                else ""
            )
            or (
                raw.get("pages")[0].get("url")
                if isinstance(raw.get("pages"), list)
                and raw.get("pages")
                and isinstance(raw.get("pages")[0], Mapping)
                else ""
            )
            or (
                raw.get("sources")[0].get("url")
                if isinstance(raw.get("sources"), list)
                and raw.get("sources")
                and isinstance(raw.get("sources")[0], Mapping)
                else ""
            )
            or legacy_id
            or source_id
        ).strip()
        normalized.append(
            {
                **raw,
                "schema": "radar-content-contract/v1/source",
                "id": source_id,
                "kind": "source",
                "source_type": _source_type_for_adapter(
                    str(raw.get("adapter") or ""),
                    str(raw.get("source_type") or ""),
                ),
                "name": str(raw.get("name") or raw.get("label") or legacy_id),
                "locator": locator,
                "schedule": schedule,
                "output_root": output_root,
                "enabled": bool(raw.get("enabled", True)),
                "legacy_id": legacy_id,
                "module_id": module_id,
            }
        )
    return normalized


def _source_payloads(
    target_root: Path,
    document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    payloads = _manifest_payloads(document, "sources")
    payloads.extend(_knowledge_source_payloads(target_root, document))
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for payload in payloads:
        source_id = str(payload.get("id") or "")
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        unique.append(payload)
    return unique


def _task_specs(
    target_root: Path,
    document: Mapping[str, Any],
) -> list[TaskSpec]:
    specs: list[TaskSpec] = []
    modules = document.get("modules")
    if isinstance(modules, list):
        for module in modules:
            if not isinstance(module, Mapping):
                continue
            tasks = module.get("tasks")
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if isinstance(task, Mapping):
                    specs.append(TaskSpec.from_payload(task, module=module))

    knowledge = _knowledge_registry_payload(target_root, document)
    if isinstance(knowledge, Mapping):
        for entry in knowledge.get("sources") or []:
            if not isinstance(entry, Mapping):
                continue
            module_id = str(entry.get("module_id") or entry.get("id") or "").strip()
            if not module_id:
                continue
            module = {
                "id": module_id,
                "kind": "knowledge",
                "enabled": bool(entry.get("enabled", True)),
                "display": {
                    "name": str(entry.get("name") or entry.get("label") or module_id)
                },
                "source": dict(entry),
            }
            for task in entry.get("tasks") or []:
                if isinstance(task, Mapping):
                    specs.append(TaskSpec.from_payload(task, module=module))
    seen: set[str] = set()
    unique: list[TaskSpec] = []
    for task in specs:
        if task.id in seen:
            raise ValueError(f"duplicate task id: {task.id}")
        seen.add(task.id)
        unique.append(task)
    return unique


def _load_document(target_root: Path) -> dict[str, Any]:
    try:
        document = load_registry_document(Path(target_root))
    except ValueError:
        document = _load_tolerant_way_document(Path(target_root))
    if not isinstance(document, dict):
        raise ValueError("Way registry document must be an object")
    return document


def _load_tolerant_way_document(target_root: Path) -> dict[str, Any]:
    root = Path(target_root)
    protocol = load_protocol(root)
    index_path = root / protocol["registry_index"]
    index = _read_json(index_path)
    if index.get("schema") != SUPPORTED_REGISTRY_SCHEMA:
        raise ValueError("registry cannot be loaded tolerantly")
    index_errors = _validate_way_index(index)
    if index_errors:
        raise ValueError("invalid Way registry index: " + "; ".join(index_errors))
    registry_root = index_path.parent
    schema_by_kind = {
        "sources": "radar-content-contract/v1/source",
        "entities": "radar-content-contract/v1/entity",
        "surfaces": "radar-content-contract/v1/surface",
        "editorial": "way-content-registry/v1/editorial",
    }
    manifests: dict[str, list[dict[str, Any]]] = {
        kind: [] for kind in schema_by_kind
    }
    diagnostics: dict[str, Any] = {
        "invalid_declarations": [],
        "added_sources": [],
        "changed_sources": [],
        "disabled_sources": [],
        "next_run_at": {},
    }
    ids: set[str] = set()
    for kind, schema_name in schema_by_kind.items():
        manifest_root = _resolve_under(
            registry_root,
            _safe_relative(index["manifest_roots"][kind], f"manifest_roots.{kind}"),
            f"manifest_roots.{kind}",
        )
        for path in sorted(manifest_root.rglob("*.json")):
            if path.is_symlink():
                continue
            payload = _read_json(path)
            errors = _validate_way_manifest(payload, kind)
            if errors:
                diagnostics["invalid_declarations"].append(
                    {
                        "path": str(path.relative_to(root).as_posix()),
                        "errors": errors,
                    }
                )
                continue
            identifier = str(payload["id"])
            if identifier in ids:
                raise ValueError(f"duplicate {kind[:-1]} id: {identifier}")
            ids.add(identifier)
            manifests[kind].append(payload)

    modules: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for reference in index.get("modules") or []:
        relative = _safe_relative(reference["manifest"], "module.manifest")
        path = _resolve_under(registry_root, relative, "module.manifest")
        payload = _read_json(path)
        errors = _validate_way_module(
            payload,
            expected_id=str(reference["id"]),
            task_ids=task_ids,
        )
        if errors:
            diagnostics["invalid_declarations"].append(
                {
                    "path": str(path.relative_to(root).as_posix()),
                    "errors": errors,
                }
            )
            continue
        modules.append({**payload, "manifest": relative})

    normalized = dict(index)
    normalized["manifests"] = manifests
    normalized["modules"] = modules
    normalized["declarations"] = []
    normalized["_diagnostics"] = diagnostics
    return normalized


def load_sources(target_root: Path) -> list[SourceSpec]:
    """Load all source manifests from the target Way checkout."""

    return [
        SourceSpec.from_payload(payload)
        for payload in _source_payloads(
            Path(target_root),
            _load_document(target_root),
        )
    ]


def load_framework_entities(target_root: Path) -> list[EntitySpec]:
    """Load all framework entity manifests from the target Way checkout."""

    return [
        EntitySpec.from_payload(payload)
        for payload in _manifest_payloads(_load_document(target_root), "entities")
    ]


def load_surfaces(target_root: Path) -> list[SurfaceSpec]:
    """Load all surface manifests from the target Way checkout."""

    return [
        SurfaceSpec.from_payload(payload)
        for payload in _manifest_payloads(_load_document(target_root), "surfaces")
    ]


def load_registry(target_root: Path) -> Registry:
    """Load the Way manifests used by Radar discovery."""

    document = _load_document(target_root)
    return Registry(
        sources=[
            SourceSpec.from_payload(payload)
            for payload in _source_payloads(Path(target_root), document)
        ],
        entities=[
            EntitySpec.from_payload(payload)
            for payload in _manifest_payloads(document, "entities")
        ],
        surfaces=[
            SurfaceSpec.from_payload(payload)
            for payload in _manifest_payloads(document, "surfaces")
        ],
        tasks=_task_specs(Path(target_root), document),
        diagnostics=document.get("_diagnostics") or {},
    )


__all__ = [
    "EntitySpec",
    "Registry",
    "SourceSpec",
    "SurfaceSpec",
    "TaskSpec",
    "load_framework_entities",
    "load_registry",
    "load_sources",
    "load_surfaces",
]
