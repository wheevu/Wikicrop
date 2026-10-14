"""Verify the opt-in stack without starting containers or touching site config."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ROOT / "extensions/WikiKGExtractor/demo/docker-compose.reviewed-offline.yml"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required for packaging checks")
class ReviewedPackagingTest(unittest.TestCase):
    def compose(self, environment, *arguments):
        return subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "-p", "wikikg-config-test",
             "-f", str(COMPOSE), *arguments],
            env=environment, capture_output=True, text=True, timeout=30, check=False,
        )

    def environment(self, directory):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("WIKIKG_", "COMPOSE_"))}
        environment.update({
            "WIKIKG_PRIVATE_ROOT": directory,
            "WIKIKG_UID": "1000", "WIKIKG_GID": "1000",
            "WIKIKG_NEO4J_PASSWORD": "synthetic-config-check-only",
        })
        return environment

    def test_stack_is_private_opt_in_and_does_not_mount_site_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.compose(self.environment(directory), "--profile", "reviewed-offline",
                                  "config", "--format", "json")
        self.assertEqual(0, result.returncode, result.stderr)
        configuration = json.loads(result.stdout)
        self.assertEqual({"graph", "tools"}, set(configuration["services"]))
        graph, tools = (configuration["services"][name] for name in ("graph", "tools"))
        self.assertIn("@sha256:", graph["image"])
        self.assertEqual(["reviewed-offline"], graph["profiles"])
        self.assertEqual("127.0.0.1", graph["ports"][0]["host_ip"])
        self.assertEqual(7687, graph["ports"][0]["target"])
        self.assertTrue(configuration["networks"]["offline"]["internal"])
        self.assertTrue(tools["read_only"])
        self.assertEqual("service:graph", tools["network_mode"])
        self.assertEqual("1000:1000", tools["user"])
        code = next(volume for volume in tools["volumes"]
                    if volume["target"] == "/workspace/extensions/WikiKGExtractor")
        self.assertTrue(code["read_only"])
        self.assertEqual(str(ROOT / "extensions/WikiKGExtractor"), code["source"])
        self.assertFalse(code.get("bind", {}).get("create_host_path", False))
        self.assertNotIn("LocalSettings.php", result.stdout)
        self.assertNotIn("AUTH: none", result.stdout)

    def test_missing_password_or_private_root_refuses_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            for key in ("WIKIKG_NEO4J_PASSWORD", "WIKIKG_PRIVATE_ROOT", "WIKIKG_UID", "WIKIKG_GID"):
                with self.subTest(key=key):
                    environment = self.environment(directory)
                    del environment[key]
                    result = self.compose(environment, "config")
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(key, result.stderr)

    def test_default_profile_starts_no_services(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.compose(self.environment(directory), "config", "--services")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
