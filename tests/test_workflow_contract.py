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


def test_textbook_workflow_is_removed():
    assert not Path(".github/workflows/update-textbooks.yml").exists()


def test_publish_follows_finished_fetches_and_is_the_only_gitee_push():
    publish = Path(".github/workflows/update-publish.yml").read_text(encoding="utf-8")
    news = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    radar = Path(".github/workflows/update-radar.yml").read_text(encoding="utf-8")
    assert "workflow_call:" in publish
    assert "47 * * * *" not in publish
    assert "cancel-in-progress: false" in publish
    assert "GITEE_REPO" in publish
    assert "GITEE_REPO" not in news
    assert "GITEE_REPO" not in radar
    assert "owner: news" in news
    assert "owner: radar" in radar
    assert "WAY_SITE_SYNC_URL" in publish
    assert "secrets.WAY_SITE_SYNC_TOKEN" in publish
    assert "Way did not pull the new snapshot" in publish
