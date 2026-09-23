#!/usr/bin/env python3
"""Select ready pending-publish files and mark them published."""

from __future__ import annotations

import json
from pathlib import Path


def publish_pending(directory: Path, *, now: str) -> list[Path]:
    pending = Path(directory)
    selected: list[Path] = []
    for path in sorted(pending.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("ready") is not True:
            continue
        payload["ready"] = False
        payload["published_at"] = now
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        selected.append(path)
    return selected


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--now", default="")
    args = parser.parse_args(argv)
    now = args.now or datetime.now(timezone.utc).isoformat()
    selected = publish_pending(Path(args.directory), now=now)
    print(len(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
