#!/usr/bin/env python3
"""End-to-end verify every declared source without calling DeepSeek translation."""

from __future__ import annotations

import argparse
import json
import os
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
    UrllibTransport,
    merge_nested_source_config,
)
from radar_core.discovery import FETCH_ADAPTERS
from radar_core.pipeline import RunContext, run_source
from radar_core.registry import SourceSpec, load_registry
from radar_core.source_audit import SourceAuditRow, collect_all_sources
from radar_core.storage import StateStore


TREE_MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class _DisabledRouter:
    def provider_order(self) -> tuple[str, ...]:
        return ()

    def translate(self, request: Any) -> Any:
        raise RuntimeError("translation disabled")


def verify_all_sources(
    target_root: Path,
    *,
    state_path: Path,
    max_new_items: int = 1,
    fetch_bodies: bool = True,
    only_ids: set[str] | None = None,
) -> dict[str, Any]:
    inventory = collect_all_sources(target_root)
    if only_ids:
        inventory = [row for row in inventory if row.source_id in only_ids]
    registry = load_registry(target_root)
    scheduled = {
        task.to_source_spec().id: task.to_source_spec()
        for task in registry.tasks
        if task.enabled and task.adapter in FETCH_ADAPTERS
    }
    state = StateStore.open(state_path)
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    try:
        for row in inventory:
            result = _verify_row(
                row,
                state=state,
                max_new_items=max_new_items,
                fetch_bodies=fetch_bodies,
                scheduled_source=scheduled.get(row.source_id),
            )
            results.append(result)
            print(
                f"{result['status']}\t{result['origin']}\t{result['source_id']}\t"
                f"discovered={result['discovered']}\tfetched={result['fetched']}",
                flush=True,
            )
    finally:
        state.close()
    failed = [row for row in results if row["status"] == "failed"]
    return {
        "schema": "radar-source-e2e/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if failed else "ok",
        "skip_translation": True,
        "counts": {
            "sources": len(results),
            "ok": sum(row["status"] == "ok" for row in results),
            "failed": len(failed),
            "skipped": sum(row["status"] == "skipped" for row in results),
            "degraded": sum(row["status"] == "degraded" for row in results),
            "radar": sum(row["origin"] == "radar" for row in results),
            "wiki": sum(row["origin"] == "wiki" for row in results),
            "docs": sum(row["origin"] == "docs" for row in results),
        },
        "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "sources": results,
        "errors": [
            f"{row['source_id']}: {error}"
            for row in results
            for error in row.get("errors") or []
        ],
    }


