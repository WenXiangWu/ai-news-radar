from __future__ import annotations

from pathlib import Path


def test_request_id_does_not_imply_force():
    workflow = Path(".github/workflows/update-radar.yml").read_text(encoding="utf-8")
    assert "RADAR_REQUEST_ID" not in workflow
    assert "--force" in workflow
    assert "--bootstrap" not in workflow
    assert "bootstrap:" not in workflow


def test_force_and_bootstrap_are_explicit_inputs():
    news = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "bootstrap:" not in news
    assert "radar_args+=(--bootstrap)" not in news
    assert "radar_run.py" not in news


def test_publish_continues_when_report_exists_even_if_radar_exit_nonzero():
    workflow = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "data/pending-publish/news.json" in workflow
    assert "write_workflow_status.py" in workflow
    assert "if: always()" in workflow
