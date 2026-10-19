"""Static guards for the isolated MariaDB lab; these tests never start Docker."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import io
import contextlib
import tempfile
import threading
import unittest
from unittest.mock import patch

EXT = Path(__file__).resolve().parents[2]
LAB = EXT / "demo" / "synthetic" / "mariadb"
RUNNER = LAB / "run.py"
SPEC = importlib.util.spec_from_file_location("synthetic_mariadb_runner", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(runner)


class MariaDbLabGuards(unittest.TestCase):
    def test_cleanup_continues_after_candidate_failures_and_fails_closed(self):
        scenarios = ("stop", "inspect", "mismatch", "mount", "identity", "malformed", "invalid_id", "enumeration", "stopped", "success")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as base:
                root = Path(base).resolve() / "owned"
                project = "wikikg-mariadb-" + "a" * 16
                candidates = ["1" * 64, "2" * 64]
                if scenario == "invalid_id":
                    candidates[0] = "not-a-container-id"
                stops = []
                image_id = "sha256:" + "a" * 64

                def fake_run(command, **kwargs):
                    if command[1:3] == ["image", "inspect"]:
                        return subprocess.CompletedProcess(command, 0, image_id + "\n", "")
                    if command[:2] == ["docker", "stop"]:
                        stops.append(command[-1])
                        if scenario == "stop" and command[-1] == candidates[0]:
                            raise subprocess.CalledProcessError(1, command)
                    if "maintenance/install.php" in command:
                        (root / "install" / "LocalSettings.php").write_text("<?php")
                    if any(str(argument).endswith("/fixture.php") for argument in command):
                        kwargs["stdout"].write(b'{"server_version":"synthetic-test"}\n')
                    return subprocess.CompletedProcess(command, 0)

                def fake_check_output(command, **kwargs):
                    if command[1] == "ps":
                        if scenario == "enumeration":
                            raise subprocess.CalledProcessError(1, command)
                        return "\n".join(candidates).encode()
                    if scenario == "inspect" and command[-1] == candidates[0]:
                        raise subprocess.TimeoutExpired(command, 30)
                    if scenario == "malformed" and command[-1] == candidates[0]:
                        return b'[{"incomplete":true}]'
                    label = project if not (scenario == "mismatch" and command[-1] == candidates[0]) else "foreign"
                    mount = str(root / "foreign") if scenario == "mount" and command[-1] == candidates[0] else str(root)
                    identity = "3" * 64 if scenario == "identity" and command[-1] == candidates[0] else command[-1]
                    return json.dumps([{
                        "Id": identity,
                        "Config": {"Labels": {"com.docker.compose.project": label}},
                        "Mounts": [{"Destination": "/private/wikikg", "Source": mount}],
                        "State": {"Running": not (scenario == "stopped" and command[-1] == candidates[0])},
                    }]).encode()

                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(runner.secrets, "token_hex", side_effect=["a" * 16, "b" * 32]), \
                        patch.object(runner.sys, "argv", ["run.py", "--private-root", str(root), "--synthetic-only"]), \
                        patch.object(runner.subprocess, "run", side_effect=fake_run), \
                        patch.object(runner.subprocess, "check_output", side_effect=fake_check_output), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = runner.main()
                success = scenario in ("stopped", "success")
                self.assertEqual(status, 0 if success else 1)
                self.assertEqual('"status": "passed"' in stdout.getvalue(), success)
                report = json.loads((root / "results.json").read_text())
                self.assertEqual(report["status"], "passed" if success else "failed")
                if scenario in ("mismatch", "mount", "identity", "malformed", "inspect", "invalid_id", "stopped"):
                    self.assertNotIn(candidates[0], stops)
                if scenario != "enumeration":
                    self.assertIn(candidates[1], stops)
                else:
                    self.assertEqual(stops, [])
                if not success:
                    self.assertEqual(report["error"], "cleanup_failed")

    def test_competing_root_creator_is_never_written_or_run(self):
        with tempfile.TemporaryDirectory() as base:
            parent = Path(base).resolve()
            root = parent / "contended"
            original = runner.safe_new_root
            validated, created = threading.Event(), threading.Event()

            def interleaved(value):
                candidate = original(value)
                validated.set()
                self.assertTrue(created.wait(2))
                return candidate

            for _ in range(10):
                validated.clear()
                created.clear()
                root = parent / ("contended-" + str(_))
                def contender():
                    self.assertTrue(validated.wait(2))
                    root.mkdir(mode=0o700)
                    (root / "results.json").write_text("ORIGINAL")
                    created.set()
                with patch.object(runner, "safe_new_root", side_effect=interleaved), \
                        patch.object(runner.subprocess, "run") as called, \
                        patch.object(runner.sys, "argv", ["run.py", "--private-root", str(root), "--synthetic-only"]), \
                        patch("builtins.print"):
                    worker = threading.Thread(target=contender)
                    worker.start()
                    self.assertEqual(runner.main(), 1)
                    worker.join()
                called.assert_not_called()
                self.assertEqual((root / "results.json").read_text(), "ORIGINAL")
                self.assertFalse((root / "failure.log").exists())

    def test_result_publication_is_exclusive_and_complete(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base).resolve()
            runner.publish_result(root, {"status": "passed"})
            self.assertIn('"passed"', (root / "results.json").read_text())
            with self.assertRaises(FileExistsError):
                runner.publish_result(root, {"status": "failed"})
            self.assertIn('"passed"', (root / "results.json").read_text())

    def test_root_replaced_after_acquisition_never_receives_diagnostics_or_results(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root, acquired = parent / "root", parent / "acquired-root"
            def replace_at_image_inspection(command, **kwargs):
                self.assertEqual(command[:3], ["docker", "image", "inspect"])
                root.rename(acquired)
                root.mkdir(mode=0o700)
                (root / "results.json").write_bytes(b"foreign replacement")
                raise OSError("synthetic failure after another actor replaced the root")
            with patch.object(runner.sys, "argv", ["run.py", "--private-root", str(root), "--synthetic-only"]), \
                    patch.object(runner.subprocess, "run", side_effect=replace_at_image_inspection) as process, \
                    patch.object(runner, "publish_result") as publication, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(runner.main(), 0)
            process.assert_called_once()
            publication.assert_not_called()
            self.assertEqual(set(root.iterdir()), {root / "results.json"})
            self.assertEqual((root / "results.json").read_bytes(), b"foreign replacement")
            self.assertTrue((acquired / ".compose-project").is_file())

    def test_publication_failure_never_reports_success_and_keeps_owned_files(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base).resolve() / "owned"
            image_id = "sha256:" + "a" * 64
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                if command[1:3] == ["image", "inspect"]:
                    return subprocess.CompletedProcess(command, 0, image_id + "\n", "")
                self.assertEqual(kwargs["env"]["WIKIKG_WIKI_IMAGE"], image_id)
                if "maintenance/install.php" in command:
                    (root / "install" / "LocalSettings.php").write_text("<?php")
                if any(str(argument).endswith("/fixture.php") for argument in command):
                    kwargs["stdout"].write(b'{"server_version":"synthetic-test"}\n')
                return subprocess.CompletedProcess(command, 0)

            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(runner.sys, "argv", ["run.py", "--private-root", str(root), "--synthetic-only"]), \
                    patch.object(runner.subprocess, "run", side_effect=fake_run), \
                    patch.object(runner.subprocess, "check_output", return_value=b"") as cleanup, \
                    patch.object(runner, "publish_result", side_effect=OSError("disk failure")) as publication, \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = runner.main()
            self.assertNotEqual(status, 0)
            publication.assert_called_once()
            self.assertNotIn('"status": "passed"', stdout.getvalue())
            self.assertIn('"status": "failed"', stderr.getvalue())
            cleanup.assert_called_once()
            self.assertEqual((root / "install" / "LocalSettings.php").read_text(), "<?php")
            self.assertTrue(any(call[:3] == ["docker", "compose", "--env-file"] for call in calls))

    def test_help_documents_exact_one_shot_invocation_and_result(self):
        result = subprocess.run(["python3", str(RUNNER), "--help"], capture_output=True, text=True, check=True)
        self.assertIn("--private-root", result.stdout)
        self.assertIn("--synthetic-only", result.stdout)
        self.assertIn("results.json", result.stdout)

    def test_unsafe_preexisting_and_symlink_paths_rejected_before_subprocess(self):
        with tempfile.TemporaryDirectory() as base:
            base_path = Path(base).resolve()
            existing = base_path / "existing"
            existing.mkdir()
            link = base_path / "link"
            link.symlink_to(existing)
            for path in ("relative/path", str(existing), str(link / "new"), str(EXT / "new"), "/var/www/html/new"):
                with self.subTest(path=path), patch.object(subprocess, "run") as called:
                    with self.assertRaises(runner.Refusal):
                        runner.safe_new_root(path)
                    called.assert_not_called()

    def test_compose_is_digest_pinned_internal_and_has_no_published_ports(self):
        compose = (LAB / "compose.yml").read_text()
        self.assertIn("mariadb@sha256:0d0945c57f75b3a3b9ebe71aedaaad005fa5305054e8fca3d2e42a8c92b43b5e", compose)
        self.assertIn("internal: true", compose)
        self.assertNotRegex(compose, r"(?m)^\s*ports:")
        self.assertIn("source: database", compose)
        self.assertIn("MARIADB_PASSWORD_FILE", compose)
        self.assertIn("MARIADB_ROOT_PASSWORD_FILE", compose)

    def test_settings_use_mysql_identity_and_file_secret_not_sqlite_overlay(self):
        settings = (LAB / "settings.php").read_text()
        self.assertIn("$wgDBtype = 'mysql'", settings)
        self.assertIn("$wgDBname = 'synthetic_mariadb'", settings)
        self.assertIn("$wgDBserver = 'db'", settings)
        self.assertIn("WIKIKG_DB_PASSWORD_FILE", settings)
        self.assertNotIn("SyntheticSettings.php", settings)
        runner_source = RUNNER.read_text()
        self.assertIn("--dbpassfile", runner_source)
        self.assertIn("--passfile", runner_source)
        self.assertNotIn("--dbpass ", runner_source)
        self.assertNotIn("--pass ", runner_source)

    def test_passwords_not_in_command_arguments_or_emitted_results(self):
        source = RUNNER.read_text()
        self.assertIn("secrets.token_urlsafe", source)
        self.assertIn("/private/wikikg/db-password", source)
        self.assertIn("/private/wikikg/admin-password", source)
        self.assertNotIn("db-password\"),", source)
        self.assertIn('"error": result.get("error", "runtime_failed")', source)


if __name__ == "__main__":
    unittest.main()
