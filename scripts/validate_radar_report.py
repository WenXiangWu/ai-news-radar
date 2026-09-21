#!/usr/bin/env python3
"""Validate a Radar operational report against the Way-owned contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validate_radar_contract import validate_contract_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Radar report")
    parser.add_argument("--path", required=True)
    parser.add_argument(
        "--schema",
        required=True,
        choices=("source-validation", "update-report", "monitor", "report-index"),
    )
    args = parser.parse_args(argv)
    errors = validate_contract_file(Path(args.path), args.schema)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"validated {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
