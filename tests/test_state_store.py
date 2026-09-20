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
