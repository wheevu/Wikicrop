"""Tests for the reproducible WikiKGExtractor browser demo command."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tools" / "wikikg_demo.py"
SPEC = importlib.util.spec_from_file_location("wikikg_demo", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
wikikg_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wikikg_demo)


class WikiKgDemoTest(unittest.TestCase):
    def test_demo_pages_use_fixture_and_add_variety_links(self) -> None:
        pages = wikikg_demo.load_demo_pages()

        self.assertEqual(set(pages), {"Lúa", "Lúa OM5451", "Lúa ST24", "Lúa ST25"})
        self.assertIn("== Danh sách giống ==", pages["Lúa"])
        self.assertIn("[[Lúa OM5451]]", pages["Lúa"])
        self.assertNotIn("Kỹ thuật trồng", "\n".join(pages))

    def test_configure_extension_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "LocalSettings.php"
            settings.write_text("<?php\n$wgSitename = 'Demo';\n", encoding="utf-8")
            with mock.patch.object(wikikg_demo, "LOCAL_SETTINGS", settings):
                wikikg_demo.configure_extension()
                wikikg_demo.configure_extension()

            result = settings.read_text(encoding="utf-8")
            self.assertEqual(result.count(wikikg_demo.CONFIG_BEGIN), 1)
            self.assertEqual(result.count("wfLoadExtension( 'WikiKGExtractor' );"), 1)
            self.assertIn("$wgWikiKGExtractorPushToNeo4j = false;", result)

    def test_existing_unrelated_local_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "LocalSettings.php"
            settings.write_text("<?php\n$wgSitename = 'Existing';\n", encoding="utf-8")
            with mock.patch.object(wikikg_demo, "LOCAL_SETTINGS", settings):
                with self.assertRaisesRegex(wikikg_demo.DemoError, "clean clone"):
                    wikikg_demo.assert_local_settings_safe()

    def test_runtime_cache_directory_is_created_for_clean_clone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_directory = Path(directory) / "cache" / "sqlite"
            with mock.patch.object(
                wikikg_demo, "CACHE_DIRECTORY", cache_directory
            ):
                wikikg_demo.ensure_runtime_directories()

            self.assertTrue(cache_directory.is_dir())

    def test_compose_command_uses_isolated_project_and_overlay(self) -> None:
        command = wikikg_demo.compose_command("ps")

        self.assertEqual(command[:4], ["docker", "compose", "-p", "wikicrop-kg-demo"])
        self.assertIn(str(wikikg_demo.DEMO_COMPOSE_FILE), command)
        self.assertEqual(command[-1], "ps")

    def test_command_environment_disables_optional_services(self) -> None:
        environment = wikikg_demo.command_environment(9123)

        self.assertEqual(environment["MW_DOCKER_PORT"], "9123")
        self.assertEqual(environment["XDEBUG_ENABLE"], "false")
        self.assertEqual(environment["XHPROF_ENABLE"], "false")


if __name__ == "__main__":
    unittest.main()
