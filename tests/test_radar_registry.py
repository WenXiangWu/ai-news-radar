from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.radar_registry import (
    flatten_tasks,
    load_registry,
    reconcile_registry,
    validate_registry,
)
from scripts.write_job_report import main as write_job_report


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_target(tmp_path: Path) -> Path:
    write_json(
        tmp_path / "radar/protocol.json",
        {
            "protocol": "way-to-agentic-radar",
            "version": "1.0",
            "registry_root": "radar/registry",
            "registry_index": "radar/registry/index.json",
            "job_report_path": "frontend/frontier/radar-data/job-report.json",
            "state_path": "frontend/frontier/radar-data/registry-state.json",
        },
    )
    write_json(
        tmp_path / "radar/registry/index.json",
        {
            "schema": "radar-registry/v1",
            "defaults": {
                "timezone": "Asia/Shanghai",
                "cron": "17 * * * *",
                "retry_count": 2,
            },
            "legacy_imports": [
                {
                    "id": "framework.wiki",
                    "kind": "framework",
                    "path": "frontend/path/frameworks/wiki/registry.json",
                    "items_key": "frameworks",
                    "task_kind": "wiki_sync",
                    "adapter": "deepwiki",
                    "adapter_field": "github",
                }
            ],
            "modules": [
                {"id": "framework.demo", "manifest": "modules/framework.demo.json"}
            ],
            "managed_roots": ["frontend/sources", "frontend/path/frameworks"],
        },
    )
    write_json(
        tmp_path / "radar/registry/modules/framework.demo.json",
        {
            "id": "framework.demo",
            "kind": "framework",
            "enabled": True,
            "display": {
                "name": "Demo",
                "target_path": "frontend/path/frameworks/demo",
            },
            "bootstrap": {
                "mode": "adopt_existing",
                "baseline": "current_content",
            },
            "tasks": [
                {
                    "id": "task.framework.demo.wiki",
                    "kind": "wiki_sync",
                    "adapter": "deepwiki",
                    "output": {"path": "frontend/path/frameworks/demo/wiki"},
                    "schedule": {"enabled": True, "cron": "17 3 * * *"},
                }
            ],
        },
    )
    write_json(
        tmp_path / "frontend/path/frameworks/wiki/registry.json",
        {
            "frameworks": [
                {
                    "id": "legacy",
                    "name": "Legacy",
                    "github": "org/legacy",
                    "root": "frontend/path/frameworks/legacy/wiki",
                }
            ]
        },
    )
    (tmp_path / "frontend/path/frameworks/demo/wiki").mkdir(parents=True)
    (tmp_path / "frontend/path/frameworks/orphan").mkdir(parents=True)
    return tmp_path


def test_load_registry_normalizes_explicit_and_legacy_tasks(tmp_path: Path):
    registry = load_registry(make_target(tmp_path))

    modules = {module["id"]: module for module in registry["modules"]}
    assert "framework.demo" in modules
    assert "framework.legacy" in modules
    tasks = {task["id"]: task for task in flatten_tasks(registry)}
    assert tasks["task.framework.demo.wiki"]["schedule"]["timezone"] == "Asia/Shanghai"
    assert tasks["task.framework.legacy.wiki"]["source"]["github"] == "org/legacy"
    assert tasks["task.framework.legacy.translation"]["adapter"] == "markdown_google"


def test_validate_registry_rejects_duplicate_task_ids(tmp_path: Path):
    registry = load_registry(make_target(tmp_path))
    registry["modules"][0]["tasks"].append(dict(registry["modules"][0]["tasks"][0]))

    errors = validate_registry(registry)

    assert any("duplicate task id" in error for error in errors)


def test_reconcile_registry_records_bootstrap_and_orphaned_output(tmp_path: Path):
    target = make_target(tmp_path)
    registry = load_registry(target)

    result = reconcile_registry(
        target,
        registry,
        target / "frontend/frontier/radar-data/registry-state.json",
    )

    assert "framework.demo" in result["added_modules"]
    assert any("framework/demo/wiki" in item for item in result["orphaned_outputs"]) is False
    assert "frontend/path/frameworks/orphan" in " ".join(result["orphaned_outputs"])
    assert result["modules"]["framework.demo"]["status"] == "pending_bootstrap"
    assert result["modules"]["framework.demo"]["baseline"]["file_count"] == 0


def test_job_report_contains_registered_task_results_and_legacy_sections(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    write_json(data_dir / "hub.json", {"today": "2026-09-20", "brief_count": 3, "lead": "lead"})
    task_report = tmp_path / "tasks.json"
    write_json(
        task_report,
        {
            "run_id": "run-1",
            "jobs": [
                {
                    "module_id": "framework.demo",
                    "task_id": "task.framework.demo.wiki",
                    "kind": "wiki_sync",
                    "status": "ok",
                    "added": 2,
                    "updated": 1,
                    "summary": "新增 2 页，更新 1 页",
                    "next_run_at": "2026-09-21T03:17:00+08:00",
                }
            ],
        },
    )
    reconciliation = tmp_path / "reconciliation.json"
    write_json(
        reconciliation,
        {
            "added_modules": ["framework.demo"],
            "changed_modules": [],
            "orphaned_outputs": [],
            "blocked_modules": [],
        },
    )
    output = data_dir / "job-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "write_job_report.py",
            "--data-dir",
            str(data_dir),
            "--task-reports",
            str(task_report),
            "--reconciliation-json",
            str(reconciliation),
            "--out",
            str(output),
        ],
    )

    assert write_job_report() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == "radar-job-report/v1"
    assert report["run_id"] == "run-1"
    assert report["jobs"][0]["summary"] == "新增 2 页，更新 1 页"
    assert report["reconciliation"]["added_modules"] == ["framework.demo"]
    assert report["frontier"]["ok"] is True


def test_job_report_derives_legacy_sources_from_registered_knowledge_tasks(
    tmp_path: Path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    write_json(data_dir / "hub.json", {"today": "2026-09-20", "brief_count": 1})
    task_report = tmp_path / "tasks.json"
    write_json(
        task_report,
        {
            "run_id": "run-knowledge",
            "jobs": [
                {
                    "module_id": "knowledge.anthropic.engineering",
                    "task_id": "task.knowledge.anthropic.engineering.sync",
                    "kind": "knowledge_sync",
                    "source": "engineering",
                    "status": "ok",
                    "remote": 25,
                    "local": 26,
                    "new": 1,
                    "translated": ["new-page"],
                    "summary": "远端 25 · 本地 26 · 新译 1",
                }
            ],
        },
    )
    output = data_dir / "job-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "write_job_report.py",
            "--data-dir",
            str(data_dir),
            "--task-reports",
            str(task_report),
            "--out",
            str(output),
        ],
    )

    assert write_job_report() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["sources"]["results"]["engineering"]["summary"].endswith("新增 slug 1")
