"""TDD for the 2026-09-21 final-review fix wave on the Radar strict-incremental
branch.

Each test pins one finding (C1, I1, I2, I3, I5) against the spec at
docs/superpowers/specs/2026-09-21-radar-strict-incremental-design.md. I4 lives in
tests/test_radar_timeout.py because it already had the relevant fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from radar_core.connectors.base import (
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpResponse,
    RawDocument,
)
from radar_core.connectors.deepwiki import DeepWikiConnector
from radar_core.registry import SourceSpec
from radar_core.storage import StateStore
from radar_core.translation.base import TranslationRequest, TranslationResponse


class _Router:
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        return TranslationResponse(
            translated_text=f"译文：{request.text}",
            provider="fake",
            model="fake-model",
            metadata={"provider": "fake"},
        )


class _CountingConnector:
    adapter_name = "fake"
    incremental_class = "revision-native"

    def __init__(self) -> None:
        self.fetches: list[str] = []
        self.revisions = {f"p{i}": f"sha-{i}-v1" for i in range(5)}

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        return DiscoveryPage(
            items=[
                DiscoveredItem(
                    source_id="source.fake",
                    native_id=native_id,
                    url=f"https://ex/{native_id}",
                    title=f"Title {native_id}",
                    published_at="2026-09-01T00:00:00Z",
                    content_type="text/markdown",
                    remote_revision=revision,
                    metadata={"path": native_id, "connector": "fake"},
                )
                for native_id, revision in self.revisions.items()
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
            content_type=item.content_type or "text/plain",
            published_at=item.published_at,
            metadata=dict(item.metadata),
        )


def _source(*, max_new_items: Any = ..., schedule_extra: dict | None = None) -> SourceSpec:
    schedule: dict[str, Any] = {
        "enabled": True,
        "timezone": "UTC",
        "cron": "0 * * * *",
    }
    if max_new_items is not ...:
        schedule["max_new_items"] = max_new_items
    if schedule_extra:
        schedule.update(schedule_extra)
    return SourceSpec.from_payload(
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.fake",
            "kind": "source",
            "source_type": "fake",
            "adapter": "fake",
            "name": "Fake",
            "locator": "https://ex.test/",
            "schedule": schedule,
            "output_root": "frontend/fake",
            "translation_profile": "prose/v1",
            "enabled": True,
        }
    )


def _context(
    state: StateStore,
    connector: _CountingConnector,
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
        router=_Router(),
        now=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
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


# ---------------------------------------------------------------------------
# C1 — max_new_items unset must not mean 0
# ---------------------------------------------------------------------------


def test_c1_schedule_without_max_new_items_still_selects_changed_items(
    tmp_path: Path,
):
    """Spec §3.5: an unset `max_new_items` must fall back to a positive
    default (50). Only an *explicit* 0 means "no body fetch" (index tasks)."""

    from radar_core.pipeline import run_source

    connector = _CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source(max_new_items=...)  # schedule has no max_new_items key

    # Baseline first.
    run_source(source, _context(state, connector, run_id="r1"))
    state.mark_source_baseline("source.fake", "r1")
    # Simulate prior fetches so all items are at parity.
    for i in range(5):
        _mark_fetched(state, f"p{i}", f"sha-{i}-v1")

    # Now change every item's remote revision; with the default cap (50) all 5
    # must be selected and fetched. If unset meant 0, none would be fetched.
    for i in range(5):
        connector.revisions[f"p{i}"] = f"sha-{i}-v2"

    result = run_source(source, _context(state, connector, run_id="r2"))

    assert result.run_kind == "incremental"
    assert result.selected == 5
    assert result.fetched == 5
    assert result.deferred == 0
    assert sorted(connector.fetches) == [f"p{i}" for i in range(5)]
    state.close()


def test_c1_explicit_zero_still_means_no_body_fetch(tmp_path: Path):
    """Spec §3.5: an explicit `max_new_items: 0` means the task does not fetch
    new bodies (index task). Changed items are deferred, not selected."""

    from radar_core.pipeline import run_source

    connector = _CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source(max_new_items=0)

    run_source(source, _context(state, connector, run_id="r1"))
    state.mark_source_baseline("source.fake", "r1")
    for i in range(5):
        _mark_fetched(state, f"p{i}", f"sha-{i}-v1")
    for i in range(5):
        connector.revisions[f"p{i}"] = f"sha-{i}-v2"

    result = run_source(source, _context(state, connector, run_id="r2"))

    assert result.run_kind == "incremental"
    assert result.selected == 0
    assert result.deferred == 5
    assert result.fetched == 0
    assert connector.fetches == []
    state.close()


# ---------------------------------------------------------------------------
# I1 — --baseline-only on an initialized source must fetch=0
# ---------------------------------------------------------------------------


def test_i1_baseline_only_on_initialized_source_fetches_nothing(tmp_path: Path):
    """Spec §3.2: `baseline_only` discovers + writes ledger, fetch=0,
    translate=0. This must hold even when the source is already initialized,
    so `--baseline-only` can be used to refresh the ledger without fetching."""

    from radar_core.pipeline import run_source

    connector = _CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source(max_new_items=10)

    # First run initializes the baseline.
    first = run_source(source, _context(state, connector, run_id="r1"))
    assert first.run_kind == "baseline_only"
    assert state.baseline_initialized("source.fake") is True

    # Second run explicitly requests baseline_only on the already-initialized
    # source. It must NOT proceed to fetch.
    connector.fetches.clear()
    result = run_source(
        source,
        _context(state, connector, mode="baseline_only", run_id="r2"),
    )

    assert result.run_kind == "baseline_only"
    assert result.status == "success"
    assert result.fetched == 0
    assert result.selected == 0
    assert connector.fetches == []
    state.close()


# ---------------------------------------------------------------------------
# I2 — DeepWiki GitHub blob fallback is dead (git/trees entries have no
# download_url)
# ---------------------------------------------------------------------------


def _github_tree_response_no_download_url(
    repo: str,
    ref: str,
    blobs: list[tuple[str, str]],
    *,
    etag: str = '"tree-v1"',
) -> HttpResponse:
    import json

    tree = [
        {
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": sha,
            # git/trees entries have no download_url field.
            "url": f"https://api.github.com/repos/{repo}/git/blobs/{sha}",
        }
        for path, sha in blobs
    ]
    payload = {"sha": f"tree-{ref}-sha", "tree": tree, "truncated": False}
    return HttpResponse(
        status_code=200,
        headers={
            "Content-Type": "application/vnd.github+json",
            "ETag": etag,
        },
        body=json.dumps(payload).encode("utf-8"),
    )


class _RouteTransport:
    def __init__(self, routes: dict[str, HttpResponse]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(url)
        if url not in self.routes:
            raise KeyError(f"unexpected request for {url}")
        return self.routes[url]


def test_i2_deepwiki_falls_back_to_github_blob_when_tree_entry_has_no_download_url(
    tmp_path: Path,
):
    """git/trees entries do not carry `download_url`. The DeepWiki connector
    must still fall back to a raw GitHub URL synthesized from repo/ref/path
    when the DeepWiki page 404s."""

    repo = "acme/demo"
    ref = "main"
    tree_url = (
        f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    )
    deepwiki_root = "https://deepwiki.com/acme/demo"
    deepwiki_page_url = f"{deepwiki_root}/README"
    synthesized_blob_url = (
        f"https://raw.githubusercontent.com/{repo}/{ref}/README.md"
    )
    transport = _RouteTransport(
        {
            tree_url: _github_tree_response_no_download_url(
                repo, ref, [("README.md", "blob-readme-v1")]
            ),
            deepwiki_page_url: HttpResponse(
                status_code=404,
                headers={"Content-Type": "text/html"},
                body=b"<html>not found</html>",
            ),
            synthesized_blob_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown"},
                body=b"# README body from raw GitHub",
            ),
        }
    )
    connector = DeepWikiConnector(
        {
            "source_id": "source.deepwiki.acme",
            "github": repo,
            "ref": ref,
            "deepwiki_url": deepwiki_root,
            "transport": transport,
        }
    )

    page = connector.discover(Cursor())
    assert len(page.items) == 1
    item = page.items[0]
    # The original git/trees entry had no `download_url`, but the remapped
    # DeepWiki item must carry a synthesized raw GitHub URL for 404 fallback.
    assert item.metadata.get("download_url") == synthesized_blob_url

    document = connector.fetch(item)

    assert "README body from raw GitHub" in document.body
    assert document.content_type == "text/markdown"
    assert synthesized_blob_url in transport.calls


# ---------------------------------------------------------------------------
# I3 — 304 ledger reconstruct is lossy
# ---------------------------------------------------------------------------


def test_i3_reconstructed_lagged_item_keeps_title_and_path(tmp_path: Path):
    """Spec §3.1: after a 304 the discovery page may be empty, but lagged
    items must still be selected from the ledger and reconstructed with
    enough metadata (title, path/url) to fetch."""

    from radar_core.pipeline import run_source

    class _NotModifiedConnector(_CountingConnector):
        def __init__(self) -> None:
            super().__init__()
            self._call = 0

        def discover(self, cursor: Cursor) -> DiscoveryPage:
            self._call += 1
            if self._call == 1:
                return super().discover(cursor)
            return DiscoveryPage(items=[], cursor=cursor, metadata={"not_modified": True})

    connector = _NotModifiedConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source(max_new_items=10)

    # Baseline.
    run_source(source, _context(state, connector, run_id="r1"))
    state.mark_source_baseline("source.fake", "r1")

    # Bring every item to parity (fetched == remote) first, then make only p1
    # lagged so the reconstruct path is the single selected item.
    for i in range(5):
        _mark_fetched(state, f"p{i}", f"sha-{i}-v1")
    state.upsert_source_item(
        {
            "source_id": "source.fake",
            "item_id": "p1",
            "canonical_url": "https://ex/p1",
            "remote_revision": "sha-1-v1",
            "fetched_revision": "sha-1-old",
            "status": "fetched",
        }
    )

    # Second run: discover 304 -> empty page. p1 must be reconstructed from
    # the ledger with title and path, then fetched.
    connector.fetches.clear()
    result = run_source(source, _context(state, connector, run_id="r2"))

    assert result.run_kind == "incremental"
    assert result.selected == 1
    assert result.fetched == 1
    assert connector.fetches == ["p1"]

    # The fetched document must carry the reconstructed title and metadata.
    rows = state.list_source_items("source.fake")
    p1 = next(row for row in rows if row["item_id"] == "p1")
    # Title persisted in the ledger must survive the reconstruct path.
    assert p1.get("title") == "Title p1"
    assert p1.get("status") == "fetched"
    state.close()


# ---------------------------------------------------------------------------
# I5 — discover upsert must not pass status: "seen"
# ---------------------------------------------------------------------------


def test_i5_rediscover_does_not_reset_fetched_status_to_seen(tmp_path: Path):
    """Spec §3.1/§4: discover upsert must only update remote_* / last_seen_at.
    It must not overwrite `fetched`/`deferred`/`blocked` with `seen`."""

    from radar_core.pipeline import run_source

    connector = _CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source(max_new_items=10)

    # Baseline.
    run_source(source, _context(state, connector, run_id="r1"))
    state.mark_source_baseline("source.fake", "r1")

    # Mark p0 as fetched at its current revision.
    _mark_fetched(state, "p0", "sha-0-v1")

    # Rediscover with no revision changes. The discover upsert must preserve
    # the `fetched` status instead of resetting it to `seen`.
    result = run_source(source, _context(state, connector, run_id="r2"))

    assert result.run_kind == "incremental"
    rows = state.list_source_items("source.fake")
    p0 = next(row for row in rows if row["item_id"] == "p0")
    assert p0.get("status") == "fetched"
    assert p0.get("fetched_revision") == "sha-0-v1"
    state.close()


def test_i5_rediscover_does_not_reset_deferred_status_to_seen(tmp_path: Path):
    """A deferred item that is rediscovered (still lagged) must stay deferred
    across discover upsert, not be reset to `seen`."""

    from radar_core.pipeline import run_source

    connector = _CountingConnector()
    state = StateStore.open(tmp_path / "state.sqlite3")
    # Cap at 1 so the other changed items become deferred.
    source = _source(max_new_items=1)

    run_source(source, _context(state, connector, run_id="r1"))
    state.mark_source_baseline("source.fake", "r1")
    for i in range(5):
        _mark_fetched(state, f"p{i}", f"sha-{i}-v1")

    # Change all 5 revisions; cap=1 -> 1 fetched, 4 deferred.
    for i in range(5):
        connector.revisions[f"p{i}"] = f"sha-{i}-v2"
    run_source(source, _context(state, connector, run_id="r2"))

    rows = state.list_source_items("source.fake")
    deferred = [
        row for row in rows if row["item_id"] in {f"p{i}" for i in range(1, 5)}
    ]
    assert deferred, "expected at least one deferred item"
    assert all(row.get("status") == "deferred" for row in deferred)

    # Rediscover with no further changes. Previously-deferred items must not
    # be reset to `seen`; they either stay deferred or get fetched this round.
    run_source(source, _context(state, connector, run_id="r3"))
    rows = state.list_source_items("source.fake")
    deferred_after = [
        row for row in rows if row["item_id"] in {f"p{i}" for i in range(1, 5)}
    ]
    assert deferred_after
    assert all(
        row.get("status") in {"deferred", "fetched"} for row in deferred_after
    ), "deferred items must not be reset to `seen` on rediscover"
    state.close()
