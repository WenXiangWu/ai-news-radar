"""Read the committed runtime config that the next scheduled run obeys."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
GRACE = timedelta(minutes=40)
SCHEMA = "radar-runtime/v1"

# Cron wake grid in Shanghai. A configured slot only matches when it sits on this grid.
WAKE_GRID = {
    "update-news": {"minute": 17, "hours": set(range(24))},
    "update-radar": {"minute": 17, "hours": {3, 9, 15, 21}},
    "update-publish": {"minute": 47, "hours": set(range(24))},
}

DEFAULTS = {
    "schema": SCHEMA,
    "workflows": {
        "update-news": {"enabled": True, "slots_shanghai": ["*:17"]},
        "update-radar": {"enabled": True, "slots_shanghai": ["03:17", "09:17", "15:17", "21:17"]},
        "update-publish": {"enabled": True, "trigger": "after_workflow", "slots_shanghai": []},
    },
    "deepseek": {"enabled": False},
}


def load_runtime(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULTS))
    if not isinstance(payload, dict):
        return json.loads(json.dumps(DEFAULTS))
    return payload


def deepseek_enabled(config: dict) -> bool:
    deepseek = config.get("deepseek") if isinstance(config, dict) else None
    if not isinstance(deepseek, dict):
        return False
    return deepseek.get("enabled") is True


def should_run(workflow_id: str, config: dict, *, event: str, now: datetime) -> bool:
    """Scheduled runs must hit a configured Shanghai slot. Manual runs ignore the clock."""
    if event == "workflow_dispatch":
        return True
    workflow = _workflow(config, workflow_id)
    if workflow.get("enabled") is False:
        return False
    if event != "schedule":
        return True
    if workflow_id not in WAKE_GRID:
        return False
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return _matches(workflow_id, _slots(workflow), moment.astimezone(SHANGHAI))


def _workflow(config: dict, workflow_id: str) -> dict:
    workflows = config.get("workflows") if isinstance(config, dict) else None
    workflow = workflows.get(workflow_id) if isinstance(workflows, dict) else None
    if isinstance(workflow, dict):
        return workflow
    fallback = DEFAULTS["workflows"].get(workflow_id) or {}
    return dict(fallback)


def _slots(workflow: dict) -> list[tuple[int | None, int]]:
    parsed: list[tuple[int | None, int]] = []
    for item in workflow.get("slots_shanghai") or []:
        text = str(item).strip()
        if ":" not in text:
            continue
        hour_text, minute_text = text.split(":", 1)
        try:
            minute = int(minute_text)
            hour = None if hour_text == "*" else int(hour_text)
        except ValueError:
            continue
        if minute < 0 or minute > 59 or (hour is not None and (hour < 0 or hour > 23)):
            continue
        parsed.append((hour, minute))
    return parsed


def _matches(workflow_id: str, slots: list[tuple[int | None, int]], now: datetime) -> bool:
    grid = WAKE_GRID[workflow_id]
    for hour, minute in slots:
        if minute != grid["minute"]:
            continue
        hours = grid["hours"] if hour is None else {hour}
        if hour is not None and hour not in grid["hours"]:
            continue
        for slot_hour in hours:
            candidate = now.replace(hour=slot_hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                candidate -= timedelta(days=1)
            delta = now - candidate
            if timedelta(0) <= delta <= GRACE:
                return True
    return False
