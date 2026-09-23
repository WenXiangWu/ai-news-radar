"""TDD tests for Task 3: select-before-fetch in the pipeline.

These tests are written first (RED) and drive the pipeline changes that
follow ledger selection instead of unconditionally fetching every item.
"""

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
from radar_core.registry import SourceSpec
from radar_core.storage import StateStore
from radar_core.translation.base import TranslationRequest, TranslationResponse


class CountingConnector:
    adapter_name = "fake"
    incremental_class = "revision-native"

    def __init__(self) -> None:
        self.fetches: list[str] = []
        self.revisions = {
            "p1": "sha-a",
            "p2": "sha-b",
        }

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        return DiscoveryPage(
            items=[
                DiscoveredItem(
                    source_id="source.fake",
                    native_id="p1",
                    url="https://ex/p1",
                    title="p1",
                    remote_revision=self.revisions["p1"],
                ),
                DiscoveredItem(
                    source_id="source.fake",
                    native_id="p2",
                    url="https://ex/p2",
                    title="p2",
                    remote_revision=self.revisions["p2"],
                ),
            ],
            cursor=Cursor(etag="etag-1"),
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        self.fetches.append(item.native_id)
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=item.native_id,
            content_type="text/plain",
        )


class RecordingRouter:
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        return TranslationResponse(
            translated_text=f"译文：{request.text}",
            provider="fake",
            model="fake-model",
            metadata={"provider": "fake"},
        )


def _source(max_new_items: int = 10) -> SourceSpec:
    return SourceSpec.from_payload(
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.fake",
            "kind": "source",
            "source_type": "fake",
            "adapter": "fake",
            "name": "Fake",
            "locator": "https://ex.test/",
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
                "max_new_items": max_new_items,
            },
            "output_root": "frontend/fake",
            "translation_profile": "prose/v1",
            "enabled": True,
        }
    )


def _context(
    state: StateStore,
    connector: CountingConnector,
    *,
    mode: str = "incremental",
    run_id: str = "run-1",
) -> Any:
    from radar_core.pipeline import RunContext

    return RunContext(
        state=state,
        run_id=run_id,
        target_locales=("zh-CN",),
        connector_factory=lambda _source: connector,
        router=RecordingRouter(),
        now=datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc),
        mode=mode,
    )


def _mark_fetched(state: StateStore, item_id: str, revision: str) -> None:
    state.upsert_source_item(
        {
            "source_id": "source.fake",
            "item_id": item_id,
            "canonical_url": f"https://ex/{item_id}",
            "remote_revision": revision,
            "fetched_revision": revision,
            "status": "fetched",
        }
    )


def test_first_run_is_baseline_only_and_does_not_fetch(tmp_path: Path):
    from radar_core.pipeline import run_source

    connector = CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    result = run_source(_source(), _context(state, connector, mode="incremental"))

    assert result.run_kind == "baseline_only"
    assert result.status == "success"
    assert connector.fetches == []
    assert result.discovered == 2
    assert result.fetched == 0
    assert state.baseline_initialized("source.fake") is True
    state.close()


def test_second_run_fetches_only_changed_item(tmp_path: Path):
    from radar_core.pipeline import run_source

    connector = CountingConnector()
    source = _source()
    state = StateStore.open(tmp_path / "state.sqlite3")

    # Run 1: establish baseline (no fetch).
    first = run_source(source, _context(state, connector, mode="incremental", run_id="r1"))
    assert first.run_kind == "baseline_only"
    assert connector.fetches == []

    # Simulate a prior successful fetch of both items at their current revisions.
    _mark_fetched(state, "p1", "sha-a")
    _mark_fetched(state, "p2", "sha-b")

    # Now p2's remote revision changes; p1 stays the same.
    connector.revisions["p2"] = "sha-c"

    second = run_source(source, _context(state, connector, mode="incremental", run_id="r2"))

    assert second.run_kind == "incremental"
    assert connector.fetches == ["p2"]
    assert second.selected == 1
    assert second.deferred == 0
    assert second.skipped_not_modified == 1
    state.close()


