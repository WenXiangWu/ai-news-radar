from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from radar_core.discovery import discover_due_control_task_operations
from radar_core.discovery import FETCH_ADAPTERS
from radar_core.registry import Registry, TaskSpec, load_registry
from radar_core.storage import StateStore
from radar_core.task_runner import CONTROL_TASK_ADAPTERS, run_registered_task


def _task(adapter: str, *, kind: str = "translation") -> TaskSpec:
    return TaskSpec.from_payload(
        {
            "id": f"task.framework.example.{adapter}",
            "kind": kind,
            "adapter": adapter,
            "output": {"path": "frontend/example"},
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
                "retry_count": 1,
                "max_runtime_minutes": 10,
            },
        },
        module={
            "id": "framework.example",
            "kind": "framework",
            "enabled": True,
            "display": {"name": "Example"},
        },
    )


def test_control_task_discovery_and_translation_are_idempotent(tmp_path: Path):
    task = _task("markdown_google")
    registry = Registry(sources=[], tasks=[task])
    state = StateStore.open(tmp_path / "state.sqlite3")
    now = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)

    operations = discover_due_control_task_operations(registry, now, state)
    assert [operation.task.id for operation in operations] == [task.id]

    result = run_registered_task(operations[0], state, "run-1", tmp_path)
    assert result.status == "skipped"
    assert "TranslationRouter" in result.summary
    state.record_run("run-2", {"status": "success"})
    assert discover_due_control_task_operations(registry, now, state) == []
    state.close()


def test_all_registered_non_fetch_adapters_have_a_control_handler():
    assert {
        "markdown_google",
        "coding_tools_catalog",
        "qdrant_editorial_catalog",
        "framework_hubs",
        "manual_editorial",
    } <= CONTROL_TASK_ADAPTERS


def test_every_adapter_in_the_way_registry_has_a_radar_execution_path():
    fixture_root = Path(__file__).parent / "fixtures" / "actual-way-registry"
    registry = load_registry(fixture_root)
    declared = {task.adapter for task in registry.tasks}

    assert declared <= FETCH_ADAPTERS | CONTROL_TASK_ADAPTERS
