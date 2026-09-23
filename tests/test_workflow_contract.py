from __future__ import annotations

from pathlib import Path


def test_update_news_workflow_has_no_bootstrap_input():
    text = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "bootstrap:" not in text
    assert "write_workflow_status.py" in text
    assert "if: always()" in text


def test_news_workflow_does_not_run_radar():
    text = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "radar_run.py" not in text
    radar = Path(".github/workflows/update-radar.yml").read_text(encoding="utf-8")
    assert "strategy:" in radar
    assert "cancel-in-progress: false" in radar
    assert radar.count("- docs-0") == 7
    assert radar.count("- knowledge-0") == 5
    assert radar.count("- wiki-0") == 3
    assert "GITEE_REPO" not in radar


def test_textbook_workflow_does_not_commit_mirrors():
    text = Path(".github/workflows/update-textbooks.yml").read_text(encoding="utf-8")
    assert "TEXTBOOK_SYNC_URL" in text
    assert "data/textbooks" not in text
    assert "bootstrap" not in text
    assert "17 0 * * *" in text


def test_publish_workflow_is_hourly_and_is_the_only_gitee_push():
    publish = Path(".github/workflows/update-publish.yml").read_text(encoding="utf-8")
    news = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    radar = Path(".github/workflows/update-radar.yml").read_text(encoding="utf-8")
    textbooks = Path(".github/workflows/update-textbooks.yml").read_text(encoding="utf-8")
    assert "47 * * * *" in publish
    assert "cancel-in-progress: false" in publish
    assert "GITEE_REPO" in publish
    assert "GITEE_REPO" not in news
    assert "GITEE_REPO" not in radar
    assert "GITEE_REPO" not in textbooks
