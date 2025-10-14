"""Command-line entry point for recon-kit."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .inventory import SnapshotError, compare_snapshots, load_snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recon-kit",
        description="Compare two local CSV asset snapshots and print JSON drift.",
        epilog="Exit status: 0 unchanged, 1 drift detected, 2 usage or input error.",
    )
    parser.add_argument("before", type=Path, help="path to the older snapshot")
    parser.add_argument("after", type=Path, help="path to the newer snapshot")
    args = parser.parse_args(argv)

    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
    except SnapshotError as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2

    report = compare_snapshots(before, after)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(any(report["counts"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
