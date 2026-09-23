"""TDD for spec §8 scenarios 1–7: strict incremental sync via fake HTTP transport.

These tests exercise the real RSSArticleConnector through the pipeline with a
deterministic transport that records every GET URL, so we can assert on the
exact HTTP traffic each scenario produces. No live network is used.

Scenario 8 (single operation timeout) is covered by tests/test_radar_timeout.py.
Scenario 9 (request_id does not imply force) is covered by
tests/test_workflow_force_contract.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar_core.connectors.base import HttpResponse
from radar_core.connectors.rss_article import RSSArticleConnector
from radar_core.registry import SourceSpec
from radar_core.storage import StateStore
from radar_core.translation.base import TranslationRequest, TranslationResponse


FEED_URL = "https://feed.example.test/feed"
ARTICLE_URLS = [f"https://feed.example.test/p{i}" for i in range(5)]


class RecordingTransport:
    """Deterministic transport: returns canned responses and logs every GET URL."""

    def __init__(self, routes: dict[str, HttpResponse] | None = None) -> None:
        self.routes: dict[str, HttpResponse] = dict(routes or {})
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

    def set(self, url: str, response: HttpResponse) -> None:
        self.routes[url] = response

    @property
    def get_urls(self) -> list[str]:
        return list(self.calls)

    def reset_calls(self) -> None:
        self.calls.clear()


class RecordingRouter:
    def __init__(self) -> None:
        self.requests: list[TranslationRequest] = []

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.requests.append(request)
        return TranslationResponse(
            translated_text=f"译文：{request.text}",
            provider="fake",
            model="fake-model",
            metadata={"provider": "fake"},
        )


def _rss_xml(items: list[dict[str, str]]) -> bytes:
    entries = "".join(
        "<item>"
        f"<title>{item['title']}</title>"
        f"<link>{item['link']}</link>"
        f"<guid>{item['guid']}</guid>"
        f"<pubDate>{item['published']}</pubDate>"
        "</item>"
        for item in items
    )
    body = (
        '<?xml version="1.0"?>'
        '<rss version="2.0"><channel>'
        "<title>Feed</title>"
        f"<link>{FEED_URL}</link>"
        f"{entries}"
        "</channel></rss>"
    )
    return body.encode("utf-8")


def _feed_response(
    items: list[dict[str, str]],
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> HttpResponse:
    headers: dict[str, str] = {"Content-Type": "application/rss+xml"}
    if etag:
        headers["ETag"] = etag
    if last_modified:
        headers["Last-Modified"] = last_modified
    return HttpResponse(status_code=200, headers=headers, body=_rss_xml(items))


def _feed_304(etag: str | None = None) -> HttpResponse:
    headers: dict[str, str] = {}
    if etag:
        headers["ETag"] = etag
    return HttpResponse(status_code=304, headers=headers, body=b"")


def _article_response(body: str, *, content_type: str = "text/html") -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"Content-Type": content_type},
        body=body.encode("utf-8"),
    )


def _items(n: int, *, published: str = "Mon, 01 Sep 2026 00:00:00 GMT") -> list[dict[str, str]]:
    return [
        {
            "title": f"p{i}",
            "link": ARTICLE_URLS[i],
            "guid": f"p{i}",
            "published": published,
        }
        for i in range(n)
    ]


def _atom_xml(
    items: list[dict[str, str]],
    *,
    updated: str = "2026-09-01T00:00:00Z",
) -> bytes:
    """Atom feed where each entry has a stable <published> and a mutable <updated>."""
    entries = "".join(
        "<entry>"
        f"<title>{item['title']}</title>"
        f"<link href=\"{item['link']}\"/>"
        f"<id>{item['guid']}</id>"
        f"<published>{item['published']}</published>"
        f"<updated>{item.get('updated', updated)}</updated>"
        "</entry>"
        for item in items
    )
    body = (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<title>Feed</title>"
        f"<updated>{updated}</updated>"
        f"{entries}"
        "</feed>"
    )
    return body.encode("utf-8")


def _atom_response(
    items: list[dict[str, str]],
    *,
    etag: str | None = None,
    updated: str = "2026-09-01T00:00:00Z",
) -> HttpResponse:
    headers: dict[str, str] = {"Content-Type": "application/atom+xml"}
    if etag:
        headers["ETag"] = etag
    return HttpResponse(status_code=200, headers=headers, body=_atom_xml(items, updated=updated))


def _source(max_new_items: int = 10) -> SourceSpec:
    return SourceSpec.from_payload(
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.rss",
            "kind": "source",
            "source_type": "rss",
            "adapter": "rss_article",
            "name": "RSS",
            "locator": FEED_URL,
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
                "max_new_items": max_new_items,
            },
            "output_root": "frontend/rss",
            "translation_profile": "prose/v1",
            "enabled": True,
        }
    )


def _context(
    state: StateStore,
    transport: RecordingTransport,
    router: RecordingRouter,
    *,
    mode: str = "incremental",
    run_id: str = "run-1",
) -> Any:
    from radar_core.pipeline import RunContext

    connector = RSSArticleConnector(
        {
            "source_id": "source.rss",
            "feed_url": FEED_URL,
            "transport": transport,
            "adapter_name": "rss_article",
        }
    )
    return RunContext(
        state=state,
        run_id=run_id,
        target_locales=("zh-CN",),
        connector_factory=lambda _source: connector,
        router=router,
        now=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        mode=mode,
    )


def _mark_fetched(state: StateStore, item_id: str, revision: str, url: str) -> None:
    state.upsert_source_item(
        {
            "source_id": "source.rss",
            "item_id": item_id,
            "canonical_url": url,
            "remote_revision": revision,
            "fetched_revision": revision,
            "status": "fetched",
        }
    )


# Scenario 1: first cron run is baseline_only; only the manifest (feed) is GET'd.
def test_scenario_1_first_cron_is_baseline_only_with_no_page_gets(tmp_path: Path):
    from radar_core.pipeline import run_source

    transport = RecordingTransport()
    transport.set(FEED_URL, _feed_response(_items(2)))
    for url in ARTICLE_URLS[:2]:
        transport.set(url, _article_response("body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")

    result = run_source(_source(), _context(state, transport, router, run_id="r1"))

    assert result.run_kind == "baseline_only"
    assert result.status == "success"
    assert result.fetched == 0
    assert transport.get_urls == [FEED_URL]
    assert router.requests == []
    state.close()


# Scenario 2: no change (304) — page GET=0, translation=0.
def test_scenario_2_no_change_304_fetches_no_pages_and_translates_nothing(tmp_path: Path):
    from radar_core.pipeline import run_source

    transport = RecordingTransport()
    transport.set(FEED_URL, _feed_response(_items(2), etag='"feed-v1"'))
    for url in ARTICLE_URLS[:2]:
        transport.set(url, _article_response("body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source()

    first = run_source(source, _context(state, transport, router, run_id="r1"))
    assert first.run_kind == "baseline_only"

    _mark_fetched(state, "p0", "Mon, 01 Sep 2026 00:00:00 GMT", ARTICLE_URLS[0])
    _mark_fetched(state, "p1", "Mon, 01 Sep 2026 00:00:00 GMT", ARTICLE_URLS[1])

    transport.reset_calls()
    transport.set(FEED_URL, _feed_304(etag='"feed-v1"'))
    router.requests.clear()

    second = run_source(source, _context(state, transport, router, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.status == "success"
    assert second.fetched == 0
    assert second.selected == 0
    assert transport.get_urls == [FEED_URL]
    assert router.requests == []
    state.close()


# Scenario 3: manifest change — only changed item pages are GET'd.
def test_scenario_3_manifest_change_fetches_only_changed_item_pages(tmp_path: Path):
    from radar_core.pipeline import run_source

    transport = RecordingTransport()
    transport.set(FEED_URL, _feed_response(_items(2), etag='"feed-v1"'))
    for url in ARTICLE_URLS[:2]:
        transport.set(url, _article_response("body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source()

    run_source(source, _context(state, transport, router, run_id="r1"))
    _mark_fetched(state, "p0", "Mon, 01 Sep 2026 00:00:00 GMT", ARTICLE_URLS[0])
    _mark_fetched(state, "p1", "Mon, 01 Sep 2026 00:00:00 GMT", ARTICLE_URLS[1])

    changed_items = [
        {
            "title": "p0",
            "link": ARTICLE_URLS[0],
            "guid": "p0",
            "published": "Mon, 01 Sep 2026 00:00:00 GMT",
        },
        {
            "title": "p1",
            "link": ARTICLE_URLS[1],
            "guid": "p1",
            "published": "Tue, 02 Sep 2026 00:00:00 GMT",
        },
    ]
    transport.reset_calls()
    transport.set(FEED_URL, _feed_response(changed_items, etag='"feed-v2"'))
    router.requests.clear()

    second = run_source(source, _context(state, transport, router, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.selected == 1
    assert second.fetched == 1
    assert second.skipped_not_modified == 1
    assert transport.get_urls == [FEED_URL, ARTICLE_URLS[1]]
    state.close()


# Scenario 4: content change — new revision + translation.
def test_scenario_4_content_change_creates_new_revision_and_translates(tmp_path: Path):
    from radar_core.pipeline import run_source

    transport = RecordingTransport()
    transport.set(FEED_URL, _feed_response(_items(1), etag='"feed-v1"'))
    transport.set(ARTICLE_URLS[0], _article_response("original body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source()

    run_source(source, _context(state, transport, router, run_id="r1"))
    _mark_fetched(state, "p0", "Mon, 01 Sep 2026 00:00:00 GMT", ARTICLE_URLS[0])

    changed_items = [
        {
            "title": "p0",
            "link": ARTICLE_URLS[0],
            "guid": "p0",
            "published": "Tue, 02 Sep 2026 00:00:00 GMT",
        }
    ]
    transport.reset_calls()
    transport.set(FEED_URL, _feed_response(changed_items, etag='"feed-v2"'))
    transport.set(ARTICLE_URLS[0], _article_response("changed body"))
    router.requests.clear()

    second = run_source(source, _context(state, transport, router, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.fetched == 1
    assert second.revisions_new == 1
    assert second.translated == 1
    assert len(router.requests) == 1
    assert transport.get_urls == [FEED_URL, ARTICLE_URLS[0]]
    state.close()


# Scenario 5: translation rerun reuses the translation key (no new translation).
def test_scenario_5_translation_rerun_reuses_translation_key(tmp_path: Path):
    from radar_core.pipeline import run_source

    # Use Atom so each entry has a stable <published> (part of source_hash) and
    # a mutable <updated> (drives remote_revision). Changing only <updated>
    # selects the item for fetch without altering the normalized source_hash,
    # so the revision and translation key are reused.
    atom_items = [
        {
            "title": "p0",
            "link": ARTICLE_URLS[0],
            "guid": "p0",
            "published": "2026-09-01T00:00:00Z",
            "updated": "2026-09-01T00:00:00Z",
        }
    ]
    transport = RecordingTransport()
    transport.set(FEED_URL, _atom_response(atom_items, etag='"feed-v1"', updated="2026-09-01T00:00:00Z"))
    transport.set(ARTICLE_URLS[0], _article_response("same body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source()

    # Establish the baseline, then fetch so a revision and translation exist.
    baseline = run_source(
        source,
        _context(state, transport, router, mode="baseline_only", run_id="r0"),
    )
    assert baseline.run_kind == "baseline_only"
    assert baseline.fetched == 0
    first = run_source(
        source,
        _context(state, transport, router, mode="incremental", run_id="r1"),
    )
    assert first.run_kind == "incremental"
    assert first.fetched == 1
    assert first.translated == 1
    assert len(router.requests) == 1

    # Second run (incremental): only <updated> changes, so remote_revision
    # changes (item is selected + fetched) but <published> and the article
    # body are identical, so the normalized source_hash is unchanged and the
    # translation key is reused — no new translation request.
    changed_atom = [
        {
            "title": "p0",
            "link": ARTICLE_URLS[0],
            "guid": "p0",
            "published": "2026-09-01T00:00:00Z",
            "updated": "2026-09-02T00:00:00Z",
        }
    ]
    transport.reset_calls()
    transport.set(FEED_URL, _atom_response(changed_atom, etag='"feed-v2"', updated="2026-09-02T00:00:00Z"))
    transport.set(ARTICLE_URLS[0], _article_response("same body"))
    router.requests.clear()

    second = run_source(source, _context(state, transport, router, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.fetched == 1
    assert second.revisions_reused == 1
    assert second.revisions_new == 0
    assert second.translation_reused == 1
    assert second.translated == 0
    assert router.requests == []
    state.close()


# Scenario 6: no stable identity — unsupported_incremental, page GET=0.
def test_scenario_6_no_stable_identity_is_unsupported_with_no_page_gets(tmp_path: Path):
    from radar_core.pipeline import run_source

    # Feed entries with links but no guid, no updated, no published, and the
    # feed response carries no ETag/Last-Modified: the connector cannot supply
    # a stable remote revision, so incremental_class becomes "unsupported".
    bare_xml = (
        '<?xml version="1.0"?>'
        '<rss version="2.0"><channel>'
        f"<title>Feed</title><link>{FEED_URL}</link>"
        "<item>"
        "<title>p0</title>"
        f"<link>{ARTICLE_URLS[0]}</link>"
        "</item>"
        "</channel></rss>"
    ).encode("utf-8")
    transport = RecordingTransport()
    transport.set(
        FEED_URL,
        HttpResponse(
            status_code=200,
            headers={"Content-Type": "application/rss+xml"},
            body=bare_xml,
        ),
    )
    transport.set(ARTICLE_URLS[0], _article_response("body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source()

    first = run_source(source, _context(state, transport, router, run_id="r1"))
    assert first.run_kind == "baseline_only"
    assert first.fetched == 0

    transport.reset_calls()
    router.requests.clear()

    second = run_source(source, _context(state, transport, router, run_id="r2"))

    assert second.status == "unsupported_incremental"
    assert second.fetched == 0
    assert transport.get_urls == [FEED_URL]
    assert router.requests == []
    state.close()


# Scenario 7: max_new_items=2 with 5 changes — only 2 fetched, rest deferred,
# and the next round continues fetching the deferred items.
def test_scenario_7_max_new_items_caps_and_continues_next_round(tmp_path: Path):
    from radar_core.pipeline import run_source

    five = _items(5)
    transport = RecordingTransport()
    transport.set(FEED_URL, _feed_response(five, etag='"feed-v1"'))
    for url in ARTICLE_URLS[:5]:
        transport.set(url, _article_response("body"))
    router = RecordingRouter()
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _source(max_new_items=2)

    run_source(source, _context(state, transport, router, run_id="r1"))
    for i in range(5):
        _mark_fetched(state, f"p{i}", "Mon, 01 Sep 2026 00:00:00 GMT", ARTICLE_URLS[i])

    # Second run: all 5 items have new remote revisions; cap is 2.
    changed_five = _items(5, published="Tue, 02 Sep 2026 00:00:00 GMT")
    transport.reset_calls()
    transport.set(FEED_URL, _feed_response(changed_five, etag='"feed-v2"'))
    router.requests.clear()

    second = run_source(source, _context(state, transport, router, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.selected == 2
    assert second.deferred == 3
    assert second.fetched == 2
    assert transport.get_urls == [FEED_URL, ARTICLE_URLS[0], ARTICLE_URLS[1]]

    # Third run: the 3 deferred items still trail their remote revisions, so
    # the next round continues fetching them (cap still 2 -> 2 more fetched).
    transport.reset_calls()
    transport.set(FEED_URL, _feed_304(etag='"feed-v2"'))
    router.requests.clear()

    third = run_source(source, _context(state, transport, router, run_id="r3"))

    assert third.run_kind == "incremental"
    assert third.selected == 2
    assert third.deferred == 1
    assert third.fetched == 2
    assert transport.get_urls == [FEED_URL, ARTICLE_URLS[2], ARTICLE_URLS[3]]
    state.close()
