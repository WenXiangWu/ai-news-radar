from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from radar_core.discovery import (
    discover_due_operations,
    register_new_sources,
)
from radar_core.registry import (
    Registry,
    SourceSpec,
    load_framework_entities,
    load_registry,
    load_sources,
    load_surfaces,
)
from radar_core.storage import StateStore


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "actual-way-registry"


def copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / FIXTURE_ROOT.name
    for source in FIXTURE_ROOT.rglob("*"):
        destination = target / source.relative_to(FIXTURE_ROOT)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    return target


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_registry(target_root: Path) -> Registry:
    return Registry(
        sources=load_sources(target_root),
        entities=load_framework_entities(target_root),
        surfaces=load_surfaces(target_root),
    )


def test_new_source_manifest_is_discovered_and_due_without_radar_code_changes(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    existing_source_path = (
        target / "radar/registry/sources/example.official-blog.json"
    )
    existing_source = json.loads(existing_source_path.read_text(encoding="utf-8"))
    existing_source["schedule"]["cron"] = "0 23 * * *"
    existing_source["enabled"] = False
    write_json(existing_source_path, existing_source)
    source_path = target / "radar/registry/sources/example.new-source.json"
    write_json(
        source_path,
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.example.new-source",
            "kind": "source",
            "source_type": "rss",
            "name": "New source",
            "locator": "https://example.com/new-feed.xml",
            "schedule": {
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "cron": "0 11 * * *",
            },
            "output_root": "frontend/sources/new-source",
            "enabled": True,
        },
    )

    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})

    registered = register_new_sources(registry, state, "run-register")
    operations = discover_due_operations(
        registry,
        datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc),
        state,
    )

    assert "source.example.new-source" in {source.id for source in registry.sources}
    assert set(registered) == {
        "source.example.official-blog",
        "source.example.new-source",
        "source.example.rss",
        "source.knowledge.example.docs",
    }
    assert state.get_source_registration("source.example.new-source") is not None
    assert [operation.source_id for operation in operations] == [
        "source.example.new-source"
    ]
    assert operations[0].next_run_at == "2026-09-21T11:00:00+08:00"
    state.close()


def test_disabled_sources_are_visible_but_not_executable_and_source_schedule_wins(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    existing_source_path = (
        target / "radar/registry/sources/example.official-blog.json"
    )
    existing_source = json.loads(existing_source_path.read_text(encoding="utf-8"))
    existing_source["schedule"]["cron"] = "0 23 * * *"
    existing_source["enabled"] = False
    write_json(existing_source_path, existing_source)
    source_path = target / "radar/registry/sources/example.disabled.json"
    write_json(
        source_path,
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.example.disabled",
            "kind": "source",
            "source_type": "rss",
            "name": "Disabled source",
            "locator": "https://example.com/disabled.xml",
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 4 * * *",
            },
            "output_root": "frontend/sources/disabled",
            "enabled": False,
        },
    )
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})
    register_new_sources(registry, state, "run-register")

    operations = discover_due_operations(
        registry,
        datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc),
        state,
    )

    assert "source.example.disabled" in {source.id for source in registry.sources}
    assert "source.example.disabled" in registry.diagnostics["disabled_sources"]
    assert "source.example.disabled" not in {
        operation.source_id for operation in operations
    }
    assert "source.example.official-blog" not in {
        operation.source_id for operation in operations
    }
    assert registry.diagnostics["next_run_at"]["source.example.disabled"] == (
        "2026-09-21T04:00:00+00:00"
    )
    state.close()


def test_due_recovers_a_cron_window_after_scheduler_restart(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})
    register_new_sources(registry, state, "run-register")

    operations = discover_due_operations(
        registry,
        datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc),
        state,
    )

    assert [operation.source_id for operation in operations] == [
        "source.example.official-blog"
    ]
    assert registry.diagnostics["next_run_at"]["source.example.official-blog"] == (
        "2026-09-21T04:17:00+08:00"
    )
    state.close()


def test_due_honors_last_scheduled_at_on_an_exact_cron_minute(tmp_path: Path):
    target = copy_fixture(tmp_path)
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})
    register_new_sources(registry, state, "run-register")
    source = registry.sources[0]
    state.record_run("run-content", {"status": "success"})
    state.advance_cursor(
        source.id,
        {
            "registry_fingerprint": source.registry_fingerprint,
            "last_scheduled_at": "2026-09-20T04:17:00+08:00",
        },
        "run-content",
    )

    operations = discover_due_operations(
        registry,
        datetime(2026, 9, 19, 20, 17, tzinfo=timezone.utc),
        state,
    )

    assert operations == []
    assert registry.diagnostics["next_run_at"]["source.example.official-blog"] == (
        "2026-09-21T04:17:00+08:00"
    )
    state.close()


def test_due_recovers_a_cron_window_when_radar_starts_a_minute_late(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "running"})
    register_new_sources(registry, state, "run-register")

    operations = discover_due_operations(
        registry,
        datetime(2026, 9, 19, 20, 18, tzinfo=timezone.utc),
        state,
    )

    assert [operation.source_id for operation in operations] == [
        "source.example.official-blog"
    ]
    assert operations[0].scheduled_at.isoformat() == (
        "2026-09-20T04:17:00+08:00"
    )
    state.close()


