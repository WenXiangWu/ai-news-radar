from __future__ import annotations

from pathlib import Path

from radar_core.dedupe import Match, accept_revision, duplicate_candidates, exact_match
from radar_core.normalize import normalize_document, normalized_hash
from radar_core.storage import StateStore


def _raw(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": "source.docs",
        "native_id": "doc-1",
        "canonical_url": "https://docs.example.test/guide?v=20260920&utm_source=feed",
        "title": "Guide",
        "body": (
            "# Guide\n\n"
            "Read [the docs](https://cdn.example.test/guide.js?build=123&utm_medium=rss).\n"
        ),
        "metadata": {
            "fetched_at": "2026-09-20T10:00:00+08:00",
            "etag": "etag-1",
            "author": "team",
        },
    }
    payload.update(overrides)
    return payload


def test_normalization_ignores_fetch_time_and_dynamic_urls():
    first = normalize_document(_raw(), "prose/v1")
    second = normalize_document(
        _raw(
            canonical_url="https://docs.example.test/guide?v=20260921&utm_source=other",
            body=(
                "# Guide\n\n"
                "Read [the docs](https://cdn.example.test/guide.js?build=999&utm_medium=web).\n"
            ),
            metadata={
                "fetched_at": "2026-09-21T10:00:00+08:00",
                "etag": "etag-2",
                "author": "team",
            },
        ),
        "prose/v1",
    )

    assert first.content_id == second.content_id
    assert first.source_hash == second.source_hash
    assert normalized_hash(first) == normalized_hash(second)
    assert "fetched_at" not in first.metadata
    assert "etag" not in first.metadata
    assert "build=" not in first.body
    assert "utm_" not in first.body


def test_changed_paragraph_creates_a_new_revision_hash():
    first = normalize_document(_raw(), "prose/v1")
    changed = normalize_document(
        _raw(body="# Guide\n\nA changed paragraph.\n"),
        "prose/v1",
    )

    assert first.content_id == changed.content_id
    assert first.source_hash != changed.source_hash
    assert normalized_hash(first) != normalized_hash(changed)


def test_native_id_wins_over_url_for_content_identity():
    first = normalize_document(_raw(), "prose/v1")
    moved = normalize_document(
        _raw(
            canonical_url="https://new.example.test/renamed-guide",
            native_id="doc-1",
        ),
        "prose/v1",
    )

    assert first.content_id == moved.content_id


def test_same_body_different_identity_is_a_reusable_content_candidate(
    tmp_path: Path,
):
    store = StateStore.open(tmp_path / "state.sqlite3")
    original = normalize_document(_raw(), "prose/v1")
    accept_revision(original, store)

    moved = normalize_document(
        _raw(
            native_id="doc-2",
            canonical_url="https://docs.example.test/other-guide",
        ),
        "prose/v1",
    )

    assert exact_match(moved, store) is None
    candidates = duplicate_candidates(moved, store)
    assert candidates == [
        Match(
            content_id=original.content_id,
            kind="possible_duplicate",
            reason="same_normalized_body",
        )
    ]
    store.close()


def test_accept_revision_is_idempotent_for_same_normalized_content(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    document = normalize_document(_raw(), "prose/v1")

    first = accept_revision(document, store)
    second = accept_revision(document, store)

    assert first.revision_id == second.revision_id
    assert first.is_new is True
    assert second.is_new is False
    assert store.count_rows("revisions") == 1
    store.close()
