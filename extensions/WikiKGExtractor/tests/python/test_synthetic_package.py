"""Static safety and portability checks for the opt-in synthetic package."""
from pathlib import Path
import importlib.util
import hashlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "demo" / "synthetic"
SPEC = importlib.util.spec_from_file_location("synthetic_package_lifecycle", PACKAGE / "run.py")
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)
RELAY_SPEC = importlib.util.spec_from_file_location("synthetic_package_relay", PACKAGE / "serve.py")
relay = importlib.util.module_from_spec(RELAY_SPEC)
RELAY_SPEC.loader.exec_module(relay)


class SyntheticPackageTests(unittest.TestCase):
    def test_subnet_overlap_ignores_null_ipam_and_ipv6_but_detects_ipv4(self):
        target = lifecycle.ipaddress.ip_network("10.241.7.0/24")
        self.assertFalse(lifecycle.subnet_overlaps(None, target))
        self.assertFalse(lifecycle.subnet_overlaps([{"Subnet": "fd00::/64"}], target))
        self.assertTrue(lifecycle.subnet_overlaps([{"Subnet": "10.241.0.0/16"}], target))

    def test_invalid_metadata_refuses_before_creating_runtime_or_building(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            os.chmod(parent, 0o700)
            manifest = parent / "wrong-manifest.json"
            manifest.write_text('{"pages":[]}')
            os.chmod(manifest, 0o600)
            runtime = parent / "new-runtime"
            process = subprocess.run([sys.executable, str(PACKAGE / "run.py"), "setup",
                                      "--private-root", str(runtime), "--corpus-manifest", str(manifest),
                                      "--synthetic-only"], capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 5, process.stderr)
            self.assertIn('"error": "invalid_manifest"', process.stderr)
            self.assertFalse(runtime.exists())

    def test_existing_private_root_symlink_and_checkout_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            os.chmod(parent, 0o700)
            with self.assertRaises(lifecycle.Refusal) as existing:
                lifecycle.safe_root(str(parent), fresh=True)
            self.assertEqual(existing.exception.code, "not_fresh")
            link = parent / "alias"
            link.symlink_to(parent, target_is_directory=True)
            for unsafe in (link, lifecycle.REPO, parent / ".." / "unexpected"):
                with self.subTest(unsafe=str(unsafe)), self.assertRaises(lifecycle.Refusal):
                    lifecycle.safe_root(str(unsafe), fresh=True)

    def test_subnet_requires_canonical_private_ipv4_24(self):
        for subnet in ("10.241.7.0/24", "192.168.9.0/24", "172.16.8.0/24"):
            with self.subTest(subnet=subnet):
                parsed = lifecycle.ipaddress.ip_network(subnet, strict=True)
                self.assertIsInstance(parsed, lifecycle.ipaddress.IPv4Network)
                self.assertTrue(parsed.is_private)
                self.assertEqual(parsed.prefixlen, 24)
        for subnet in ("10.241.7.1/24", "10.241.7.0/23", "fd00::/24",
                       "127.0.0.0/24", "169.254.10.0/24", "100.64.0.0/24"):
            with self.subTest(subnet=subnet):
                with self.assertRaises(ValueError):
                    parsed = lifecycle.ipaddress.ip_network(subnet, strict=True)
                    if (not isinstance(parsed, lifecycle.ipaddress.IPv4Network)
                            or parsed.prefixlen != 24
                            or not any(parsed.subnet_of(pool) for pool in lifecycle.PRIVATE_IPV4_POOLS)):
                        raise ValueError("invalid subnet")

    def test_private_compose_override_is_applied_without_secret_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            (root / ".compose-project").write_text("wikikg-synthetic-" + "a" * 16)
            os.chmod(root / ".compose-project", 0o600)
            (root / "private-compose.yml").write_text("networks:\n  synthetic:\n    ipam:\n      config:\n        - subnet: 10.241.7.0/24\n")
            os.chmod(root / "private-compose.yml", 0o600)
            with patch.object(lifecycle.subprocess, "run") as run:
                run.return_value.returncode = 0
                lifecycle.compose(root, "ps")
            command = run.call_args.args[0]
            self.assertIn(str(root / "private-compose.yml"), command)
            self.assertNotIn("password", " ".join(command).lower())

    def test_all_owned_package_files_exist(self):
        for name in ("wiki.dockerfile", "compose.yml", "run.py", "fixture.php",
                     "workflow.py", "SyntheticSettings.php"):
            with self.subTest(name=name):
                self.assertTrue((PACKAGE / name).is_file())

    def test_build_has_public_pins_and_verified_mediawiki_archive(self):
        dockerfile = (PACKAGE / "wiki.dockerfile").read_text()
        self.assertIn("python:3.13.16-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed", dockerfile)
        self.assertIn("php:8.3.14-apache-bookworm@sha256:7a3fd6ae9c9d240810bae95d3aaf894be4f0ec1a022b0a5f129d658a625e2ac2", dockerfile)
        self.assertIn("mediawiki/1.43/mediawiki-1.43.1.tar.gz", dockerfile)
        self.assertIn("3c85aa9c4cd6c353c67804a3a58e7b79615d4141c3d66b101e5f3aeb40653d6c", dockerfile)
        self.assertIn("composer install", dockerfile)
        self.assertNotIn("composer update", dockerfile)
        self.assertEqual(hashlib.sha256((PACKAGE / "mediawiki-composer.lock").read_bytes()).hexdigest(),
                         "c566ac3235c650530fd58f114ca6eb4e6af4f73ebe1dbf9f2e28cbf58a60686c")

    def test_compose_is_runtime_isolated_and_loopback_only(self):
        compose = (PACKAGE / "compose.yml").read_text()
        self.assertNotIn("ports:", compose)
        self.assertIn('HTTPServer(("127.0.0.1", port)', (PACKAGE / "serve.py").read_text())
        self.assertIn("internal: true", compose)
        self.assertIn("create_host_path: false", compose)
        self.assertIn("MW_CONFIG_FILE: /private/wikikg/LocalSettings.synthetic.php", compose)
        self.assertIn('WIKIKG_SYNTHETIC: "1"', compose)

    def test_cli_requires_explicit_synthetic_acknowledgement_and_never_deletes(self):
        source = (PACKAGE / "run.py").read_text()
        self.assertIn('required=True, help="required safety acknowledgement"', source)
        self.assertIn('"error": "invalid_arguments"', source)
        self.assertIn('choices=("setup", "start", "serve", "workflow", "test", "stop")', source)
        self.assertNotIn('"down"', source)
        self.assertNotIn('"-v"', source)
        self.assertIn('"stop", "wiki"', source)

    def test_fixture_is_guarded_and_contains_only_synthetic_named_pages(self):
        fixture = (PACKAGE / "fixture.php").read_text()
        for title in ("Verification crop", "Verification crop Swift", "Verification crop Range",
                      "Other crop", "Other crop Later", "Unclassified fixture", "Verification alias"):
            self.assertIn(title, fixture)
        self.assertIn("WIKIKG_SYNTHETIC", fixture)
        self.assertIn("synthetic-marker", fixture)
        self.assertIn("1000000", fixture)

    def test_permission_denial_and_test_database_are_separate(self):
        settings = (PACKAGE / "SyntheticSettings.php").read_text()
        self.assertIn("permission-denied", settings)
        self.assertIn("wikikgextractor-review", settings)
        self.assertIn("WIKIKG_SYNTHETIC_TEST", settings)
        self.assertIn("wikitest", settings)
        self.assertIn("syntheticwiki", settings)

    def test_relay_refuses_foreign_mount_wrong_port_and_noninternal_network(self):
        self.relay_cases(success=False)

    def test_relay_binds_loopback_and_retains_only_content_free_owned_pid_record(self):
        self.relay_cases(success=True)

    def relay_cases(self, *, success):
        cases = ("valid",) if success else ("foreign", "port", "writable", "external")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                os.chmod(root, 0o700)
                for name, text in ((".compose-project", "wikikg-synthetic-" + "f" * 16),
                                   ("synthetic-marker", "seeded")):
                    (root / name).write_text(text)
                    os.chmod(root / name, 0o600)
                container = {"Mounts": [{"Destination": "/private/wikikg", "Source": str(root), "RW": True}],
                             "Config": {"Env": ["WIKIKG_SYNTHETIC=1", "WIKIKG_HTTP_PORT=8787",
                                                  "MW_CONFIG_FILE=/private/wikikg/LocalSettings.synthetic.php"]},
                             "HostConfig": {"ReadonlyRootfs": True},
                             "NetworkSettings": {"Networks": {"isolated": {}}}}
                container = copy.deepcopy(container)
                if case == "foreign":
                    container["Mounts"][0]["Source"] = str(root.parent)
                elif case == "port":
                    container["Config"]["Env"][1] = "WIKIKG_HTTP_PORT=9999"
                elif case == "writable":
                    container["HostConfig"]["ReadonlyRootfs"] = False

                def docker_output(command, **kwargs):
                    self.assertNotIn("WIKIKG_TEST_SECRET", kwargs["env"])
                    if command[1] == "ps":
                        return ("a" * 64 + "\n").encode()
                    if command[1] == "inspect":
                        return json.dumps([container]).encode()
                    if command[1:3] == ["network", "inspect"]:
                        return json.dumps([{"Internal": case != "external"}]).encode()
                    self.fail("Unexpected Docker command")

                output = io.StringIO()
                with patch.dict(sys.modules, {"run": lifecycle}), \
                     patch.dict(os.environ, {"WIKIKG_TEST_SECRET": "must-not-forward"}), \
                     patch.object(relay.subprocess, "check_output", side_effect=docker_output), \
                     patch.object(relay, "HTTPServer") as server, redirect_stdout(output):
                    if success:
                        relay.serve(root, 8787)
                        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8787))
                        server.return_value.serve_forever.assert_called_once()
                        server.return_value.server_close.assert_called_once()
                    else:
                        with self.assertRaisesRegex(ValueError, "synthetic_relay_refused"):
                            relay.serve(root, 8787)
                        server.assert_not_called()
                records = list(root.glob("relay-*.json"))
                if success:
                    self.assertEqual(len(records), 1)
                    self.assertEqual(records[0].stat().st_mode & 0o777, 0o600)
                    self.assertEqual(json.loads(records[0].read_text())["pid"], os.getpid())
                    self.assertNotIn("must-not-forward", output.getvalue())
                else:
                    self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
