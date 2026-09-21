"""Skip translation: fetch still runs, DeepSeek/Google are never called."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from radar_core.connectors.base import Cursor, DiscoveredItem, DiscoveryPage, RawDocument
from radar_core.pipeline import RunContext, run_source
from radar_core.registry import SourceSpec
from radar_core.storage import StateStore
from radar_core.translation.base import TranslationRequest, TranslationResponse
from scripts.radar_run import parse_args


class _ExplodingRouter:
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        raise AssertionError("translation router must not be called")


class _FetchConnector:
    adapter_name = "fake"
    incremental_class = "revision-native"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        return DiscoveryPage(
            items=[
                DiscoveredItem(
                    source_id="source.docs",
                    native_id="guide.md",
                    url="https://docs.example.test/guide.md",
                    title="Guide",
                    content_type="text/markdown",
                    remote_revision="rev-1",
                )
            ],
            cursor=Cursor(token="cursor-1"),
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text="Hello world",
            content_type=item.content_type,
        )


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
                "max_new_items": 10,
            },
            "output_root": "frontend/docs",
            "translation_profile": "prose/v1",
            "enabled": True,
        }
    )


def test_skip_translation_fetches_without_calling_the_router(tmp_path: Path):
    context = RunContext(
        state=StateStore.open(tmp_path / "state.sqlite3"),
        run_id="run-1",
        target_locales=("zh-CN",),
        connector_factory=lambda _source: _FetchConnector(),
        router=_ExplodingRouter(),
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        mode="bootstrap",
        skip_translation=True,
    )

    result = run_source(_source(), context)

    assert result.status == "success"
    assert result.fetched == 1
    assert result.translated == 0
    assert context.state.count_rows("translations") == 0
    context.state.close()


def test_radar_run_exposes_skip_translation_and_max_new_items_flags():
    args = parse_args(
        [
            "--target-root",
            ".",
            "--state",
            "state.sqlite3",
            "--report",
            "report.json",
            "--skip-translation",
            "--max-new-items",
            "1",
        ]
    )

    assert args.skip_translation is True
    assert args.max_new_items == 1
