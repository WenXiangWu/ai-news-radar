from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar_core.connectors.base import (
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    RawDocument,
)
from radar_core.discovery import Operation
from radar_core.registry import SourceSpec
from radar_core.storage import StateStore
from radar_core.translation.base import TranslationRequest, TranslationResponse


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
            "enabled": True,
        }
    )


def _operation(source: SourceSpec) -> Operation:
    return Operation(
        source_id=source.id,
        source=source,
        scheduled_at=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
        next_run_at="2026-09-20T01:00:00+00:00",
        cursor={},
        entity_ids=("framework.docs",),
        surface_ids=("surface.docs",),
    )


class FakeConnector:
    adapter_name = "fake"

    def __init__(self, body: str = "Hello world", *, fail_fetch: bool = False):
        self.body = body
        self.fail_fetch = fail_fetch
        self.discover_calls = 0
        self.fetch_calls = 0

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        self.discover_calls += 1
        return DiscoveryPage(
            items=[
                DiscoveredItem(
                    source_id="source.docs",
                    native_id="guide.md",
                    url="https://docs.example.test/guide.md",
                    title="Guide",
                    content_type="text/markdown",
                )
            ],
            cursor=Cursor(
                token=f"cursor-{self.discover_calls}",
                metadata={"last_scheduled_at": "2026-09-20T00:00:00+00:00"},
            ),
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        self.fetch_calls += 1
        if self.fail_fetch:
            raise ValueError("fixture fetch failed")
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=self.body,
            content_type=item.content_type,
        )


class RecordingRouter:
    def __init__(self) -> None:
        self.requests: list[TranslationRequest] = []

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.requests.append(request)
        return TranslationResponse(
            translated_text=f"你好：{request.text}",
            provider="fake",
            model="fake-model",
            metadata={"provider": "fake"},
        )


class EmptyRouter:
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        return TranslationResponse(
            translated_text=None,
            provider=None,
            model=None,
            reason="providers_exhausted",
        )


def _context(tmp_path: Path, connector: FakeConnector, router: RecordingRouter) -> Any:
    from radar_core.pipeline import RunContext

    return RunContext(
        state=StateStore.open(tmp_path / "state.sqlite3"),
        run_id="run-1",
        target_locales=("zh-CN",),
        connector_factory=lambda _source: connector,
        router=router,
        now=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
    )


def test_run_source_is_idempotent_for_existing_translation(tmp_path: Path):
    from radar_core.pipeline import run_source

    source = _source()
    connector = FakeConnector()
    router = RecordingRouter()
    context = _context(tmp_path, connector, router)

    first = run_source(source, context)
    second = run_source(source, replace(context, run_id="run-2"))

    assert first.fetched == 1
    assert first.translated == 1
    assert second.fetched == 1
    assert second.translation_reused == 1
    assert len(router.requests) == 1
    assert context.state.count_rows("revisions") == 1
    assert context.state.count_rows("translations") == 1
    assert context.state.get_cursor("source.docs")["cursor"]["token"] == "cursor-2"
    context.state.close()


def test_task_derived_connector_config_flattens_nested_source_payload():
    from radar_core.pipeline import _connector_config
    from radar_core.registry import TaskSpec

    task = TaskSpec.from_payload(
        {
            "id": "task.source.coding-tools.catalog",
            "kind": "index_sync",
            "adapter": "composite",
            "output": {"path": "frontend/path/coding-tools"},
            "schedule": {"enabled": True, "timezone": "UTC", "cron": "0 * * * *"},
        },
        module={
            "id": "source.coding-tools",
            "kind": "source",
            "enabled": True,
            "display": {"name": "Coding tools"},
            "source": {
                "sources": [
                    {
                        "adapter": "llms_txt",
                        "url": "https://docs.example.test/llms.txt",
                    }
                ]
            },
        },
    )

    config = _connector_config(task.to_source_spec(), "composite")

    assert config["sources"] == [
        {
            "adapter": "llms_txt",
            "url": "https://docs.example.test/llms.txt",
        }
    ]


def test_changed_source_body_creates_one_new_revision_and_translation(tmp_path: Path):
    from radar_core.pipeline import run_source

    source = _source()
    connector = FakeConnector("Hello world")
    router = RecordingRouter()
    context = _context(tmp_path, connector, router)

    first = run_source(source, context)
    connector.body = "Hello changed world"
    second = run_source(source, replace(context, run_id="run-2"))

    assert first.revisions_new == 1
    assert second.revisions_new == 1
    assert second.translated == 1
    assert len(router.requests) == 2
    assert context.state.count_rows("revisions") == 2
    assert context.state.count_rows("translations") == 2
    context.state.close()


def test_run_operation_records_context_and_does_not_advance_cursor_on_failure(
    tmp_path: Path,
):
    from radar_core.pipeline import run_operation

    source = _source()
    connector = FakeConnector(fail_fetch=True)
    router = RecordingRouter()
    context = _context(tmp_path, connector, router)

    result = run_operation(_operation(source), context)

    assert result.status == "failed"
    assert result.source_id == "source.docs"
    assert result.entity_ids == ("framework.docs",)
    assert result.surface_ids == ("surface.docs",)
    assert context.state.get_cursor("source.docs") is None
    assert context.state.count_rows("translations") == 0
    context.state.close()


def test_translation_provider_exhaustion_does_not_advance_cursor_or_baseline(
    tmp_path: Path,
):
    from radar_core.discovery import register_new_sources
    from radar_core.pipeline import run_operation

    source = _source()
    connector = FakeConnector()
    context = _context(tmp_path, connector, EmptyRouter())
    context.state.record_run("bootstrap", {"status": "success"})
    from radar_core.registry import Registry

    registry = Registry(sources=[source])
    register_new_sources(registry, context.state, "bootstrap")

    result = run_operation(_operation(source), replace(context, run_id="run-failed"))

    assert result.status == "partial"
    assert result.failed == 1
    assert context.state.get_cursor(source.id) is None
    assert context.state.get_source_registration(source.id)[
        "baseline_fingerprint"
    ] is None
    translation = context.state.iter_rows("translations")[0]
    assert translation["status"] == "needs_review"
    context.state.close()
