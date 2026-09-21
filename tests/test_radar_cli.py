from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.radar_run import _order_control_operations, main
from scripts.radar_run import _final_status
from scripts.radar_run import _safe_endpoint
from radar_core.discovery import TaskOperation
from radar_core.registry import TaskSpec
from radar_core.storage import StateStore


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "actual-way-registry"


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "way"
    for source in FIXTURE_ROOT.rglob("*"):
        destination = target / source.relative_to(FIXTURE_ROOT)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    return target


def test_radar_run_dry_run_discovers_registered_module_sources_without_writes(
    tmp_path: Path,
):
    target = _copy_fixture(tmp_path)
    state_path = tmp_path / "state.sqlite3"
    report_path = tmp_path / "run-report.json"

    exit_code = main(
        [
            "--target-root",
            str(target),
            "--state",
            str(state_path),
            "--report",
            str(report_path),
            "--dry-run",
            "--now",
            "2026-09-19T19:30:00+00:00",
        ]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "radar-run-report/v1"
    assert report["status"] == "dry_run"
    assert report["started_at"]
    assert report["finished_at"]
    assert report["duration_ms"] >= 0
    assert {row["id"] for row in report["providers"]} == {"deepseek", "google"}
    assert any(
        row.get("task_id") == "task.framework.deepseek-harness.wiki"
        for row in report["operations"]
    )
    operation = next(
        row
        for row in report["operations"]
        if row.get("task_id") == "task.framework.deepseek-harness.wiki"
    )
    assert operation["module_id"] == "framework.deepseek-harness"
    assert operation["adapter"] == "deepwiki"
    assert "verification" in report
    assert report["verification"]["sources"]
    store = StateStore.open(state_path)
    assert store.count_rows("cursors") == 0
    store.close()


def test_control_tasks_are_ordered_after_their_dependencies():
    def task(task_id: str, depends_on: list[str]) -> TaskSpec:
        return TaskSpec.from_payload(
            {
                "id": task_id,
                "kind": "index_sync",
                "adapter": "framework_hubs",
                "output": {"path": "frontend"},
                "schedule": {
                    "enabled": True,
                    "timezone": "UTC",
                    "cron": "0 * * * *",
                },
                "depends_on": depends_on,
            },
            module={
                "id": "framework.example",
                "kind": "framework",
                "enabled": True,
                "display": {"name": "Example"},
            },
        )

    translation = task("task.framework.example.translation", [])
    index = task("task.framework.example.index", [translation.id])
    operations = [
        TaskOperation(
            task=index,
            scheduled_at=datetime.now(timezone.utc),
            next_run_at=None,
            cursor={},
        ),
        TaskOperation(
            task=translation,
            scheduled_at=datetime.now(timezone.utc),
            next_run_at=None,
            cursor={},
        ),
    ]

    assert [item.task.id for item in _order_control_operations(operations)] == [
        translation.id,
        index.id,
    ]


def test_provider_endpoint_redacts_query_credentials():
    assert _safe_endpoint("https://api.example.test/v1?api_key=secret") == (
        "https://api.example.test/v1"
    )


def test_partial_and_blocked_operations_fail_the_publish_gate():
    assert _final_status([{"status": "partial"}], dry_run=False) == "partial"
    assert _final_status([{"status": "blocked"}], dry_run=False) == "failed"
