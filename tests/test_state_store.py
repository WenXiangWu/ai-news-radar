from __future__ import annotations

from pathlib import Path

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
    translation = {
        "translation_key": "translation_demo",
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
    assert store.get_translation("translation_demo")["provider"] == "google"
    store.close()


def test_state_store_advances_cursor_only_when_explicitly_called(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    store = StateStore.open(path)

    assert store.get_cursor("source.demo") is None
    store.record_run("run-1", {"status": "failed"})
    assert store.get_cursor("source.demo") is None

    store.advance_cursor("source.demo", {"etag": "abc", "page": 2}, "run-1")

    assert store.get_cursor("source.demo") == {
        "source_id": "source.demo",
        "cursor": {"etag": "abc", "page": 2},
        "run_id": "run-1",
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
