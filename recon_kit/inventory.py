"""Read, validate, normalize, and compare local inventory snapshots."""

import csv
import ipaddress
import re
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict

TRACKED_FIELDS = ("hostname", "ip", "owner")
COLUMNS = ("asset_id", *TRACKED_FIELDS)
ASSET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
# csv.reader(strict=True) still accepts bare quotes within unquoted fields.
CSV_FIELD = r'(?:[^",\r\n]*|"(?:[^"\r\n]|"")*")'
CSV_RECORD = re.compile(rf"{CSV_FIELD}(?:,{CSV_FIELD})*")


class SnapshotError(ValueError):
    """A snapshot could not be opened or did not meet the input contract."""


@dataclass(frozen=True, slots=True)
class Asset:
    asset_id: str
    hostname: str | None
    ip: str | None
    owner: str | None


class Report(TypedDict):
    schema_version: int
    counts: dict[str, int]
    added: list[dict[str, str | None]]
    removed: list[dict[str, str | None]]
    changed: list[dict[str, object]]


def _records(lines: Iterator[str]) -> Iterator[tuple[int, list[str]]]:
    for number, line in enumerate(lines, start=1):
        record = line.removesuffix("\n").removesuffix("\r")
        if not CSV_RECORD.fullmatch(record):
            raise SnapshotError(f"line {number}: invalid CSV quoting")
        try:
            row = next(csv.reader((record,), strict=True))
        except csv.Error:
            raise SnapshotError(f"line {number}: invalid CSV record") from None
        yield number, row


def _normalize(row: Mapping[str, str]) -> Asset:
    asset_id, hostname, ip, owner = (row[key].strip(" \t") for key in COLUMNS)
    if not ASSET_ID.fullmatch(asset_id):
        raise SnapshotError(
            "asset_id must be 1 to 128 ASCII letters, digits, dots, underscores, "
            "or hyphens, starting with a letter or digit"
        )

    if hostname:
        hostname = hostname.removesuffix(".")
        if len(hostname) > 253 or not all(
            DNS_LABEL.fullmatch(label) for label in hostname.split(".")
        ):
            raise SnapshotError("hostname must contain valid ASCII DNS labels")
        hostname = hostname.lower()

    if ip:
        if "%" in ip:
            raise SnapshotError("ip must not contain a zone identifier")
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            raise SnapshotError("ip must be an IPv4 or IPv6 address") from None

    if not (hostname or ip):
        raise SnapshotError("at least one of hostname or ip is required")
    if owner and (len(owner) > 200 or not owner.isprintable()):
        raise SnapshotError("owner must be at most 200 printable characters")

    return Asset(asset_id.lower(), hostname or None, ip or None, owner or None)


def load_snapshot(path: Path) -> dict[str, Asset]:
    """Load a UTF-8 CSV snapshot indexed by normalized asset ID."""
    assets: dict[str, Asset] = {}
    first_seen: dict[str, int] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            records = _records(stream)
            _, header = next(records, (0, []))
            if len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
                raise SnapshotError(
                    "line 1: expected exactly these headers: " + ", ".join(COLUMNS)
                )

            for number, row in records:
                if len(row) != len(header):
                    raise SnapshotError(
                        f"line {number}: expected {len(header)} fields, got {len(row)}"
                    )
                try:
                    asset = _normalize(dict(zip(header, row, strict=True)))
                except SnapshotError as exc:
                    raise SnapshotError(f"line {number}: {exc}") from None
                if asset.asset_id in first_seen:
                    first = first_seen[asset.asset_id]
                    raise SnapshotError(
                        f"line {number}: duplicate asset_id (first seen on line {first})"
                    )
                assets[asset.asset_id] = asset
                first_seen[asset.asset_id] = number
    except SnapshotError as exc:
        raise SnapshotError(f"{str(path)!r}: {exc}") from None
    except UnicodeError:
        raise SnapshotError(f"{str(path)!r}: expected UTF-8 text") from None
    except OSError as exc:
        raise SnapshotError(f"{str(path)!r}: {exc.strerror or 'read failed'}") from None
    return assets


def compare_snapshots(before: Mapping[str, Asset], after: Mapping[str, Asset]) -> Report:
    """Summarize drift in normalized records, ordered by asset ID."""
    added = [asdict(after[key]) for key in sorted(after.keys() - before.keys())]
    removed = [asdict(before[key]) for key in sorted(before.keys() - after.keys())]
    changed: list[dict[str, object]] = []

    for key in sorted(before.keys() & after.keys()):
        if before[key] == after[key]:
            continue
        old, new = asdict(before[key]), asdict(after[key])
        fields = {
            field: {"before": old[field], "after": new[field]}
            for field in TRACKED_FIELDS
            if old[field] != new[field]
        }
        changed.append({"asset_id": key, "fields": fields})

    return {
        "schema_version": 1,
        "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
        "added": added,
        "removed": removed,
        "changed": changed,
    }
