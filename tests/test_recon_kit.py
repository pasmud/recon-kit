"""Contract tests for snapshot validation and the offline CLI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from recon_kit.inventory import Asset, SnapshotError, compare_snapshots, load_snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
HEADER = "asset_id,hostname,ip,owner\n"


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(
            tempfile.TemporaryDirectory(prefix=".recon-kit-test-", dir=ROOT)
        )
        self.path = Path(directory) / "snapshot.csv"

    def load(self, text):
        self.path.write_bytes(text.encode("utf-8"))
        return load_snapshot(self.path)

    def assert_invalid(self, text, diagnostic):
        with self.assertRaises(SnapshotError) as caught:
            self.load(text)
        self.assertIn(self.path.name, str(caught.exception))
        self.assertIn(diagnostic, str(caught.exception))

    def test_normalizes_reordered_bom_csv_with_quotes_and_no_final_newline(self):
        result = self.load(
            '\ufeffowner,ip,asset_id,hostname\r\n'
            '"  Équipe, ""Blue""  ", 2001:0DB8:0:0:0:0:0:42 , NODE_1 , API.Example.Test. '
        )
        self.assertEqual(result, {
            "node_1": Asset("node_1", "api.example.test", "2001:db8::42", 'Équipe, "Blue"'),
        })

    def test_optional_values_and_repeated_addresses(self):
        result = self.load(HEADER + "node1,,192.0.2.7,\nnode2,,192.0.2.7, \t\nnode3,lab,,\n")
        self.assertEqual(result, {
            "node1": Asset("node1", None, "192.0.2.7", None),
            "node2": Asset("node2", None, "192.0.2.7", None),
            "node3": Asset("node3", "lab", None, None),
        })

    def test_header_only_is_valid(self):
        self.assertEqual(self.load(HEADER), {})

    def test_rejects_invalid_headers(self):
        for header in (
            "", "\n", "asset_id,hostname,ip\n", "asset_id,hostname,ip,owners\n",
            "asset_id,hostname,ip,ip\n", "asset_id,hostname,ip,owner,owner\n",
            "ASSET_ID,hostname,ip,owner\n", "asset_id,hostname,ip, owner\n",
        ):
            with self.subTest(header=header):
                self.assert_invalid(header, "line 1: expected exactly these headers")

    def test_rejects_malformed_records(self):
        for row, diagnostic in (
            ("\n", "expected 4 fields, got 0"),
            ("a,host,,\n\n", "line 3: expected 4 fields, got 0"),
            ("a,host,\n", "expected 4 fields, got 3"),
            ("a,host,,x,y\n", "expected 4 fields, got 5"),
            ('a,host,,"unclosed\n', "line 2: invalid CSV quoting"),
            ('a,host,,"a\nb"\n', "line 2: invalid CSV quoting"),
            ('a,host,,"x"y\n', "line 2: invalid CSV quoting"),
            ('a,host,,x"y\n', "line 2: invalid CSV quoting"),
        ):
            with self.subTest(row=row):
                self.assert_invalid(HEADER + row, diagnostic)

    def test_rejects_invalid_values(self):
        for row, field in (
            (",host,,", "asset_id"),
            ("-node,host,,", "asset_id"),
            ("bad node,host,,", "asset_id"),
            ("Key,host,,", "asset_id"),
            ("a,,,", "at least one of hostname or ip"),
            ("a,.,192.0.2.7,", "hostname"),
            ("a,host..example,,", "hostname"),
            ("a,host.example..,,", "hostname"),
            ("a,-host.example,,", "hostname"),
            ("a,host_.example,,", "hostname"),
            ("a,Key.example,,", "hostname"),
            ("a,host,192.0.2.999,", "ip"),
            ("a,,192.0.02.7,", "ip"),
            ("a,,192.0.2.7/24,", "ip"),
            ("a,,2001:db8::1%test,", "zone identifier"),
            ("a,,[2001:db8::1],", "ip"),
            ("a,host,,Blue\x00Team", "owner"),
            ("a,host,,Blue\tTeam", "owner"),
        ):
            with self.subTest(row=row):
                self.assert_invalid(HEADER + row, field)

    def test_accepts_size_boundaries(self):
        asset_id = "A" * 128
        hostname = ".".join(["B" * 63] * 3 + ["C" * 61])
        owner = "é" * 200
        result = self.load(HEADER + f"{asset_id},{hostname}.,,{owner}")
        self.assertEqual(result, {
            asset_id.lower(): Asset(asset_id.lower(), hostname.lower(), None, owner),
        })

    def test_rejects_values_above_size_boundaries(self):
        for row, field in (
            (f"{'a' * 129},host,,", "asset_id"),
            (f"a,{'a' * 64}.example,,", "hostname"),
            ("a," + ".".join(["a" * 63] * 3 + ["b" * 62]) + ",,", "hostname"),
            (f"a,host,,{'é' * 201}", "owner"),
        ):
            with self.subTest(field=field, row=row):
                self.assert_invalid(HEADER + row, field)

    def test_record_size_counts_characters_without_bom_or_line_ending(self):
        row = "a,host,," + " " * (4096 - len("a,host,,") - 1) + "é"
        for ending in ("", "\n", "\r\n", "\r"):
            with self.subTest(ending=ending):
                header = "\ufeff" + HEADER.rstrip("\n") + (ending or "\n")
                self.assertEqual(self.load(header + row + ending)["a"].owner, "é")
                self.assert_invalid(header + row + " " + ending,
                                    "line 2: CSV record exceeds 4096 characters")

    def test_record_size_is_checked_before_parsing_the_header(self):
        self.assert_invalid('"' * 5000, "line 1: CSV record exceeds 4096 characters")

    def test_snapshot_row_limit_excludes_header(self):
        text = HEADER + "".join(f"node-{i},host,,\n" for i in range(10_000))
        self.assertEqual(len(self.load(text)), 10_000)
        self.assert_invalid(text + "extra,host,,", "line 10002: snapshot exceeds 10000 data rows")

    def test_rejects_duplicate_normalized_id_with_both_lines(self):
        self.assert_invalid(
            HEADER + "node-a,host,,\nnode-b,,192.0.2.3,\n NODE-A ,other,,\n",
            "line 4: duplicate asset_id (first seen on line 2)",
        )

    def test_rejects_invalid_utf8(self):
        self.path.write_bytes(HEADER.encode("ascii") + b"a,host,,\xff\n")
        with self.assertRaisesRegex(SnapshotError, "snapshot.csv.*expected UTF-8 text"):
            load_snapshot(self.path)


class ComparisonTests(unittest.TestCase):
    def test_cannot_ignore_identity_in_python_api(self):
        with self.assertRaisesRegex(ValueError, "cannot ignore fields: asset_id"):
            compare_snapshots({}, {}, ignore_fields=("asset_id",))


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(
            tempfile.TemporaryDirectory(prefix=".recon-kit-test-", dir=ROOT)
        )
        self.directory = Path(directory)

    def snapshot(self, name, *rows):
        path = self.directory / name
        path.write_text(HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
        return path

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "recon_kit", *args],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
        )

    def test_example_reports_added_removed_and_changed(self):
        result = self.run_cli(EXAMPLES / "before.csv", EXAMPLES / "after.csv")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 1,
            "counts": {"added": 1, "removed": 1, "changed": 1},
            "added": [
                {"asset_id": "worker-01", "hostname": "worker.example.test",
                 "ip": "198.51.100.30", "owner": None},
            ],
            "removed": [
                {"asset_id": "retired-01", "hostname": "old.example.test",
                 "ip": "192.0.2.30", "owner": "Operations"},
            ],
            "changed": [
                {"asset_id": "app-01", "fields": {
                    "owner": {"before": "Platform", "after": "Application Services"},
                }},
            ],
        })

    def test_reports_all_changed_fields_including_cleared_values(self):
        before = self.snapshot("old.csv", "example,old.example.test,192.0.2.2,Ops")
        after = self.snapshot("new.csv", "EXAMPLE,new.example.test,,ops")
        result = self.run_cli(before, after)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], [
            {"asset_id": "example", "fields": {
                "hostname": {"before": "old.example.test", "after": "new.example.test"},
                "ip": {"before": "192.0.2.2", "after": None},
                "owner": {"before": "Ops", "after": "ops"},
            }},
        ])

    def test_summary_reports_counts_and_changed_field_names_without_values(self):
        result = self.run_cli(
            "--format", "summary", EXAMPLES / "before.csv", EXAMPLES / "after.csv",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout, "Added: 1  Removed: 1  Changed: 1\n"
            "+ worker-01\n- retired-01\n~ app-01: owner\n",
        )

    def test_empty_summary_reports_zero_counts_and_success(self):
        snapshot = self.snapshot("same.csv", "a,host,,")
        result = self.run_cli("--format", "summary", snapshot, snapshot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Added: 0  Removed: 0  Changed: 0\n")

    def test_ignoring_only_difference_returns_success(self):
        before = self.snapshot("old.csv", "a,host,,Ops")
        after = self.snapshot("new.csv", "a,host,,New Team")
        result = self.run_cli("--ignore-field", "owner", before, after)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"], {"added": 0, "removed": 0, "changed": 0})
        self.assertEqual(report["changed"], [])

    def test_repeatable_ignore_fields_preserve_other_drift_and_membership(self):
        before = self.snapshot(
            "old.csv", "a,host,192.0.2.1,Ops", "b,old-host,,Ops", "gone,host,,Old",
        )
        after = self.snapshot(
            "new.csv", "a,host,192.0.2.2,New", "b,new-host,,New", "added,host,,New",
        )
        result = self.run_cli("--ignore-field", "owner", "--ignore-field", "ip", before, after)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"], {"added": 1, "removed": 1, "changed": 1})
        self.assertEqual(report["changed"], [
            {"asset_id": "b", "fields": {"hostname": {"before": "old-host", "after": "new-host"}}},
        ])
        self.assertEqual(report["added"][0]["owner"], "New")
        self.assertEqual(report["removed"][0]["owner"], "Old")

    def test_ignored_fields_are_still_validated(self):
        before = self.snapshot("old.csv", "a,host,,Ops")
        after = self.snapshot("new.csv", "a,host,," + "a" * 201)
        result = self.run_cli("--ignore-field", "owner", before, after)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("line 2: owner must be at most 200 printable characters", result.stderr)

    def test_equivalent_snapshots_return_zero(self):
        before = self.snapshot("old.csv", "N-1,API.Example.Test.,2001:0DB8::0010,Ops")
        after = self.snapshot("new.csv", "n-1,api.example.test,2001:db8::10, Ops ")
        result = self.run_cli(before, after)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 1,
            "counts": {"added": 0, "removed": 0, "changed": 0},
            "added": [], "removed": [], "changed": [],
        })

    def test_header_only_can_add_or_remove_every_record(self):
        empty = self.snapshot("empty.csv")
        populated = self.snapshot("populated.csv", "a,asset.example.test,,")
        for before, after, kind in ((empty, populated, "added"), (populated, empty, "removed")):
            with self.subTest(kind=kind):
                result = self.run_cli(before, after)
                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["counts"][kind], 1)
                self.assertEqual(sum(report["counts"].values()), 1)
                self.assertEqual(report[kind][0]["asset_id"], "a")

    def test_output_is_identical_when_input_rows_are_permuted(self):
        old_rows = (
            "r2,r2.example.test,,", "c2,c2.example.test,,old",
            "r1,r1.example.test,,", "c1,c1.example.test,,old",
        )
        new_rows = (
            "a2,a2.example.test,,", "c2,new-c2.example.test,,new",
            "a1,a1.example.test,,", "c1,c1.example.test,,new",
        )
        original = self.run_cli(
            self.snapshot("old.csv", *old_rows), self.snapshot("new.csv", *new_rows),
        )
        permuted = self.run_cli(
            self.snapshot("old-reordered.csv", *reversed(old_rows)),
            self.snapshot("new-reordered.csv", *reversed(new_rows)),
        )
        self.assertEqual(original.returncode, 1, original.stderr)
        self.assertEqual(permuted.returncode, 1, permuted.stderr)
        self.assertEqual(original.stdout, permuted.stdout)
        report = json.loads(original.stdout)
        for kind, prefix in (("added", "a"), ("removed", "r"), ("changed", "c")):
            with self.subTest(kind=kind):
                self.assertEqual([row["asset_id"] for row in report[kind]],
                                 [prefix + "1", prefix + "2"])
        for suffix in ("", "-reordered"):
            with self.subTest(summary=suffix):
                summary = self.run_cli(
                    "--format", "summary", self.directory / f"old{suffix}.csv",
                    self.directory / f"new{suffix}.csv",
                )
                self.assertEqual(summary.returncode, 1, summary.stderr)
                self.assertEqual(
                    summary.stdout, "Added: 2  Removed: 2  Changed: 2\n"
                    "+ a1\n+ a2\n- r1\n- r2\n~ c1: owner\n~ c2: hostname, owner\n",
                )

    def test_invalid_snapshot_has_no_partial_report(self):
        good = self.snapshot("valid.csv", "a,genuine.example.test,,")
        bad = self.snapshot("invalid.csv", "b,valid.example.test,,", " b ,other.example.test,,")
        for before, after in ((good, bad), (bad, good)):
            with self.subTest(before=before.name):
                result = self.run_cli(before, after)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid.csv", result.stderr)
                self.assertIn("line 3: duplicate asset_id (first seen on line 2)", result.stderr)

    def test_oversized_record_has_no_report(self):
        good = self.snapshot("valid.csv", "a,host,,")
        bad = self.snapshot("oversized.csv", "a,host,," + " " * 4096)
        result = self.run_cli(good, bad)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("oversized.csv", result.stderr)
        self.assertIn("line 2: CSV record exceeds 4096 characters", result.stderr)

    def test_unreadable_input_has_no_report(self):
        result = self.run_cli(EXAMPLES / "before.csv", self.directory / "missing.csv")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("missing.csv", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_arguments_are_usage_errors(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
