#!/usr/bin/env python3
"""Build the safe generated-path manifest consumed by way-to-agentic deploys."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _safe_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid export path: {value}")
    return path.as_posix()


def build_export_manifest(registry: dict[str, Any]) -> dict[str, Any]:
    paths = {"frontend/frontier/radar-data"}
    for module in registry.get("modules") or []:
        if not isinstance(module, dict):
            continue
        display = module.get("display") if isinstance(module.get("display"), dict) else {}
        if display.get("target_path"):
            paths.add(_safe_path(str(display["target_path"])))
        for task in module.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            output = task.get("output") if isinstance(task.get("output"), dict) else {}
            if output.get("path"):
                paths.add(_safe_path(str(output["path"])))
    return {
        "schema": "radar-export-manifest/v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "paths": sorted(paths),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a Radar export manifest")
    parser.add_argument("--registry-report", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.registry_report).read_text(encoding="utf-8"))
    registry = payload.get("registry") if isinstance(payload, dict) else {}
    if not isinstance(registry, dict):
        registry = {}
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_export_manifest(registry), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
