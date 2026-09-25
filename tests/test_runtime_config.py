from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.runtime_config import deepseek_enabled, load_runtime, should_run

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).parents[1]


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 23, hour, minute, tzinfo=SHANGHAI)


def test_default_file_keeps_the_current_schedule_and_deepseek_off():
    config = json.loads((ROOT / "data/radar-runtime.json").read_text(encoding="utf-8"))
    assert config["schema"] == "radar-runtime/v1"
    assert config["workflows"]["update-news"]["slots_shanghai"] == ["*:17"]
    assert config["workflows"]["update-radar"]["slots_shanghai"] == ["03:17", "09:17", "15:17", "21:17"]
    assert "update-textbooks" not in config["workflows"]
    assert config["workflows"]["update-publish"]["trigger"] == "after_workflow"
    assert config["workflows"]["update-publish"]["slots_shanghai"] == []
    assert config["deepseek"]["enabled"] is False
    assert "key" not in json.dumps(config).lower()
    assert "token" not in json.dumps(config).lower()
    assert "secret" not in json.dumps(config).lower()


def test_scheduled_run_matches_configured_shanghai_slots_with_start_delay():
    config = load_runtime(ROOT / "data/radar-runtime.json")
    assert should_run("update-news", config, event="schedule", now=at(9, 17))
    assert should_run("update-news", config, event="schedule", now=at(9, 40))
    assert should_run("update-radar", config, event="schedule", now=at(9, 17))
    assert should_run("update-radar", config, event="schedule", now=at(14, 10))
    assert should_run("update-radar", config, event="schedule", now=at(15, 16))
    assert not should_run("update-textbooks", config, event="schedule", now=at(8, 17))
    assert should_run("update-publish", config, event="workflow_call", now=at(1, 0))
    assert not should_run("update-publish", config, event="schedule", now=at(13, 47))


def test_off_grid_slot_and_disabled_workflow_do_not_run_on_schedule():
    config = {
        "workflows": {
            "update-news": {"enabled": True, "slots_shanghai": ["09:30"]},
            "update-radar": {"enabled": False, "slots_shanghai": ["09:17"]},
        },
        "deepseek": {"enabled": True},
    }
    assert not should_run("update-news", config, event="schedule", now=at(9, 30))
    assert not should_run("update-radar", config, event="schedule", now=at(9, 17))
    assert should_run("update-radar", config, event="workflow_dispatch", now=at(1, 0))
    assert deepseek_enabled(config) is True


def test_narrowing_news_to_one_hour_skips_the_other_hourly_wakes():
    config = {"workflows": {"update-news": {"enabled": True, "slots_shanghai": ["09:17"]}}}
    assert should_run("update-news", config, event="schedule", now=at(9, 20))
    assert not should_run("update-news", config, event="schedule", now=at(10, 17))


def test_workflows_read_the_runtime_gate_before_doing_work():
    for name in ("update-news.yml", "update-radar.yml", "update-publish.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "scripts/runtime_gate.py" in text
        assert "needs: gate" in text
        assert "needs.gate.outputs.run == 'true'" in text
    news = (ROOT / ".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "needs.gate.outputs.deepseek" in news
    assert "vars.TRANSLATE_USE_DEEPSEEK" not in news
