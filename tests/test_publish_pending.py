from __future__ import annotations

import json
from pathlib import Path

from scripts.publish_pending import publish_pending


def test_publish_pending_keeps_unready_files(tmp_path: Path):
    pending = tmp_path / "pending"
    pending.mkdir()
    ready = pending / "docs-a.json"
    ready.write_text('{"ready": true, "source_ids": ["docs.ollama"]}', encoding="utf-8")
    waiting = pending / "docs-b.json"
    waiting.write_text('{"ready": false, "source_ids": ["docs.a2a"]}', encoding="utf-8")
    selected = publish_pending(pending, now="2026-09-23T03:47:00Z")
    assert [path.name for path in selected] == ["docs-a.json"]
    published = json.loads(ready.read_text())
    assert published["ready"] is False
    assert published["published_at"] == "2026-09-23T03:47:00Z"
    assert json.loads(waiting.read_text())["ready"] is False


def test_publish_pending_leaves_other_workflows_ready(tmp_path: Path):
    pending = tmp_path / "pending"
    pending.mkdir()
    news = pending / "news.json"
    news.write_text(
        '{"ready": true, "workflow_id": "update-news", "source_ids": ["official_ai"]}',
        encoding="utf-8",
    )
    radar = pending / "docs-01.json"
    radar.write_text(
        '{"ready": true, "workflow_id": "update-radar", "source_ids": ["docs.ollama"]}',
        encoding="utf-8",
    )
    selected = publish_pending(pending, now="2026-09-23T06:20:00Z", workflow_id="update-news")
    assert [path.name for path in selected] == ["news.json"]
    assert json.loads(radar.read_text(encoding="utf-8"))["ready"] is True
