"""Tell a workflow whether this wake-up should do work, and whether DeepSeek is on."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime_config import deepseek_enabled, load_runtime, should_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--event", default="schedule")
    parser.add_argument("--config", default="data/radar-runtime.json")
    parser.add_argument("--now", default="")
    args = parser.parse_args(argv)
    config = load_runtime(Path(args.config))
    now = _parse_now(args.now)
    run = should_run(args.workflow, config, event=args.event, now=now)
    print(f"run={'true' if run else 'false'}")
    print(f"deepseek={'1' if deepseek_enabled(config) else '0'}")
    if not run:
        print(f"skip {args.workflow}: this scheduled wake is outside the runtime slots", file=sys.stderr, flush=True)
    return 0


def _parse_now(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
