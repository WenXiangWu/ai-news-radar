from __future__ import annotations

from datetime import datetime, timezone

from radar_core.discovery import register_new_sources
from radar_core.registry import Registry, SourceSpec
from radar_core.storage import StateStore
from radar_core.verification import build_baseline_verification


def _source() -> SourceSpec:
    return SourceSpec.from_payload(
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.docs",
            "kind": "source",
            "source_type": "llms_txt",
            "adapter": "llms_txt",
            "name": "Docs",
            "locator": "https://docs.example.test/llms.txt",
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
            },
            "output_root": "frontend/docs",
            "translation_profile": "prose/v1",
            "policy_version": "translation/policy-v1",
            "enabled": True,
            "module_id": "knowledge.example.docs",
        }
    )


def test_baseline_verification_reports_pending_until_a_successful_run_commits_it(
    tmp_path,
):
    source = _source()
    registry = Registry(sources=[source])
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})
    register_new_sources(registry, state, "run-register")

    pending = build_baseline_verification(registry, state, operations=[])

    assert pending["status"] == "pending"
    assert pending["counts"]["baseline_pending"] == 1
    assert pending["sources"][0]["baseline_status"] == "staged"

    state.record_run("run-content", {"status": "success"})
    state.commit_source_registration(source.id, "run-register")
    state.advance_cursor(
        source.id,
        {
            "token": "v1",
            "last_scheduled_at": "2026-09-20T00:00:00+00:00",
        },
        "run-content",
    )
    verified = build_baseline_verification(
        registry,
        state,
        operations=[
            {
                "source_id": source.id,
                "task_id": "task.knowledge.example.docs.sync",
                "status": "success",
                "revisions_new": 1,
                "translated": 1,
            }
        ],
    )

    assert verified["status"] == "ok"
    assert verified["counts"]["baseline_verified"] == 1
    row = verified["sources"][0]
    assert row["baseline_status"] == "verified"
    assert row["cursor_status"] == "present"
    assert row["module_id"] == "knowledge.example.docs"
    assert row["updated"] == 1
    assert row["translated"] == 1
    state.close()


def test_baseline_verification_exposes_partial_runs_as_not_verified(tmp_path):
    source = _source()
    registry = Registry(sources=[source])
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})
    register_new_sources(registry, state, "run-register")

    report = build_baseline_verification(
        registry,
        state,
        operations=[
            {
                "source_id": source.id,
                "task_id": "task.knowledge.example.docs.sync",
                "status": "partial",
                "failed": 1,
                "errors": ["translation failed"],
            }
        ],
    )

    assert report["status"] == "failed"
    row = report["sources"][0]
    assert row["status"] == "partial"
    assert row["baseline_status"] == "staged"
    assert row["cursor_status"] == "missing"
    assert row["errors"] == ["translation failed"]
    state.close()
