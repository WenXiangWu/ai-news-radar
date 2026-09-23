#!/usr/bin/env python3
"""Write a radar-workflow-status/v1 file."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECRET = re.compile(r"(?i)(api[_-]?key|token|secret)")
CONCLUSIONS = {"success", "failure", "cancelled"}


def write_workflow_status(
    path: Path,
    payload: dict[str, Any],
    *,
    run_id: str,
    html_url: str,
) -> None:
    workflow_id = str(payload.get("workflow_id") or "").strip()
    conclusion = str(payload.get("conclusion") or "").strip()
    push = payload.get("push")
    sources = payload.get("sources")
    if not workflow_id:
        raise ValueError("workflow_id is required")
    if conclusion not in CONCLUSIONS:
        raise ValueError("conclusion must be success, failure, or cancelled")
    if not isinstance(push, dict) or "attempted" not in push:
        raise ValueError("push.attempted is required")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "schema": "radar-workflow-status/v1",
        "workflow_id": workflow_id,
        "name": payload.get("name") or workflow_id,
        "schedule": payload.get("schedule") or "",
        "combined_lanes": list(payload.get("combined_lanes") or []),
        "groups": list(payload.get("groups") or []),
        "run_id": run_id,
        "html_url": html_url,
        "conclusion": conclusion,
        "started_at": payload.get("started_at") or now,
        "finished_at": payload.get("finished_at") or now,
        "push": push,
        "sources": sources,
    }
    _strip_secrets(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--html-url", default="")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    write_workflow_status(
        Path(args.out),
        payload,
        run_id=args.run_id,
        html_url=args.html_url,
    )
    return 0


def _strip_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if SECRET.search(str(key)):
                del value[key]
                continue
            if isinstance(value[key], str) and SECRET.search(value[key]):
                del value[key]
                continue
            _strip_secrets(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_secrets(item)


if __name__ == "__main__":
    raise SystemExit(main())
