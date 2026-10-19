"""The three lab publishers share a no-overwrite and recovery contract."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[2] / "demo/synthetic"


def publisher(kind):
    spec = importlib.util.spec_from_file_location("publish_" + kind, BASE / kind / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResultPublicationTests(unittest.TestCase):
    def test_complete_private_result_and_only_own_staging_removed(self):
        for kind in ("mariadb", "chatbot", "restore"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                unrelated = root / ".results-unrelated"
                unrelated.write_bytes(b"keep unrelated evidence")
                publisher(kind).publish_result(root, {"status": "passed", "text": "đã kiểm tra"})
                self.assertEqual(json.loads((root / "results.json").read_text())["text"], "đã kiểm tra")
                self.assertEqual((root / "results.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(root.glob(".results-*")), [unrelated])

    def test_link_failure_preserves_complete_stage_without_publishing(self):
        for kind in ("mariadb", "chatbot", "restore"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                module = publisher(kind)
                def fail_install(source, target, **kwargs):
                    self.assertEqual(json.loads(Path(source).read_text()), {"status": "passed"})
                    self.assertFalse(Path(target).exists())
                    raise OSError("synthetic install failure")
                with patch.object(module.os, "link", side_effect=fail_install):
                    with self.assertRaises(OSError):
                        module.publish_result(root, {"status": "passed"})
                self.assertFalse((root / "results.json").exists())
                self.assertEqual(len(list(root.glob(".results-*"))), 1)

    def test_sync_failures_keep_recoverable_bytes_and_never_erase_installed_result(self):
        for kind in ("mariadb", "chatbot", "restore"):
            for failed_sync in (1, 2, 3):
                with self.subTest(kind=kind, failed_sync=failed_sync), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    module = publisher(kind)
                    real_sync = module.os.fsync
                    calls = 0
                    def sync(fd):
                        nonlocal calls
                        calls += 1
                        if calls == failed_sync:
                            raise OSError("synthetic sync failure")
                        return real_sync(fd)
                    with patch.object(module.os, "fsync", side_effect=sync):
                        with self.assertRaises(OSError):
                            module.publish_result(root, {"status": "passed"})
                    target = root / "results.json"
                    stages = list(root.glob(".results-*"))
                    self.assertTrue(target.exists() or stages)
                    for path in ([target] if target.exists() else []) + stages:
                        self.assertEqual(json.loads(path.read_text()), {"status": "passed"})
                    if failed_sync >= 2:
                        self.assertTrue(target.exists())

    def test_existing_result_and_oversize_payload_never_overwritten(self):
        for kind in ("mariadb", "chatbot", "restore"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                target = root / "results.json"
                target.write_bytes(b"foreign result")
                module = publisher(kind)
                with self.assertRaises(FileExistsError):
                    module.publish_result(root, {"status": "passed"})
                self.assertEqual(target.read_bytes(), b"foreign result")
                before = set(root.iterdir())
                with self.assertRaises(OSError):
                    module.publish_result(root, {"text": "x" * 65536})
                self.assertEqual(set(root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
