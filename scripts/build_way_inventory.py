#!/usr/bin/env python3
"""Scan Way module catalogs into a coverage inventory."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SITE_OWNED = {"center", "frameworks", "practice"}
FRAMEWORK_CARDS = {"deepseek-harness", "cordis"}
WIKI_SKIP = {"deepseek-harness", "cordis"}
REASONS = {
    "site_owned": "本站页面，没有上游仓库",
    "covered_by_framework_module": "课程卡片指向已有框架模块",
    "not_git_tutorial": "不是 Git 教程仓",
    "pdf_source": "来源是 PDF，页内图由站点重绘",
    "manual_editorial": "登记为人工维护，Radar 不覆盖正文",
    "static_catalog": "静态目录，没有 Radar 任务",
    "not_in_registry": "未写入登记表，Radar 不会生成任务",
    "textbook_mirror": "教材镜像，不翻译",
    "knowledge_sync": "官方资料同步",
    "docs_sync": "框架文档同步",
    "wiki_sync": "DeepWiki 架构同步",
    "news_collect": "新闻采集",
    "push_blocked": "抓取已完成，尚未推上站点",
    "bootstrap_rejected": "全量更新已被拒绝",
    "run_failed": "最近一次失败",
    "not_run": "尚未成功执行",
}


def build_way_inventory(way_root: Path) -> dict[str, Any]:
    root = Path(way_root)
    registries = _registries(root)
    modules = [
        _course_center(root, registries),
        _official_docs(registries),
        _frameworks(root, registries),
        _frontier(root),
        _summits(root),
    ]
    return {
        "schema": "way-inventory/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modules": modules,
    }


def source_groups(
    registries: dict[str, Any],
    existing: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Group Radar source ids. Docs and wiki keep separate ids for the same framework."""
    knowledge_ids = []
    for row in registries.get("knowledge_rows") or []:
        module_id = str(row.get("module_id") or row.get("id") or "")
        if not module_id:
            continue
        knowledge_ids.append(module_id if module_id.startswith("source.") else f"source.{module_id}")
    return assign_groups(
        {
            "docs_ids": [f"docs.{item_id}" for item_id in registries.get("docs_ids") or []],
            "knowledge_ids": knowledge_ids,
            "wiki_ids": [f"wiki.{item_id}" for item_id in registries.get("wiki_ids") or []],
        },
        existing,
    )


