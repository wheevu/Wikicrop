"""Host-side refusal, ownership, and packaging checks for graph-backed demos."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "demo" / "synthetic" / "questions"
SPEC = importlib.util.spec_from_file_location("synthetic_questions_lifecycle", PACKAGE / "run.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
VERIFY_SPEC = importlib.util.spec_from_file_location("synthetic_questions_verify", PACKAGE / "verify.py")
verification = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(verification)


class ConnectedPackageTests(unittest.TestCase):
    def test_invalid_subnets_are_refused_before_root_or_docker_access(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            os.chmod(base, 0o700)
            for index, subnet in enumerate(("10.3.4.1/24", "10.3.4.0/23", "127.0.0.0/24",
                                            "169.254.10.0/24", "fd00::/24")):
                with self.subTest(subnet=subnet):
                    root = base / ("new-root-" + str(index))
                    result = subprocess.run([sys.executable, str(PACKAGE / "run.py"), "setup",
                        "--private-root", str(root), "--corpus-manifest", str(base / "manifest"),
                        "--subnet", subnet, "--synthetic-only"], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stderr)["error"], "invalid_arguments")
                    self.assertFalse(root.exists())
        valid = ("10.241.7.0/24", "172.16.8.0/24", "192.168.9.0/24")
        for value in valid:
            network = runner.ipaddress.ip_network(value, strict=True)
            self.assertTrue(any(network.subnet_of(pool) for pool in runner.PRIVATE_IPV4_POOLS))
        target = runner.ipaddress.ip_network("10.241.7.0/24")
        self.assertFalse(runner.subnet_overlaps(None, target))
        self.assertFalse(runner.subnet_overlaps([{"Subnet": "fd00::/64"}], target))
        self.assertTrue(runner.subnet_overlaps([{"Subnet": "10.241.0.0/16"}], target))

    def test_private_root_rejects_checkout_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            os.chmod(parent, 0o700)
            link = parent / "alias"
            link.symlink_to(parent, target_is_directory=True)
            for candidate in (str(ROOT), str(link), str(parent / "../escape")):
                with self.subTest(candidate=candidate), self.assertRaises(runner.Refusal):
                    runner.root_path(candidate, fresh=True)

    def test_compose_is_private_no_db_publish_and_graph_only_receives_auth(self):
        compose = (PACKAGE / "compose.yml").read_text()
        self.assertIn("neo4j:2026.09.0@sha256:29efb5ebfb51ec75545a945bc3ac0bdfbeb6bc2686a8ae5053fe0f4dcc926441", compose)
        self.assertIn("network_mode: service:graph", compose)
        self.assertIn("internal: true", compose)
        self.assertNotIn("ports:", compose)
        self.assertIn("NEO4J_AUTH", compose)
        graph = compose.split("  wiki:", 1)[0]
        wiki = compose.split("  wiki:", 1)[1]
        self.assertIn("NEO4J_AUTH", graph)
        self.assertNotIn("NEO4J_AUTH", wiki)
        self.assertIn("synthetic-marker", graph)
        self.assertIn("read_only: true", wiki)

    def test_compose_commands_do_not_put_password_in_argv_or_forward_host_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            (root / "wiki").mkdir(mode=0o700)
            (root / "wiki" / "connected").mkdir(mode=0o700)
            (root / "wiki" / ".compose-project").write_text("wikikg-synthetic-0123456789abcdef\n")
            os.chmod(root / "wiki" / ".compose-project", 0o600)
            (root / "wiki" / ".synthetic-http-port").write_text("8787\n")
            os.chmod(root / "wiki" / ".synthetic-http-port", 0o600)
            (root / ".wiki-image-id").write_text("sha256:" + "a" * 64 + "\n")
            os.chmod(root / ".wiki-image-id", 0o600)
            secret = "do-not-log-this-secret"
            (root / "wiki" / "connected" / "graph-password").write_text(secret)
            os.chmod(root / "wiki" / "connected" / "graph-password", 0o600)
            with patch.object(runner.subprocess, "check_output", return_value=b"sha256:" + b"a" * 64), \
                    patch.object(runner.subprocess, "run") as execute, \
                    patch.dict(os.environ, {"FOREIGN_SECRET": "must-not-forward"}):
                execute.return_value.returncode = 0
                runner.compose(root, "config")
            argv = execute.call_args.args[0]
            env = execute.call_args.kwargs["env"]
            self.assertNotIn(secret, " ".join(argv))
            self.assertNotIn("FOREIGN_SECRET", env)
            self.assertEqual(env["NEO4J_AUTH"], "neo4j/" + secret)
            logged = (root / "compose-commands.log").read_text()
            self.assertNotIn(secret, logged)

    def test_owned_container_check_rejects_a_foreign_marker_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            (root / "wiki").mkdir(mode=0o700)
            marker = root / "wiki" / "synthetic-marker"
            marker.write_text("seeded")
            (root / "wiki" / ".compose-project").write_text("wikikg-synthetic-0123456789abcdef\n")
            os.chmod(root / "wiki" / ".compose-project", 0o600)
            project = "wikikg-synthetic-0123456789abcdef"
            ids = ("a" * 64, "b" * 64)
            containers = {}
            for service, container_id in zip(("graph", "wiki"), ids):
                env = ["WIKIKG_SYNTHETIC=1"]
                if service == "graph":
                    env.append("NEO4J_AUTH=neo4j/private")  # gitleaks:allow - synthetic fixture sentinel, not a credential
                    mounts = [{"Destination": "/private/wikikg/synthetic-marker",
                               "Source": str(marker), "RW": False}]
                    readonly = False
                else:
                    env.append("MW_CONFIG_FILE=/private/wikikg/LocalSettings.synthetic.php")
                    mounts = [
                        {"Destination": "/private/wikikg", "Source": str(root / "wiki"), "RW": True},
                        {"Destination": "/var/www/html/w/extensions/WikiKGExtractor",
                         "Source": str(ROOT), "RW": False},
                    ]
                    readonly = True
                containers[container_id] = [{"Mounts": mounts, "Config": {
                    "Env": env, "Labels": {"wikikg.synthetic.owner": project,
                        "wikikg.synthetic.questions": "1", "com.docker.compose.service": service}},
                    "HostConfig": {"ReadonlyRootfs": readonly},
                    "NetworkSettings": {"Networks": {"synthetic": {}}},
                    "State": {"Running": True}}]
            def docker(command, **kwargs):
                if command[:2] == ["docker", "ps"]:
                    return ("\n".join(ids) + "\n").encode()
                if command[:2] == ["docker", "inspect"]:
                    return json.dumps(containers[command[2]]).encode()
                if command[:3] == ["docker", "network", "inspect"]:
                    return b'[{"Internal":true}]'
                raise AssertionError(command)
            with patch.object(verification.subprocess, "check_output", side_effect=docker):
                self.assertEqual(verification.owned_containers(root), ids)
                containers[ids[0]][0]["Mounts"][0]["Source"] = str(root / "foreign-marker")
                with self.assertRaisesRegex(ValueError, "foreign_mount"):
                    verification.owned_containers(root)

    def test_setup_exec_guard_binds_project_private_mount_and_readonly_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "wiki").mkdir(mode=0o700)
            project = "wikikg-synthetic-0123456789abcdef"
            record = {"Config": {"Labels": {"com.docker.compose.project": project,
                "com.docker.compose.service": "wiki"}, "Env": ["WIKIKG_SYNTHETIC=1",
                "MW_CONFIG_FILE=/private/wikikg/LocalSettings.synthetic.php"]},
                "Mounts": [
                    {"Destination": "/private/wikikg", "Source": str(root / "wiki"), "RW": True},
                    {"Destination": "/var/www/html/w/extensions/WikiKGExtractor",
                     "Source": str(ROOT), "RW": False}],
                "HostConfig": {"ReadonlyRootfs": True},
                "NetworkSettings": {"Networks": {"synthetic": {}}}}
            def docker(command, **kwargs):
                if command[:2] == ["docker", "inspect"]:
                    return json.dumps([record]).encode()
                if command[:3] == ["docker", "network", "inspect"]:
                    return b'[{"Internal":true}]'
                raise AssertionError(command)
            with patch.object(runner.subprocess, "check_output", side_effect=docker):
                runner.inspect_owned_wiki(root, "a" * 64, project)
                record["Mounts"][0]["Source"] = str(root / "foreign")
                with self.assertRaisesRegex(runner.Refusal, "foreign_wiki_container"):
                    runner.inspect_owned_wiki(root, "a" * 64, project)

    def test_final_report_is_exclusive_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            connected = root / "connected"
            connected.mkdir(mode=0o700)
            report = connected / "final-report.json"
            report.write_text('{"status":"preserve"}')
            os.chmod(report, 0o600)
            with self.assertRaisesRegex(ValueError, "report_exists"):
                verification.verify(root)
            self.assertEqual(report.read_text(), '{"status":"preserve"}')

    def test_verify_publishes_failure_after_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            (root / "connected").mkdir(mode=0o700)
            with patch.object(verification, "stop_owned", side_effect=ValueError("stop failure")):
                report = verification.verify(root, None)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["cleanup"], "failed")
            self.assertEqual(json.loads((root / "connected" / "final-report.json").read_text()), report)

    def test_report_publication_is_private_exclusive_and_fsynced(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            os.chmod(parent, 0o700)
            path = parent / "final-report.json"
            real_fsync = os.fsync
            with patch.object(verification.os, "fsync", wraps=real_fsync) as fsync:
                verification._publish_report(path, {"status": "failed", "cleanup": "passed"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {"status": "failed", "cleanup": "passed"})
            self.assertEqual(fsync.call_count, 2)
            with self.assertRaises(FileExistsError):
                verification._publish_report(path, {"status": "passed"})
            self.assertEqual(json.loads(path.read_text())["status"], "failed")
            too_large = parent / "too-large.json"
            with self.assertRaisesRegex(ValueError, "report_limit"):
                verification._publish_report(too_large, {"payload": "x" * 32769})
            self.assertFalse(too_large.exists())

    def test_explicit_playwright_cli_requires_the_pinned_package(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "node_modules" / "@playwright" / "test"
            package.mkdir(parents=True)
            cli = package / "cli.js"
            cli.write_text("// pinned cli")
            (package / "package.json").write_text(json.dumps({"name": "@playwright/test", "version": "1.63.0"}))
            self.assertEqual(verification._playwright_cli(str(cli)), cli.resolve())
            (package / "package.json").write_text(json.dumps({"name": "@playwright/test", "version": "1.62.0"}))
            with self.assertRaisesRegex(ValueError, "playwright_cli_invalid"):
                verification._playwright_cli(str(cli))

    def test_cleanup_attempts_each_owned_candidate_after_independent_stop_failure(self):
        root = Path("/private/synthetic-owner")
        ids = {"wiki": "a" * 64, "graph": "b" * 64}
        candidate_results = [(ids.copy(), ["invalid-extra"]), (ids.copy(), []), (ids.copy(), [])]
        with patch.object(verification, "owned_candidates", side_effect=candidate_results), \
                patch.object(verification.subprocess, "check_output", return_value=b'[{"State":{"Running":true}}]'), \
                patch.object(verification.subprocess, "run", side_effect=[RuntimeError("wiki stop"), None]) as stop:
            with self.assertRaisesRegex(ValueError, "cleanup_failed"):
                verification.stop_owned(root)
        self.assertEqual(stop.call_count, 2)
        self.assertEqual([call.args[0][-1] for call in stop.call_args_list], [ids["wiki"], ids["graph"]])

    def test_graph_outage_still_executes_only_the_validated_owned_wiki(self):
        wiki = "a" * 64
        with patch.object(verification, "owned_candidates", return_value={"wiki": wiki}), \
                patch.object(verification.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as execute:
            result = verification._inside(Path("/private/root"), wiki, "python3", "-B")
        self.assertEqual(result.returncode, 0)
        self.assertIn(wiki, execute.call_args.args[0])
        with patch.object(verification, "owned_candidates", return_value={"wiki": wiki}), \
                patch.object(verification.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "owned_wiki_changed"):
                verification._inside(Path("/private/root"), "b" * 64, "python3")
        execute.assert_not_called()

    def test_scenario_oracle_is_independent_and_has_all_thirteen_cases(self):
        scenarios = json.loads((PACKAGE / "scenarios.json").read_text())
        ids = {case["id"] for case in scenarios["scenarios"]}
        self.assertEqual(ids, set(verification.STATIC_IDS + verification.STATE_IDS))
        self.assertTrue(all("expected" in case and "query" in case for case in scenarios["scenarios"]))
        self.assertEqual(len(ids), 13)

    def test_permission_loss_verification_wraps_real_bridge_and_retains_its_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            (root / "wiki" / "connected" / "attempts").mkdir(parents=True, mode=0o700)
            os.chmod(root / "wiki", 0o700)
            os.chmod(root / "wiki" / "connected", 0o700)
            os.chmod(root / "wiki" / "connected" / "attempts", 0o700)
            expected = {"exit": 2, "status": "dependency_failure", "query_state": "refused",
                        "reason": "offline_authorization_refused"}
            case = {"id": "permission-loss", "query": {}, "expected": expected}
            def execute(root_arg, wiki_id, *args, **kwargs):
                wrapper = args[3]
                compile(wrapper, "permission-loss-wrapper", "exec")
                (root / "wiki" / "permission-denied.connected-verify").write_text("deny")
                result = {"status": "dependency_failure", "reasons": [{"code": "offline_authorization_refused"}]}
                (root / "wiki" / "connected" / "attempts" / "permission-loss.result.json").write_text(json.dumps(result))
                return SimpleNamespace(returncode=2)
            report = {}
            with patch.object(verification, "_inside", side_effect=execute):
                verification.permission_loss_case(root, "owned-wiki", case, report,
                                                   root / "wiki" / "connected" / "reviewed.json")
            self.assertIn("permission-loss", report)
            self.assertFalse((root / "wiki" / "permission-denied").exists())

    def test_verified_runner_uses_only_actual_cli_php_and_playwright(self):
        source = (PACKAGE / "verify.py").read_text()
        self.assertIn("offline_answer.py", source)
        self.assertIn("exportReviewedProjection.php", (PACKAGE / "run.py").read_text())
        self.assertIn("playwright", source)
        self.assertNotIn("mock", source.lower())
        self.assertIn("--expected-version=2", source)
        self.assertIn("Synthetic verifier fixture reset only", source)
        self.assertIn("reviewed-after-rejection-reset.json", source)
        approved = source.index("_approve_rejection_fixture(root, rejected_id)")
        exported = source.index('refreshed = _export(root, "reviewed-after-rejection-reset.json")')
        positive = source.index("positive_code, positive = cli_case")
        source_positive = source.index("source_positive_code, source_positive_result = cli_case")
        edited = source.index('_run_browser(root, browser, "source-change", cli)')
        source_cli = source.index('source_code, source_result = cli_case')
        outage = source.index('subprocess.run(["docker", "stop", "--time", "10", graph]')
        self.assertLess(approved, exported)
        self.assertLess(exported, positive)
        self.assertLess(positive, edited)
        self.assertLess(positive, source_positive)
        self.assertLess(source_positive, edited)
        self.assertLess(edited, source_cli)
        self.assertLess(source_cli, outage)
        self.assertIn('"graph_generation_changed"', (PACKAGE / "scenarios.json").read_text())
        self.assertIn('expected_state = ("refused" if case["id"] == "partial-manifest"', source)
        self.assertIn('"--playwright-cli"', (PACKAGE / "run.py").read_text())
        self.assertIn('"node", str(cli)', source)
        self.assertNotIn('"npx"', source)
        self.assertIn('reviewOfflineClaim.php', source)
        self.assertNotIn('"maintenance/run.php", "extensions/WikiKGExtractor/maintenance/reviewOfflineClaim.php"', source)

    def test_container_port_and_fixture_inputs_use_canonical_private_mount(self):
        source = (PACKAGE / "run.py").read_text()
        self.assertIn('"WIKIKG_HTTP_PORT": port', source)
        self.assertIn('root / "wiki" / "connected" / "graph-password"', source)
        self.assertIn('"extensions/WikiKGExtractor/demo/synthetic/questions/fixture.php"', source)
        self.assertNotIn('for item in connected.iterdir()', source)


if __name__ == "__main__":
    unittest.main()