def test_registry_baseline_is_not_committed_until_source_succeeds(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "running"})
    register_new_sources(registry, state, "run-register")

    assert state.get_source_registration("source.example.official-blog") is not None
    assert state.get_source_registration(
        "source.example.official-blog"
    )["baseline_fingerprint"] is None
    assert state.count_rows("cursors") == 0

    state.record_run("run-failed", {"status": "failed"})
    state.stage_source_registration(
        "source.example.official-blog",
        registry.sources[0].to_dict(),
        registry.sources[0].registry_fingerprint,
        "run-failed",
    )
    assert state.get_source_registration(
        "source.example.official-blog"
    )["baseline_fingerprint"] is None
    with pytest.raises(ValueError, match="successful run"):
        state.commit_source_registration("source.example.official-blog", "run-failed")

    state.record_run("run-success", {"status": "success"})
    state.stage_source_registration(
        "source.example.official-blog",
        registry.sources[0].to_dict(),
        registry.sources[0].registry_fingerprint,
        "run-success",
    )
    state.commit_source_registration("source.example.official-blog", "run-success")

    assert state.get_source_registration(
        "source.example.official-blog"
    )["baseline_fingerprint"] == registry.sources[0].registry_fingerprint
    state.close()


def test_disabled_source_is_registered_without_an_operational_cursor(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    source_path = target / "radar/registry/sources/example.disabled.json"
    write_json(
        source_path,
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.example.disabled",
            "kind": "source",
            "source_type": "rss",
            "name": "Disabled",
            "locator": "https://example.com/disabled.xml",
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 4 * * *",
            },
            "output_root": "frontend/sources/disabled",
            "enabled": False,
        },
    )
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "running"})
    register_new_sources(registry, state, "run-register")

    assert state.get_source_registration("source.example.disabled") is not None
    assert state.get_cursor("source.example.disabled") is None
    state.close()


def test_invalid_manifest_is_reported_without_blocking_valid_sources(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    write_json(
        target / "radar/registry/sources/invalid.json",
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.example.invalid",
            "kind": "source",
            "source_type": "rss",
            "name": "Invalid",
            "locator": "https://example.com/invalid.xml",
            "schedule": {
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "cron": "not a cron",
            },
            "output_root": "frontend/sources/invalid",
        },
    )

    registry = load_registry(target)

    assert [source.id for source in registry.sources] == [
        "source.example.official-blog",
        "source.example.rss",
        "source.knowledge.example.docs",
    ]
    assert any(
        item["path"].endswith("radar/registry/sources/invalid.json")
        for item in registry.diagnostics["invalid_declarations"]
    )


def test_duplicate_source_ids_are_rejected_instead_of_silently_dropped(
    tmp_path: Path,
):
    target = copy_fixture(tmp_path)
    duplicate = json.loads(
        (target / "radar/registry/sources/example.official-blog.json").read_text(
            encoding="utf-8"
        )
    )
    write_json(target / "radar/registry/sources/duplicate.json", duplicate)

    with pytest.raises(ValueError, match="duplicate source"):
        load_sources(target)


def test_load_sources_includes_legacy_knowledge_registry(tmp_path: Path):
    target = tmp_path / "legacy-way"
    write_json(
        target / "radar/protocol.json",
        {
            "protocol": "way-to-agentic-radar",
            "version": "1.0",
            "registry_root": "radar/registry",
            "registry_index": "radar/registry/index.json",
        },
    )
    write_json(
        target / "radar/registry/index.json",
        {
            "schema": "radar-registry/v1",
            "defaults": {
                "timezone": "UTC",
                "cron": "0 5 * * *",
            },
            "knowledge_registry": "knowledge.json",
            "managed_roots": [],
            "modules": [],
        },
    )
    write_json(
        target / "radar/registry/knowledge.json",
        {
            "sources": [
                {
                    "module_id": "knowledge.anthropic.engineering",
                    "id": "engineering",
                    "label": "Anthropic Engineering",
                    "output": "frontend/sources/engineering",
                    "adapter": "knowledge_source",
                    "fulltext": True,
                    "enabled": True,
                    "tasks": [
                        {
                            "kind": "knowledge_sync",
                            "schedule": {
                                "enabled": True,
                                "timezone": "Asia/Shanghai",
                                "cron": "0 4 * * *",
                            },
                        }
                    ],
                }
            ]
        },
    )

    sources = load_sources(target)

    assert [source.id for source in sources] == [
        "source.knowledge.anthropic.engineering"
    ]
    assert sources[0].source_type == "knowledge"
    assert sources[0].schedule["cron"] == "0 4 * * *"
    assert sources[0].output_root == "frontend/sources/engineering"


def test_discovery_reports_changed_and_invalid_declarations(tmp_path: Path):
    target = copy_fixture(tmp_path)
    registry = make_registry(target)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.record_run("run-register", {"status": "success"})
    register_new_sources(registry, state, "run-register")

    changed = load_sources(target)[0].to_dict()
    changed["name"] = "Changed official blog"
    changed["schedule"] = {
        "enabled": True,
        "timezone": "UTC",
        "cron": "not a cron",
    }
    invalid_registry = Registry(
        sources=[
            SourceSpec.from_payload(changed),
            SourceSpec.from_payload(
                {
                    **changed,
                    "id": "source.example.invalid",
                    "name": "Invalid source",
                }
            ),
        ],
        entities=[],
        surfaces=[],
    )

    operations = discover_due_operations(
        invalid_registry,
        datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc),
        state,
    )

    assert operations == []
    assert invalid_registry.diagnostics["changed_sources"] == [
        "source.example.official-blog"
    ]
    assert {
        item["source_id"] for item in invalid_registry.diagnostics["invalid_declarations"]
    } == {"source.example.official-blog", "source.example.invalid"}
    assert invalid_registry.diagnostics["next_run_at"][
        "source.example.official-blog"
    ] is None
    state.close()
