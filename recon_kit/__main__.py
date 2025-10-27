"""Command-line entry point for recon-kit."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .inventory import (
    TRACKED_FIELDS,
    ComparisonError,
    Report,
    SnapshotError,
    compare_snapshots,
    load_snapshot,
)


def _counts_line(counts: dict[str, int]) -> str:
    return f"Added: {counts['added']}  Removed: {counts['removed']}  Changed: {counts['changed']}"


def _summary(report: Report) -> str:
    counts_line = _counts_line(report["counts"])
    if scope := report.get("scope"):
        selection = f"Scope: asset ID prefix {scope['asset_id_prefix']!r}"
        matches = f"before: {scope['matched_before']}, after: {scope['matched_after']}"
        lines = [
            f"{selection} ({matches})",
            f"In scope: {counts_line}",
            f"Outside scope: {_counts_line(scope['outside_counts'])}",
        ]
    else:
        lines = [counts_line]
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
    parser.add_argument(
        "--ignore-field", action="append", choices=TRACKED_FIELDS, default=[],
        help="ignore changes to this field on shared assets (repeatable)",
    )
    parser.add_argument(
        "--asset-id-prefix", metavar="PREFIX",
        help="show details for IDs starting with PREFIX; count other drift separately",
    )
    args = parser.parse_args(argv)

    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
        report = compare_snapshots(
            before, after, ignore_fields=args.ignore_field, asset_id_prefix=args.asset_id_prefix,
        )
    except (ComparisonError, SnapshotError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2

    if args.format == "summary":
        print(_summary(report))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    scope = report.get("scope")
    outside_counts = scope["outside_counts"] if scope else {}
    return int(any(report["counts"].values()) or any(outside_counts.values()))


if __name__ == "__main__":
    raise SystemExit(main())
