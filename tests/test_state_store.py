from __future__ import annotations

from pathlib import Path

import pytest

from radar_core.ids import translation_key
from radar_core.storage import StateStore


def test_state_store_persists_items_revisions_and_idempotent_translations(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    item = store.get_or_create_item(
        "content_demo",
        {"source_id": "source.demo", "title": "Demo"},
    )
    revision = store.record_revision(
        {
            "revision_id": "revision_demo",
            "content_id": "content_demo",
            "source_hash": "source-hash",
            "normalizer_version": "normalizer/v1",
            "status": "published",
        }
    )
    expected_key = translation_key(
        "content_demo",
        "revision_demo",
        "zh-CN",
        "prose/v1",
        "policy/v1",
    )
    translation = {
        "translation_key": expected_key,
        "content_id": "content_demo",
        "revision_id": "revision_demo",
        "target_locale": "zh-CN",
        "translation_profile": "prose/v1",
        "policy_version": "policy/v1",
        "status": "machine_passed",
        "provider": "deepseek",
    }
    store.upsert_translation(translation)
    store.upsert_translation({**translation, "provider": "google"})

    assert item["content_id"] == "content_demo"
    assert revision["revision_id"] == "revision_demo"
    assert store.get_translation(expected_key)["provider"] == "google"
    store.close()


def test_state_store_advances_cursor_only_for_successful_runs(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    store = StateStore.open(path)

    assert store.get_cursor("source.demo") is None
    store.record_run("run-1", {"status": "failed"})
    assert store.get_cursor("source.demo") is None

    with pytest.raises(ValueError, match="successful run"):
        store.advance_cursor("source.demo", {"etag": "abc", "page": 2}, "run-1")

    assert store.get_cursor("source.demo") is None

    store.record_run("run-2", {"status": "success"})
    store.advance_cursor("source.demo", {"etag": "abc", "page": 2}, "run-2")

    assert store.get_cursor("source.demo") == {
        "source_id": "source.demo",
        "cursor": {"etag": "abc", "page": 2},
        "run_id": "run-2",
    }
    store.close()


def test_state_store_enforces_unique_revision_and_translation_keys(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    payload = {
        "revision_id": "revision_same",
        "content_id": "content_demo",
        "source_hash": "same",
        "normalizer_version": "normalizer/v1",
        "status": "new",
    }

    first = store.record_revision(payload)
    second = store.record_revision({**payload, "status": "published"})

    assert first["revision_id"] == second["revision_id"]
    assert store.count_rows("revisions") == 1
    store.upsert_translation(
        {
            "translation_key": "translation_same",
            "content_id": "content_demo",
            "revision_id": "revision_same",
            "target_locale": "zh-CN",
            "translation_profile": "prose/v1",
            "policy_version": "policy/v1",
            "status": "machine_passed",
        }
    )
    assert store.count_rows("translations") == 1
    store.close()


def test_state_store_rejects_inconsistent_revision_identity(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    payload = {
        "revision_id": "revision_same",
        "content_id": "content_demo",
        "source_hash": "same",
        "normalizer_version": "normalizer/v1",
        "status": "new",
    }
    store.record_revision(payload)

    with pytest.raises(ValueError, match="revision identity"):
        store.record_revision({**payload, "content_id": "content_other"})

    assert store.record_revision(payload)["content_id"] == "content_demo"
    store.close()


def test_state_store_derives_mismatched_translation_key(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    payload = {
        "translation_key": "wrong-key",
        "content_id": "content_demo",
        "revision_id": "revision_demo",
        "target_locale": "zh-CN",
        "translation_profile": "prose/v1",
        "policy_version": "policy/v1",
        "status": "machine_passed",
    }
    expected_key = translation_key(
        "content_demo",
        "revision_demo",
        "zh-CN",
        "prose/v1",
        "policy/v1",
    )

    store.upsert_translation(payload)

    assert store.get_translation("wrong-key") is None
    assert store.get_translation(expected_key)["translation_key"] == expected_key
    store.close()


def test_state_store_same_translation_tuple_with_different_keys_is_idempotent(
    tmp_path: Path,
):
    store = StateStore.open(tmp_path / "state.sqlite3")
    payload = {
        "translation_key": "first-key",
        "content_id": "content_demo",
        "revision_id": "revision_demo",
        "target_locale": "zh-CN",
        "translation_profile": "prose/v1",
        "policy_version": "policy/v1",
        "status": "machine_passed",
    }
    expected_key = translation_key(
        "content_demo",
        "revision_demo",
        "zh-CN",
        "prose/v1",
        "policy/v1",
    )

    store.upsert_translation(payload)
    store.upsert_translation({**payload, "translation_key": "second-key", "provider": "google"})

    assert store.count_rows("translations") == 1
    assert store.get_translation(expected_key)["provider"] == "google"
    store.close()


def test_state_store_persists_source_snapshots_and_items(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    store.record_source_snapshot(
        "source.demo",
        {
            "snapshot_id": "snap-1",
            "fetched_at": "2026-09-21T00:00:00+00:00",
            "etag": 'W/"abc"',
            "item_count": 1,
            "status": "ok",
        },
    )
    store.upsert_source_item(
        {
            "source_id": "source.demo",
            "item_id": "item-1",
            "canonical_url": "https://example.com/item-1",
            "remote_revision": "rev-1",
            "status": "seen",
        }
    )
    store.upsert_source_item(
        {
            "source_id": "source.demo",
            "item_id": "item-1",
            "canonical_url": "https://example.com/item-1",
            "remote_revision": "rev-2",
            "status": "seen",
        }
    )

    items = store.list_source_items("source.demo")
    assert len(items) == 1
    assert items[0]["item_id"] == "item-1"
    assert items[0]["remote_revision"] == "rev-2"
    assert items[0]["fetched_revision"] is None
    assert store.count_rows("source_snapshots") == 1
    assert store.count_rows("source_items") == 1
    store.close()


def test_discover_upsert_preserves_existing_item_status(tmp_path: Path):
    store = StateStore.open(tmp_path / "state.sqlite3")
    for item_id, status in (
        ("fetched-item", "fetched"),
        ("deferred-item", "deferred"),
        ("blocked-item", "blocked"),
    ):
        store.upsert_source_item(
            {
                "source_id": "source.demo",
                "item_id": item_id,
                "canonical_url": f"https://example.com/{item_id}",
                "remote_revision": "rev-1",
                "status": status,
            }
        )

    for item_id in ("fetched-item", "deferred-item", "blocked-item"):
        store.upsert_source_item(
            {
                "source_id": "source.demo",
                "item_id": item_id,
                "canonical_url": f"https://example.com/{item_id}",
                "remote_revision": "rev-2",
            }
        )

    by_id = {row["item_id"]: row for row in store.list_source_items("source.demo")}
    assert by_id["fetched-item"]["status"] == "fetched"
    assert by_id["deferred-item"]["status"] == "deferred"
    assert by_id["blocked-item"]["status"] == "blocked"
    assert by_id["fetched-item"]["remote_revision"] == "rev-2"
    store.close()
