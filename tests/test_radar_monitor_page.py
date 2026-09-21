from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
MONITOR = ROOT / "monitor"


def test_radar_monitor_is_a_standalone_page_with_actions_entrypoint():
    html = (MONITOR / "index.html").read_text(encoding="utf-8")
    js = (MONITOR / "monitor.js").read_text(encoding="utf-8")

    for marker in (
        "数据源监控",
        "每日更新",
        "翻译提供方",
        "模块总览",
        "moduleTable",
        "sourceTable",
        "reportList",
    ):
        assert marker in html or marker in js
    assert "/data/radar-monitor.json" in js
    assert "/data/radar-update-report.json" in js
    assert "/data/radar-reports/index.json" in js
    assert "github.com/WenXiangWu/ai-news-radar/actions/workflows/update-news.yml" in (
        html + js
    )
    assert "/api/admin/radar/" not in (html + js)


def test_monitor_page_assets_are_present():
    assert (MONITOR / "index.html").is_file()
    assert (MONITOR / "monitor.css").is_file()
    assert (MONITOR / "monitor.js").is_file()
