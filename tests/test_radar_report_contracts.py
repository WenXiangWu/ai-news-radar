from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.validate_radar_contract import validate_contract_file
from scripts.validate_radar_report import main


def test_source_validation_and_update_report_contracts_accept_generated_shapes(
    tmp_path: Path,
):
    source_validation = {
        "schema": "radar-source-validation/v1",
        "generated_at": "2026-09-21T00:00:00Z",
        "status": "ok",
        "counts": {"sources": 1, "healthy": 1, "failed": 0},
        "sources": [
            {
                "source_id": "source.example.docs",
                "module_id": "knowledge.example.docs",
                "adapter": "llms_txt",
                "status": "ok",
                "health": {"status": "healthy"},
                "baseline_status": "verified",
                "cursor_status": "present",
                "errors": [],
            }
        ],
        "errors": [],
        "live": False,
    }
    update = {
        "schema": "radar-update-report/v1",
        "run_id": "run-20260921T000000Z-test",
        "generated_at": "2026-09-21T00:00:00Z",
        "status": "success",
        "summary": {"modules": 1, "updated": 1, "translated": 1},
        "verification": {"status": "ok", "sources": []},
        "source_validation": source_validation,
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

    source_path = tmp_path / "source-validation.json"
    source_path.write_text(json.dumps(source_validation), encoding="utf-8")
    update_path = tmp_path / "update-report.json"
    update_path.write_text(json.dumps(update), encoding="utf-8")

    assert validate_contract_file(source_path, "source-validation") == []
    assert validate_contract_file(update_path, "update-report") == []

    assert main(["--path", str(source_path), "--schema", "source-validation"]) == 0
    assert main(["--path", str(update_path), "--schema", "update-report"]) == 0

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "validate_radar_report.py"),
            "--path",
            str(source_path),
            "--schema",
            "source-validation",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_monitor_and_report_index_contracts_accept_generated_shapes(tmp_path: Path):
    monitor = {
        "schema": "radar-monitor/v1",
        "generated_at": "2026-09-21T03:20:00Z",
        "run": {
            "run_id": "run-test",
            "status": "success",
            "started_at": "2026-09-21T03:17:00Z",
            "finished_at": "2026-09-21T03:17:12Z",
            "duration_ms": 12000,
            "report_path": "/radar-content/radar-run-report.json",
        },
        "summary": {
            "modules": 1,
            "sources": 1,
            "enabled_sources": 1,
            "healthy_sources": 1,
            "failed_sources": 0,
            "baseline_verified": 1,
            "baseline_pending": 0,
            "updated": 1,
            "translated": 1,
        },
        "providers": [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "configured": True,
                "model": "deepseek-chat",
                "status": "configured",
                "success_count": 1,
                "failure_count": 0,
                "fallback_count": 0,
            }
        ],
        "modules": [
            {
                "module_id": "knowledge.example.docs",
                "name": "Example docs",
                "kind": "knowledge",
                "enabled": True,
                "status": "success",
                "updated": 1,
                "translated": 1,
                "errors": [],
            }
        ],
        "sources": [
            {
                "source_id": "source.knowledge.example.docs",
                "module_id": "knowledge.example.docs",
                "module_name": "Example docs",
                "enabled": True,
                "adapter": "llms_txt",
                "locator": "https://docs.example.test/llms.txt",
                "schedule": {"cron": "17 3 * * *", "timezone": "Asia/Shanghai"},
                "status": "success",
                "reachability": {"status": "healthy"},
                "baseline": {"status": "verified"},
                "metrics": {"updated": 1, "translated": 1},
                "errors": [],
                "translations": [],
            }
        ],
        "reports": {
            "latest_update": "/radar-content/radar-update-report.json",
            "latest_validation": "/radar-content/source-validation.json",
            "history_index": "/radar-content/reports/index.json",
        },
    }
    index = {
        "schema": "radar-report-index/v1",
        "generated_at": "2026-09-21T03:20:00Z",
        "reports": [
            {
                "date": "2026-09-21",
                "status": "success",
                "generated_at": "2026-09-21T03:20:00Z",
                "summary": {"updated": 1},
                "path": "/radar-content/reports/2026-09-21.json",
            }
        ],
    }
    monitor_path = tmp_path / "monitor.json"
    index_path = tmp_path / "index.json"
    monitor_path.write_text(json.dumps(monitor), encoding="utf-8")
    index_path.write_text(json.dumps(index), encoding="utf-8")

    assert validate_contract_file(monitor_path, "monitor") == []
    assert validate_contract_file(index_path, "report-index") == []
