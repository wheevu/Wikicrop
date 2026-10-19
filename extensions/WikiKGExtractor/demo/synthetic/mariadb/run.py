#!/usr/bin/env python3
"""One-shot, private MariaDB compatibility lab. Never manages an existing project."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
COMPOSE = HERE / "compose.yml"
PROJECT_RE = re.compile(r"wikikg-mariadb-[0-9a-f]{16}\Z")


class Refusal(Exception):
    pass


def safe_new_root(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise Refusal("unsafe_private_root")
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise Refusal("unsafe_private_root")
    parent = path.parent.resolve(strict=True)
    if path.exists() or path.is_symlink():
        raise Refusal("private_root_must_not_exist")
    if parent == REPO or REPO in parent.parents or parent == Path("/var/www/html") or Path("/var/www/html") in parent.parents:
        raise Refusal("unsafe_private_root")
    info = parent.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refusal("unsafe_private_root")
    return parent / path.name


def publish_result(root: Path, result: dict) -> None:
    """Publish complete result bytes without replacing an existing file."""
    data = (json.dumps(result, indent=2) + "\n").encode()
    if len(data) > 65536:
        raise OSError("result exceeds publication bound")
    fd, temporary_name = tempfile.mkstemp(prefix=".results-", dir=root)
    temporary = Path(temporary_name)
    dirfd = -1
    try:
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


def owned_root(root: Path | None, identity: tuple[int, int] | None) -> bool:
    if root is None or identity is None:
        return False
    try:
        info = root.lstat()
        return (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700
                and (info.st_dev, info.st_ino) == identity)
    except OSError:
        return False


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Install fresh MediaWiki on isolated MariaDB and exercise real extension storage.")
    parser.add_argument("--private-root", required=True, help="new owner-only directory outside checkout and web root; results retained at <private-root>/results.json")
    parser.add_argument("--synthetic-only", action="store_true", required=True, help="required safety acknowledgement")
    parser.add_argument("--wiki-image", default="wikikg-synthetic-wiki:1.43.1-php8.3.14", help="cached MediaWiki 1.43.1/PHP 8.3.14 image")
    args = parser.parse_args()
    root: Path | None = None
    project = ""
    started = False
    acquired = False
    root_identity: tuple[int, int] | None = None
    result: dict = {"status": "failed", "synthetic_only": True,
                    "scope": "MediaWiki MySQL adapter/schema/store compatibility only",
                    "runtime": {}, "cases": {},
                    "limitations": ["Not full offline authorization or production compatibility."]}
    try:
        root = safe_new_root(args.private_root)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", args.wiki_image):
            raise Refusal("invalid_wiki_image")
        root.mkdir(mode=0o700)
        acquired = True
        os.chmod(root, 0o700)
        info = root.lstat()
        root_identity = (info.st_dev, info.st_ino)
        project = "wikikg-mariadb-" + secrets.token_hex(8)
        for name, value in ((".compose-project", project), ("synthetic-marker", "fresh"),
                            ("secret-key", secrets.token_hex(32)), ("db-password", secrets.token_urlsafe(36)),
                            ("root-password", secrets.token_urlsafe(36)), ("admin-password", secrets.token_urlsafe(36))):
            target = root / name
            target.write_text(value, encoding="utf-8")
            os.chmod(target, 0o600)
        settings_dir = root / "install"
        settings_dir.mkdir(mode=0o700)
        os.chmod(settings_dir, 0o700)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()),
               "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")),
               "WIKIKG_PRIVATE_ROOT": str(root), "WIKIKG_WIKI_IMAGE": args.wiki_image,
               "COMPOSE_PROJECT_NAME": project,
               "WIKIKG_UID": str(os.getuid()), "WIKIKG_GID": str(os.getgid())}
        fd, log_path = tempfile.mkstemp(prefix="commands-", suffix=".log", dir=root)

        def compose(*command: str) -> None:
            nonlocal started
            if command[:2] == ("up", "-d"):
                started = True
            with os.fdopen(os.dup(fd), "wb") as log:
                log.write((json.dumps({"command": list(command)}) + "\n").encode())
                log.flush()
                completed = subprocess.run(["docker", "compose", "--env-file", "/dev/null", "-p", project,
                    "-f", str(COMPOSE), *command], cwd=REPO, env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=False, timeout=300)
            if completed.returncode:
                raise Refusal("runtime_command_failed")

        try:
            image = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", args.wiki_image],
                cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, check=False)
            if image.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}\n?", image.stdout):
                raise Refusal("wiki_image_unavailable_or_invalid")
            result["runtime"]["wiki_image_id"] = image.stdout.strip()
            env["WIKIKG_WIKI_IMAGE"] = image.stdout.strip()
            compose("up", "-d", "--wait", "db")
            compose("run", "--no-deps", "wiki", "php", "maintenance/install.php",
                    "--dbtype", "mysql", "--dbserver", "db", "--dbname", "synthetic_mariadb",
                    "--dbuser", "wikikg_synthetic", "--dbpassfile", "/private/wikikg/db-password",
                    "--passfile", "/private/wikikg/admin-password", "--confpath", "/private/wikikg/install",
                    "--server", "http://localhost", "--scriptpath", "/w", "--lang", "en",
                     "Synthetic MariaDB Compatibility", "Synthetic reviewer")
            # Use the dedicated settings, never the installer's DB credential config.
            (root / "install" / "LocalSettings.php").chmod(0o600)
            (root / "LocalSettings.synthetic.php").write_text(
                "<?php\nrequire '/var/www/html/w/extensions/WikiKGExtractor/demo/synthetic/mariadb/settings.php';\n",
                encoding="utf-8")
            os.chmod(root / "LocalSettings.synthetic.php", 0o600)
            for index in (1, 2):
                compose("run", "--no-deps", "wiki", "php", "maintenance/run.php", "update", "--quick")
                result["runtime"][f"updater_pass_{index}"] = "passed"
            compose("run", "--no-deps", "wiki", "php",
                    "extensions/WikiKGExtractor/demo/synthetic/mariadb/fixture.php")
            result["runtime"]["fixture"] = "passed"
            log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")
            fixture_lines = [line for line in log_text.splitlines() if '"server_version"' in line]
            if not fixture_lines:
                raise Refusal("fixture_result_missing")
            fixture_result = json.loads(fixture_lines[-1])
            result["runtime"]["server_version"] = fixture_result["server_version"]
            result["runtime"]["updater_passes"] = 2
            result["runtime"]["database"] = "synthetic_mariadb"
            result["cases"] = {"schema_tables_and_indexes": "passed", "page_revision_save": "passed",
                "idempotent_import": "passed", "collision_rejection": "passed", "pending_read": "passed",
                "approved_then_rejected": "passed", "stale_version_no_event": "passed",
                "append_only_history_and_evidence_invariants": "passed"}
            result["status"] = "passed"
        finally:
            os.close(fd)
            if started:
                cleanup_failed = False
                try:
                    # Candidate enumeration is constrained to this invocation's random project ID.
                    ids = subprocess.check_output(["docker", "ps", "-aq", "--no-trunc", "--filter",
                        "label=com.docker.compose.project=" + project], env=env, timeout=30).decode().split()
                except Exception:
                    ids = []
                    cleanup_failed = True
                for container_id in ids:
                    try:
                        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
                            cleanup_failed = True
                            continue
                        details = json.loads(subprocess.check_output(["docker", "inspect", container_id], env=env, timeout=30))[0]
                        labels = details["Config"]["Labels"]
                        private = [m for m in details["Mounts"] if m["Destination"] == "/private/wikikg"]
                        if (details["Id"] != container_id or labels.get("com.docker.compose.project") != project or len(private) != 1
                                or Path(private[0]["Source"]).resolve() != root):
                            cleanup_failed = True
                            continue
                        if details["State"]["Running"]:
                            subprocess.run(["docker", "stop", "-t", "10", container_id], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=True, timeout=30)
                    except Exception:
                        cleanup_failed = True
                if cleanup_failed:
                    result["status"] = "failed"
                    result["error"] = "cleanup_failed"
    except Refusal as error:
        result["status"] = "failed"
        result["error"] = str(error)
    except Exception:
        if acquired and owned_root(root, root_identity):
            try:
                (root / "failure.log").write_text(traceback.format_exc())
            except OSError:
                pass
        result["status"] = "failed"
        result["error"] = "runtime_failed"
    if acquired and owned_root(root, root_identity):
        try:
            publish_result(root, result)
        except OSError:
            result["status"] = "failed"
            result["error"] = "result_publication_failed"
    elif result["status"] == "passed":
        result["status"] = "failed"
        result["error"] = "private_root_unavailable"
    if result["status"] == "passed":
        print(json.dumps({"status": "passed", "synthetic_only": True, "results": str(root / "results.json")}))
        return 0
    print(json.dumps({"status": "failed", "synthetic_only": True,
                      "error": result.get("error", "runtime_failed")} ), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
