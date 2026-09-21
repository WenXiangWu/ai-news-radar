from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .discovery import TaskOperation
from .registry import TaskSpec
from .storage import StateStore


CONTROL_TASK_ADAPTERS = frozenset(
    {
        "markdown_google",
        "coding_tools_catalog",
        "qdrant_editorial_catalog",
        "framework_hubs",
        "manual_editorial",
    }
)


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    adapter: str
    status: str
    summary: str
    scheduled_at: str
    next_run_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "task_id": self.task_id,
            "adapter": self.adapter,
            "status": self.status,
            "summary": self.summary,
            "scheduled_at": self.scheduled_at,
            "next_run_at": self.next_run_at,
        }
        if self.error:
            result["error"] = self.error
        return result


def run_registered_task(
    operation: TaskOperation,
    state: StateStore,
    run_id: str,
    target_root: Path,
) -> TaskRunResult:
    """Run a non-fetch task registered by Way.

    Translation tasks are intentionally completed by the source pipeline's
    TranslationRouter. Index tasks invoke only the fixed Way projection
    commands declared by the adapter map; arbitrary shell commands are never
    accepted from a manifest.
    """

    task = operation.task
    try:
        if task.adapter not in CONTROL_TASK_ADAPTERS:
            raise LookupError(f"unknown control task adapter: {task.adapter}")
        if task.adapter == "markdown_google":
            result = TaskRunResult(
                task_id=task.id,
                adapter=task.adapter,
                status="skipped",
                summary="由统一 TranslationRouter 随来源流水线完成翻译",
                scheduled_at=operation.scheduled_at.isoformat(),
                next_run_at=operation.next_run_at,
            )
        elif task.adapter == "manual_editorial":
            result = TaskRunResult(
                task_id=task.id,
                adapter=task.adapter,
                status="skipped",
                summary="人工维护任务已登记，Radar 不覆盖导读内容",
                scheduled_at=operation.scheduled_at.isoformat(),
                next_run_at=operation.next_run_at,
            )
        else:
            output = _run_index_command(task, Path(target_root))
            result = TaskRunResult(
                task_id=task.id,
                adapter=task.adapter,
                status="success",
                summary=output or "索引投影任务执行完成",
                scheduled_at=operation.scheduled_at.isoformat(),
                next_run_at=operation.next_run_at,
            )
        _advance_task_cursor(operation, state, run_id)
        return result
    except Exception as exc:  # noqa: BLE001
        return TaskRunResult(
            task_id=task.id,
            adapter=task.adapter,
            status="failed",
            summary=str(exc)[:500] or type(exc).__name__,
            scheduled_at=operation.scheduled_at.isoformat(),
            next_run_at=operation.next_run_at,
            error=str(exc)[:500] or type(exc).__name__,
        )


def _run_index_command(task: TaskSpec, target_root: Path) -> str:
    command_by_adapter = {
        "coding_tools_catalog": (
            "scripts/build_coding_tools_catalogs.py",
        ),
        "qdrant_editorial_catalog": (
            "scripts/build_qdrant_editorial_catalog.py",
        ),
        "framework_hubs": (
            "scripts/wiki_sync.py",
            "patch-hubs",
        ),
    }
    command = command_by_adapter.get(task.adapter)
    if command is None:
        raise LookupError(f"no index command for adapter: {task.adapter}")
    script = target_root / command[0]
    if not script.is_file():
        raise FileNotFoundError(f"registered task script is missing: {script}")
    process = subprocess.run(
        [sys.executable, *command],
        cwd=str(target_root),
        capture_output=True,
        text=True,
        timeout=max(1, int(task.schedule.get("max_runtime_minutes") or 30)) * 60,
        check=False,
    )
    if process.returncode:
        message = (process.stderr or process.stdout or "index task failed").strip()
        raise RuntimeError(message[:500])
    return (process.stdout or process.stderr or "").strip()[-500:]


def _advance_task_cursor(
    operation: TaskOperation,
    state: StateStore,
    run_id: str,
) -> None:
    state.record_run(
        run_id,
        {
            "status": "success",
            "task_id": operation.task.id,
            "adapter": operation.task.adapter,
        },
    )
    state.advance_cursor(
        operation.task.id,
        {
            "last_scheduled_at": operation.scheduled_at.isoformat(),
            "next_run_at": operation.next_run_at,
        },
        run_id,
    )


__all__ = [
    "CONTROL_TASK_ADAPTERS",
    "TaskRunResult",
    "run_registered_task",
]
