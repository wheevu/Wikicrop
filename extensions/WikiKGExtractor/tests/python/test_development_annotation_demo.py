"""Independent CLI consumers of the maintained, fictional annotation demonstration."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import development_annotation_demo as demo  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DevelopmentAnnotationDemoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="annotation-demo-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.directory.chmod(0o700)
        self.script = ROOT / "tools" / "development_annotation_demo.py"

    def run_cli(self, flags: list[str], *, optimized: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *(["-O"] if optimized else []), str(self.script), *flags],
            cwd=self.directory,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_fresh_relative_directory_reproduces_twelve_exact_results_and_hashes(self) -> None:
        result = self.run_cli(["--output", "private demo"])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertNotIn("6-8", result.stdout)
        self.assertNotIn("synthetic-fixture", result.stdout)
        summary = json.loads(result.stdout)
        output = self.directory / "private demo"
        report_path = output / "verification.json"
        report = json.loads(report_path.read_bytes())
        self.assertEqual("passed", report["status"])
        self.assertTrue(report["synthetic"])
        self.assertEqual(sha(report_path), summary["report_sha256"])
        self.assertLessEqual(report_path.stat().st_size, 65536)
        self.assertEqual(12, report["commands"])
        self.assertEqual(7, report["annotations_accounted"])
        self.assertEqual(4, report["candidates_accounted"])
        self.assertEqual(0, report["human_decisions"])
        self.assertEqual("not_computed", report["scientific_scores"])
        self.assertTrue(report["version_1_preserved"])
        self.assertEqual(sha(demo.FIXTURE), report["fixture_sha256"])
        self.assertEqual(sha(self.script), report["tool_sha256"])
        self.assertEqual(sha(demo.SCRIPT), report["workflow_sha256"])
        self.assertEqual(
            {
                "ambiguous": 1,
                "outside_scope": 1,
                "possible_pair": 2,
                "unmatched_annotations": 1,
                "unmatched_candidates": 1,
                "unresolved_annotation": 1,
            },
            report["groups"],
        )
        commands = json.loads((output / "commands.json").read_bytes())
        self.assertEqual(commands, report["command_results"])
        self.assertEqual(
            [
                "prepare",
                "prepare",
                "locate",
                "validate",
                "retain",
                "retain",
                "compare",
                "compare",
                "validate",
                "validate",
                "compare",
                "retain",
            ],
            [item["argv"][2] for item in commands],
        )
        self.assertEqual([0] * 8 + [2] * 3 + [0], [item["exit_code"] for item in commands])
        self.assertEqual(
            [
                "created",
                "unchanged",
                "created",
                "valid_format_and_coordinates",
                "created",
                "unchanged",
                "created",
                "unchanged",
                "error",
                "error",
                "error",
                "created",
            ],
            [item["result"]["status"] for item in commands],
        )
        self.assertEqual(
            ["EVIDENCE_SPAN", "EVIDENCE_PIN", "CANDIDATE_CHANGED"],
            [item["result"]["code"] for item in commands if item["exit_code"] == 2],
        )
        paths = {
            str(path.relative_to(output)): path
            for path in output.rglob("*")
            if path.is_file() and path != report_path
        }
        self.assertEqual(set(paths), set(report["artifact_sha256"]))
        self.assertEqual(
            {name: sha(path) for name, path in paths.items()}, report["artifact_sha256"]
        )
        self.assertTrue(
            all(
                stat.S_IMODE(path.stat().st_mode) == 0o600
                for path in output.rglob("*")
                if path.is_file()
            )
        )
        self.assertTrue(
            all(
                stat.S_IMODE(path.stat().st_mode) == 0o700
                for path in [output, *(path for path in output.rglob("*") if path.is_dir())]
            )
        )
        original = json.loads((output / "submission-v1" / "submission.json").read_bytes())
        corrected = json.loads((output / "submission-v2" / "submission.json").read_bytes())
        self.assertEqual("synthetic", original["provenance"]["exposure"])
        self.assertEqual(1, original["version"])
        self.assertEqual(2, corrected["version"])
        self.assertEqual(
            sha(output / "submission-v1" / "submission.json"),
            corrected["previous_submission_sha256"],
        )
        self.assertEqual(
            (output / "working.json").read_bytes(),
            (output / "submission-v1" / "submission.json").read_bytes(),
        )
        self.assertFalse((output / "refused-comparison").exists())
        discrepancy = json.loads((output / "comparison" / "report.json").read_bytes())
        self.assertCountEqual(
            [f"A{i}" for i in range(1, 8)],
            [
                identifier
                for group in discrepancy["groups"]
                for identifier in group["annotation_ids"]
            ],
        )
        self.assertEqual(
            4,
            len(
                {
                    identifier
                    for group in discrepancy["groups"]
                    for identifier in group["candidate_ids"]
                }
            ),
        )
        self.assertTrue(all(group["human_decision"] is None for group in discrepancy["groups"]))
        with (output / "comparison" / "decisions.csv").open(newline="") as stream:
            self.assertTrue(
                all(
                    not row[key]
                    for row in csv.DictReader(stream)
                    for key in ("first_view", "second_view", "adjudicator_id", "decision", "reason")
                )
            )
        template = json.loads((output / "pack" / "submission-template.json").read_bytes())
        self.assertEqual([], template["annotations"])
        self.assertTrue(all(item["status"] == "not_started" for item in template["page_reviews"]))
        self.assertNotIn(
            b"SYNTHETIC SEALED METADATA ONLY",
            b"".join(path.read_bytes() for path in (output / "pack").iterdir()),
        )

    def test_checks_still_run_under_python_optimized_mode(self) -> None:
        result = self.run_cli(["--output", "optimized demo"], optimized=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(12, json.loads(result.stdout)["commands"])
        self.assertNotIn("assert ", self.script.read_text())

    def test_existing_destination_is_refused_without_rewriting_evidence(self) -> None:
        output = self.directory / "existing"
        output.mkdir(mode=0o700)
        sentinel = output / "original.txt"
        sentinel.write_bytes(b"Synthetic earlier evidence.")
        before = sentinel.read_bytes(), sentinel.stat().st_mtime_ns
        result = self.run_cli(["--output", str(output)])
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("OUTPUT_EXISTS", json.loads(result.stderr)["code"])
        self.assertEqual(before, (sentinel.read_bytes(), sentinel.stat().st_mtime_ns))
        self.assertEqual([sentinel], list(output.iterdir()))

    def test_unsafe_parent_and_document_root_are_refused_before_writing(self) -> None:
        public = self.directory / "public"
        public.mkdir(mode=0o755)
        for flags, code in (
            (["--output", str(public / "new")], "PRIVATE_DIRECTORY"),
            (["--output", "hosted", "--document-root", str(self.directory)], "PUBLIC_DESTINATION"),
            (["--output", str(ROOT / "demo" / "unsafe-demo")], "PUBLIC_DESTINATION"),
        ):
            with self.subTest(code=code):
                result = self.run_cli(flags)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertEqual(code, json.loads(result.stderr)["code"])
                self.assertFalse(
                    Path(
                        flags[1] if Path(flags[1]).is_absolute() else self.directory / flags[1]
                    ).exists()
                )

    def test_failed_command_keeps_results_but_has_no_passed_report(self) -> None:
        output = self.directory / "failed demo"
        stdout, stderr = io.StringIO(), io.StringIO()
        failure = subprocess.CompletedProcess([], 0, '{"status":"unexpected"}', "")
        with patch.object(demo.subprocess, "run", return_value=failure), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            result = demo.main(["--output", str(output)])
        self.assertEqual(2, result)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("DEMO_CHECK", json.loads(stderr.getvalue())["code"])
        self.assertFalse((output / "verification.json").exists())
        self.assertEqual(1, len(json.loads((output / "commands.json").read_bytes())))

    def test_help_and_unknown_flags_do_not_accept_real_inputs(self) -> None:
        result = self.run_cli(["--help"])
        self.assertEqual(0, result.returncode)
        self.assertIn("<private-root/demo>", result.stdout)
        for flags in (
            [],
            ["--input", "anything"],
            ["--out", "new"],
            ["--output", "new", "--heldout"],
        ):
            result = self.run_cli(flags)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertEqual("ARGUMENTS", json.loads(result.stderr)["code"])
        text = self.script.read_text()
        self.assertNotIn("test_development_annotation", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/Study/Thesis", text)


if __name__ == "__main__":
    unittest.main()
