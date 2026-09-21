from __future__ import annotations

import json
from pathlib import Path

from scripts.build_radar_monitor_snapshot import (
    build_monitor_snapshot,
    build_report_index,
)
from scripts.validate_radar_contract import validate_contract_file


def _write_registry(root: Path) -> None:
    registry = root / "radar" / "registry"
    (registry / "modules").mkdir(parents=True, exist_ok=True)
    (registry / "sources").mkdir(parents=True, exist_ok=True)
    (registry / "index.json").write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "id": "knowledge.example.docs",
                        "manifest": "modules/knowledge.example.docs.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (registry / "modules/knowledge.example.docs.json").write_text(
        json.dumps(
            {
                "id": "knowledge.example.docs",
                "kind": "knowledge",
                "enabled": True,
                "display": {"name": "Example docs"},
                "tasks": [
                    {
                        "id": "task.knowledge.example.docs.sync",
                        "adapter": "llms_txt",
                        "schedule": {
                            "cron": "17 3 * * *",
                            "timezone": "Asia/Shanghai",
                        },
                    }
                ],
                "source": {
                    "id": "source.knowledge.example.docs",
                    "adapter": "llms_txt",
                    "locator": "https://docs.example.test/llms.txt",
                    "enabled": True,
                    "schedule": {
                        "cron": "17 3 * * *",
                        "timezone": "Asia/Shanghai",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (registry / "sources/source.example.disabled.json").write_text(
        json.dumps(
            {
                "id": "source.example.disabled",
                "module_id": "source.example.disabled",
                "name": "Disabled example",
                "enabled": False,
                "adapter": "rss_article",
                "locator": "https://example.test/feed.xml",
                "schedule": {
                    "cron": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
            }
        ),
        encoding="utf-8",
    )


def _write_reports(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_report = tmp_path / "run-report.json"
    run_report.write_text(
        json.dumps(
            {
                "schema": "radar-run-report/v1",
                "run_id": "run-monitor-test",
                "status": "success",
                "started_at": "2026-09-21T03:17:00+00:00",
                "finished_at": "2026-09-21T03:17:12+00:00",
                "duration_ms": 12000,
                "provider_counts": {"deepseek": 2, "google": 1},
                "provider_failures": {"deepseek": 1},
                "operations": [
                    {
                        "source_id": "source.knowledge.example.docs",
                        "module_id": "knowledge.example.docs",
                        "module_name": "Example docs",
                        "task_id": "task.knowledge.example.docs.sync",
                        "adapter": "llms_txt",
                        "status": "success",
                        "scheduled_at": "2026-09-21T03:17:00+08:00",
                        "next_run_at": "2026-09-22T03:17:00+08:00",
                        "discovered": 2,
                        "fetched": 2,
                        "revisions_new": 1,
                        "translated": 1,
                        "baseline_after": {
                            "baseline_fingerprint": "fingerprint-1",
                            "cursor_run_id": "run-monitor-test",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    validation = tmp_path / "source-validation.json"
    validation.write_text(
        json.dumps(
            {
                "schema": "radar-source-validation/v1",
                "generated_at": "2026-09-21T03:18:00+00:00",
                "status": "ok",
                "sources": [
                    {
                        "source_id": "source.knowledge.example.docs",
                        "module_id": "knowledge.example.docs",
                        "status": "ok",
                        "adapter": "llms_txt",
                        "health": {"status": "healthy"},
                        "live": {
                            "enabled": True,
                            "discovered": 2,
                            "fetched": 1,
                            "checked_at": "2026-09-21T03:18:00+00:00",
                            "latency_ms": 240,
                        },
                        "baseline_status": "verified",
                        "cursor_status": "present",
                        "errors": [],
                    }
                ],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    update = tmp_path / "update-report.json"
    update.write_text(
        json.dumps(
            {
                "schema": "radar-update-report/v1",
                "run_id": "run-monitor-test",
                "generated_at": "2026-09-21T03:18:00+00:00",
                "status": "success",
                "modules": [
                    {
                        "module_id": "knowledge.example.docs",
                        "name": "Example docs",
                        "kind": "knowledge",
                        "enabled": True,
                        "status": "success",
                        "updated": 1,
                        "translated": 1,
                        "translation_links": [],
                        "errors": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_report, validation, update


def test_snapshot_uses_radar_owned_public_paths(tmp_path: Path):
    target = tmp_path / "way"
    _write_registry(target)
    run_report, validation, update = _write_reports(tmp_path)

    snapshot = build_monitor_snapshot(
        target,
        run_report,
        validation,
        update,
        generated_at="2026-09-21T03:20:00+00:00",
        public_prefix="/data",
        reports_prefix="/data/radar-reports",
    )

    assert snapshot["run"]["report_path"] == "/data/radar-run-report.json"
    assert snapshot["reports"]["latest_update"] == "/data/radar-update-report.json"
    assert snapshot["reports"]["latest_validation"] == "/data/source-validation.json"
    assert snapshot["reports"]["history_index"] == "/data/radar-reports/index.json"
    assert {row["source_id"] for row in snapshot["sources"]} == {
        "source.knowledge.example.docs",
        "source.example.disabled",
    }
    assert "api_key" not in json.dumps(snapshot)
    snapshot_path = tmp_path / "radar-monitor.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert validate_contract_file(snapshot_path, "monitor") == []


def test_report_index_can_publish_under_radar_data(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-09-21.json").write_text(
        json.dumps(
            {
                "schema": "radar-update-report/v1",
                "generated_at": "2026-09-21T03:20:00+00:00",
                "status": "success",
                "summary": {"updated": 1},
            }
        ),
        encoding="utf-8",
    )

    index = build_report_index(
        reports,
        public_prefix="/data/radar-reports",
    )

    assert index["reports"][0]["path"] == "/data/radar-reports/2026-09-21.json"