def assign_groups(
    registries: dict[str, Any],
    existing: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Sticky groups of at most six sources. Existing ids stay in their group."""
    groups = {
        group_id: list(source_ids)
        for group_id, source_ids in (existing or {}).items()
    }
    assigned = {source_id for source_ids in groups.values() for source_id in source_ids}
    for kind, ids in (
        ("docs", registries.get("docs_ids") or []),
        ("knowledge", registries.get("knowledge_ids") or []),
        ("wiki", registries.get("wiki_ids") or []),
    ):
        pending = [source_id for source_id in sorted(ids) if source_id not in assigned]
        for group_id in sorted(group for group in groups if group.startswith(f"{kind}-")):
            while pending and len(groups[group_id]) < 6:
                groups[group_id].append(pending.pop(0))
                assigned.add(groups[group_id][-1])
        slot = 1
        while pending:
            group_id = f"{kind}-{slot:02d}"
            slot += 1
            if group_id in groups:
                continue
            groups[group_id] = pending[:6]
            assigned.update(groups[group_id])
            pending = pending[6:]
    return groups


def _registries(root: Path) -> dict[str, Any]:
    knowledge = _read_json(root / "radar/registry/knowledge.json").get("sources") or []
    docs = _read_json(root / "frontend/path/frameworks/sync/registry.json").get("frameworks") or []
    wiki = _read_json(root / "frontend/path/frameworks/wiki/registry.json").get("frameworks") or []
    modules = _read_json(root / "radar/registry/index.json").get("modules") or []
    textbooks = _read_json(root / "radar/registry/textbooks.json").get("books") or []
    editorial_ids = set()
    for module in modules:
        module_id = str(module.get("id") or "")
        manifest = root / "radar/registry" / str(module.get("manifest") or "")
        if manifest.is_file():
            payload = _read_json(manifest)
            for task in payload.get("tasks") or []:
                if task.get("adapter") == "manual_editorial":
                    editorial_ids.add(module_id)
    return {
        "knowledge_ids": {str(row.get("id")) for row in knowledge if row.get("id")},
        "knowledge_rows": knowledge,
        "docs_ids": {str(row.get("id")) for row in docs if row.get("id")},
        "docs_rows": docs,
        "wiki_ids": {
            str(row.get("id"))
            for row in wiki
            if row.get("id") and str(row.get("id")) not in WIKI_SKIP and row.get("github")
        },
        "wiki_rows": wiki,
        "editorial_ids": editorial_ids,
        "textbook_ids": {str(row.get("id")) for row in textbooks if row.get("id")},
        "textbook_rows": textbooks,
    }


def _course_center(root: Path, registries: dict[str, Any]) -> dict[str, Any]:
    text = (root / "frontend/path/catalog.js").read_text(encoding="utf-8")
    courses = text.split("var COURSES = [", 1)[1].split("var COLUMNS = [", 1)[0]
    columns = text.split("var COLUMNS = [", 1)[1].split("var STAGES = [", 1)[0]
    items = [_course_item(row, registries) for row in _js_entries(courses)]
    items.extend(_course_item(row, registries) for row in _js_entries(columns))
    known = {item["item_id"] for item in items}
    for book in registries["textbook_rows"]:
        book_id = str(book.get("id") or "")
        if book_id and book_id not in known:
            items.append(
                _item(
                    book_id,
                    str(book.get("name") or book_id),
                    str(book.get("link") or ""),
                    "textbook_mirror",
                    "update-textbooks",
                    book_id,
                )
            )
    return {"module_id": "course-center", "name": "课程中心", "items": items}


def _course_item(row: dict[str, str], registries: dict[str, Any]) -> dict[str, Any]:
    item_id = row["id"]
    if item_id in SITE_OWNED:
        return _item(item_id, row["title"], row.get("href", ""), "site_owned", None, None)
    if item_id in FRAMEWORK_CARDS:
        return _item(
            item_id,
            row["title"],
            row.get("href", ""),
            "covered_by_framework_module",
            None,
            None,
        )
    if item_id == "claude-code":
        return _item(item_id, row["title"], row.get("href", ""), "not_git_tutorial", None, None)
    if item_id == "claude-code-report":
        return _item(item_id, row["title"], row.get("href", ""), "pdf_source", None, None)
    if item_id in registries["textbook_ids"]:
        return _item(
            item_id,
            row["title"],
            row.get("href", ""),
            "textbook_mirror",
            "update-textbooks",
            item_id,
        )
    return _item(item_id, row["title"], row.get("href", ""), "not_in_registry", None, None)


def _official_docs(registries: dict[str, Any]) -> dict[str, Any]:
    items = [
        _item(
            str(row.get("id")),
            str(row.get("label") or row.get("id")),
            str(row.get("output") or ""),
            "knowledge_sync",
            "update-radar",
            (
                str(row.get("module_id"))
                if str(row.get("module_id") or "").startswith("source.")
                else f"source.{row.get('module_id') or row.get('id')}"
            ),
        )
        for row in registries["knowledge_rows"]
        if row.get("id")
    ]
    return {"module_id": "official-docs", "name": "官方资料", "items": items}


def _frameworks(root: Path, registries: dict[str, Any]) -> dict[str, Any]:
    text = (root / "frontend/path/frameworks-embed.js").read_text(encoding="utf-8")
    leaves = []
    for item_id, file_name in re.findall(r'id:\s*"([^"]+)"[^}]*?file:\s*"([^"]+)"', text):
        if re.search(r".+/.+/index\.md$", file_name):
            leaves.append(item_id)
    items = []
    seen = set()
    for item_id in leaves:
        if item_id in seen:
            continue
        seen.add(item_id)
        if item_id in FRAMEWORK_CARDS:
            items.append(_item(item_id, item_id, f"/learn/frameworks/#{item_id}", "covered_by_framework_module", None, None))
        elif item_id in registries["editorial_ids"]:
            items.append(_item(item_id, item_id, f"/learn/frameworks/#{item_id}", "manual_editorial", None, None))
        elif item_id in registries["docs_ids"]:
            items.append(_item(item_id, item_id, f"/learn/frameworks/#{item_id}", "docs_sync", "update-radar", f"docs.{item_id}"))
        elif item_id in registries["wiki_ids"]:
            items.append(_item(item_id, item_id, f"/learn/frameworks/#{item_id}", "wiki_sync", "update-radar", f"wiki.{item_id}"))
        else:
            items.append(_item(item_id, item_id, f"/learn/frameworks/#{item_id}", "not_in_registry", None, None))
    return {"module_id": "frameworks", "name": "开源框架", "items": items}


def _frontier(root: Path) -> dict[str, Any]:
    collectors = [
        ("official_ai", "Official AI Updates"),
        ("curated_media", "Curated Media"),
        ("aibreakfast", "AI Breakfast"),
        ("followbuilders", "Follow Builders"),
        ("techurls", "TechURLs"),
        ("buzzing", "Buzzing"),
        ("iris", "Info Flow"),
        ("bestblogs", "BestBlogs"),
        ("zeli", "Zeli"),
        ("hackernews", "Hacker News"),
        ("aihubtoday", "AI HubToday"),
        ("aibase", "AIbase"),
        ("aihot", "AI HOT"),
        ("newsnow", "NewsNow"),
    ]
    report = root / "frontend/frontier/radar-data/job-report.json"
    ok = False
    if report.is_file():
        frontier = _read_json(report).get("frontier") or {}
        ok = bool(frontier.get("ok"))
    reason = "news_collect" if ok else "run_failed"
    items = [
        _item(site_id, name, "/discover/", reason, "update-news", site_id)
        for site_id, name in collectors
    ]
    return {"module_id": "frontier", "name": "前沿追踪", "items": items}


def _summits(root: Path) -> dict[str, Any]:
    text = (root / "frontend/frontier/summits/summit-data.js").read_text(encoding="utf-8")
    items = [
        _item(item_id, item_id, f"/discover/summits/detail.html?id={item_id}", "static_catalog", None, None)
        for item_id in re.findall(r'\n\s*id:\s*"([^"]+)"', text)
    ]
    return {"module_id": "summits", "name": "技术峰会", "items": items}


def _item(
    item_id: str,
    name: str,
    surface: str,
    reason_code: str,
    workflow_id: str | None,
    radar_source_id: str | None,
) -> dict[str, Any]:
    coverage = {
        "site_owned": "excluded",
        "covered_by_framework_module": "excluded",
        "not_git_tutorial": "excluded",
        "pdf_source": "excluded",
        "manual_editorial": "excluded",
        "static_catalog": "excluded",
        "not_in_registry": "unregistered",
    }.get(reason_code, "registered")
    return {
        "item_id": item_id,
        "name": name,
        "surface": surface,
        "radar_source_id": radar_source_id,
        "workflow_id": workflow_id,
        "coverage": coverage,
        "reason_code": reason_code,
        "reason_zh": REASONS[reason_code],
        "baseline": "not_applicable" if coverage in {"excluded", "unregistered"} else "absent",
        "baseline_at": None,
    }


def _js_entries(block: str) -> list[dict[str, str]]:
    rows = []
    for match in re.finditer(
        r'id:\s*"([^"]+)"[\s\S]*?title:\s*"([^"]+)"[\s\S]*?href:\s*"([^"]+)"',
        block,
    ):
        rows.append({"id": match.group(1), "title": match.group(2), "href": match.group(3)})
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
