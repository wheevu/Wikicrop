#!/usr/bin/env python3
"""Guarded lifecycle for the isolated synthetic WikiKG verification wiki."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import re

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
COMPOSE = HERE / "compose.yml"
ERRORS = {
    "invalid_arguments": 2, "unsafe_private_root": 3, "not_fresh": 4,
    "invalid_manifest": 5, "compose_failed": 6, "not_setup": 7,
}


class Refusal(Exception):
    def __init__(self, code: str):
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"status": "refused", "error": "invalid_arguments"}), file=sys.stderr)
        raise SystemExit(ERRORS["invalid_arguments"])


def fail(code: str) -> None:
    print(json.dumps({"status": "refused", "error": code}), file=sys.stderr)
    raise SystemExit(ERRORS[code])


def safe_root(value: str, fresh: bool = False) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise Refusal("unsafe_private_root")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Refusal("unsafe_private_root")
    try:
        real = path.resolve(strict=True)
    except FileNotFoundError:
        if not fresh:
            raise Refusal("not_setup")
        real = path
    if real == REPO or REPO in real.parents or real == Path("/var/www/html") or Path("/var/www/html") in real.parents:
        raise Refusal("unsafe_private_root")
    if fresh:
        if path.exists():
            raise Refusal("not_fresh")
        path.mkdir(mode=0o700, parents=True)
        os.chmod(path, 0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refusal("unsafe_private_root")
    return path


def private_file(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600
    except OSError:
        return False


def compose(root: Path, *args: str) -> None:
    project_file = root / ".compose-project"
    if not private_file(project_file):
        raise Refusal("not_setup")
    project = project_file.read_text().strip()
    if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project):
        raise Refusal("not_setup")
    port = os.environ.get("WIKIKG_HTTP_PORT", "8787")
    if not port.isascii() or not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise Refusal("invalid_arguments")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()), "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")),
        "WIKIKG_PRIVATE_ROOT": str(root), "WIKIKG_UID": str(os.getuid()),
        "WIKIKG_GID": str(os.getgid()), "WIKIKG_HTTP_PORT": os.environ.get("WIKIKG_HTTP_PORT", "8787"),
        "COMPOSE_PROJECT_NAME": project,
    }
    # No inherited Compose interpolation, proxy, cloud credentials, or runtime
    # secrets. Startup and all later container networking remain internal.
    arguments = list(args)
    if arguments and arguments[0] in ("run", "exec"):
        if "--user" not in arguments:
            arguments[1:1] = ["--user", f"{os.getuid()}:{os.getgid()}"]
    fd, log_name = tempfile.mkstemp(prefix="command-", suffix=".log", dir=root)
    with os.fdopen(fd, "wb") as log:
        result = subprocess.run(["docker", "compose", "--env-file", "/dev/null", "-p", project,
                                 "-f", str(COMPOSE), *arguments], cwd=REPO,
                                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    if result.returncode:
        raise Refusal("compose_failed")


def parser() -> argparse.ArgumentParser:
    p = Parser(description="Build and run isolated synthetic WikiKG verification.")
    p.add_argument("action", choices=("setup", "start", "serve", "workflow", "test", "stop"))
    p.add_argument("--private-root", required=True, help="new owner-only directory outside checkout and web root")
    p.add_argument("--corpus-manifest", help="owner-only pinned corpus metadata input, required for setup")
    p.add_argument("--synthetic-only", action="store_true", required=True, help="required safety acknowledgement")
    return p


def main() -> None:
    try:
        os.umask(0o077)
        args = parser().parse_args()
        manifest = None
        if args.action == "setup":
            if not args.corpus_manifest:
                raise Refusal("invalid_manifest")
            supplied = Path(args.corpus_manifest).expanduser()
            if not supplied.is_absolute() or ".." in supplied.parts:
                raise Refusal("invalid_manifest")
            current = Path(supplied.anchor)
            for part in supplied.parts[1:]:
                current /= part
                if current.is_symlink():
                    raise Refusal("invalid_manifest")
            manifest = supplied.resolve(strict=True)
            parent = manifest.parent.stat()
            if (not private_file(manifest) or parent.st_uid != os.geteuid()
                    or stat.S_IMODE(parent.st_mode) != 0o700):
                raise Refusal("invalid_manifest")
            sys.path.insert(0, str(HERE.parents[1] / "tools"))
            import bounded_discovery
            if (manifest.stat().st_size > bounded_discovery.MAX_MANIFEST_BYTES
                    or hashlib.sha256(manifest.read_bytes()).hexdigest() != bounded_discovery.MANIFEST_SHA256):
                raise Refusal("invalid_manifest")
        root = safe_root(args.private_root, fresh=args.action == "setup")
        if args.action == "setup":
            # Input is pinned by bounded_discovery's existing frozen hash check.
            shutil.copyfile(manifest, root / "corpus-metadata.json")
            os.chmod(root / "corpus-metadata.json", 0o600)
            for name, content in (("synthetic-marker", "fresh"), ("secret-key", secrets.token_hex(32)),
                                  ("reviewer-password", secrets.token_urlsafe(32))):
                (root / name).write_text(content)
                os.chmod(root / name, 0o600)
            (root / ".compose-project").write_text("wikikg-synthetic-" + secrets.token_hex(8))
            os.chmod(root / ".compose-project", 0o600)
            for name in ("db", "test-db", "installer-settings", "installer-test-settings",
                         "wiki-cache", "test-cache", "test-temp", "exports", "posix"):
                (root / name).mkdir(mode=0o700)
            config = "/var/www/html/w/extensions/WikiKGExtractor/demo/synthetic/SyntheticSettings.php"
            compose(root, "build", "wiki")
            # This randomly named project's new volume has no data to replace.
            # Change only its root and newly created test/run directories.
            compose(root, "run", "--no-deps", "--user", "0:0", "wiki", "python3", "-c",
                    "import pathlib,os; p=pathlib.Path('/private/wikikg/posix'); "
                    "assert not list(p.iterdir()); "
                    "[(q.mkdir(mode=0o700), os.chown(q," + str(os.getuid()) + "," + str(os.getgid()) + ")) "
                    "for q in (p/'runs', p/'test-temp')]; "
                    f"os.chmod(p,0o700); os.chown(p,{os.getuid()},{os.getgid()})")
            # Credential is supplied via private passfile; never placed in argv or logs.
            compose(root, "run", "--no-deps", "wiki", "php", "maintenance/install.php",
                    "--dbtype", "sqlite", "--dbpath", "/private/wikikg/db", "--dbname", "syntheticwiki",
                    "--server", "http://127.0.0.1:" + os.environ.get("WIKIKG_HTTP_PORT", "8787"),
                    "--scriptpath", "/w", "--lang", "en", "--passfile", "/private/wikikg/reviewer-password",
                     "--confpath", "/private/wikikg/installer-settings",
                    "Synthetic WikiKG Verification", "Synthetic reviewer")
            # Keep the installer's generated config private; use a small explicit config.
            os.chmod(root / "installer-settings" / "LocalSettings.php", 0o600)
            compose(root, "run", "--no-deps", "-e", "WIKIKG_SYNTHETIC_TEST=1", "wiki", "php",
                    "maintenance/install.php", "--dbtype", "sqlite", "--dbpath", "/private/wikikg/test-db",
                    "--dbname", "wikitest", "--server", "http://127.0.0.1:" + os.environ.get("WIKIKG_HTTP_PORT", "8787"),
                    "--scriptpath", "/w", "--lang", "en", "--passfile", "/private/wikikg/reviewer-password",
                     "--confpath", "/private/wikikg/installer-test-settings",
                    "Synthetic WikiKG Test Database", "Synthetic reviewer")
            os.chmod(root / "installer-test-settings" / "LocalSettings.php", 0o600)
            (root / "LocalSettings.synthetic.php").write_text(
                "<?php\n$wgSitename = 'Synthetic WikiKG Verification';\n"
                "$wgMetaNamespace = 'Synthetic_WikiKG_Verification';\n"
                "require " + repr(str(config)) + ";\n"
                "wfLoadExtension( 'WikiKGExtractor' );\n"
            )
            os.chmod(root / "LocalSettings.synthetic.php", 0o600)
            compose(root, "run", "--no-deps", "wiki", "php", "maintenance/run.php", "update", "--quick")
            compose(root, "run", "--no-deps", "-e", "WIKIKG_SYNTHETIC_TEST=1", "wiki", "php",
                     "maintenance/run.php", "update", "--quick")
            compose(root, "run", "--no-deps", "wiki", "php",
                     "extensions/WikiKGExtractor/demo/synthetic/fixture.php")
            print(json.dumps({"status": "ready", "synthetic_only": True, "fresh_databases": 2}))
            return
        if not private_file(root / "LocalSettings.synthetic.php") or not private_file(root / "corpus-metadata.json"):
            raise Refusal("not_setup")
        if args.action == "start":
            compose(root, "up", "-d", "--no-build", "wiki")
        elif args.action == "serve":
            from serve import serve
            serve(root, int(os.environ.get("WIKIKG_HTTP_PORT", "8787")))
        elif args.action == "workflow":
            compose(root, "exec", "-T", "wiki", "python3",
                    "/var/www/html/w/extensions/WikiKGExtractor/demo/synthetic/workflow.py")
        elif args.action == "test":
            compose(root, "exec", "-T", "-e", "WIKIKG_SYNTHETIC_TEST=1", "wiki", "php",
                      "tests/phpunit/phpunit.php", "--do-not-cache-result", "extensions/WikiKGExtractor/tests/phpunit")
            compose(root, "exec", "-T", "-e", "TMPDIR=/private/wikikg/posix/test-temp", "wiki", "python3",
                    "-B", "-m", "unittest", "discover", "-s", "extensions/WikiKGExtractor/tests/python",
                    "-p", "test_*.py")
        elif args.action == "stop":
            compose(root, "stop", "wiki")
        print(json.dumps({"status": "completed", "action": args.action, "synthetic_only": True}))
    except Refusal as error:
        fail(error.code)
    except SystemExit:
        raise
    except Exception:
        fail("compose_failed")


if __name__ == "__main__":
    main()