def test_unsupported_connector_fetches_nothing(tmp_path: Path):
    from radar_core.pipeline import run_source

    connector = CountingConnector()
    connector.incremental_class = "unsupported"
    state = StateStore.open(tmp_path / "state.sqlite3")
    # Baseline must already exist so we reach the incremental_class check.
    state.mark_source_baseline("source.fake", "r0")

    result = run_source(_source(), _context(state, connector, mode="incremental"))

    assert result.status == "unsupported_incremental"
    assert result.run_kind == "incremental"
    assert connector.fetches == []
    assert result.fetched == 0
    state.close()


def test_missing_revision_fields_marks_unsupported(tmp_path: Path):
    """Connector without incremental_class whose items lack revision/etag/last_modified."""

    from radar_core.pipeline import run_source

    class BareConnector(CountingConnector):
        incremental_class = None  # type: ignore[assignment]

        def discover(self, cursor: Cursor) -> DiscoveryPage:
            return DiscoveryPage(
                items=[
                    DiscoveredItem(
                        source_id="source.fake",
                        native_id="p1",
                        url="https://ex/p1",
                        title="p1",
                    ),
                    DiscoveredItem(
                        source_id="source.fake",
                        native_id="p2",
                        url="https://ex/p2",
                        title="p2",
                    ),
                ],
                cursor=Cursor(etag="etag-1"),
            )

    connector = BareConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.mark_source_baseline("source.fake", "r0")

    result = run_source(_source(), _context(state, connector, mode="incremental"))

    assert result.status == "unsupported_incremental"
    assert connector.fetches == []
    state.close()


def test_max_new_items_caps_selection_and_defers_rest(tmp_path: Path):
    from radar_core.pipeline import run_source

    class FiveConnector(CountingConnector):
        def discover(self, cursor: Cursor) -> DiscoveryPage:
            return DiscoveryPage(
                items=[
                    DiscoveredItem(
                        source_id="source.fake",
                        native_id=f"p{i}",
                        url=f"https://ex/p{i}",
                        title=f"p{i}",
                        remote_revision=f"sha-{i}",
                    )
                    for i in range(5)
                ],
                cursor=Cursor(etag="etag-1"),
            )

        def fetch(self, item: DiscoveredItem) -> RawDocument:
            self.fetches.append(item.native_id)
            return RawDocument(
                source_id=item.source_id,
                native_id=item.native_id,
                url=item.url,
                title=item.title,
                text=item.native_id,
                content_type="text/plain",
            )

    connector = FiveConnector()
    source = _source(max_new_items=2)
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.mark_source_baseline("source.fake", "r0")

    result = run_source(source, _context(state, connector, mode="incremental"))

    assert result.run_kind == "incremental"
    assert result.selected == 2
    assert result.deferred == 3
    assert result.fetched == 2
    assert connector.fetches == ["p0", "p1"]
    state.close()


def test_bootstrap_is_disabled(tmp_path: Path):
    from radar_core.pipeline import run_source

    connector = CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    result = run_source(_source(), _context(state, connector, mode="bootstrap"))

    assert result.status == "failed"
    assert result.fetched == 0
    assert result.errors == ["bootstrap is disabled"]
    assert connector.fetches == []
    state.close()


def test_baseline_at_is_written_once(tmp_path: Path):
    from radar_core.pipeline import run_source

    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source()
    first = run_source(
        source, _context(state, CountingConnector(), mode="incremental", run_id="r1")
    )
    assert first.run_kind == "baseline_only"
    stamped = state.baseline_at(source.id)
    assert stamped
    run_source(
        source, _context(state, CountingConnector(), mode="incremental", run_id="r2")
    )
    assert state.baseline_at(source.id) == stamped
    state.close()


def test_no_change_run_fetches_nothing(tmp_path: Path):
    from radar_core.pipeline import run_source

    connector = CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.mark_source_baseline("source.fake", "r0")
    _mark_fetched(state, "p1", "sha-a")
    _mark_fetched(state, "p2", "sha-b")

    result = run_source(_source(), _context(state, connector, mode="incremental"))

    assert result.run_kind == "incremental"
    assert result.status == "success"
    assert connector.fetches == []
    assert result.fetched == 0
    assert result.selected == 0
    assert result.skipped_not_modified == 2
    state.close()


