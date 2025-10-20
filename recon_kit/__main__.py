"""Command-line entry point for recon-kit."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .inventory import Report, SnapshotError, compare_snapshots, load_snapshot


def _summary(report: Report) -> str:
    counts = report["counts"]
    lines = [
        f"Added: {counts['added']}  Removed: {counts['removed']}  Changed: {counts['changed']}"
    ]
    lines.extend(f"+ {asset['asset_id']}" for asset in report["added"])
    lines.extend(f"- {asset['asset_id']}" for asset in report["removed"])
    for asset in report["changed"]:
        fields = ", ".join(sorted(asset["fields"]))
        lines.append(f"~ {asset['asset_id']}: {fields}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recon-kit",
        description="Compare two local CSV asset snapshots and report drift.",
        epilog="Exit status: 0 unchanged, 1 drift detected, 2 usage or input error.",
    )
    parser.add_argument("before", type=Path, help="path to the older snapshot")
    parser.add_argument("after", type=Path, help="path to the newer snapshot")
    parser.add_argument(
        "--format", choices=("json", "summary"), default="json",
        help="report format (default: json)",
    )
    args = parser.parse_args(argv)

    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
    except SnapshotError as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2

    report = compare_snapshots(before, after)
    if args.format == "summary":
        print(_summary(report))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return int(any(report["counts"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
