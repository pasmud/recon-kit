# recon-kit

**Compare two local snapshots of assets you own.** recon-kit reads CSV exports and
reports which assets were added, removed, or changed. It matches by a stable
asset ID, so an address or ownership change stays attached to the same asset.
It does not discover assets, resolve names, scan hosts, or make network requests.

## Quickstart

From the repository root, use Python 3.11 or newer. No installation or third-party
packages are needed.

```sh
python3 -m recon_kit examples/before.csv examples/after.csv
```

The bundled snapshots are synthetic. This example exits with status `1` and prints:

```json
{
  "added": [
    {
      "asset_id": "worker-01",
      "hostname": "worker.example.test",
      "ip": "198.51.100.30",
      "owner": null
    }
  ],
  "changed": [
    {
      "asset_id": "app-01",
      "fields": {
        "owner": {
          "after": "Application Services",
          "before": "Platform"
        }
      }
    }
  ],
  "counts": {
    "added": 1,
    "changed": 1,
    "removed": 1
  },
  "removed": [
    {
      "asset_id": "retired-01",
      "hostname": "old.example.test",
      "ip": "192.0.2.30",
      "owner": "Operations"
    }
  ],
  "schema_version": 1
}
```

For your own exports, pass the older file first. In a POSIX shell, this example
saves the report and handles drift explicitly (including under `set -e`):

```sh
status=0
python3 -m recon_kit previous.local.csv current.local.csv > drift.local.json || status=$?
case "$status" in
  0) printf '%s\n' 'No drift' ;;
  1) printf '%s\n' 'Drift detected; see drift.local.json' ;;
  *) printf '%s\n' 'Comparison failed; see stderr' >&2; exit "$status" ;;
esac
```

| Status | Meaning |
| --- | --- |
| `0` | Valid snapshots, no drift. The report has zero counts. |
| `1` | Valid snapshots, drift detected. The report is still written. |
| `2` | Invalid usage or input. Diagnostics go to stderr; no report is written. |

Both snapshots must validate before a report is printed. Row errors include the
file path and one-based physical line number, counting the header as line 1.
Duplicate ID errors also give the first occurrence's line. Unreadable files and
invalid UTF-8 are reported at file level. `python3 -m recon_kit --help` shows usage.

## Text summary

JSON is the default. For a shorter view, use `--format summary`:

```sh
python3 -m recon_kit --format summary examples/before.csv examples/after.csv
```

```text
Added: 1  Removed: 1  Changed: 1
+ worker-01
- retired-01
~ app-01: owner
```

The summary lists asset IDs in order within each group, with alphabetized field
names for changed assets. Use JSON for before and after values. Both formats use
the same exit statuses.

## Snapshot format

```csv
asset_id,hostname,ip,owner
mail-01,MAIL.Example.Test.,192.0.2.15,"Operations, East"
cache-02,,2001:db8::4,
```

- Use comma-delimited UTF-8 CSV (an optional BOM is accepted). The four headers
  must appear exactly once, in any order, with the spelling shown above. Quote
  fields containing commas, and double a literal quote inside a quoted field.
  Each record occupies one line; blank lines and multiline fields are rejected.
  A header-only snapshot represents zero assets; a zero-byte file is invalid.
- Each record (including the header) is limited to 4,096 Unicode characters
  before parsing, including quotes, delimiters, and padding but excluding the
  optional BOM and line ending. Each snapshot may contain up to 10,000 data rows.
  Exceeding either limit is an input error, not a truncated comparison.
- Leading and trailing ASCII spaces and tabs are trimmed from data values.
  Provide at least one of `hostname` and `ip`. Blank `hostname`, `ip`, and
  `owner` values become JSON `null`.

| Field | Validation and normalization |
| --- | --- |
| `asset_id` | 1–128 ASCII letters, digits, dots, underscores, or hyphens, starting with a letter or digit. Lowercased and unique within each snapshot. Keep this ID stable across exports. |
| `hostname` | ASCII DNS labels, at most 63 characters each and 253 total after removing one optional final dot. Labels begin and end with a letter or digit and may have internal hyphens. Single-label names are accepted. Lowercased. |
| `ip` | One IPv4 or IPv6 address, written in canonical form in the report. No prefixes, ports, brackets, or IPv6 zone identifiers. IPv4 and IPv4-mapped IPv6 are distinct. |
| `owner` | At most 200 printable Unicode characters. Case and internal spacing are preserved. |

Added and removed records contain all normalized fields. Changed records include
only the fields that differ, each with `before` and `after` values. The counts are
counts of assets, not fields. Arrays are sorted by normalized asset ID; JSON
object keys are also sorted. Equivalent snapshots produce the same report
regardless of row order, column order, or input file name.

## Limitations

This compares what the two exports say. It cannot tell whether a removed row
means the asset was retired or just omitted from an export. Changing an asset ID
shows as a removal and an addition, even if its hostname or IP is unchanged.
Repeated hostnames and addresses are allowed because they are not identity keys.
Both snapshots and the report are held in memory; the CLI is intended for
inventories that fit comfortably in memory. Reports contain inventory data, so
store them alongside the snapshots with the same access controls. Local files
ending in `.local.csv` or `.local.json` are ignored by Git in this repository.

## Verification

Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
git diff --check
git show --format= --check HEAD
```

CI runs the same test suite on Python 3.11 and 3.14 for pushes and pull requests.
Tests use synthetic data and do not require network access.

The October 2025 commit timeline is illustrative portfolio metadata and is not
evidence that the work was performed on those dates.

Released under the [MIT License](LICENSE).