def test_304_discover_with_empty_page_still_fetches_lagged_ledger_item(
    tmp_path: Path,
):
    """Spec §3.1: after a 304, lagged items (fetched_revision !=
    remote_revision) must still be selected from the ledger even when the
    discovery page is empty. The pipeline reconstructs a DiscoveredItem from
    the ledger row and fetches it."""

    from radar_core.pipeline import run_source

    class NotModifiedConnector(CountingConnector):
        """Second discover returns an empty page (manifest 304)."""

        def __init__(self) -> None:
            super().__init__()
            self._call = 0

        def discover(self, cursor: Cursor) -> DiscoveryPage:
            self._call += 1
            if self._call == 1:
                return super().discover(cursor)
            return DiscoveryPage(items=[], cursor=cursor)

    connector = NotModifiedConnector()
    source = _source()
    state = StateStore.open(tmp_path / "state.sqlite3")

    # Run 1: baseline_only.
    first = run_source(source, _context(state, connector, mode="incremental", run_id="r1"))
    assert first.run_kind == "baseline_only"
    assert connector.fetches == []

    # Simulate p1 fetched at sha-a; p2 fetched at an older revision (lagged).
    _mark_fetched(state, "p1", "sha-a")
    state.upsert_source_item(
        {
            "source_id": "source.fake",
            "item_id": "p2",
            "canonical_url": "https://ex/p2",
            "remote_revision": "sha-b",
            "fetched_revision": "sha-old",
            "status": "fetched",
        }
    )

    # Run 2: discover returns 304 (empty page). p2 is lagged in the ledger and
    # must still be fetched from the ledger row.
    second = run_source(source, _context(state, connector, mode="incremental", run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.selected == 1
    assert connector.fetches == ["p2"]
    assert second.fetched == 1
    state.close()


def test_unsupported_incremental_run_status_is_not_hard_failed(tmp_path: Path):
    """The run row status for an unsupported_incremental operation must be
    consistent with the operation result, not a hard 'failed'."""

    from radar_core.pipeline import run_source

    connector = CountingConnector()
    connector.incremental_class = "unsupported"
    state = StateStore.open(tmp_path / "state.sqlite3")
    state.mark_source_baseline("source.fake", "r0")

    result = run_source(_source(), _context(state, connector, mode="incremental"))

    assert result.status == "unsupported_incremental"
    # The run row should not be a hard "failed"; it should reflect the
    # per-operation unsupported state.
    run_row = state.iter_rows("runs")[0]
    assert run_row["status"] != "failed"
    state.close()


def test_http_404_item_is_marked_missing_and_next_item_is_fetched(tmp_path: Path):
    from radar_core.connectors.base import ConnectorError
    from radar_core.pipeline import run_source

    class PartialGoneConnector(CountingConnector):
        def fetch(self, item: DiscoveredItem) -> RawDocument:
            if item.native_id == "p1":
                raise ConnectorError(
                    "llms_txt request failed with HTTP 404 for https://ex/p1"
                )
            return super().fetch(item)

    connector = PartialGoneConnector()
    source = _source(max_new_items=1)
    state = StateStore.open(tmp_path / "state.sqlite3")
    first = run_source(
        source, _context(state, connector, mode="baseline_only", run_id="r1")
    )
    assert first.status == "success"
    assert connector.fetches == []

    second = run_source(
        source, _context(state, connector, mode="incremental", run_id="r2")
    )

    assert second.status == "success"
    assert connector.fetches == ["p2"]
    assert second.fetched == 1
    rows = {row["item_id"]: row for row in state.list_source_items("source.fake")}
    assert rows["p1"]["status"] == "missing"
    state.close()


def test_oversized_item_is_blocked_and_next_item_is_fetched(tmp_path: Path):
    from radar_core.connectors.base import ConnectorError
    from radar_core.pipeline import run_source

    class OversizedConnector(CountingConnector):
        def fetch(self, item: DiscoveredItem) -> RawDocument:
            if item.native_id == "p1":
                raise ConnectorError(
                    "llms_txt response size exceeds 4194304 bytes for https://ex/p1"
                )
            return super().fetch(item)

    connector = OversizedConnector()
    source = _source(max_new_items=1)
    state = StateStore.open(tmp_path / "state.sqlite3")
    run_source(source, _context(state, connector, mode="baseline_only", run_id="r1"))
    second = run_source(
        source, _context(state, connector, mode="incremental", run_id="r2")
    )

    assert second.status == "success"
    assert connector.fetches == ["p2"]
    rows = {row["item_id"]: row for row in state.list_source_items("source.fake")}
    assert rows["p1"]["status"] == "blocked"
    state.close()
