"""Every declared source must be accounted for before a live e2e run."""

from __future__ import annotations

import json
from pathlib import Path

from radar_core.connectors.base import ConnectorFactory
from radar_core.discovery import FETCH_ADAPTERS
from radar_core.source_audit import collect_all_sources, FETCHABLE_LEGACY_ADAPTERS


WAY_ROOT = Path(
    "/Users/dz0400857/Desktop/way-to-agentic/.worktrees/radar-strict-incremental"
)


def test_collect_all_sources_covers_radar_wiki_and_docs():
    inventory = collect_all_sources(WAY_ROOT)

    origins = {row.origin for row in inventory}
    assert origins >= {"radar", "wiki", "docs"}
    assert len([row for row in inventory if row.origin == "radar"]) >= 20
    assert len([row for row in inventory if row.origin == "wiki"]) == 15
    assert len([row for row in inventory if row.origin == "docs"]) >= 35
    ids = [row.source_id for row in inventory]
    assert len(ids) == len(set(ids))
    assert "source.framework.deepseek-harness" in ids
    assert "wiki.deepseek-harness" in ids
    assert "docs.langchain" in ids


def test_example_radar_source_is_skipped_without_network(tmp_path: Path):
    from radar_core.source_audit import SourceAuditRow
    from radar_core.storage import StateStore
    from scripts.verify_all_sources import _verify_row

    result = _verify_row(
        SourceAuditRow(
            source_id="source.example.official-blog",
            origin="radar",
            adapter="rss_article",
            locator="https://example.com/blog",
            enabled=True,
            payload={"id": "source.example.official-blog"},
        ),
        state=StateStore.open(tmp_path / "state.sqlite3"),
        max_new_items=1,
        fetch_bodies=True,
    )

    assert result["status"] == "skipped"
    assert "fixture" in result["errors"][0]
    inventory = collect_all_sources(WAY_ROOT)
    fetch_rows = [
        row
        for row in inventory
        if row.origin == "radar" and row.adapter in FETCH_ADAPTERS
    ]
    assert fetch_rows
    missing = [
        row.source_id
        for row in fetch_rows
        if row.adapter not in ConnectorFactory.supported_adapters()
    ]
    assert missing == []


def test_legacy_docs_adapters_are_classified():
    inventory = collect_all_sources(WAY_ROOT)
    docs = [row for row in inventory if row.origin == "docs"]
    unknown = [
        (row.source_id, row.adapter)
        for row in docs
        if row.adapter not in FETCHABLE_LEGACY_ADAPTERS
        and row.adapter not in ConnectorFactory.supported_adapters()
    ]
    # mintlify / langchain_md / next_data must still be listed, not dropped.
    assert unknown == [] or all(
        adapter in {"mintlify", "langchain_md", "next_data"}
        for _source_id, adapter in unknown
    )
    assert any(row.adapter == "mintlify" for row in docs)
    assert any(row.adapter == "llms_txt" for row in docs)


def test_radar_scheduler_covers_every_docs_and_uncovered_wiki_source():
    from radar_core.discovery import FETCH_ADAPTERS, discover_due_task_operations
    from radar_core.registry import load_registry
    from radar_core.storage import StateStore
    from datetime import datetime, timezone

    registry = load_registry(WAY_ROOT)
    scheduled = {
        task.to_source_spec().id
        for task in registry.tasks
        if task.enabled and task.adapter in FETCH_ADAPTERS
    }
    docs = [row for row in collect_all_sources(WAY_ROOT) if row.origin == "docs"]
    wikis = [row for row in collect_all_sources(WAY_ROOT) if row.origin == "wiki"]
    already_on_radar = {
        "wiki.deepseek-harness",
        "wiki.cordis",
    }
    missing_docs = [
        row.source_id for row in docs if row.source_id not in scheduled
    ]
    missing_wikis = [
        row.source_id
        for row in wikis
        if row.source_id not in scheduled and row.source_id not in already_on_radar
    ]
    assert missing_docs == [], missing_docs
    assert missing_wikis == [], missing_wikis
    adapters = {
        task.adapter
        for task in registry.tasks
        if task.id.startswith("task.docs.") or task.id.startswith("task.wiki.")
    }
    assert adapters <= FETCH_ADAPTERS

    operations = discover_due_task_operations(
        registry,
        datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        StateStore.open(Path("/tmp/radar-schedule-test.sqlite3")),
        force=True,
    )
    op_ids = {operation.source_id for operation in operations}
    assert "docs.langchain" in op_ids
    assert "wiki.pydantic-ai" in op_ids
    assert "wiki.deepseek-harness" not in op_ids


def test_legacy_docs_adapters_are_remapped_to_fetchable_connectors():
    from radar_core.registry import load_registry

    registry = load_registry(WAY_ROOT)
    by_id = {
        task.to_source_spec().id: task
        for task in registry.tasks
        if task.enabled
    }
    assert by_id["docs.langchain"].adapter == "llms_txt"
    assert by_id["docs.chroma"].adapter == "github_tree"
    assert "chroma-core/chroma" in str(by_id["docs.chroma"].source.get("repo") or "")
    assert by_id["docs.milvus"].adapter == "llms_txt"
    assert str(by_id["docs.milvus"].source.get("url") or "").endswith("/llms.txt")
    assert by_id["docs.guardrails-ai"].adapter == "html_collection"
    assert by_id["docs.dspy"].adapter == "github_tree"
    assert by_id["docs.dspy"].source.get("repo") == "stanfordnlp/dspy"
    assert "github" in str(by_id["docs.phoenix"].source.get("drop_re") or "")
    assert by_id["wiki.pydantic-ai"].adapter == "deepwiki"


def test_docs_github_tree_connector_config_maps_owner_repo(monkeypatch):
    from radar_core.pipeline import _connector_config
    from radar_core.registry import load_registry

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    registry = load_registry(WAY_ROOT)
    task = next(task for task in registry.tasks if task.source_id == "docs.dspy")
    config = _connector_config(task.to_source_spec(), "github_tree")
    assert config["repo"] == "stanfordnlp/dspy"
    assert config["path_prefix"].startswith("docs")
    assert config["token"] == "test-token"


def test_docs_connector_config_maps_official_html_listing_url():
    from radar_core.source_audit import SourceAuditRow
    from scripts.verify_all_sources import _docs_connector_config

    config = _docs_connector_config(
        SourceAuditRow(
            source_id="docs.humanlayer",
            origin="docs",
            adapter="html_collection",
            locator="https://docs.humanlayer.com/",
            enabled=True,
            payload={"official": "https://docs.humanlayer.com/"},
        )
    )

    assert config["listing_url"] == "https://docs.humanlayer.com/"


def test_six_degraded_docs_sources_use_fetchable_adapters():
    inventory = {row.source_id: row for row in collect_all_sources(WAY_ROOT)}
    expected = {
        "docs.opik": "llms_txt",
        "docs.faiss": "html_collection",
        "docs.humanlayer": "html_collection",
        "docs.dspy": "github_tree",
        "docs.letta": "llms_txt",
        "docs.spring-ai": "github_tree",
    }
    for source_id, adapter in expected.items():
        assert inventory[source_id].adapter == adapter, source_id
    assert ".adoc" in (inventory["docs.spring-ai"].payload.get("extensions") or [])
    assert "download" in str(inventory["docs.letta"].payload.get("drop_re") or "")
    dspy = inventory["docs.dspy"]
    assert dspy.payload.get("github_owner") == "stanfordnlp"
    assert dspy.payload.get("github_repo") == "dspy"
    assert str(dspy.payload.get("github_docs_prefix") or "").startswith("docs")
