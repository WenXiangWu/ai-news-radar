#!/usr/bin/env python3
"""Execute due tasks declared by the way-to-agentic Radar registry."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    from scripts.radar_registry import flatten_tasks, load_registry
except ModuleNotFoundError:  # direct `python scripts/run_registered_tasks.py`
    from radar_registry import flatten_tasks, load_registry

Executor = Callable[[dict[str, Any], dict[str, Any], Path], dict[str, Any]]


def _field_matches(expression: str, value: int, minimum: int, maximum: int) -> bool:
    for raw_part in str(expression or "*").split(","):
        part = raw_part.strip()
        if not part:
            continue
        base, _, raw_step = part.partition("/")
        step = int(raw_step) if raw_step else 1
        if step <= 0:
            continue
        if base in ("", "*"):
            start, end = minimum, maximum
        elif "-" in base:
            start_s, end_s = base.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(base)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def cron_matches(expression: str, value: datetime) -> bool:
    fields = str(expression or "").split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    return (
        _field_matches(minute, value.minute, 0, 59)
        and _field_matches(hour, value.hour, 0, 23)
        and _field_matches(dom, value.day, 1, 31)
        and _field_matches(month, value.month, 1, 12)
        and _field_matches(dow, (value.weekday() + 1) % 7, 0, 6)
    )


def _schedule_time(task: dict[str, Any], value: datetime) -> datetime:
    schedule = task.get("schedule") or {}
    try:
        tz = ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Shanghai")
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _last_scheduled(task: dict[str, Any], now: datetime) -> datetime | None:
    current = _schedule_time(task, now).replace(second=0, microsecond=0)
    expression = str((task.get("schedule") or {}).get("cron") or "")
    for _ in range(366 * 24 * 60):
        if cron_matches(expression, current):
            return current
        current -= timedelta(minutes=1)
    return None


def next_run_at(task: dict[str, Any], after: datetime) -> str | None:
    current = _schedule_time(task, after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    expression = str((task.get("schedule") or {}).get("cron") or "")
    for _ in range(366 * 24 * 60):
        if cron_matches(expression, current):
            return current.isoformat()
        current += timedelta(minutes=1)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def select_due_tasks(
    tasks: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    task_state = state.get("tasks") if isinstance(state, dict) else {}
    if not isinstance(task_state, dict):
        task_state = {}
    due: list[dict[str, Any]] = []
    for task in tasks:
        schedule = task.get("schedule") or {}
        if not bool(schedule.get("enabled", True)):
            continue
        scheduled = _last_scheduled(task, now)
        if scheduled is None:
            continue
        previous = task_state.get(str(task.get("id"))) or {}
        last_run = _parse_datetime(previous.get("last_run_at"))
        if last_run is None:
            local_now = _schedule_time(task, now)
            if scheduled.date() != local_now.date():
                continue
            due.append(task)
            continue
        if last_run < scheduled:
            due.append(task)
    return due


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"tasks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"tasks": {}}
    return data if isinstance(data, dict) else {"tasks": {}}


def _default_executor(task: dict[str, Any], module: dict[str, Any], target_root: Path) -> dict[str, Any]:
    adapter = str(task.get("adapter") or "")
    if task.get("kind") == "translation" and module.get("kind") == "knowledge":
        return {
            "ok": True,
            "status": "skipped",
            "summary": "知识源同步任务已完成增量翻译",
        }
    if adapter == "rss":
        return {
            "ok": True,
            "status": "ok",
            "summary": "由 Radar 新闻聚合流水线统一采集",
        }
    if adapter == "knowledge_source":
        try:
            from scripts.sync_knowledge_sources import sync_one
        except ModuleNotFoundError:
            from sync_knowledge_sources import sync_one

        source_id = str((module.get("source") or {}).get("legacy_id") or "")
        result = sync_one(
            source_id,
            target_root / "frontend/sources",
            int((task.get("schedule") or {}).get("max_new_items") or 0),
        )
        result["summary"] = (
            f"远端 {result.get('remote', '—')} · 本地 {result.get('local', '—')} · "
            f"新译 {len(result.get('translated') or [])}"
        )
        return result
    if adapter == "markdown_google":
        try:
            from scripts.translate_registered_markdown import translate_task
        except ModuleNotFoundError:
            from translate_registered_markdown import translate_task

        return translate_task(task, target_root)
    if adapter == "framework_hubs":
        proc = subprocess.run(
            [sys.executable, "scripts/wiki_sync.py", "patch-hubs"],
            cwd=str(target_root),
            capture_output=True,
            text=True,
            timeout=int((task.get("schedule") or {}).get("max_runtime_minutes") or 30) * 60,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout or "framework hub sync failed")[:500])
        return {"ok": True, "summary": (proc.stdout or "框架索引已更新").strip()[-500:]}
    if adapter in {"deepwiki", "llms_txt", "mintlify", "github_tree", "next_data", "langchain_md"}:
        legacy_id = str((task.get("source") or {}).get("legacy_id") or "")
        if task.get("kind") == "wiki_sync":
            command = [sys.executable, "scripts/wiki_sync.py", "fetch", "--id", legacy_id]
        else:
            command = [
                sys.executable,
                "scripts/docs_sync.py",
                "detect",
                "--id",
                legacy_id,
                "--content",
            ]
        timeout = int((task.get("schedule") or {}).get("max_runtime_minutes") or 30) * 60
        proc = subprocess.run(
            command,
            cwd=str(target_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout or "framework sync failed")[:500])
        if task.get("kind") == "docs_sync":
            apply_proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/docs_sync.py",
                    "apply",
                    "--id",
                    legacy_id,
                    "--refresh-nav",
                ],
                cwd=str(target_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if apply_proc.returncode:
                raise RuntimeError(
                    (apply_proc.stderr or apply_proc.stdout or "framework apply failed")[:500]
                )
            output = apply_proc.stdout or proc.stdout
        else:
            output = proc.stdout
        return {"ok": True, "summary": (output or "框架同步完成").strip()[-500:]}
    raise LookupError(f"unknown adapter: {adapter}")


def _summary(result: dict[str, Any], task: dict[str, Any]) -> str:
    if result.get("summary"):
        return str(result["summary"])[:500]
    translated = len(result.get("translated") or [])
    added = result.get("added")
    updated = result.get("updated")
    if translated or added is not None or updated is not None:
        return f"新增 {added or 0} · 更新 {updated or 0} · 翻译 {translated}"
    return f"{task.get('kind') or '任务'}执行完成"


def run_registered_tasks(
    target_root: Path,
    registry: dict[str, Any],
    state_path: Path,
    *,
    now: datetime | None = None,
    executor: Executor | None = None,
) -> dict[str, Any]:
    target_root = Path(target_root).resolve()
    current = now or datetime.now(timezone.utc)
    state = _load_state(state_path)
    task_state = state.setdefault("tasks", {})
    if not isinstance(task_state, dict):
        task_state = {}
        state["tasks"] = task_state
    modules = {
        str(module.get("id")): module
        for module in registry.get("modules") or []
        if isinstance(module, dict)
    }
    tasks = flatten_tasks(registry)
    due_ids = {str(task.get("id")) for task in select_due_tasks(tasks, state=state, now=current)}
    reports: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    runner = executor or _default_executor
    knowledge_remaining = max(
        0,
        int(os.environ.get("KNOWLEDGE_MAX_NEW_TOTAL") or 4),
    )
    knowledge_per_source = max(0, int(os.environ.get("KNOWLEDGE_MAX_NEW") or 2))
    raw_knowledge_sources = str(os.environ.get("KNOWLEDGE_SOURCES") or "").strip()
    allowed_knowledge_sources = (
        {item.strip() for item in raw_knowledge_sources.split(",") if item.strip()}
        if raw_knowledge_sources
        else None
    )

    for task in tasks:
        task_id = str(task.get("id") or "")
        module = modules.get(str(task.get("module_id"))) or {}
        next_at = next_run_at(task, current)
        if task_id not in due_ids:
            reports.append(
                {
                    "module_id": task.get("module_id"),
                    "task_id": task_id,
                    "kind": task.get("kind"),
                    "status": "skipped",
                    "summary": "未到执行时间",
                    "next_run_at": next_at,
                }
            )
            statuses[task_id] = "skipped"
            continue
        deps = [str(dep) for dep in task.get("depends_on") or []]
        failed_deps = [
            dep
            for dep in deps
            if statuses.get(dep) in {"failed", "blocked"}
            or (
                statuses.get(dep) is None
                and str((task_state.get(dep) or {}).get("last_status") or "")
                in {"failed", "blocked"}
            )
        ]
        base_report = {
            "module_id": task.get("module_id"),
            "task_id": task_id,
            "kind": task.get("kind"),
            "source": (module.get("source") or {}).get("legacy_id"),
            "module_name": (module.get("display") or {}).get("name"),
            "schedule": task.get("schedule") or {},
            "next_run_at": next_at,
        }
        source_id = str(base_report.get("source") or "")
        if (
            allowed_knowledge_sources is not None
            and module.get("kind") == "knowledge"
            and task.get("kind") == "knowledge_sync"
            and source_id not in allowed_knowledge_sources
        ):
            reports.append(
                {
                    **base_report,
                    "status": "skipped",
                    "summary": "未纳入 KNOWLEDGE_SOURCES",
                }
            )
            statuses[task_id] = "skipped"
            continue
        if failed_deps:
            result = {
                **base_report,
                "status": "blocked",
                "summary": "依赖任务失败：" + ", ".join(failed_deps),
                "error": "dependency_failed",
            }
            statuses[task_id] = "blocked"
            reports.append(result)
            continue
        try:
            task_for_run = task
            if module.get("kind") == "knowledge" and task.get("kind") == "knowledge_sync":
                task_for_run = dict(task)
                schedule = dict(task.get("schedule") or {})
                schedule["max_new_items"] = min(
                    knowledge_per_source,
                    int(schedule.get("max_new_items") or knowledge_per_source),
                    knowledge_remaining,
                )
                task_for_run["schedule"] = schedule
            result = runner(task_for_run, module, target_root) or {}
            status = str(result.get("status") or ("ok" if result.get("ok", True) else "failed"))
            if status not in {"ok", "partial", "failed", "blocked", "skipped"}:
                status = "ok" if result.get("ok", True) else "failed"
            report = {
                **base_report,
                **{
                    key: result[key]
                    for key in (
                        "added",
                        "updated",
                        "removed",
                        "translated",
                        "remote",
                        "local",
                        "new",
                        "errors",
                        "source_revision",
                        "error",
                    )
                    if key in result
                },
                "status": status,
                "summary": _summary(result, task),
            }
            if module.get("kind") == "knowledge" and task.get("kind") == "knowledge_sync":
                knowledge_remaining = max(
                    0,
                    knowledge_remaining - len(result.get("translated") or []),
                )
        except LookupError as exc:
            report = {
                **base_report,
                "status": "blocked",
                "summary": str(exc)[:500],
                "error": str(exc)[:500],
            }
        except Exception as exc:  # noqa: BLE001
            report = {
                **base_report,
                "status": "failed",
                "summary": str(exc)[:500],
                "error": str(exc)[:500],
            }
        statuses[task_id] = str(report["status"])
        task_state[task_id] = {
            "last_run_at": current.isoformat(),
            "last_status": report["status"],
            "last_summary": report["summary"],
            "last_error": report.get("error"),
            "next_run_at": report.get("next_run_at"),
        }
        reports.append(report)

    state["updated_at"] = current.isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "schema": "radar-task-report/v1",
        "run_id": current.isoformat(),
        "updated_at": current.isoformat(),
        "jobs": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute due tasks from way-to-agentic Radar registry")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    target_root = Path(args.target_root).resolve()
    registry = load_registry(target_root)
    report = run_registered_tasks(target_root, registry, Path(args.state))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
