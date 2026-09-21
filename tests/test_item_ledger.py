from __future__ import annotations

from pathlib import Path

from radar_core.ledger import select_items
from radar_core.storage import StateStore


def test_select_items_skips_unchanged_and_defers_over_cap():
    rows = [
        {"item_id": "a", "remote_revision": "1", "fetched_revision": "1"},
        {"item_id": "b", "remote_revision": "2", "fetched_revision": "1"},
        {"item_id": "c", "remote_revision": "3", "fetched_revision": None},
        {"item_id": "d", "remote_revision": "4", "fetched_revision": None},
    ]
    selected, deferred = select_items(rows, max_new_items=2)
    assert [row["item_id"] for row in selected] == ["b", "c"]
    assert [row["item_id"] for row in deferred] == ["d"]


def test_select_items_max_new_items_nonpositive_selects_nothing():
    rows = [
        {"item_id": "b", "remote_revision": "2", "fetched_revision": "1"},
        {"item_id": "c", "remote_revision": "3", "fetched_revision": None},
    ]
    selected, deferred = select_items(rows, max_new_items=0)
    assert selected == []
    assert [row["item_id"] for row in deferred] == ["b", "c"]


def test_baseline_initialized_is_sticky(tmp_path: Path):
    state = StateStore.open(tmp_path / "state.sqlite3")
    assert state.baseline_initialized("source.demo") is False
    state.mark_source_baseline("source.demo", "run-1")
    assert state.baseline_initialized("source.demo") is True
    state.close()
    reopened = StateStore.open(tmp_path / "state.sqlite3")
    assert reopened.baseline_initialized("source.demo") is True
    reopened.close()
