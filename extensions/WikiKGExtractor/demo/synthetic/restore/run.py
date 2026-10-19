#!/usr/bin/env python3
"""Rehearse guarded, quiesced SQLite recovery in disposable synthetic wikis."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
SYNTHETIC = HERE.parent
REPO = HERE.parents[4]
LIFECYCLE = SYNTHETIC / "run.py"
COMPOSE = SYNTHETIC / "compose.yml"
CAPTURE = HERE / "capture.php"
DB_RELATIVE = Path("db/syntheticwiki.sqlite")


def validate(root_arg: str, manifest_arg: str) -> tuple[Path, Path]:
    root, manifest = Path(root_arg).expanduser(), Path(manifest_arg).expanduser()
    for path in (root, manifest):
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe path")
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError("symlink path")
    if root.exists() or root == REPO or REPO in root.parents or root == Path("/var/www/html") or Path("/var/www/html") in root.parents:
        raise ValueError("root must be fresh, outside checkout and webroot")
    if root == manifest or root in manifest.parents or manifest in root.parents:
        raise ValueError("overlapping paths")
    parent_info = root.parent.stat()
    if parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) != 0o700:
        raise ValueError("root parent must be owner-private")
    if not manifest.is_file():
        raise ValueError("manifest unavailable")
    info, parent = manifest.stat(), manifest.parent.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("manifest must be owner-private")
    return root, manifest.resolve(strict=True)


def backup_database(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file() or any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError("source database or sidecars unsafe")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    # Writers and sidecars must already be absent. Immutable mode avoids creating
    # WAL bookkeeping when reading a quiesced WAL-mode file through host sharing.
    src = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("backup integrity failure")
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(destination, 0o600)


def restore_database(backup: Path, target: Path) -> None:
    if backup.is_symlink() or not backup.is_file() or target.is_symlink() or not target.is_file() or backup == target or any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError("fresh target database or sidecars unsafe")
    src = sqlite3.connect(backup.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("restore integrity failure")
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(target, 0o600)


def publish_result(root: Path, result: dict) -> None:
    """Durably install a complete private report without replacing prior data."""
    data = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(data) > 65536:
        raise OSError("result exceeds publication bound")
    fd, temporary_name = tempfile.mkstemp(prefix=".results-", dir=root)
    temporary = Path(temporary_name)
    dirfd = -1
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(fd)
        os.link(temporary, root / "results.json", follow_symlinks=False)
        dirfd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        os.fsync(dirfd)
        os.unlink(temporary)
        os.fsync(dirfd)
    finally:
        if dirfd >= 0:
            os.close(dirfd)
        os.close(fd)


class Rehearsal:
    def __init__(self, root: Path, manifest: Path):
        self.root, self.manifest = root, manifest
        self.logs = root / "logs"
        self.logs.mkdir(mode=0o700, parents=True)
        self.projects: dict[str, tuple[str, Path]] = {}
        self.command_index = 0

    def run(self, name: str, argv: list[str], *, env: dict[str, str] | None = None) -> str:
        self.command_index += 1
        log_path = self.logs / f"{self.command_index:03d}-{name}.log"
        if env is None:
            env = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
        with log_path.open("xb") as log:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    env=env, cwd=REPO, timeout=900)
        if result.returncode:
            raise RuntimeError(f"{name} failed with code {result.returncode}")
        return log_path.read_text()

    def lifecycle(self, label: str, action: str, root: Path) -> None:
        argv = [sys.executable, str(LIFECYCLE), action, "--private-root", str(root), "--synthetic-only"]
        if action == "setup":
            argv += ["--corpus-manifest", str(self.manifest)]
        try:
            self.run(f"{label}-{action}", argv)
        finally:
            project_file = root / ".compose-project"
            if project_file.is_file() and not project_file.is_symlink():
                project = project_file.read_text().strip()
                if re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project):
                    self.projects[root.name] = (project, root)

    def identity(self, label: str, root: Path) -> tuple[str, str]:
        project_file = root / ".compose-project"
        project = project_file.read_text().strip()
        if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project):
            raise RuntimeError("invalid owned project identity")
        if (project, root) not in self.projects.values():
            raise RuntimeError("project was not created by this rehearsal")
        output = self.run(label + "-inspect", ["docker", "ps", "-aq", "--no-trunc",
            "--filter", "label=com.docker.compose.project=" + project,
            "--filter", "label=com.docker.compose.service=wiki",
            "--filter", "label=com.docker.compose.oneoff=False"])
        containers = output.strip().splitlines()
        if len(containers) != 1:
            raise RuntimeError("expected one owned wiki container")
        container = containers[0]
        if not re.fullmatch(r"[0-9a-f]{12,64}", container):
            raise RuntimeError("could not identify exact owned container")
        inspect = json.loads(self.run(label + "-inspect-json", ["docker", "inspect", container]))[0]
        labels = inspect["Config"]["Labels"] or {}
        mounts = inspect["Mounts"]
        if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != "wiki":
            raise RuntimeError("container project/service label mismatch")
        private_mount = [m for m in mounts if m.get("Destination") == "/private/wikikg"]
        if len(private_mount) != 1 or Path(private_mount[0]["Source"]).resolve() != root.resolve():
            raise RuntimeError("private mount identity mismatch")
        if not inspect["HostConfig"]["ReadonlyRootfs"]:
            raise RuntimeError("wiki root filesystem is not read-only")
        for network in inspect["NetworkSettings"]["Networks"]:
            details = json.loads(self.run(label + "-network", ["docker", "network", "inspect", network]))[0]
            if not details["Internal"]:
                raise RuntimeError("wiki network is not internal")
        return container, inspect["State"]["Status"]

    def assert_stopped(self, label: str, root: Path) -> None:
        _, status = self.identity(label, root)
        if status != "exited":
            raise RuntimeError(f"{label} container is not stopped")
        project = (root / ".compose-project").read_text().strip()
        if self.run(label + "-writers", ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + project]).strip():
            raise RuntimeError("owned project still has running writers")
        db = root / DB_RELATIVE
        if any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise RuntimeError(f"{label} database has WAL/SHM sidecars")

    def capture(self, label: str, root: Path) -> dict:
        container, status = self.identity(label, root)
        if status != "running":
            raise RuntimeError("capture requires running wiki")
        out = self.run(label + "-capture", ["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}", container, "php",
            "/var/www/html/w/extensions/WikiKGExtractor/demo/synthetic/restore/capture.php"])
        doc = json.loads(out)
        (self.logs / f"{label}-capture.json").write_text(json.dumps(doc, sort_keys=True))
        os.chmod(self.logs / f"{label}-capture.json", 0o600)
        return doc

    def cleanup(self) -> bool:
        clean = True
        for label, (project, root) in list(self.projects.items()):
            try:
                container, _ = self.identity(label + "-cleanup", root)
                self.run(label + "-cleanup-stop", ["docker", "stop", container])
                self.assert_stopped(label + "-cleanup", root)
            except Exception:
                clean = False
                print(f"Cleanup required for owned project {project}: verify labels, then docker compose -p {project} -f {COMPOSE} stop wiki", file=sys.stderr)
        return clean

    def execute(self) -> dict:
        source, target = self.root / "source", self.root / "target"
        self.lifecycle("source", "setup", source)
        self.lifecycle("source", "start", source)
        self.lifecycle("source", "workflow", source)
        initial = self.capture("source-initial", source)
        records = initial["records"]
        if len(records) != 9 or any(len(r["review_events"]) != 1 for r in records):
            raise RuntimeError("source workflow did not yield nine singly-reviewed claims")
        snapshot = records[0]["snapshot_id"]
        container, _ = self.identity("source", source)
        self.run("source-reject", ["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}", container, "php",
            "/var/www/html/w/extensions/WikiKGExtractor/maintenance/reviewOfflineClaim.php", "--actor=Synthetic reviewer",
            "--offline-only", "--audience=actor-only", "--synthetic-only", "--snapshot=" + snapshot,
            "--status=rejected", "--expected-version=1", "--reason=Synthetic restore fixture only"])
        before = self.capture("source-rejected", source)
        changed = [r for r in before["records"] if r["snapshot_id"] == snapshot]
        if len(changed) != 1 or len(changed[0]["review_events"]) != 2 or changed[0]["latest_review"]["status"] != "rejected":
            raise RuntimeError("expected append-only rejection event missing")
        self.lifecycle("source", "stop", source)
        self.assert_stopped("source", source)
        source_db = source / DB_RELATIVE
        source_digest = hashlib.sha256(source_db.read_bytes()).hexdigest()
        backup = self.root / "backup" / "syntheticwiki.sqlite"
        backup_database(source_db, backup)
        self.lifecycle("target", "setup", target)
        self.lifecycle("target", "start", target)
        self.lifecycle("target", "stop", target)
        self.assert_stopped("target", target)
        target_db = target / DB_RELATIVE
        if not target_db.is_file():
            raise RuntimeError("target setup database missing")
        restore_database(backup, target_db)
        self.lifecycle("target", "start", target)
        restored = self.capture("target-restored", target)
        if restored != before:
            raise RuntimeError("restored records/source hashes differ")
        if hashlib.sha256(source_db.read_bytes()).hexdigest() != source_digest:
            raise RuntimeError("source database changed after backup")
        self.lifecycle("target", "stop", target)
        return {"status": "passed", "synthetic_only": True, "scope": "quiesced SQLite recovery only; graph and uploads not tested",
                 "records": len(before["records"]), "rejected_snapshot": snapshot, "backup": str(backup),
                 "source_unchanged": True, "review_events": sum(len(r["review_events"]) for r in before["records"]),
                 "sources": len(before["sources"]), "integrity": "ok", "full_records_equal": True}


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--corpus-manifest", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    args = parser.parse_args()
    try:
        root, manifest = validate(args.private_root, args.corpus_manifest)
    except Exception:
        print(json.dumps({"status": "refused", "synthetic_only": True}), file=sys.stderr)
        return 2
    try:
        root.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        print(json.dumps({"status": "refused", "synthetic_only": True}), file=sys.stderr)
        return 2
    os.chmod(root, 0o700)
    rehearsal = Rehearsal(root, manifest)
    try:
        result = rehearsal.execute()
        code = 0
    except Exception as error:
        (rehearsal.logs / "failure.log").write_text(traceback.format_exc())
        result = {"status": "failed", "synthetic_only": True, "scope": "quiesced SQLite recovery only; graph and uploads not tested",
                  "error": type(error).__name__}
        code = 1
    finally:
        if not rehearsal.cleanup():
            result.update(status="failed", error="owned_cleanup_failed")
            code = 1
    try:
        publish_result(root, result)
    except OSError:
        print(json.dumps({"status": "failed", "synthetic_only": True, "error": "result_publication_failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
