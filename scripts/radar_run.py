#!/usr/bin/env python3
"""Run due Radar operations declared by a Way registry."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar_core.config import RuntimeConfig
from radar_core.discovery import (
    discover_due_control_task_operations,
    discover_due_operations,
    discover_due_task_operations,
    register_new_sources,
    register_new_tasks,
)
from radar_core.pipeline import RunContext, run_operation
from radar_core.registry import load_registry
from radar_core.storage import StateStore
from radar_core.task_runner import run_registered_task
from radar_core.translation.deepseek import DeepSeekProvider
from radar_core.translation.google import GoogleProvider
from radar_core.translation.router import TranslationRouter
from radar_core.verification import build_baseline_verification


class RadarOperationTimeout(BaseException):
    """Interrupt a connector without being swallowed by per-item handlers."""


@contextmanager
def _operation_deadline(seconds: float):
    """Install a process-local deadline for one synchronous operation."""

    try:
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
        def _alarm(_signum, _frame):
            raise RadarOperationTimeout()

        signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, max(0.01, float(seconds)))
    except (AttributeError, ValueError):
        # Signal timers are unavailable outside the main interpreter thread.
        # HTTP and subprocess adapters still have their own request limits.
        yield
        return

    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(
                signal.ITIMER_REAL,
                previous_timer[0],
                previous_timer[1],
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run due Radar operations")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-translation",
        action="store_true",
        help="Fetch and normalize only; never call DeepSeek or Google Translate",
    )
    parser.add_argument(
        "--max-new-items",
        type=int,
        default=None,
        help="Override registry max_new_items for this run",
    )
    parser.add_argument("--now", default="")
    parser.add_argument("--only-source", default="")
    parser.add_argument("--only-module", default="")
    parser.add_argument("--force", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--baseline-only",
        action="store_true",
        help="Discover and write ledger only; no fetch/translate",
    )
    mode.add_argument(
        "--bootstrap",
        action="store_true",
        help="Allow bootstrap fetch of new items (explicit only)",
    )
    parser.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=float(os.environ.get("RADAR_RUN_MAX_RUNTIME_MINUTES", "30")),
        help="Maximum wall-clock budget for the complete run; 0 disables the budget",
    )
    parser.add_argument(
        "--max-operation-runtime-minutes",
        type=float,
        default=float(os.environ.get("RADAR_OPERATION_MAX_RUNTIME_MINUTES", "8")),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target_root = Path(args.target_root).resolve()
    state_path = Path(args.state)
    report_path = Path(args.report)
    now = _parse_now(args.now)
    started_clock = time.perf_counter()
    started_at = now
    run_id = _run_id(now)
    config = RuntimeConfig.from_env(
        {
            **os.environ,
            "RADAR_WAY_ROOT": str(target_root),
            "RADAR_STATE_PATH": str(state_path),
        }
    )
    registry = load_registry(target_root)
    state = StateStore.open(state_path)
    state.record_run(run_id, {"status": "running", "dry_run": args.dry_run})
    results: list[dict[str, Any]] = []
    try:
        if not args.dry_run:
            register_new_sources(registry, state, run_id)
            register_new_tasks(registry, state, run_id)
        force_target = bool(args.force or args.only_source or args.only_module)
        operations = discover_due_operations(
            registry,
            now,
            state,
            force=force_target,
        )
        operations.extend(
            discover_due_task_operations(
                registry,
                now,
                state,
                force=force_target,
            )
        )
        operations = _unique_operations(operations)
        control_operations = discover_due_control_task_operations(
            registry,
            now,
            state,
            force=force_target,
        )
        if args.only_source:
            operations = [
                operation
                for operation in operations
                if operation.source_id == args.only_source
                or operation.task_id == args.only_source
            ]
            control_operations = [
                operation
                for operation in control_operations
                if operation.task.id == args.only_source
            ]
        if args.only_module:
            operations = [
                operation
                for operation in operations
                if str(
                    operation.source.payload.get("module_id")
                    or operation.task_id
                    or ""
                )
                == args.only_module
            ]
            control_operations = [
                operation
                for operation in control_operations
                if operation.task.module_id == args.only_module
            ]

        if args.bootstrap:
            run_mode = "bootstrap"
        elif args.baseline_only:
            run_mode = "baseline_only"
        else:
            run_mode = "incremental"
        context = RunContext(
            state=state,
            run_id=run_id,
            target_locales=config.target_locales,
            router=_router(config, skip_translation=args.skip_translation),
            now=now,
            dry_run=args.dry_run,
            mode=run_mode,
            skip_translation=args.skip_translation,
            max_new_items_override=args.max_new_items,
        )
        for index, operation in enumerate(operations):
            baseline_before = _baseline_snapshot(state, operation.source_id)
            if args.dry_run:
                payload = {
                    "source_id": operation.source_id,
                    "task_id": operation.task_id,
                    "status": "dry_run",
                    "scheduled_at": operation.scheduled_at.isoformat(),
                    "next_run_at": operation.next_run_at,
                    "adapter": operation.source.payload.get("adapter"),
                }
                results.append(
                    _decorate_source_result(
                        payload,
                        operation,
                        baseline_before,
                        state,
                    )
                )
                continue
            remaining = _remaining_budget_seconds(
                started_clock,
                args.max_runtime_minutes,
            )
            if remaining is not None and remaining <= 0:
                results.extend(
                    [
                        _deferred_source_result(
                            pending,
                            state,
                            reason="本轮运行预算已用尽，等待下一轮执行",
                        )
                        for pending in operations[index:]
                    ]
                )
                break
            results.append(
                _run_bounded_source_operation(
                    operation,
                    context,
                    baseline_before,
                    budget_seconds=remaining,
                    max_operation_runtime_minutes=args.max_operation_runtime_minutes,
                )
            )
        failed_task_ids = {
            str(result.get("task_id") or "")
            for result in results
            if str(result.get("status") or "") in {"failed", "partial"}
            and result.get("task_id")
        }
        ordered_control = _order_control_operations(control_operations)
        for index, operation in enumerate(ordered_control):
            remaining = _remaining_budget_seconds(
                started_clock,
                args.max_runtime_minutes,
            )
            if remaining is not None and remaining <= 0:
                results.extend(
                    [
                        _deferred_task_result(
                            pending,
                            reason="本轮运行预算已用尽，等待下一轮执行",
                        )
                        for pending in ordered_control[index:]
                    ]
                )
                break
            blocked_dependencies = [
                dependency
                for dependency in operation.task.depends_on
                if dependency in failed_task_ids
            ]
            if args.dry_run:
                results.append(
                    _decorate_task_result(
                        {
                            "task_id": operation.task.id,
                            "adapter": operation.task.adapter,
                            "status": "dry_run",
                            "summary": "已发现控制任务，未执行",
                            "scheduled_at": operation.scheduled_at.isoformat(),
                            "next_run_at": operation.next_run_at,
                        },
                        operation,
                    )
                )
                continue
            if blocked_dependencies:
                results.append(
                    _decorate_task_result(
                        {
                            "task_id": operation.task.id,
                            "adapter": operation.task.adapter,
                            "status": "blocked",
                            "summary": (
                                "依赖任务失败："
                                + ", ".join(blocked_dependencies)
                            ),
                            "scheduled_at": operation.scheduled_at.isoformat(),
                            "next_run_at": operation.next_run_at,
                        },
                        operation,
                    )
                )
                continue
            results.append(
                _run_bounded_task_operation(
                    operation,
                    state,
                    run_id,
                    target_root,
                    budget_seconds=remaining,
                    max_operation_runtime_minutes=args.max_operation_runtime_minutes,
                )
            )

        final_status = _final_status(results, dry_run=args.dry_run)
        state.record_run(
            run_id,
            {
                "status": final_status,
                "dry_run": args.dry_run,
                "operations": results,
                "discovery": registry.diagnostics,
            },
        )
        report = _build_report(
            run_id=run_id,
            status=final_status,
            now=now,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
            request_id=str(os.environ.get("RADAR_REQUEST_ID") or "").strip(),
            operations=results,
            diagnostics=registry.diagnostics,
            verification=build_baseline_verification(
                registry,
                state,
                operations=results,
            ),
            providers=_provider_status(config, results),
            dry_run=args.dry_run,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {report_path}", flush=True)
        return 1 if final_status in {"failed", "partial"} else 0
    except Exception as exc:  # noqa: BLE001
        message = _safe_error(exc)
        results.append(
            {
                "status": "failed",
                "summary": message,
                "error": message,
                "failed": 1,
                "errors": [message],
            }
        )
        try:
            state.record_run(
                run_id,
                {
                    "status": "failed",
                    "dry_run": args.dry_run,
                    "operations": results,
                    "discovery": registry.diagnostics,
                },
            )
        except Exception:
            pass
        report = _build_report(
            run_id=run_id,
            status="failed",
            now=now,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
            request_id=str(os.environ.get("RADAR_REQUEST_ID") or "").strip(),
            operations=results,
            diagnostics=registry.diagnostics,
            verification=_safe_verification(registry, state, results),
            providers=_provider_status(config, results),
            dry_run=args.dry_run,
        )
        _write_report(report_path, report)
        print(f"Radar run failed; wrote {report_path}: {message}", flush=True)
        return 1
    finally:
        state.close()


def _router(config: RuntimeConfig, *, skip_translation: bool = False) -> Any:
    if skip_translation:
        return _DisabledTranslationRouter()
    return TranslationRouter(
        deepseek=DeepSeekProvider(
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_api_base_url,
            model=config.deepseek_model,
            timeout_seconds=config.http_timeout_seconds,
        ),
        google=GoogleProvider(
            endpoint=config.google_translate_endpoint,
            timeout_seconds=config.http_timeout_seconds,
        ),
    )


class _DisabledTranslationRouter:
    def provider_order(self) -> tuple[str, ...]:
        return ()

    def translate(self, request: Any) -> Any:
        raise RuntimeError("translation disabled by --skip-translation")


def _parse_now(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_id(now: datetime) -> str:
    return f"run-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _unique_operations(operations: list[Any]) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for operation in sorted(
        operations,
        key=lambda item: (str(item.task_id or ""), item.source_id),
    ):
        if operation.id in seen:
            continue
        seen.add(operation.id)
        unique.append(operation)
    return unique


def _order_control_operations(operations: list[Any]) -> list[Any]:
    pending = {operation.task.id: operation for operation in operations}
    ordered: list[Any] = []
    while pending:
        ready = [
            operation
            for operation in pending.values()
            if all(dependency not in pending for dependency in operation.task.depends_on)
        ]
        if not ready:
            ready = sorted(pending.values(), key=lambda item: item.task.id)
        else:
            ready = sorted(ready, key=lambda item: item.task.id)
        for operation in ready:
            pending.pop(operation.task.id, None)
            ordered.append(operation)
    return ordered


def _final_status(results: list[dict[str, Any]], *, dry_run: bool) -> str:
    if dry_run:
        return "dry_run"
    statuses = {str(result.get("status") or "") for result in results}
    # A hard "failed" operation fails the whole run.
    if "failed" in statuses:
        return "failed"
    # `unsupported_incremental` is a per-operation failure: degrade the run to
    # partial so the report is still written and downstream steps continue,
    # but do not treat it as a hard run-level failure.
    # `partial` from fetch/translation failures also degrades the run.
    if "partial" in statuses or "unsupported_incremental" in statuses:
        return "partial"
    # Spec §6: `blocked` (budget exhaustion or dependency waiting) is not a
    # hard failure, but it must degrade the run to `partial` so downstream
    # steps know the budget was exhausted and the report is still written.
    # `deferred` (per-item cap) stays soft and does not by itself degrade.
    if "blocked" in statuses:
        return "partial"
    return "success"


def _remaining_budget_seconds(
    started_clock: float,
    max_runtime_minutes: float,
) -> float | None:
    if max_runtime_minutes <= 0:
        return None
    return (max_runtime_minutes * 60) - (time.perf_counter() - started_clock)


def _operation_timeout_seconds(
    operation: Any,
    *,
    budget_seconds: float | None = None,
    max_operation_runtime_minutes: float | None = None,
) -> float:
    if hasattr(operation, "task"):
        schedule = operation.task.schedule
    else:
        schedule = operation.schedule if hasattr(operation, "schedule") else {}
    raw_seconds = schedule.get("max_runtime_seconds")
    if raw_seconds is not None:
        try:
            seconds = max(0.01, float(raw_seconds))
        except (TypeError, ValueError):
            seconds = 30 * 60
    else:
        try:
            minutes = max(1.0, float(schedule.get("max_runtime_minutes") or 30))
        except (TypeError, ValueError):
            minutes = 30
        seconds = minutes * 60
    if max_operation_runtime_minutes is not None and max_operation_runtime_minutes > 0:
        seconds = min(seconds, max(0.01, float(max_operation_runtime_minutes) * 60))
    if budget_seconds is not None:
        seconds = min(seconds, max(0.01, budget_seconds))
    return seconds


def _run_bounded_source_operation(
    operation: Any,
    context: RunContext,
    baseline_before: dict[str, Any],
    *,
    budget_seconds: float | None = None,
    max_operation_runtime_minutes: float | None = None,
) -> dict[str, Any]:
    timeout_seconds = _operation_timeout_seconds(
        operation,
        budget_seconds=budget_seconds,
        max_operation_runtime_minutes=max_operation_runtime_minutes,
    )
    started_clock = time.perf_counter()
    print(
        f"Radar operation start: {operation.id} "
        f"source={operation.source_id} timeout={timeout_seconds:.2f}s",
        flush=True,
    )
    try:
        with _operation_deadline(timeout_seconds):
            result = run_operation(operation, context)
        payload = result.to_dict()
    except RadarOperationTimeout:
        payload = _source_failure_payload(
            operation,
            f"operation timed out after {timeout_seconds:.2f}s",
            started_clock,
        )
        payload["timeout_seconds"] = timeout_seconds
    except Exception as exc:  # noqa: BLE001
        payload = _source_failure_payload(
            operation,
            _safe_error(exc),
            started_clock,
        )
    decorated = _decorate_source_result(
        payload,
        operation,
        baseline_before,
        context.state,
    )
    print(
        f"Radar operation finish: {operation.id} status={decorated.get('status')}",
        flush=True,
    )
    return decorated


def _run_bounded_task_operation(
    operation: Any,
    state: StateStore,
    run_id: str,
    target_root: Path,
    *,
    budget_seconds: float | None = None,
    max_operation_runtime_minutes: float | None = None,
) -> dict[str, Any]:
    timeout_seconds = _operation_timeout_seconds(
        operation,
        budget_seconds=budget_seconds,
        max_operation_runtime_minutes=max_operation_runtime_minutes,
    )
    started_clock = time.perf_counter()
    print(
        f"Radar task start: {operation.task.id} timeout={timeout_seconds:.2f}s",
        flush=True,
    )
    try:
        with _operation_deadline(timeout_seconds):
            result = run_registered_task(
                operation,
                state,
                run_id,
                target_root,
            )
        payload = result.to_dict()
    except RadarOperationTimeout:
        payload = {
            "task_id": operation.task.id,
            "adapter": operation.task.adapter,
            "status": "failed",
            "summary": (
                f"控制任务超时（{timeout_seconds:.2f}s），等待下一轮重试"
            ),
            "error": f"task timed out after {timeout_seconds:.2f}s",
        }
    except Exception as exc:  # noqa: BLE001
        message = _safe_error(exc)
        payload = {
            "task_id": operation.task.id,
            "adapter": operation.task.adapter,
            "status": "failed",
            "summary": message,
            "error": message,
        }
    payload["duration_ms"] = max(
        0,
        int((time.perf_counter() - started_clock) * 1000),
    )
    decorated = _decorate_task_result(payload, operation)
    print(
        f"Radar task finish: {operation.task.id} status={decorated.get('status')}",
        flush=True,
    )
    return decorated


def _source_failure_payload(
    operation: Any,
    error: str,
    started_clock: float,
) -> dict[str, Any]:
    return {
        "source_id": operation.source_id,
        "status": "failed",
        "ok": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0, int((time.perf_counter() - started_clock) * 1000)),
        "discovered": 0,
        "fetched": 0,
        "normalized": 0,
        "revisions_new": 0,
        "revisions_reused": 0,
        "translated": 0,
        "translation_reused": 0,
        "quality_failed": 0,
        "failed": 1,
        "errors": [str(error)[:500]],
        "provider_counts": {},
        "provider_failures": {},
        "provider_fallbacks": {},
        "cursor": {},
        "updated_content_ids": [],
        "translated_content_ids": [],
    }


def _deferred_source_result(
    operation: Any,
    state: StateStore,
    *,
    reason: str,
) -> dict[str, Any]:
    payload = _source_failure_payload(operation, reason, time.perf_counter())
    payload["status"] = "blocked"
    payload["error"] = reason
    return _decorate_source_result(
        payload,
        operation,
        _baseline_snapshot(state, operation.source_id),
        state,
        )


def _deferred_task_result(operation: Any, *, reason: str) -> dict[str, Any]:
    return _decorate_task_result(
        {
            "task_id": operation.task.id,
            "adapter": operation.task.adapter,
            "status": "blocked",
            "summary": reason,
            "error": reason,
        },
        operation,
    )


def _safe_verification(
    registry: Any,
    state: StateStore,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return build_baseline_verification(registry, state, operations=operations)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": _safe_error(exc), "sources": []}


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_report(
    *,
    run_id: str,
    status: str,
    now: datetime,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    request_id: str,
    operations: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    verification: dict[str, Any],
    providers: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    counts = {
        key: sum(int(result.get(key) or 0) for result in operations)
        for key in (
            "discovered",
            "fetched",
            "normalized",
            "revisions_new",
            "revisions_reused",
            "translated",
            "translation_reused",
            "quality_failed",
            "failed",
        )
    }
    return {
        "schema": "radar-run-report/v1",
        "run_id": run_id,
        "status": status,
        "dry_run": dry_run,
        "updated_at": now.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": max(0, int(duration_ms)),
        "trigger_request_id": request_id or None,
        "counts": counts,
        "provider_counts": _aggregate_counts(operations, "provider_counts"),
        "provider_failures": _aggregate_counts(operations, "provider_failures"),
        "provider_fallbacks": _aggregate_counts(operations, "provider_fallbacks"),
        "providers": providers,
        "operations": operations,
        "diagnostics": diagnostics,
        "verification": verification,
    }


def _aggregate_counts(
    operations: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for operation in operations:
        values = operation.get(field)
        if not isinstance(values, dict):
            continue
        for provider, value in values.items():
            try:
                counts[str(provider)] = counts.get(str(provider), 0) + int(value or 0)
            except (TypeError, ValueError):
                continue
    return counts


def _provider_status(
    config: RuntimeConfig,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = _aggregate_counts(operations, "provider_counts")
    failures = _aggregate_counts(operations, "provider_failures")
    fallbacks = _aggregate_counts(operations, "provider_fallbacks")
    providers = [
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "configured": bool(config.deepseek_api_key),
            "model": config.deepseek_model,
            "base_url": _safe_endpoint(config.deepseek_api_base_url),
        },
        {
            "id": "google",
            "name": "Google Translate",
            "configured": bool(config.google_translate_endpoint),
            "model": "google-translate-gtx",
            "base_url": _safe_endpoint(config.google_translate_endpoint),
        },
    ]
    for provider in providers:
        provider_id = provider["id"]
        provider.update(
            {
                "status": (
                    "configured"
                    if provider["configured"]
                    else "not_configured"
                ),
                "success_count": counts.get(provider_id, 0),
                "failure_count": failures.get(provider_id, 0),
                "fallback_count": fallbacks.get(provider_id, 0),
            }
        )
    return providers


def _safe_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(str(value or ""))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return ""


def _baseline_snapshot(state: StateStore, source_id: str) -> dict[str, Any]:
    registration = state.get_source_registration(source_id)
    cursor = state.get_cursor(source_id)
    return {
        "baseline_fingerprint": (
            registration.get("baseline_fingerprint")
            if isinstance(registration, dict)
            else None
        ),
        "staged_fingerprint": (
            registration.get("staged_fingerprint")
            if isinstance(registration, dict)
            else None
        ),
        "cursor_present": cursor is not None,
        "cursor_run_id": (
            cursor.get("run_id") if isinstance(cursor, dict) else None
        ),
    }


def _decorate_source_result(
    payload: dict[str, Any],
    operation: Any,
    baseline_before: dict[str, Any],
    state: StateStore,
) -> dict[str, Any]:
    source = operation.source
    source_payload = source.payload
    after = _baseline_snapshot(state, operation.source_id)
    payload.update(
        {
            "task_id": operation.task_id,
            "module_id": str(
                source_payload.get("module_id")
                or operation.task_id
                or operation.source_id
            ),
            "module_name": str(
                source_payload.get("module_name")
                or source_payload.get("name")
                or operation.source_id
            ),
            "adapter": str(
                source_payload.get("adapter")
                or source_payload.get("connector")
                or ""
            ),
            "scheduled_at": operation.scheduled_at.isoformat(),
            "next_run_at": operation.next_run_at,
            "baseline_before": baseline_before,
            "baseline_after": after,
            "baseline_committed": (
                after["baseline_fingerprint"]
                and after["baseline_fingerprint"]
                != baseline_before.get("baseline_fingerprint")
            )
            or (
                after["baseline_fingerprint"]
                and after["baseline_fingerprint"]
                == source.registry_fingerprint
            ),
            "cursor_advanced": (
                after["cursor_run_id"] != baseline_before.get("cursor_run_id")
            ),
        }
    )
    return payload


def _decorate_task_result(payload: dict[str, Any], operation: Any) -> dict[str, Any]:
    task = operation.task
    payload.update(
        {
            "module_id": task.module_id,
            "module_name": task.module_name or task.module_id,
            "kind": task.kind,
            "source_id": task.source_id,
        }
    )
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
