from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .contracts import load_registry_document


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


@dataclass
class Registry:
    sources: list[SourceSpec]
    entities: list[EntitySpec] = field(default_factory=list)
    surfaces: list[SurfaceSpec] = field(default_factory=list)
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
            or raw.get("official")
            or raw.get("github")
            or legacy_id
            or source_id
        ).strip()
        normalized.append(
            {
                **raw,
                "schema": "radar-content-contract/v1/source",
                "id": source_id,
                "kind": "source",
                "source_type": str(raw.get("source_type") or "knowledge"),
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
            continue
        seen.add(source_id)
        unique.append(payload)
    return unique


def _load_document(target_root: Path) -> dict[str, Any]:
    document = load_registry_document(Path(target_root))
    if not isinstance(document, dict):
        raise ValueError("Way registry document must be an object")
    return document


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
    )


__all__ = [
    "EntitySpec",
    "Registry",
    "SourceSpec",
    "SurfaceSpec",
    "load_framework_entities",
    "load_registry",
    "load_sources",
    "load_surfaces",
]
