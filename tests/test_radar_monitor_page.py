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
    assert 'radarDataUrl("radar-monitor.json")' in js
    assert 'radarDataUrl("radar-update-report.json")' in js
    assert 'radarDataUrl("radar-reports/index.json")' in js
    assert 'new URL("../data/"' in js
    assert 'var SNAPSHOT_URL = "/data/radar-monitor.json"' not in js
    assert "github.com/WenXiangWu/ai-news-radar/actions/workflows/update-news.yml" in (
        html + js
    )
    assert "/api/admin/radar/" not in (html + js)
    assert "renderUnavailableMonitor" in js
    assert "不能把缺失数据显示为 0" in js


def test_monitor_page_has_workflow_and_coverage_sections():
    html = (MONITOR / "index.html").read_text(encoding="utf-8")
    js = (MONITOR / "monitor.js").read_text(encoding="utf-8")
    assert "workflowCards" in html
    assert "coverageModules" in html
    assert "renderWorkflows" in js
    assert "renderCoverage" in js
    assert "不适用" in js
    assert "bootstrap" not in html
    assert "全量更新" not in html
    assert "全量更新" not in js


def test_monitor_page_splits_content_into_tabs():
    html = (MONITOR / "index.html").read_text(encoding="utf-8")
    js = (MONITOR / "monitor.js").read_text(encoding="utf-8")
    for marker in ("今日执行", "注册信息", "Way 对照", "历史报告", "翻译提供方", "未登记"):
        assert marker in html
    assert 'data-tab="today"' in html
    assert 'data-tab="config"' in html
    assert "showTab" in js
    assert "location.hash" in js
    assert "未上报" in js


def test_monitor_page_assets_are_present():
    assert (MONITOR / "index.html").is_file()
    assert (MONITOR / "monitor.css").is_file()
    assert (MONITOR / "monitor.js").is_file()
