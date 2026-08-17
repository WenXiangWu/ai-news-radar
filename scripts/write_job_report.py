#!/usr/bin/env python3
"""Write job-report.json for the learning-site scheduler (status + summary only)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _frontier(data_dir: Path) -> dict[str, Any]:
    hub = _load(data_dir / "hub.json")
    date = str(hub.get("today") or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"))
    if not hub:
        return {
            "ok": False,
            "status": "error",
            "date": date,
            "summary": "缺少 hub.json",
            "error": "hub.json missing",
            "link": "/frontier/",
        }
    brief = hub.get("brief_count")
    lead = str(hub.get("lead") or "").strip()
    bits = [f"精选 {brief} 条" if brief is not None else "已生成快照"]
    if lead:
        bits.append(lead[:120])
    return {
        "ok": True,
        "status": "ok",
        "date": date,
        "summary": " · ".join(bits),
        "error": None,
        "link": f"/frontier/#{date}",
    }


def _sources(knowledge: dict[str, Any] | None) -> dict[str, Any] | None:
    if not knowledge:
        return None
    results_in = knowledge.get("results")
    if not isinstance(results_in, list):
        results_in = []
    results: dict[str, Any] = {}
    parts: list[str] = []
    any_err = False
    for row in results_in:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("source") or "")
        if not sid:
            continue
        errors = [str(x) for x in (row.get("errors") or []) if x]
        translated = row.get("translated") or []
        n_tr = len(translated) if isinstance(translated, list) else 0
        ok = bool(row.get("ok", not errors))
        if not ok:
            any_err = True
        summary = (
            f"新译 {n_tr} · 远端 {row.get('remote', '—')} · 本地 {row.get('local', '—')}"
            + (f" · 新增 slug {row.get('new')}" if row.get("new") is not None else "")
        )
        if row.get("skipped"):
            summary = str(row.get("skipped"))
        results[sid] = {
            "ok": ok,
            "error": "; ".join(errors)[:500] if errors else None,
            "summary": summary,
        }
        parts.append(f"{sid}:新译{n_tr}")
    overall_ok = bool(knowledge.get("ok", not any_err)) and not any_err
    return {
        "ok": overall_ok,
        "date": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"),
        "summary": "；".join(parts) if parts else "本轮无知识源结果",
        "error": None if overall_ok else "部分知识源失败",
        "results": results,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--knowledge-json", default="")
    p.add_argument("--out", default="")
    args = p.parse_args()
    data_dir = Path(args.data_dir)
    knowledge = _load(Path(args.knowledge_json)) if args.knowledge_json else None
    if knowledge == {}:
        knowledge = None
    frontier = _frontier(data_dir)
    sources = _sources(knowledge)
    report = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "date": frontier.get("date"),
        "frontier": frontier,
        "sources": sources,
    }
    out = Path(args.out) if args.out else data_dir / "job-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
