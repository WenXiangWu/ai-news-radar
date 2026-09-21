from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
import shutil

from radar_core.discovery import Operation
from radar_core.pipeline import RunContext
from radar_core.registry import SourceSpec
from radar_core.storage import StateStore
from scripts import radar_run


def _source() -> SourceSpec:
    return SourceSpec.from_payload(
        {
            "id": "source.timeout",
            "source_type": "local",
            "adapter": "local_import",
            "name": "Timeout fixture",
            "locator": "local://timeout",
            "output_root": "frontend/timeout",
            "enabled": True,
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
                "max_runtime_seconds": 0.05,
            },
        }
    )


def test_bounded_operation_turns_a_hung_connector_into_a_reportable_failure(
    tmp_path: Path,
    monkeypatch,
):
    source = _source()
    operation = Operation(
        source_id=source.id,
        source=source,
        scheduled_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        next_run_at=None,
        cursor={},
    )
    state = StateStore.open(tmp_path / "state.sqlite3")
    context = RunContext(
        state=state,
        run_id="run-timeout",
        now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )

    def hung_operation(_operation, _context):
        time.sleep(0.2)
        raise AssertionError("the timeout should interrupt before this line")

    monkeypatch.setattr(radar_run, "run_operation", hung_operation)

    started = time.perf_counter()
    result = radar_run._run_bounded_source_operation(
        operation,
        context,
        {},
    )
    elapsed = time.perf_counter() - started
    state.close()

    assert elapsed < 0.18
    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert result["timeout_seconds"] == 0.05
    assert "timed out" in result["errors"][0]


def test_workflow_operation_cap_overrides_a_long_registry_deadline(tmp_path: Path):
    source = SourceSpec.from_payload(
        {
            "id": "source.timeout",
            "source_type": "local",
            "adapter": "local_import",
            "name": "Timeout fixture",
            "locator": "local://timeout",
            "output_root": "frontend/timeout",
            "enabled": True,
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
                "max_runtime_minutes": 30,
            },
        }
    )
    operation = Operation(
        source_id=source.id,
        source=source,
        scheduled_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        next_run_at=None,
        cursor={},
    )
    state = StateStore.open(tmp_path / "state.sqlite3")
    context = RunContext(
        state=state,
        run_id="run-cap",
        now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )

    assert radar_run._operation_timeout_seconds(
        operation,
        max_operation_runtime_minutes=0.001,
    ) == 0.06
    state.close()


def test_run_budget_writes_flat_blocked_results(tmp_path: Path):
    fixture_root = Path(__file__).parent / "fixtures" / "actual-way-registry"
    target = tmp_path / "way"
    shutil.copytree(fixture_root, target)
    report_path = tmp_path / "run-report.json"

    exit_code = radar_run.main(
        [
            "--target-root",
            str(target),
            "--state",
            str(tmp_path / "state.sqlite3"),
            "--report",
            str(report_path),
            "--force",
            "--max-runtime-minutes",
            "0.00001",
            "--now",
            "2026-09-21T06:30:00Z",
        ]
    )

    # Budget exhaustion produces soft `blocked` results. Per the design these
    # are not a hard failure, but the run must degrade to `partial` (not
    # `success`) so downstream steps know the budget was exhausted. The report
    # is still written and the exit code reflects the partial status.
    assert exit_code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["operations"]
    assert all(isinstance(row, dict) for row in report["operations"])
    assert any(row["status"] == "blocked" for row in report["operations"])
