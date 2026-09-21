from __future__ import annotations

import json
from pathlib import Path

from scripts.write_job_report import _registered_jobs
from scripts.write_job_report import main


def test_job_report_accepts_radar_run_operations():
    jobs = _registered_jobs(
        {
            "run_id": "run-test",
            "operations": [
                {
                    "task_id": "task.framework.deepseek-harness.wiki",
                    "status": "success",
                    "discovered": 2,
                    "fetched": 2,
                    "translated": 2,
                }
            ],
        }
    )

    assert jobs[0]["status"] == "ok"
    assert jobs[0]["summary"] == "发现 2 · 抓取 2 · 翻译 2"


def test_job_report_links_the_daily_radar_update_report(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "hub.json").write_text(
        json.dumps({"today": "2026-09-21", "brief_count": 1}),
        encoding="utf-8",
    )
    radar_report = tmp_path / "radar-update-report.json"
    radar_report.write_text(
        json.dumps(
            {
                "status": "success",
                "summary": {"modules": 3, "updated": 2, "translated": 4},
            }
        ),
        encoding="utf-8",
    )
    monitor = tmp_path / "radar-monitor.json"
    monitor.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-21T03:20:00Z",
                "run": {"status": "success"},
                "summary": {
                    "modules": 3,
                    "sources": 4,
                    "healthy_sources": 4,
                    "failed_sources": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "job-report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "write_job_report.py",
            "--data-dir",
            str(data_dir),
            "--radar-update-report",
            str(radar_report),
            "--radar-monitor",
            str(monitor),
            "--out",
            str(output),
        ],
    )

    assert main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["radar_update"]["status"] == "success"
    assert report["radar_update"]["link"].endswith("radar-update-report.json")
    assert report["radar_monitor"]["status"] == "success"
    assert report["radar_monitor"]["summary"] == "模块 3 个 · 数据源 4 · 可达 4 · 失败 0"
    assert report["radar_monitor"]["link"] == "https://news.learnprompt.pro/monitor/"
