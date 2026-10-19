#!/usr/bin/env python3
"""Owned lifecycle for the graph-backed synthetic question demonstration."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SYNTHETIC = HERE.parent
REPO = HERE.parents[4]
EXTENSION = HERE.parents[2]
BASE_RUNNER = SYNTHETIC / "run.py"
COMPOSE = HERE / "compose.yml"
GRAPH_IMAGE = "neo4j:2026.09.0@sha256:29efb5ebfb51ec75545a945bc3ac0bdfbeb6bc2686a8ae5053fe0f4dcc926441"
PRIVATE_IPV4_POOLS = tuple(ipaddress.ip_network(value) for value in
                           ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def subnet_overlaps(configs, target: ipaddress.IPv4Network) -> bool:
    for config in configs or []:
        allocated = config.get("Subnet")
        if allocated:
            parsed = ipaddress.ip_network(allocated, strict=False)
            if isinstance(parsed, ipaddress.IPv4Network) and parsed.overlaps(target):
                return True
    return False


class Refusal(Exception):
    pass


def private(path: Path, mode: int) -> bool:
    try:
        st = path.lstat()
        return stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == mode
    except OSError:
        return False


def http_port(root: Path) -> int:
    path = root / "wiki" / ".synthetic-http-port"
    if not private(path, 0o600):
        raise Refusal("not_setup")
    value = path.read_text().strip()
    if not value.isascii() or not value.isdigit() or not 1024 <= int(value) <= 65535:
        raise Refusal("not_setup")
    return int(value)


def root_path(value: str, fresh: bool = False) -> Path:
    path = Path(value).expanduser()
    web = Path("/var/www/html")
    if (not path.is_absolute() or ".." in path.parts or path == REPO or REPO in path.parents
            or path == web or web in path.parents):
        raise Refusal("unsafe_private_root")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise Refusal("unsafe_private_root")
    if fresh:
        if path.exists():
            raise Refusal("not_fresh")
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)
    if not path.is_dir() or path.stat().st_uid != os.geteuid() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise Refusal("unsafe_private_root")
    return path


def run(argv: list[str], *, env: dict[str, str], log: Path | None = None, timeout: int = 180) -> None:
    if log:
        with log.open("ab") as output:
            os.chmod(log, 0o600)
            result = subprocess.run(argv, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
    else:
        result = subprocess.run(argv, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    if result.returncode:
        raise Refusal("command_failed")


def compose(root: Path, *argv: str) -> None:
    project_file = root / "wiki" / ".compose-project"
    if not private(project_file, 0o600):
        raise Refusal("not_setup")
    project = project_file.read_text().strip()
    if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project):
        raise Refusal("not_setup")
    port_file = root / "wiki" / ".synthetic-http-port"
    if not private(port_file, 0o600):
        raise Refusal("not_setup")
    port = port_file.read_text().strip()
    if not port.isascii() or not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise Refusal("not_setup")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()),
           "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")),
           "WIKIKG_QUESTIONS_ROOT": str(root), "WIKIKG_UID": str(os.getuid()),
           "WIKIKG_GID": str(os.getgid()), "COMPOSE_PROJECT_NAME": project,
           "WIKIKG_HTTP_PORT": port}
    image_file = root / ".wiki-image-id"
    if not private(image_file, 0o600):
        raise Refusal("wiki_image_missing")
    image_id = image_file.read_text().strip()
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise Refusal("wiki_image_missing")
    env["WIKIKG_WIKI_IMAGE"] = image_id
    password_file = root / "wiki" / "connected" / "graph-password"
    if not private(password_file, 0o600):
        raise Refusal("not_setup")
    env["NEO4J_AUTH"] = "neo4j/" + password_file.read_text().rstrip("\n")
    command = ["docker", "compose", "--env-file", "/dev/null", "-p", project, "-f", str(COMPOSE)]
    override = root / "private-compose.yml"
    if override.exists():
        if not private(override, 0o600):
            raise Refusal("not_setup")
        command += ["-f", str(override)]
    result = subprocess.run(command + list(argv), cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    with (root / "compose-commands.log").open("a") as log:
        log.write(json.dumps({"action": argv[0] if argv else "", "arguments": list(argv),
                              "exit": result.returncode}, sort_keys=True) + "\n")
    os.chmod(root / "compose-commands.log", 0o600)
    if result.returncode:
        raise Refusal("compose_failed")


def base(root: Path, action: str, manifest: str | None = None, subnet: str | None = None) -> None:
    command = [sys.executable, str(BASE_RUNNER), action, "--private-root", str(root / "wiki"), "--synthetic-only"]
    if manifest:
        command += ["--corpus-manifest", manifest]
    if subnet:
        command += ["--subnet", subnet]
    port_file = root / "wiki" / ".synthetic-http-port"
    port = str(http_port(root)) if private(port_file, 0o600) else os.environ.get("WIKIKG_HTTP_PORT", "8787")
    run(command, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()),
                      "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")),
                      "WIKIKG_HTTP_PORT": port},
        log=root / "base-lifecycle.log", timeout=1800)


def wiki_id(root: Path) -> str:
    project_file = root / "wiki" / ".compose-project"
    if not private(project_file, 0o600):
        raise Refusal("not_setup")
    project = project_file.read_text().strip()
    if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project):
        raise Refusal("not_setup")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    ids = subprocess.check_output(["docker", "ps", "-q", "--no-trunc", "--filter",
        "label=com.docker.compose.project=" + project, "--filter", "label=com.docker.compose.service=wiki"],
        env=env, timeout=20, stderr=subprocess.DEVNULL).decode().split()
    if len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{64}", ids[0]):
        raise Refusal("owned_wiki_missing")
    inspect_owned_wiki(root, ids[0], project)
    return ids[0]


def inspect_owned_wiki(root: Path, container_id: str, project: str) -> None:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    record = json.loads(subprocess.check_output(["docker", "inspect", container_id], env=env,
        stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20))[0]
    labels = record.get("Config", {}).get("Labels", {})
    runtime = dict(item.split("=", 1) for item in record.get("Config", {}).get("Env", []) if "=" in item)
    mounts = {item.get("Destination"): item for item in record.get("Mounts", [])}
    private_mount = mounts.get("/private/wikikg", {})
    code_mount = mounts.get("/var/www/html/w/extensions/WikiKGExtractor", {})
    if (labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != "wiki"
            or runtime.get("WIKIKG_SYNTHETIC") != "1"
            or runtime.get("MW_CONFIG_FILE") != "/private/wikikg/LocalSettings.synthetic.php"
            or Path(private_mount.get("Source", "/")).resolve() != (root / "wiki").resolve()
            or private_mount.get("RW") is not True
            or Path(code_mount.get("Source", "/")).resolve() != EXTENSION
            or code_mount.get("RW") is not False
            or record.get("HostConfig", {}).get("ReadonlyRootfs") is not True
            or "NEO4J_AUTH" in runtime):
        raise Refusal("foreign_wiki_container")
    for network in record.get("NetworkSettings", {}).get("Networks", {}):
        details = json.loads(subprocess.check_output(["docker", "network", "inspect", network], env=env,
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20))[0]
        if details.get("Internal") is not True:
            raise Refusal("foreign_wiki_container")


def in_wiki(root: Path, *argv: str, timeout: int = 90) -> bytes:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    result = subprocess.run(["docker", "exec", "--user", f"{os.getuid()}:{os.getgid()}",
                             wiki_id(root), *argv],
                           env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
    log_path = root / "wiki" / "connected" / "setup.log"
    with log_path.open("ab") as log:
        log.write(result.stdout[:100000])
    if result.returncode:
        raise Refusal("wiki_command_failed")
    return result.stdout


def load_graph(root: Path, manifest: str = "/private/wikikg/connected/reviewed.json") -> None:
    # The pinned reviewed_projection loader owns all graph writes and validation.
    script = ("import os,sys; p='/private/wikikg/connected/graph-password'; "
              "os.environ['WIKIKG_NEO4J_PASSWORD']=open(p,encoding='utf-8').read().rstrip('\\n'); "
              "sys.path.insert(0,'/var/www/html/w/extensions/WikiKGExtractor/tools'); "
              "import reviewed_projection; "
              "sys.exit(reviewed_projection.main(['load','--manifest'," + repr(manifest) + ","
              "'--uri','bolt://127.0.0.1:7687']))")
    in_wiki(root, "python3", "-B", "-c", script, timeout=90)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("setup", "start", "serve", "verify", "stop"))
    p.add_argument("--private-root", required=True)
    p.add_argument("--corpus-manifest", help="pinned owner-only metadata required for setup")
    p.add_argument("--subnet", help="unused canonical private IPv4 /24, setup only")
    p.add_argument("--playwright-cli", help="explicit pinned @playwright/test cli.js")
    p.add_argument("--synthetic-only", action="store_true", required=True)
    return p


def main() -> int:
    os.umask(0o077)
    args = parser().parse_args()
    base_started = False
    questions_started = False
    try:
        subnet = None
        if args.subnet is not None:
            if args.action != "setup":
                raise Refusal("invalid_arguments")
            try:
                subnet = ipaddress.ip_network(args.subnet, strict=True)
            except ValueError:
                raise Refusal("invalid_arguments") from None
            if (not isinstance(subnet, ipaddress.IPv4Network) or subnet.prefixlen != 24
                    or not any(subnet.subnet_of(pool) for pool in PRIVATE_IPV4_POOLS)
                    or str(subnet) != args.subnet):
                raise Refusal("invalid_arguments")
            docker_env = {key: value for key, value in os.environ.items()
                          if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
            try:
                ids = subprocess.check_output(["docker", "network", "ls", "-q"], env=docker_env,
                    stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15).decode("ascii").split()
                for network_id in ids:
                    item = json.loads(subprocess.check_output(["docker", "network", "inspect", network_id],
                        env=docker_env, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15))[0]
                    if subnet_overlaps((item.get("IPAM") or {}).get("Config"), subnet):
                        raise Refusal("subnet_in_use")
            except Refusal:
                raise
            except Exception:
                raise Refusal("docker_network_inspection_failed") from None
        root = root_path(args.private_root, fresh=args.action == "setup")
        if args.action == "setup":
            if not args.corpus_manifest:
                raise Refusal("invalid_manifest")
            port = os.environ.get("WIKIKG_HTTP_PORT", "8787")
            if not port.isascii() or not port.isdigit() or not 1024 <= int(port) <= 65535:
                raise Refusal("invalid_arguments")
            if subnet:
                (root / ".synthetic-subnet").write_text(str(subnet) + "\n")
                os.chmod(root / ".synthetic-subnet", 0o600)
            base(root, "setup", args.corpus_manifest, str(subnet) if subnet else None)
            port_file = root / "wiki" / ".synthetic-http-port"
            port_file.write_text(port + "\n")
            os.chmod(port_file, 0o600)
            env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
            image_id = subprocess.check_output(["docker", "image", "inspect", "--format={{.Id}}",
                "wikikg-synthetic-wiki:1.43.1-php8.3.14"], env=env, stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=15).decode("ascii").strip()
            if not image_id.startswith("sha256:") or len(image_id) != 71:
                raise Refusal("wiki_image_missing")
            (root / ".wiki-image-id").write_text(image_id + "\n")
            os.chmod(root / ".wiki-image-id", 0o600)
            if subnet:
                source = root / "wiki" / "private-compose.yml"
                override = root / "private-compose.yml"
                override.write_bytes(source.read_bytes())
                os.chmod(override, 0o600)
            base_started = True
            base(root, "start")
            base(root, "workflow")
            connected = root / "wiki" / "connected"
            connected.mkdir(mode=0o700)
            os.chmod(connected, 0o700)
            password_file = connected / "graph-password"
            password_file.write_text(secrets.token_urlsafe(48) + "\n")
            os.chmod(password_file, 0o600)
            (root / "wiki" / "connected").mkdir(mode=0o700, exist_ok=True)
            os.chmod(root / "wiki" / "connected", 0o700)
            in_wiki(root, "php", "extensions/WikiKGExtractor/demo/synthetic/questions/fixture.php", timeout=120)
            export_target = "/private/wikikg/connected/reviewed.json"
            in_wiki(root, "php", "extensions/WikiKGExtractor/maintenance/exportReviewedProjection.php",
                    "--actor=Synthetic reviewer", "--synthetic-only", "--document-root=/var/www/html",
                    "--output=" + export_target, timeout=90)
            for name in ("questions.json", "fixture-identities.json", "fixture-provenance.json"):
                item = connected / name
                info = item.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    raise Refusal("fixture_metadata_unsafe")
                os.chmod(item, 0o600)
            settings = root / "wiki" / "LocalSettings.synthetic.php"
            with settings.open("a") as output:
                output.write("\n$wgWikiKGSyntheticQuestionsEnabled = true;\n")
                output.write("$wgWikiKGSyntheticQuestionsDirectory = '/private/wikikg/connected';\n")
            os.chmod(settings, 0o600)
            base(root, "stop")
            base_started = False
            print(json.dumps({"status": "ready", "synthetic_only": True, "private_root": str(root)}))
            return 0
        if not private(root / "wiki" / ".compose-project", 0o600) or not private(root / "wiki" / "connected" / "graph-password", 0o600):
            raise Refusal("not_setup")
        if args.action == "start":
            questions_started = True
            compose(root, "up", "-d", "--no-build")
            deadline = time.monotonic() + 45
            while True:
                try:
                    load_graph(root)
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise Refusal("graph_start_failed")
                    time.sleep(1)
        elif args.action == "serve":
            sys.path.insert(0, str(SYNTHETIC))
            from serve import serve
            serve(root / "wiki", http_port(root), request_timeout=40)
        elif args.action == "verify":
            from verify import verify
            report = verify(root, args.playwright_cli)
            print(json.dumps({"status": report.get("status"), "report": str(root / "connected" / "final-report.json")}, sort_keys=True))
            return 0 if report.get("status") == "passed" and report.get("cleanup") == "passed" else 1
        elif args.action == "stop":
            from verify import stop_owned
            stop_owned(root)
        if args.action == "start":
            pass
        print(json.dumps({"status": "completed", "action": args.action, "synthetic_only": True}))
        return 0
    except Exception as exc:
        if base_started:
            try:
                base(root, "stop")
            except Exception:
                pass
        if questions_started:
            try:
                from verify import stop_owned
                stop_owned(root)
            except Exception:
                pass
        error = exc if isinstance(exc, Refusal) else Refusal("operation_failed")
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
