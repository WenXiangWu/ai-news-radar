from __future__ import annotations

from pathlib import Path

from scripts.validate_registered_sources import validate_registered_sources


def test_actual_way_registry_has_an_executable_adapter_for_every_enabled_source():
    registry_root = Path(__file__).parent / "fixtures" / "actual-way-registry"

    errors = validate_registered_sources(registry_root)

    assert errors == []


def test_source_validation_report_includes_each_enabled_source_and_health_status():
    from scripts.validate_registered_sources import build_source_validation_report

    registry_root = Path(__file__).parent / "fixtures" / "actual-way-registry"

    report = build_source_validation_report(registry_root)

    assert report["schema"] == "radar-source-validation/v1"
    assert report["counts"]["sources"] >= 1
    assert report["status"] == "ok"
    assert all(row["health"]["status"] in {"healthy", "degraded"} for row in report["sources"])
    assert all(row["checked_at"] for row in report["sources"])
    assert all(row["latency_ms"] >= 0 for row in report["sources"])
    assert all(row["live"]["latency_ms"] >= 0 for row in report["sources"])


def test_knowledge_sync_task_inherits_adapter_from_source_declaration():
    from radar_core.registry import load_registry

    registry_root = Path(__file__).parent / "fixtures" / "actual-way-registry"
    registry = load_registry(registry_root)

    task = next(
        item
        for item in registry.tasks
        if item.id == "task.knowledge.example.docs.sync"
    )

    assert task.adapter == "llms_txt"
    source = task.to_source_spec()
    assert source.source_type == "llms_txt"
