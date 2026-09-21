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


def _registered_jobs(task_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(task_report, dict):
        return []
    raw = task_report.get("jobs")
    if not isinstance(raw, list):
        raw = task_report.get("tasks")
    if not isinstance(raw, list):
        raw = task_report.get("operations")
    if not isinstance(raw, list):
        return []
    jobs: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        job = dict(row)
        status = str(job.get("status") or "").strip()
        if status == "success":
            job["status"] = "ok"
        elif status == "dry_run":
            job["status"] = "skipped"
        if not job.get("summary"):
            job["summary"] = (
                f"发现 {int(job.get('discovered') or 0)} · "
                f"抓取 {int(job.get('fetched') or 0)} · "
                f"翻译 {int(job.get('translated') or 0)}"
            )
        jobs.append(job)
    return jobs


def _knowledge_from_jobs(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        if str(job.get("kind") or "") != "knowledge_sync":
            continue
        source = str(job.get("source") or "").strip()
        if not source:
            continue
        rows.append(
            {
                "source": source,
                "ok": str(job.get("status") or "") in {"ok", "skipped"},
                "remote": job.get("remote"),
                "local": job.get("local"),
                "new": job.get("new"),
                "translated": job.get("translated") or [],
                "errors": job.get("errors") or ([job.get("error")] if job.get("error") else []),
                "summary": job.get("summary"),
            }
        )
    if not rows:
        return None
    return {
        "ok": all(bool(row.get("ok")) for row in rows),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": rows,
    }


def _radar_summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [
        row
        for row in jobs
        if str(row.get("status") or "").lower() in {"failed", "error", "blocked"}
    ]
    partial = [
        row for row in jobs if str(row.get("status") or "").lower() in {"partial", "warning"}
    ]
    ok = not failed and not partial
    return {
        "ok": ok,
        "status": "ok" if ok else ("error" if failed else "partial"),
        "summary": (
            f"任务 {len(jobs)} 个 · 成功 {len(jobs) - len(failed) - len(partial)}"
            f" · 部分 {len(partial)} · 失败 {len(failed)}"
        ),
        "error": None if ok else ("存在失败或阻塞任务" if failed else "存在部分成功任务"),
    }


def _radar_update_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(report, dict) or not report:
        return None
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    status = str(report.get("status") or "unknown")
    return {
        "ok": status == "success",
        "status": status,
        "summary": (
            f"模块 {int(summary.get('modules') or 0)} 个 · "
            f"更新 {int(summary.get('updated') or 0)} · "
            f"翻译 {int(summary.get('translated') or 0)}"
        ),
        "path": "frontend/radar-content/radar-update-report.json",
        "link": "/frontier/radar-data/radar-update-report.json",
        "error": None if status == "success" else "模块更新报告存在未完成项",
    }


def _radar_monitor_summary(monitor: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(monitor, dict) or not monitor:
        return None
    summary = monitor.get("summary") if isinstance(monitor.get("summary"), dict) else {}
    run = monitor.get("run") if isinstance(monitor.get("run"), dict) else {}
    return {
        "status": str(run.get("status") or "unknown"),
        "summary": (
            f"模块 {int(summary.get('modules') or 0)} 个 · "
            f"数据源 {int(summary.get('sources') or 0)} · "
            f"可达 {int(summary.get('healthy_sources') or 0)} · "
            f"失败 {int(summary.get('failed_sources') or 0)}"
        ),
        "path": "data/radar-monitor.json",
        "link": "https://news.learnprompt.pro/monitor/",
        "generated_at": monitor.get("generated_at"),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--knowledge-json", default="")
    p.add_argument("--registry-json", default="")
    p.add_argument("--reconciliation-json", default="")
    p.add_argument("--task-reports", default="")
    p.add_argument("--radar-update-report", default="")
    p.add_argument("--radar-monitor", default="")
    p.add_argument("--out", default="")
    args = p.parse_args()
    data_dir = Path(args.data_dir)
    knowledge = _load(Path(args.knowledge_json)) if args.knowledge_json else None
    if knowledge == {}:
        knowledge = None
    task_report = _load(Path(args.task_reports)) if args.task_reports else None
    radar_update = (
        _load(Path(args.radar_update_report))
        if args.radar_update_report
        else None
    )
    radar_monitor = (
        _load(Path(args.radar_monitor))
        if args.radar_monitor
        else None
    )
    reconciliation = _load(Path(args.reconciliation_json)) if args.reconciliation_json else {}
    registry = _load(Path(args.registry_json)) if args.registry_json else {}
    if isinstance(reconciliation.get("reconciliation"), dict):
        reconciliation = reconciliation["reconciliation"]
    if isinstance(registry.get("registry"), dict):
        registry = registry["registry"]
    frontier = _frontier(data_dir)
    jobs = _registered_jobs(task_report)
    if knowledge is None:
        knowledge = _knowledge_from_jobs(jobs)
    sources = _sources(knowledge)
    radar = _radar_summary(jobs)
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "schema": "radar-job-report/v1",
        "run_id": str((task_report or {}).get("run_id") or now),
        "updated_at": now,
        "date": frontier.get("date"),
        "radar": {
            **radar,
            "jobs": jobs,
            "reconciliation": reconciliation if isinstance(reconciliation, dict) else {},
        },
        "jobs": jobs,
        "reconciliation": reconciliation if isinstance(reconciliation, dict) else {},
        "registry": {
            "schema": registry.get("schema"),
            "module_count": len(registry.get("modules") or [])
            if isinstance(registry, dict)
            else 0,
        },
        "frontier": frontier,
        "sources": sources,
    }
    radar_update_summary = _radar_update_report(radar_update)
    if radar_update_summary is not None:
        report["radar_update"] = radar_update_summary
    radar_monitor_summary = _radar_monitor_summary(radar_monitor)
    if radar_monitor_summary is not None:
        report["radar_monitor"] = radar_monitor_summary
    out = Path(args.out) if args.out else data_dir / "job-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
