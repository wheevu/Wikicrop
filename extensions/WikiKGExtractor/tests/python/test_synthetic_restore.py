import importlib.util
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).parents[2] / "demo/synthetic/restore/run.py"
SPEC = importlib.util.spec_from_file_location("synthetic_restore", MODULE)
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


class RestoreSafetyTests(unittest.TestCase):
    def invoke_main(self, root, RehearsalClass=None):
        manifest = root.parent / "manifest.json"
        manifest.write_text("{}")
        manifest.chmod(0o600)
        argv = ["restore", "--private-root", str(root), "--corpus-manifest", str(manifest), "--synthetic-only"]
        stdout, stderr = io.StringIO(), io.StringIO()
        adapter = RehearsalClass
        if adapter is None:
            adapter = type("Adapter", (), {
                "__init__": lambda self, root, manifest: setattr(self, "root", root),
                "execute": lambda self: {"status": "passed", "synthetic_only": True},
                "cleanup": lambda self: True,
            })
        with mock.patch.object(restore, "Rehearsal", adapter), mock.patch.object(restore.sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = restore.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_main_publishes_private_complete_result_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            base.chmod(0o700)
            root = base / "fresh"
            code, stdout, _ = self.invoke_main(root)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["status"], "passed")
            report = root / "results.json"
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            before = report.read_bytes()
            code, stdout, _ = self.invoke_main(root)
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(report.read_bytes(), before)

    def test_publication_failure_has_no_success_stdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            base.chmod(0o700)
            root = base / "fresh"
            with mock.patch.object(restore, "publish_result", side_effect=OSError("blocked"), create=True):
                code, stdout, stderr = self.invoke_main(root)
            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertTrue(stderr)

    def test_main_preserves_result_obstructions_after_completed_rehearsal(self):
        for kind in ("file", "directory", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                base.chmod(0o700)
                root = base / "fresh"
                sentinel = base / "sentinel"
                sentinel.write_bytes(b"keep prior evidence")
                cleaned = []
                class Adapter:
                    def __init__(self, root, manifest):
                        self.root = root
                    def execute(self):
                        report = self.root / "results.json"
                        if kind == "directory":
                            report.mkdir(mode=0o700)
                            (report / "sentinel").write_bytes(b"keep prior evidence")
                        elif kind == "symlink":
                            report.symlink_to(sentinel)
                        else:
                            report.write_bytes(b"keep prior evidence")
                        return {"status": "passed", "synthetic_only": True}
                    def cleanup(self):
                        cleaned.append(True)
                        return True
                code, stdout, stderr = self.invoke_main(root, Adapter)
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr)["error"], "result_publication_failed")
                self.assertEqual(cleaned, [True])
                report = root / "results.json"
                actual = report / "sentinel" if kind == "directory" else report
                self.assertEqual(actual.read_bytes(), b"keep prior evidence")
                self.assertEqual(sentinel.read_bytes(), b"keep prior evidence")
                self.assertEqual(report.is_symlink(), kind == "symlink")
                self.assertEqual(len(list(root.glob(".results-*"))), 1)

    def test_interrupted_publication_retains_staging_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with mock.patch.object(restore.os, "link", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    restore.publish_result(root, {"status": "passed"})
            stages = list(root.glob(".results-*"))
            self.assertEqual(len(stages), 1)
            self.assertEqual(stages[0].stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / "results.json").exists())

    def test_cleanup_failure_publishes_failed_report_and_no_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            base.chmod(0o700)
            root = base / "fresh"
            adapter = type("Adapter", (), {
                "__init__": lambda self, root, manifest: None,
                "execute": lambda self: {"status": "passed"},
                "cleanup": lambda self: False,
            })
            code, stdout, _ = self.invoke_main(root, adapter)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stdout)["status"], "failed")

    def test_inputs_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            manifest = base / "manifest.json"
            manifest.write_text("{}")
            existing = base / "exists"
            existing.mkdir()
            for root in (str(existing), str(base / ".." / "escape"), str(manifest)):
                with self.assertRaises(ValueError):
                    restore.validate(root, str(manifest))
            link = base / "link"
            link.symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                restore.validate(str(link / "fresh"), str(manifest))

    def test_overlapping_root_and_manifest_parent_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "new-root"
            root.mkdir(mode=0o700)
            manifest = root / "manifest.json"
            manifest.write_text("{}")
            manifest.chmod(0o600)
            root.chmod(0o700)
            with self.assertRaises(ValueError):
                restore.validate(str(root), str(manifest))

    def test_wal_sidecars_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, backup = base / "source.db", base / "backup.db"
            sqlite3.connect(source).close()
            Path(str(source) + "-wal").touch()
            with self.assertRaises(ValueError):
                restore.backup_database(source, backup)

    def test_sqlite_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, backup, target = (base / name for name in ("source.db", "backup.db", "target.db"))
            db = sqlite3.connect(source)
            db.executescript("CREATE TABLE records(id INTEGER PRIMARY KEY, body TEXT); CREATE TABLE events(id INTEGER PRIMARY KEY, record_id INTEGER, status TEXT);")
            db.execute("INSERT INTO records VALUES(1, 'claim')")
            db.executemany("INSERT INTO events(record_id,status) VALUES(1,?)", [("approved",), ("rejected",)])
            db.commit()
            db.close()
            restore.backup_database(source, backup)
            sqlite3.connect(target).close()
            restore.restore_database(backup, target)
            db = sqlite3.connect(target)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(db.execute("SELECT * FROM records").fetchall(), [(1, "claim")])
            self.assertEqual(db.execute("SELECT status FROM events ORDER BY id").fetchall(), [("approved",), ("rejected",)])
            db.close()

    def test_backup_never_overwrites_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source, backup = base / "source.db", base / "backup.db"
            sqlite3.connect(source).close()
            backup.write_bytes(b"keep existing backup")
            with self.assertRaises(FileExistsError):
                restore.backup_database(source, backup)
            self.assertEqual(backup.read_bytes(), b"keep existing backup")

    def test_root_parent_must_be_owner_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            inputs = base / "inputs"
            inputs.mkdir(mode=0o700)
            manifest = inputs / "manifest.json"
            manifest.write_text("{}")
            manifest.chmod(0o600)
            base.chmod(0o755)
            with self.assertRaises(ValueError):
                restore.validate(str(base / "new"), str(manifest))
            base.chmod(0o700)
            self.assertEqual(restore.validate(str(base / "new"), str(manifest))[0], base / "new")


if __name__ == "__main__":
    unittest.main()
