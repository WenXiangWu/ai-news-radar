from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.run_registered_tasks import (
    cron_matches,
    run_registered_tasks,
    select_due_tasks,
)
from scripts.translate_registered_markdown import translate_task


TZ = ZoneInfo("Asia/Shanghai")


def task(task_id: str, cron: str, adapter: str = "unit") -> dict:
    return {
        "id": task_id,
        "module_id": "module.demo",
        "kind": "knowledge_sync",
        "adapter": adapter,
        "schedule": {
            "enabled": True,
            "timezone": "Asia/Shanghai",
            "cron": cron,
            "retry_count": 1,
            "max_runtime_minutes": 10,
            "max_new_items": 2,
        },
        "output": {"path": "frontend/sources/demo"},
        "depends_on": [],
    }


def test_cron_matching_supports_hourly_and_step_fields():
    assert cron_matches("17 * * * *", datetime(2026, 9, 20, 3, 17, tzinfo=TZ))
    assert not cron_matches("17 * * * *", datetime(2026, 9, 20, 3, 18, tzinfo=TZ))
    assert cron_matches("*/15 * * * *", datetime(2026, 9, 20, 3, 30, tzinfo=TZ))


def test_select_due_tasks_does_not_run_future_schedule():
    due = select_due_tasks(
        [task("task.future", "47 4 * * *")],
        state={},
        now=datetime(2026, 9, 20, 3, 17, tzinfo=TZ),
    )

    assert due == []


def test_run_registered_tasks_reports_success_and_next_run(tmp_path: Path):
    state_path = tmp_path / "state.json"
    jobs = [task("task.demo", "17 * * * *")]

    def executor(current_task, module, target_root):
        return {"ok": True, "added": 2, "updated": 1, "summary": "新增 2 条，更新 1 条"}

    report = run_registered_tasks(
        tmp_path,
        {"modules": [{"id": "module.demo", "enabled": True, "tasks": jobs}]},
        state_path,
        now=datetime(2026, 9, 20, 3, 17, tzinfo=TZ),
        executor=executor,
    )

    assert report["jobs"][0]["status"] == "ok"
    assert report["jobs"][0]["summary"] == "新增 2 条，更新 1 条"
    assert report["jobs"][0]["next_run_at"]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["tasks"]["task.demo"]["last_status"] == "ok"


def test_unknown_adapter_is_blocked_and_explained(tmp_path: Path):
    jobs = [task("task.unknown", "17 * * * *", adapter="not-installed")]

    report = run_registered_tasks(
        tmp_path,
        {"modules": [{"id": "module.demo", "enabled": True, "tasks": jobs}]},
        tmp_path / "state.json",
        now=datetime(2026, 9, 20, 3, 17, tzinfo=TZ),
    )

    assert report["jobs"][0]["status"] == "blocked"
    assert "not-installed" in report["jobs"][0]["summary"]


def test_manual_editorial_adapter_reports_explicit_ownership(tmp_path: Path):
    registered = {
        "modules": [
            {
                "id": "framework.editorial",
                "kind": "framework",
                "enabled": True,
                "tasks": [
                    {
                        "id": "task.framework.editorial.ownership",
                        "module_id": "framework.editorial",
                        "kind": "index_sync",
                        "adapter": "manual_editorial",
                        "schedule": {
                            "enabled": True,
                            "timezone": "Asia/Shanghai",
                            "cron": "17 * * * *",
                        },
                        "output": {"path": "frontend/path/frameworks/editorial"},
                    }
                ],
            }
        ]
    }

    report = run_registered_tasks(
        tmp_path,
        registered,
        tmp_path / "state.json",
        now=datetime(2026, 9, 20, 3, 17, tzinfo=TZ),
    )

    assert report["jobs"][0]["status"] == "skipped"
    assert "人工维护" in report["jobs"][0]["summary"]


def test_registered_translation_only_processes_stale_pages_and_preserves_code(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "frontend/path/frameworks/demo/wiki"
    source = root / "_source/en/intro.md"
    source.parent.mkdir(parents=True)
    source.write_text("Before\n\n```python\nprint('hello')\n```\n\nAfter\n", encoding="utf-8")
    (root / "_sync").mkdir(parents=True)
    (root / "_sync/stale.json").write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "source": "_source/en/intro.md",
                        "zh": "zh/intro.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.translate_registered_markdown.translate_markdown_google",
        lambda text: text.replace("Before", "之前").replace("After", "之后"),
    )
    task_config = {
        "id": "task.demo.translation",
        "kind": "translation",
        "source": {
            "stale_manifest": "frontend/path/frameworks/demo/wiki/_sync/stale.json",
            "source_root": "frontend/path/frameworks/demo/wiki",
        },
        "schedule": {"max_new_items": 5},
    }

    result = translate_task(task_config, tmp_path)

    assert result["status"] == "ok"
    assert result["translated"] == 1
    translated = (root / "zh/intro.md").read_text(encoding="utf-8")
    assert "之前" in translated and "之后" in translated
    assert "```python\nprint('hello')\n```" in translated


def test_publish_workflow_runs_the_radar_export_and_way_import():
    workflow = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")

    assert "scripts/radar_run.py" in workflow
    assert "scripts/radar_export.py" in workflow
    assert "scripts/import_radar_export.py" in workflow
    assert "scripts/build_radar_content_registry.py" in workflow
    assert "scripts/build_radar_projections.py" in workflow
    assert "scripts/build_radar_update_report.py" in workflow
    assert "scripts/build_radar_monitor_snapshot.py" in workflow
    assert "radar-monitor.json" in workflow
    assert "--radar-monitor" in workflow
    assert "reports/index.json" in workflow
    assert "RADAR_SCOPE" in workflow
    assert "--only-module" in workflow
    # --force may appear as an optional flag, but must not be tied to request_id.
    assert "--force" in workflow
    assert 'if [ -n "${RADAR_REQUEST_ID:-}" ]; then' not in workflow
    assert "inputs.force_all" in workflow
    assert "radar-update-report.json" in workflow
    assert "source-validation.json" in workflow
    assert "radar_exit=0" in workflow
    assert "report exists" in workflow or "continuing export" in workflow
    assert "radar-state.sqlite3" in workflow
    assert "RADAR_RUN_MAX_RUNTIME_MINUTES" in workflow
    assert "--max-runtime-minutes" in workflow
    assert "RADAR_OPERATION_MAX_RUNTIME_MINUTES" in workflow
    assert "--max-operation-runtime-minutes" in workflow
    assert "frontend/radar-content" in workflow
    assert "data/radar-monitor.json" in workflow
    assert "data/radar-update-report.json" in workflow
    assert "data/source-validation.json" in workflow
    assert "data/radar-reports" in workflow
    assert "--public-prefix /data" in workflow
    assert "--reports-prefix /data/radar-reports" in workflow
    assert 'rm -f "$work/frontend/radar-content/radar-monitor.json"' in workflow
