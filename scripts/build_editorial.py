#!/usr/bin/env python3
"""Build calendar dailies, hot boards, topics, week/month packs from radar snapshots."""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
HOT_DECAY_HOURS = 12
HOT_SCORE_SCALE = 60

OFFICIAL_SITE_IDS = frozenset({"official_ai"})
OFFICIAL_SOURCE_HINTS = (
    "openai", "anthropic", "deepmind", "google", "huggingface", "github",
    "nvidia", "microsoft", "qwen", "deepseek", "langchain", "mcp",
    "simon willison",
)
AGGREGATOR_SITE_IDS = frozenset({
    "techurls", "buzzing", "iris", "newsnow", "aibase", "aihot",
    "aihubtoday", "bestblogs", "zeli",
})

TOPICS: list[dict[str, Any]] = [
    {"slug": "openai", "group": "vendor", "label": "OpenAI / ChatGPT", "blurb": "GPT、ChatGPT、Sora 与公司动态", "patterns": [r"openai", r"chatgpt", r"\bgpt-?\d", r"\bsora\b"]},
    {"slug": "anthropic", "group": "vendor", "label": "Anthropic / Claude", "blurb": "Claude、Claude Code 与安全研究", "patterns": [r"anthropic", r"claude"]},
    {"slug": "google", "group": "vendor", "label": "Google / Gemini", "blurb": "Gemini、DeepMind、Veo", "patterns": [r"gemini", r"deepmind", r"\bveo\b", r"google ai"]},
    {"slug": "deepseek", "group": "vendor", "label": "DeepSeek", "blurb": "开源权重与技术报告", "patterns": [r"deepseek"]},
    {"slug": "qwen", "group": "vendor", "label": "通义千问 Qwen", "blurb": "Qwen 开源与端侧", "patterns": [r"\bqwen\b", r"通义", r"千问"]},
    {"slug": "agent", "group": "tech", "label": "Agent 智能体", "blurb": "规划、工具调用、多步任务", "patterns": [r"\bagent\b", r"智能体", r"manus", r"claude code", r"codex"]},
    {"slug": "coding", "group": "tech", "label": "AI 编码", "blurb": "编码助手与开发工作流", "patterns": [r"cursor", r"copilot", r"codex", r"vibe coding", r"编码"]},
    {"slug": "mcp", "group": "tech", "label": "MCP 与工具", "blurb": "MCP、function calling、工具链", "patterns": [r"\bmcp\b", r"function calling", r"tool use"]},
    {"slug": "opensource", "group": "tech", "label": "开源生态", "blurb": "权重开放与社区项目", "patterns": [r"开源", r"open.?source", r"huggingface", r"weights"]},
    {"slug": "launch", "group": "form", "label": "模型发布", "blurb": "新模型、权重与价格", "patterns": [r"release", r"发布", r"weights", r"model_release"]},
    {"slug": "paper", "group": "form", "label": "论文研究", "blurb": "论文、基准与评测", "patterns": [r"arxiv", r"paper", r"论文", r"benchmark", r"research_paper"]},
    {"slug": "industry", "group": "form", "label": "行业动态", "blurb": "融资、监管与公司", "patterns": [r"融资", r"监管", r"acquire", r"industry", r"policy"]},
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: dict[str, Any], *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def shanghai_date(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SHANGHAI).date().isoformat()


def story_rep(story: dict[str, Any]) -> dict[str, Any]:
    primary = story.get("primary_item") if isinstance(story.get("primary_item"), dict) else {}
    if primary.get("title") or primary.get("url"):
        if not primary.get("site_id") and isinstance(story.get("sources"), list):
            match = next((s for s in story["sources"] if isinstance(s, dict) and s.get("url") == primary.get("url")), None)
            match = match or (story["sources"][0] if story["sources"] else {})
            if isinstance(match, dict):
                merged = dict(match)
                merged.update(primary)
                return merged
        return primary
    sources = story.get("sources") if isinstance(story.get("sources"), list) else []
    if sources and isinstance(sources[0], dict):
        return sources[0]
    return story


def story_title(story: dict[str, Any]) -> str:
    rep = story_rep(story)
    return str(rep.get("title_zh") or rep.get("title") or story.get("title") or "未命名").strip()


def story_url(story: dict[str, Any]) -> str:
    rep = story_rep(story)
    return str(rep.get("url") or story.get("url") or story.get("primary_url") or "").strip()


def story_source(story: dict[str, Any]) -> str:
    rep = story_rep(story)
    return str(rep.get("source") or story.get("source") or "").strip()


def story_site_id(story: dict[str, Any]) -> str:
    rep = story_rep(story)
    return str(rep.get("site_id") or story.get("site_id") or "").strip()


def story_time(story: dict[str, Any]) -> datetime | None:
    return parse_iso(story.get("latest_at")) or parse_iso(story.get("earliest_at")) or parse_iso(story_rep(story).get("published_at"))


def story_source_count(story: dict[str, Any]) -> int:
    explicit = story.get("duplicate_count") or story.get("source_count")
    try:
        n = int(explicit)
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    sources = story.get("sources") if isinstance(story.get("sources"), list) else []
    return max(1, len(sources))


def story_score(story: dict[str, Any]) -> int:
    raw = story.get("importance_score") or story.get("score") or story.get("importance") or 0
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    return int(round(n * 100 if n <= 1 else n))


def is_official(story: dict[str, Any]) -> bool:
    site = story_site_id(story).lower()
    if site in OFFICIAL_SITE_IDS:
        return True
    if site.startswith("opmlrss"):
        blob = f"{story_source(story)} {story_url(story)}".lower()
        return any(h in blob for h in OFFICIAL_SOURCE_HINTS)
    blob = f"{story_source(story)} {story_url(story)}".lower()
    return any(h in blob for h in OFFICIAL_SOURCE_HINTS)


def is_aggregator(story: dict[str, Any]) -> bool:
    return story_site_id(story) in AGGREGATOR_SITE_IDS


def haystack(story: dict[str, Any]) -> str:
    rep = story_rep(story)
    return " ".join(
        str(x) for x in (
            story_title(story),
            story_source(story),
            story_url(story),
            story.get("category"),
            story.get("importance_label"),
            rep.get("ai_label"),
            story.get("ai_label"),
        ) if x
    ).lower()


def topic_slugs(story: dict[str, Any]) -> list[str]:
    text = haystack(story)
    hits = []
    for topic in TOPICS:
        if any(re.search(p, text, re.I) for p in topic["patterns"]):
            hits.append(topic["slug"])
    return hits


def slim(story: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    rep = story_rep(story)
    row = {
        "title": story_title(story),
        "url": story_url(story),
        "source": story_source(story),
        "site_id": story_site_id(story),
        "published_at": (story_time(story) or datetime.now(timezone.utc)).isoformat(),
        "source_count": story_source_count(story),
        "score": story_score(story),
        "official": is_official(story),
        "topics": topic_slugs(story),
        "reason": str(rep.get("recommend_reason_zh") or story.get("recommend_reason_zh") or ""),
        "label": str(story.get("importance_label") or story.get("category") or ""),
    }
    if extra:
        row.update(extra)
    return row


def hotness(story: dict[str, Any], now: datetime) -> float:
    sources = story_source_count(story)
    if sources < 2:
        return 0.0
    latest = story_time(story)
    age_hours = 24.0
    if latest:
        age_hours = max(0.0, (now - latest).total_seconds() / 3600)
    return (sources - 1) * math.exp(-age_hours / HOT_DECAY_HOURS)


def hot_score(story: dict[str, Any], now: datetime) -> int:
    raw = hotness(story, now)
    if raw <= 0:
        return 0
    return max(1, min(100, int(round(raw * HOT_SCORE_SCALE))))


def pick_daily(stories: list[dict[str, Any]], now: datetime, limit: int = 20) -> list[dict[str, Any]]:
    today = shanghai_date(now)
    in_day = []
    for story in stories:
        day = shanghai_date(story_time(story))
        if day == today or story_time(story) and (now - story_time(story)).total_seconds() <= 36 * 3600:
            in_day.append(story)
    if not in_day:
        in_day = stories[:80]

    official = [s for s in in_day if is_official(s)]
    crossing = [s for s in in_day if story_source_count(s) >= 2 and not is_aggregator(s)]
    rest = [s for s in in_day if not is_aggregator(s) and s not in official and s not in crossing]
    fillers = [s for s in in_day if is_aggregator(s)]

    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def take(pool: list[dict[str, Any]], cap: int) -> None:
        ranked = sorted(pool, key=lambda s: (story_score(s), story_source_count(s), story_time(s) or now), reverse=True)
        for story in ranked:
            if len(picked) >= limit:
                return
            url = story_url(story)
            if not url or url in seen:
                continue
            seen.add(url)
            picked.append(story)

    take(official, 10)
    take(crossing, 8)
    take(rest, 6)
    if len(picked) < 12:
        take(fillers, 12 - len(picked))
    return picked[:limit]


def write_hot(stories: list[dict[str, Any]], now: datetime, output_dir: Path, hours: int, name: str) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=hours)
    rows = []
    for story in stories:
        t = story_time(story)
        if not t or t < cutoff:
            continue
        h = hotness(story, now)
        if h <= 0:
            continue
        rows.append(slim(story, {"hot_score": hot_score(story, now)}))
    rows.sort(key=lambda r: (r.get("hot_score") or 0, r.get("source_count") or 0), reverse=True)
    rows = rows[:20]
    dump_json(output_dir / "hot" / f"{name}.json", {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "total_items": len(rows),
        "items": rows,
    })
    return rows


def write_topics(stories: list[dict[str, Any]], now: datetime, output_dir: Path) -> list[dict[str, Any]]:
    index = []
    for topic in TOPICS:
        items = []
        for story in stories:
            if topic["slug"] in topic_slugs(story):
                items.append(slim(story))
        items.sort(key=lambda r: r.get("published_at") or "", reverse=True)
        items = items[:40]
        dump_json(output_dir / "topics" / f"{topic['slug']}.json", {
            "generated_at": now.isoformat(),
            "slug": topic["slug"],
            "label": topic["label"],
            "group": topic["group"],
            "blurb": topic["blurb"],
            "total_items": len(items),
            "items": items,
        })
        index.append({
            "slug": topic["slug"],
            "label": topic["label"],
            "group": topic["group"],
            "blurb": topic["blurb"],
            "count": len(items),
        })
    dump_json(output_dir / "topics" / "index.json", {
        "generated_at": now.isoformat(),
        "total_topics": len(index),
        "topics": index,
    })
    return index


def upsert_daily_index(output_dir: Path, date: str, total: int, generated_at: str) -> list[dict[str, Any]]:
    path = output_dir / "dailies" / "index.json"
    payload = load_json(path)
    items = [x for x in payload.get("items", []) if isinstance(x, dict) and x.get("date") != date]
    items.append({"date": date, "total_items": total, "generated_at": generated_at})
    items.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    items = items[:90]
    dump_json(path, {"generated_at": generated_at, "total_days": len(items), "items": items})
    return items


def pack_period(dailies_dir: Path, dates: list[str], title: str) -> dict[str, Any]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for date in dates:
        payload = load_json(dailies_dir / f"{date}.json")
        for row in payload.get("items") or []:
            url = str(row.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            items.append(row)
    items.sort(key=lambda r: (r.get("official") is True, r.get("score") or 0, r.get("published_at") or ""), reverse=True)
    return {
        "title": title,
        "dates": dates,
        "total_items": len(items[:40]),
        "items": items[:40],
    }


def write_week_month(output_dir: Path, now: datetime, daily_index: list[dict[str, Any]]) -> None:
    today = now.astimezone(SHANGHAI).date()
    week_dates = []
    for i in range(7):
        week_dates.append((today - timedelta(days=i)).isoformat())
    iso = today.isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"
    week_pack = pack_period(output_dir / "dailies", week_dates, f"{week_id} 周报")
    week_pack["generated_at"] = now.isoformat()
    week_pack["period"] = week_id
    dump_json(output_dir / "weekly" / f"{week_id}.json", week_pack)
    dump_json(output_dir / "weekly" / "index.json", {
        "generated_at": now.isoformat(),
        "latest": week_id,
        "items": [{"id": week_id, "total_items": week_pack["total_items"]}],
    })

    month_id = today.strftime("%Y-%m")
    month_dates = [x["date"] for x in daily_index if str(x.get("date") or "").startswith(month_id)]
    month_pack = pack_period(output_dir / "dailies", month_dates, f"{month_id} 月报")
    month_pack["generated_at"] = now.isoformat()
    month_pack["period"] = month_id
    dump_json(output_dir / "monthly" / f"{month_id}.json", month_pack)
    dump_json(output_dir / "monthly" / "index.json", {
        "generated_at": now.isoformat(),
        "latest": month_id,
        "items": [{"id": month_id, "total_items": month_pack["total_items"]}],
    })


def build(output_dir: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stories_pack = load_json(output_dir / "stories-merged.json")
    brief_pack = load_json(output_dir / "daily-brief.json")
    stories = [s for s in stories_pack.get("stories") or [] if isinstance(s, dict)]
    if not stories:
        stories = [s for s in brief_pack.get("items") or [] if isinstance(s, dict)]

    picked = pick_daily(stories, now, 20)
    today = shanghai_date(now) or now.date().isoformat()
    daily_items = [slim(s) for s in picked]
    hot_today = [slim(s, {"hot_score": hot_score(s, now)}) for s in picked if story_source_count(s) >= 2][:8]
    lead_bits = [row["title"] for row in daily_items[:3] if row.get("title")]
    daily = {
        "generated_at": now.isoformat(),
        "date": today,
        "timezone": "Asia/Shanghai",
        "lead": "；".join(lead_bits) if lead_bits else "今日雷达精选已更新。",
        "total_items": len(daily_items),
        "selection": "official_first_then_crossing",
        "hot": hot_today,
        "items": daily_items,
    }
    dump_json(output_dir / "dailies" / f"{today}.json", daily)
    daily_index = upsert_daily_index(output_dir, today, len(daily_items), now.isoformat())

    hot_24 = write_hot(stories, now, output_dir, 24, "24h")
    write_hot(stories, now, output_dir, 168, "7d")
    topics = write_topics(stories, now, output_dir)
    write_week_month(output_dir, now, daily_index)

    hub = {
        "generated_at": now.isoformat(),
        "today": today,
        "lead": daily["lead"],
        "brief_count": len(daily_items),
        "hot_preview": (hot_24 or hot_today)[:5],
        "topics": topics,
        "days": [x.get("date") for x in daily_index[:14]],
        "week": load_json(output_dir / "weekly" / "index.json").get("latest"),
        "month": load_json(output_dir / "monthly" / "index.json").get("latest"),
    }
    dump_json(output_dir / "hub.json", hub)
    return hub


def main() -> int:
    parser = argparse.ArgumentParser(description="Build editorial JSON packs from radar snapshots")
    parser.add_argument("--output-dir", default="data")
    args = parser.parse_args()
    hub = build(Path(args.output_dir))
    print(f"Wrote editorial pack for {hub.get('today')} ({hub.get('brief_count')} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
