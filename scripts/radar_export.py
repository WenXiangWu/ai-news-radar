#!/usr/bin/env python3
"""Build and verify a Radar content export bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar_core.export import build_export_bundle, verify_export_bundle
from radar_core.storage import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Radar export bundle")
    parser.add_argument("--state", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    state = StateStore.open(Path(args.state))
    try:
        build_export_bundle(args.run_id, state, Path(args.out))
    finally:
        state.close()
    errors = verify_export_bundle(Path(args.out))
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"verified {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
