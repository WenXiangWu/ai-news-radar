"""Inventory every Radar, Wiki, and docs source declared in a Way checkout."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .discovery import FETCH_ADAPTERS
from .registry import load_registry
from .verification import _source_declarations


FETCHABLE_LEGACY_ADAPTERS = frozenset(
    {
        "llms_txt",
        "github_tree",
        "deepwiki",
        "html_collection",
        "static_pages",
        "rss",
        "rss_article",
        "composite",
        "mintlify",
        "langchain_md",
        "next_data",
    }
)


@dataclass(frozen=True)
class SourceAuditRow:
    source_id: str
    origin: str
    adapter: str
    locator: str
    enabled: bool
    payload: dict[str, Any] = field(default_factory=dict)


def collect_all_sources(target_root: Path) -> list[SourceAuditRow]:
    root = Path(target_root)
    rows: list[SourceAuditRow] = []
    rows.extend(_radar_rows(root))
    rows.extend(_wiki_rows(root))
    rows.extend(_docs_rows(root))
    return rows


def _radar_rows(root: Path) -> list[SourceAuditRow]:
    registry = load_registry(root)
    rows: list[SourceAuditRow] = []
    for declaration in _source_declarations(registry):
        source = declaration["source"]
        if source.id.startswith(("docs.", "wiki.")):
            continue
        rows.append(
            SourceAuditRow(
                source_id=source.id,
                origin="radar",
                adapter=str(declaration["adapter"] or ""),
                locator=str(source.locator or ""),
                enabled=bool(source.enabled),
                payload=source.to_dict(),
            )
        )
    return rows


def _wiki_rows(root: Path) -> list[SourceAuditRow]:
    path = root / "frontend" / "path" / "frameworks" / "wiki" / "registry.json"
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    rows: list[SourceAuditRow] = []
    for entry in document.get("frameworks") or []:
        if not isinstance(entry, Mapping):
            continue
        identifier = str(entry.get("id") or "").strip()
        if not identifier:
            continue
        github = str(entry.get("github") or "").strip()
        rows.append(
            SourceAuditRow(
                source_id=f"wiki.{identifier}",
                origin="wiki",
                adapter="deepwiki",
                locator=github or str(entry.get("root") or ""),
                enabled=True,
                payload=dict(entry),
            )
        )
    return rows


def _docs_rows(root: Path) -> list[SourceAuditRow]:
    path = root / "frontend" / "path" / "frameworks" / "sync" / "registry.json"
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    rows: list[SourceAuditRow] = []
    for entry in document.get("frameworks") or []:
        if not isinstance(entry, Mapping):
            continue
        identifier = str(entry.get("id") or "").strip()
        if not identifier:
            continue
        adapter = str(entry.get("adapter") or "").strip()
        locator = str(
            entry.get("official")
            or (entry.get("index_urls") or [None])[0]
            or entry.get("docs_json_url")
            or entry.get("root")
            or ""
        ).strip()
        rows.append(
            SourceAuditRow(
                source_id=f"docs.{identifier}",
                origin="docs",
                adapter=adapter,
                locator=locator,
                enabled=True,
                payload=dict(entry),
            )
        )
    return rows


def fetch_adapters() -> frozenset[str]:
    return FETCH_ADAPTERS
