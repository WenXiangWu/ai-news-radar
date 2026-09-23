from __future__ import annotations

import json
from pathlib import Path

from scripts.build_way_inventory import assign_groups, build_way_inventory


WAY = Path("/Users/dz0400857/Desktop/way-to-agentic")


def test_live_inventory_marks_known_gaps():
    inventory = build_way_inventory(WAY)
    modules = {module["module_id"]: module for module in inventory["modules"]}
    assert list(modules) == [
        "course-center",
        "official-docs",
        "frameworks",
        "frontier",
        "summits",
    ]
    frameworks = {item["item_id"]: item for item in modules["frameworks"]["items"]}
    assert frameworks["vllm"]["coverage"] == "unregistered"
    assert frameworks["vllm"]["reason_zh"] == "未写入登记表，Radar 不会生成任务"
    courses = {item["item_id"]: item for item in modules["course-center"]["items"]}
    assert courses["center"]["coverage"] == "excluded"
    assert courses["center"]["reason_code"] == "site_owned"
    summits = modules["summits"]["items"]
    assert summits
    assert {item["reason_code"] for item in summits} == {"static_catalog"}
    assert all(item["reason_zh"] for item in summits)


def test_assign_groups_keeps_existing_ids():
    registries = {
        "docs_ids": ["a", "b", "c"],
        "knowledge_ids": [],
        "wiki_ids": [],
    }
    groups = assign_groups(registries, {"docs-01": ["c"]})
    assert groups["docs-01"][0] == "c"
    assert "c" not in groups["docs-01"][1:]
    assert set(groups["docs-01"]) == {"a", "b", "c"}


def test_workflow_status_strips_secrets(tmp_path: Path):
    from scripts.write_workflow_status import write_workflow_status

    path = tmp_path / "status.json"
    write_workflow_status(
        path,
        {
            "workflow_id": "update-news",
            "conclusion": "success",
            "push": {"attempted": False, "api_key": "secret-value"},
            "sources": [{"source_id": "official_ai", "token": "hidden"}],
        },
        run_id="run-1",
        html_url="https://example.test/run",
    )
    text = path.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert "hidden" not in text
    assert json.loads(text)["schema"] == "radar-workflow-status/v1"