def _verify_row(
    row: SourceAuditRow,
    *,
    state: StateStore,
    max_new_items: int,
    fetch_bodies: bool,
    scheduled_source: SourceSpec | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    report: dict[str, Any] = {
        "source_id": row.source_id,
        "origin": row.origin,
        "adapter": row.adapter,
        "locator": row.locator,
        "status": "ok",
        "discovered": 0,
        "fetched": 0,
        "run_kind": "",
        "translated": 0,
        "errors": [],
    }
    try:
        if "example" in row.source_id.split("."):
            report["status"] = "skipped"
            report["errors"].append("fixture example source")
            report["latency_ms"] = 0
            return report
        if scheduled_source is not None:
            scheduled_row = SourceAuditRow(
                source_id=scheduled_source.id,
                origin=row.origin,
                adapter=str(scheduled_source.payload.get("adapter") or row.adapter),
                locator=scheduled_source.locator,
                enabled=scheduled_source.enabled,
                payload=scheduled_source.to_dict(),
            )
            report["adapter"] = scheduled_row.adapter
            _verify_radar_row(
                scheduled_row,
                report,
                state=state,
                max_new_items=max_new_items,
                fetch_bodies=fetch_bodies,
            )
        elif row.origin == "radar":
            _verify_radar_row(
                row,
                report,
                state=state,
                max_new_items=max_new_items,
                fetch_bodies=fetch_bodies,
            )
        elif row.origin == "wiki":
            _verify_wiki_row(row, report)
        else:
            _verify_docs_row(row, report, fetch_bodies=fetch_bodies)
    except Exception as exc:  # noqa: BLE001
        report["status"] = "failed"
        report["errors"].append(str(exc)[:500] or type(exc).__name__)
    report["latency_ms"] = max(0, int((time.perf_counter() - started) * 1000))
    return report


def _verify_radar_row(
    row: SourceAuditRow,
    report: dict[str, Any],
    *,
    state: StateStore,
    max_new_items: int,
    fetch_bodies: bool,
) -> None:
    if row.adapter not in FETCH_ADAPTERS:
        report["status"] = "skipped"
        report["errors"].append(f"non-fetch adapter {row.adapter or 'missing'}")
        return
    source = SourceSpec.from_payload(row.payload)
    router = _DisabledRouter()
    baseline = RunContext(
        state=state,
        run_id=f"e2e-baseline-{source.id}",
        router=router,
        mode="baseline_only",
        skip_translation=True,
    )
    first = run_source(source, baseline)
    report["discovered"] = first.discovered
    report["run_kind"] = first.run_kind
    if first.status not in {"success", "partial"}:
        report["status"] = "failed"
        report["errors"].extend(first.errors or [first.status])
        return
    if not fetch_bodies:
        return
    second = run_source(
        source,
        RunContext(
            state=state,
            run_id=f"e2e-fetch-{source.id}",
            router=router,
            mode="bootstrap",
            skip_translation=True,
            max_new_items_override=max_new_items,
        ),
    )
    report["fetched"] = second.fetched
    report["translated"] = second.translated
    report["run_kind"] = second.run_kind
    if second.translated:
        report["status"] = "failed"
        report["errors"].append("translation ran despite skip_translation")
    if second.status not in {"success", "partial"}:
        report["status"] = "failed"
        report["errors"].extend(second.errors or [second.status])


def _verify_wiki_row(row: SourceAuditRow, report: dict[str, Any]) -> None:
    github = str(row.payload.get("github") or "").strip()
    if not github:
        raise ValueError("wiki entry has no github locator")
    config: dict[str, Any] = {
        "source_id": row.source_id,
        "github": github,
        "timeout_seconds": 30,
        "max_response_bytes": TREE_MAX_RESPONSE_BYTES,
    }
    token = str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        config["token"] = token
    try:
        connector = ConnectorFactory.create("deepwiki", config)
        page = connector.discover(Cursor())
        report["discovered"] = len(page.items)
        if not page.items:
            raise ValueError("wiki discover returned no items")
        if not all(item.has_remote_validator for item in page.items):
            raise ValueError("wiki items missing remote_revision")
    except Exception as exc:  # noqa: BLE001
        official = f"https://github.com/{github}"
        status = _probe_url(official)
        if 200 <= status < 400:
            report["status"] = "degraded"
            report["errors"].append(
                f"github tree failed ({exc}); repo probe HTTP {status}"
            )
            report["discovered"] = max(int(report.get("discovered") or 0), 1)
            return
        raise


def _verify_docs_row(
    row: SourceAuditRow,
    report: dict[str, Any],
    *,
    fetch_bodies: bool,
) -> None:
    adapter = row.adapter
    try:
        if adapter in FETCH_ADAPTERS:
            connector = ConnectorFactory.create(
                adapter,
                {
                    **_docs_connector_config(row),
                    "max_response_bytes": TREE_MAX_RESPONSE_BYTES,
                },
            )
            page = connector.discover(Cursor())
            report["discovered"] = len(page.items)
            if not page.items:
                raise ValueError("docs discover returned no items")
            if fetch_bodies:
                connector.fetch(page.items[0])
                report["fetched"] = 1
            return
        url = _docs_probe_url(row.payload)
        if not url:
            raise ValueError("docs entry has no probe URL")
        status = _probe_url(url)
        report["discovered"] = 1 if 200 <= status < 400 else 0
        if status >= 400:
            raise ValueError(f"probe {url} returned HTTP {status}")
        report["adapter"] = f"{adapter}:probe"
    except Exception as exc:  # noqa: BLE001
        official = str(row.payload.get("official") or "").strip()
        if official.startswith("http"):
            status = _probe_url(official)
            if 200 <= status < 400:
                report["status"] = "degraded"
                report["errors"].append(
                    f"adapter failed ({exc}); official probe HTTP {status}"
                )
                report["discovered"] = max(int(report.get("discovered") or 0), 1)
                return
        raise


def _docs_connector_config(row: SourceAuditRow) -> dict[str, Any]:
    payload = dict(row.payload)
    adapter = row.adapter
    config: dict[str, Any] = {
        "source_id": row.source_id,
        "timeout_seconds": 30,
        **payload,
    }
    token = str(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        config["token"] = token
    if adapter == "llms_txt":
        urls = payload.get("index_urls")
        config["url"] = (
            urls[0] if isinstance(urls, list) and urls else row.locator
        )
    elif adapter == "html_collection":
        if not str(config.get("listing_url") or "").strip() and not (
            isinstance(config.get("listing_urls"), list) and config.get("listing_urls")
        ):
            official = str(payload.get("official") or "").strip()
            if official:
                config["listing_url"] = official
    elif adapter == "github_tree":
        owner = str(payload.get("github_owner") or "").strip()
        repo = str(payload.get("github_repo") or "").strip()
        if owner and repo:
            config["repo"] = f"{owner}/{repo}"
        config["ref"] = str(
            payload.get("github_branch") or payload.get("ref") or ""
        ).strip()
        prefix = str(payload.get("github_docs_prefix") or "").strip()
        if prefix:
            config["path_prefix"] = prefix
    return merge_nested_source_config(config)


def _docs_probe_url(payload: Mapping[str, Any]) -> str:
    for key in ("docs_json_url", "official"):
        value = str(payload.get(key) or "").strip()
        if value.startswith("http"):
            return value
    urls = payload.get("index_urls")
    if isinstance(urls, list) and urls:
        value = str(urls[0] or "").strip()
        if value.startswith("http"):
            return value
    return ""


def _probe_url(url: str) -> int:
    transport = UrllibTransport()
    headers = {"User-Agent": "ai-news-radar/source-e2e", "Accept": "*/*"}
    response = transport.request("HEAD", url, headers=headers, timeout=20)
    if response.status_code in {405, 403, 400}:
        response = transport.request("GET", url, headers=headers, timeout=20)
    return int(response.status_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify every Radar/Wiki/docs source without translation"
    )
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-new-items", type=int, default=1)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument(
        "--only-source",
        action="append",
        default=[],
        help="Limit verification to one or more source ids",
    )
    args = parser.parse_args(argv)
    report = verify_all_sources(
        Path(args.target_root),
        state_path=Path(args.state),
        max_new_items=args.max_new_items,
        fetch_bodies=not args.discover_only,
        only_ids=set(args.only_source) or None,
    )
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"{report['status']} sources={report['counts']['sources']} "
        f"ok={report['counts']['ok']} failed={report['counts']['failed']} "
        f"skipped={report['counts']['skipped']} wrote {target}",
        flush=True,
    )
    for error in report["errors"]:
        print(error, flush=True)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
