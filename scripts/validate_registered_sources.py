#!/usr/bin/env python3
"""Validate that every enabled registered fetch task has a runnable adapter."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar_core.connectors.base import (
    ConnectorFactory,
    Cursor,
    merge_nested_source_config,
)
from radar_core.registry import SourceSpec, load_registry
from radar_core.storage import StateStore
from radar_core.verification import _source_declarations


def build_source_validation_report(
    target_root: Path,
    *,
    state_path: Path | None = None,
    live: bool = False,
    max_items: int = 1,
) -> dict[str, Any]:
    registry = load_registry(Path(target_root))
    state = StateStore.open(state_path) if state_path is not None else None
    rows: list[dict[str, Any]] = []
    try:
        for declaration in _source_declarations(registry):
            source = declaration["source"]
            if not source.enabled and not declaration["task_ids"]:
                continue
            row = _validate_source(
                declaration,
                state=state,
                live=live,
                max_items=max_items,
            )
            rows.append(row)
    finally:
        if state is not None:
            state.close()

    errors = [
        f"{row['source_id']}: {error}"
        for row in rows
        for error in row.get("errors") or []
    ]
    failed = sum(row["status"] == "failed" for row in rows)
    return {
        "schema": "radar-source-validation/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if errors else "ok",
        "counts": {
            "sources": len(rows),
            "healthy": sum(row["status"] == "ok" for row in rows),
            "failed": failed,
            "baseline_verified": sum(
                row.get("baseline_status") == "verified" for row in rows
            ),
            "baseline_pending": sum(
                row.get("baseline_status") in {"pending", "missing", "staged"}
                for row in rows
            ),
        },
        "sources": rows,
        "errors": errors,
        "live": live,
    }


def validate_registered_sources(
    target_root: Path,
    *,
    state_path: Path | None = None,
    live: bool = False,
    max_items: int = 1,
) -> list[str]:
    return build_source_validation_report(
        target_root,
        state_path=state_path,
        live=live,
        max_items=max_items,
    )["errors"]


def _validate_source(
    declaration: Mapping[str, Any],
    *,
    state: StateStore | None,
    live: bool,
    max_items: int,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    started_clock = time.perf_counter()
    source = declaration["source"]
    row: dict[str, Any] = {
        "source_id": source.id,
        "module_id": declaration["module_id"],
        "module_name": declaration["module_name"],
        "task_ids": list(declaration["task_ids"]),
        "adapter": declaration["adapter"],
        "locator": source.locator,
        "enabled": source.enabled,
        "schedule": dict(source.schedule),
        "status": "ok",
        "health": {},
        "checked_at": checked_at,
        "latency_ms": 0,
        "baseline_status": "not_checked",
        "cursor_status": "not_checked",
        "live": {
            "enabled": live,
            "discovered": 0,
            "fetched": 0,
            "checked_at": checked_at,
            "latency_ms": 0,
        },
        "errors": [],
    }
    adapter = _adapter_for(source, declaration["adapter"])
    row["adapter"] = adapter
    if not adapter:
        row["status"] = "failed"
        row["errors"].append("adapter is missing")
        return row
    if not source.locator or source.locator in {
        source.id,
        source.payload.get("legacy_id"),
    }:
        row["status"] = "failed"
        row["errors"].append("locator is missing or placeholder")
        return row
    try:
        connector_config = merge_nested_source_config(source.to_dict())
        connector_config.update(
            {
                "source_id": source.id,
                "adapter_name": adapter,
                "locator": source.locator,
                **_inferred_locator_config(adapter, source),
            }
        )
        connector = ConnectorFactory.create(adapter, connector_config)
        health = connector.healthcheck()
        row["health"] = {
            "adapter": health.adapter,
            "status": health.status,
            "message": health.message,
            "network": health.network,
            "details": dict(health.details),
        }
        if health.status not in {"healthy", "degraded"}:
            row["status"] = "failed"
            row["errors"].append(health.message or "connector healthcheck failed")
        elif live:
            page = connector.discover(Cursor())
            row["live"]["discovered"] = len(page.items)
            for item in page.items[: max(0, int(max_items))]:
                connector.fetch(item)
                row["live"]["fetched"] += 1
    except Exception as exc:  # noqa: BLE001
        row["status"] = "failed"
        row["errors"].append(str(exc)[:500] or type(exc).__name__)
    row["latency_ms"] = max(
        0,
        int((time.perf_counter() - started_clock) * 1000),
    )
    row["live"]["latency_ms"] = row["latency_ms"]

    if state is not None:
        registration = state.get_source_registration(source.id)
        cursor = state.get_cursor(source.id)
        if registration is None:
            row["baseline_status"] = "missing"
        elif registration.get("baseline_fingerprint") == source.registry_fingerprint:
            row["baseline_status"] = "verified"
        elif registration.get("staged_fingerprint") == source.registry_fingerprint:
            row["baseline_status"] = "staged"
        elif registration.get("baseline_fingerprint"):
            row["baseline_status"] = "stale"
        else:
            row["baseline_status"] = "pending"
        row["cursor_status"] = "present" if cursor is not None else "missing"
        row["baseline_fingerprint"] = registration.get("baseline_fingerprint") if registration else None
        row["cursor_run_id"] = cursor.get("run_id") if cursor else None
    return row


def _adapter_for(source: SourceSpec, fallback: str) -> str:
    adapter = str(
        source.payload.get("adapter")
        or source.payload.get("connector")
        or ""
    ).strip()
    if adapter:
        return adapter
    return {
        "official_blog": "rss_article",
        "rss": "rss_article",
        "llms_txt": "llms_txt",
        "github": "github_tree",
        "deepwiki": "deepwiki",
        "local": "local_import",
    }.get(source.source_type, fallback)


def _inferred_locator_config(adapter: str, source: SourceSpec) -> dict[str, object]:
    if adapter in {"rss", "rss_article"}:
        return {"feed_url": source.payload.get("feed_url") or source.locator}
    if adapter in {"llms_txt", "deepwiki"}:
        return {"url": source.payload.get("url") or source.locator}
    if adapter == "html_collection":
        return {
            "listing_url": source.payload.get("listing_url") or source.locator,
            "listing_urls": source.payload.get("listing_urls"),
        }
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate registered Radar source adapters"
    )
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--state", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-items", type=int, default=1)
    args = parser.parse_args(argv)
    report = build_source_validation_report(
        Path(args.target_root),
        state_path=Path(args.state) if args.state else None,
        live=args.live,
        max_items=args.max_items,
    )
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {target}")
    errors = list(report["errors"])
    if errors:
        for error in errors:
            print(error)
        return 1
    print("all enabled registered sources have runnable adapters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
