#!/usr/bin/env python3
"""Guarded browser integration lab for WikiChatbot with synthetic fixtures only."""
from __future__ import annotations
import argparse
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNTHETIC = HERE.parent
REPO = HERE.parents[4]
EXTENSION = REPO / "extensions/WikiChatbot"
DEFAULT_CLI = REPO / "extensions/WikiKGExtractor/tests/browser/node_modules/@playwright/test/cli.js"

class Refusal(Exception): pass

def safe_path(raw: str, must_exist: bool = False) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute() or ".." in p.parts: raise Refusal()
    cur = Path(p.anchor)
    for part in p.parts[1:]:
        cur /= part
        if cur.is_symlink(): raise Refusal()
    if must_exist:
        p = p.resolve(strict=True)
        info = p.stat()
        if not p.is_file() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600: raise Refusal()
        parent = p.parent.stat()
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700: raise Refusal()
    else:
        if p.exists(): raise Refusal()
        p = p.resolve(strict=False)
        if p == REPO or REPO in p.parents or p == Path("/var/www/html") or Path("/var/www/html") in p.parents: raise Refusal()
        parent = p.parent.stat()
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700: raise Refusal()
    return p

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

def stop(p):
    if not p: return
    running = p.poll() is None
    if running:
        try: p.terminate()
        except ProcessLookupError: pass
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try: p.kill()
        except ProcessLookupError: pass
        p.wait(timeout=5)

def publish_result(root, result):
    payload = (json.dumps(result, sort_keys=True) + "\n").encode()
    if len(payload) > 65536: raise OSError("result exceeds limit")
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    stage = None
    fd = -1
    try:
        fd, stage = tempfile.mkstemp(prefix=".results-", dir=root)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(payload); output.flush(); os.fsync(output.fileno())
        os.link(stage, root / "results.json", follow_symlinks=False)
        os.fsync(root_fd)
        os.unlink(stage)
        stage = None
        os.fsync(root_fd)
    finally:
        if fd >= 0: os.close(fd)
        os.close(root_fd)

def owned_containers(project, wiki, environment):
    ids = subprocess.check_output(["docker", "ps", "-aq", "--no-trunc", "--filter",
        "label=com.docker.compose.project=" + project], env=environment, timeout=30).decode().split()
    details = []
    for container_id in ids:
        info = json.loads(subprocess.check_output(["docker", "inspect", container_id], env=environment, timeout=30))[0]
        private = [m for m in info["Mounts"] if m["Destination"] == "/private/wikikg"]
        if (info["Config"]["Labels"].get("com.docker.compose.project") != project
                or len(private) != 1 or Path(private[0]["Source"]).resolve() != wiki):
            raise Refusal()
        details.append(info)
    return details

def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--private-root", required=True, help="new private directory outside checkout")
    ap.add_argument("--corpus-manifest", required=True, help="pinned owner-only metadata input")
    ap.add_argument("--synthetic-only", action="store_true", required=True)
    ap.add_argument("--playwright-cli", help="explicit pinned @playwright/test cli.js")
    a = ap.parse_args(); root = wiki = override = project = None; relay = stub = None; port = 0
    handles = []
    created = False
    cases = ["php-hook-resource-loader", "fixture-literal-response", "http-failure-recovery",
             "malformed-and-invalid-payload-recovery", "real-timeout-late-response-recovery",
             "out-of-order-concurrent-response-position"]
    result = {"status":"failed","synthetic_only":True,"cases":cases,"error":"chatbot_lab_failed"}
    try:
        # Validate both paths before any subprocess, including setup.
        root = safe_path(a.private_root)
        manifest = safe_path(a.corpus_manifest, True)
        if root == manifest or root in manifest.parents or manifest in root.parents: raise Refusal()
        cli = Path(a.playwright_cli or os.environ.get("PLAYWRIGHT_CLI", str(DEFAULT_CLI))).resolve(strict=True)
        package = json.loads((cli.parent / "package.json").read_text())
        if cli.name != "cli.js" or package.get("name") != "@playwright/test" or package.get("version") != "1.63.0": raise Refusal()
        root.mkdir(mode=0o700, parents=True); os.chmod(root, 0o700)
        created = True
        wiki = root / "wiki"
        port, stub_port = free_port(), free_port()
        while port == stub_port: stub_port = free_port()
        clean = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()),
                  "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", str(Path.home()/".docker")), "WIKIKG_HTTP_PORT": str(port)}
        def private_log(name):
            handle = (root / (name + ".log")).open("xb")
            handles.append(handle)
            return handle
        setup_log = private_log("setup")
        setup = subprocess.run([sys.executable, "-B", str(SYNTHETIC/"run.py"), "setup", "--private-root", str(wiki), "--corpus-manifest", str(manifest), "--synthetic-only"], cwd=REPO, env=clean, stdin=subprocess.DEVNULL, stdout=setup_log, stderr=subprocess.STDOUT, timeout=900)
        if setup.returncode: raise Refusal()
        marker = wiki/".compose-project"
        if marker.is_symlink() or not marker.is_file() or stat.S_IMODE(marker.stat().st_mode) != 0o600: raise Refusal()
        project = marker.read_text().strip()
        if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project): raise Refusal()
        settings = wiki/"LocalSettings.synthetic.php"
        if settings.is_symlink() or stat.S_IMODE(settings.stat().st_mode) != 0o600: raise Refusal()
        with settings.open("a") as f:
            f.write(f"\nwfLoadExtension( 'WikiChatbot' );\n$wgWikiChatbotEnabled = true;\n$wgWikiChatbotApiUrl = 'http://127.0.0.1:{stub_port}/synthetic-fixture';\n")
        os.chmod(settings, 0o600)
        override = root/"compose-chatbot.yml"
        override.write_text(f"services:\n  wiki:\n    volumes:\n      - type: bind\n        source: {json.dumps(str(EXTENSION))}\n        target: /var/www/html/w/extensions/WikiChatbot\n        read_only: true\n        bind:\n          create_host_path: false\n")
        os.chmod(override, 0o600)
        compose_env = {"PATH": clean["PATH"], "HOME": clean["HOME"], "DOCKER_CONFIG": clean["DOCKER_CONFIG"],
                       "WIKIKG_PRIVATE_ROOT": str(wiki), "WIKIKG_UID": str(os.getuid()), "WIKIKG_GID": str(os.getgid()),
                       "WIKIKG_HTTP_PORT": str(port), "COMPOSE_PROJECT_NAME": project}
        def compose(*args):
            return subprocess.run(["docker","compose","--env-file","/dev/null","-p",project,"-f",str(SYNTHETIC/"compose.yml"),"-f",str(override),*args], cwd=REPO, env=compose_env, stdin=subprocess.DEVNULL, stdout=private_log("compose-" + args[0]), stderr=subprocess.STDOUT, check=True, timeout=180)
        relay_env = dict(clean, WIKIKG_HTTP_PORT=str(port))
        compose("up","-d","--no-build","wiki")
        relay = subprocess.Popen([sys.executable,"-B",str(SYNTHETIC/"run.py"),"serve","--private-root",str(wiki),"--synthetic-only"], cwd=REPO, env=relay_env, stdin=subprocess.DEVNULL, stdout=private_log("relay"), stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"; deadline=time.monotonic()+90
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic()<deadline and relay.poll() is None:
            try:
                with opener.open(base+"/w/index.php?title=Main_Page",timeout=25) as r:
                    if r.status==200: break
            except Exception: time.sleep(.3)
        else: raise Refusal()
        log=root/"stub-requests.json"
        stub=subprocess.Popen([sys.executable,"-B",str(HERE/"stub.py"),str(stub_port),base,str(log)],cwd=REPO,env=clean,stdin=subprocess.DEVNULL,stdout=private_log("stub"),stderr=subprocess.STDOUT)
        api=f"http://127.0.0.1:{stub_port}/synthetic-fixture"; deadline=time.monotonic()+10
        (root / "resources.json").write_text(json.dumps({"relay_pid": relay.pid, "stub_pid": stub.pid,
            "wiki_url": base, "stub_url": api, "project": project,
            "cleanup": "terminate owned relay/stub handles and stop only validated project containers"}) + "\n")
        while time.monotonic()<deadline and stub.poll() is None:
            try:
                req=urllib.request.Request(api,method="OPTIONS",headers={"Origin":base})
                with opener.open(req,timeout=1) as r:
                    if r.status==204: break
            except Exception: time.sleep(.1)
        else: raise Refusal()
        artifacts=root/"browser-artifacts"; artifacts.mkdir(mode=0o700)
        benv={"PATH":clean["PATH"],"HOME":clean["HOME"],"NODE_PATH":str(cli.parents[2]),"WIKIKG_WIKI_URL":base,"WIKIKG_CHATBOT_STUB_URL":api,"WIKIKG_SCREENSHOT":str(artifacts/"chatbot.png"),"ARTIFACT_ROOT":str(artifacts)}
        test=subprocess.run(["node",str(cli),"test","--config",str(REPO/"extensions/WikiChatbot/tests/mediawiki/playwright.config.cjs")],cwd=REPO,env=benv,stdin=subprocess.DEVNULL,stdout=private_log("playwright"),stderr=subprocess.STDOUT,timeout=300)
        requests=json.loads(log.read_text()) if log.exists() else []
        expected_queries = ["synthetic question", "synthetic failure", "synthetic question",
            "synthetic malformed json", "synthetic question", "synthetic invalid answer",
            "synthetic question", "synthetic empty answer", "synthetic question",
            "synthetic missing answer", "synthetic question", "synthetic timeout late",
            "synthetic question", "synthetic concurrent alpha", "synthetic concurrent beta"]
        controls = json.loads((root / "stub-controls.json").read_text())
        if (test.returncode or len(requests) != len(expected_queries)
                or any(not isinstance(r, dict) or set(r) != {"query"} or not isinstance(r["query"], str) for r in requests)
                or sorted(r["query"] for r in requests) != sorted(expected_queries)
                or sorted(controls) != ["await_late", "release_alpha"]): raise Refusal()
        result={"status":"passed","synthetic_only":True,"cases":cases,"artifacts":str(artifacts)}
    except Exception:
        if created:
            try: (root / "failure.log").write_text(traceback.format_exc())
            except Exception: pass
        result={"status":"failed","synthetic_only":True,"cases":cases,"error":"chatbot_lab_failed"}
    finally:
        cleanup_errors = []
        for process in (stub, relay):
            try: stop(process)
            except Exception: cleanup_errors.append("owned_process_cleanup_failed")
        if wiki and (wiki / ".compose-project").is_file():
            try:
                project = (wiki / ".compose-project").read_text().strip()
                if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project): raise Refusal()
                env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"HOME":str(Path.home()),"DOCKER_CONFIG":os.environ.get("DOCKER_CONFIG",str(Path.home()/".docker")),"WIKIKG_PRIVATE_ROOT":str(wiki),"WIKIKG_UID":str(os.getuid()),"WIKIKG_GID":str(os.getgid()),"WIKIKG_HTTP_PORT":str(port),"COMPOSE_PROJECT_NAME":project}
                for info in owned_containers(project, wiki, env):
                    if info["State"]["Running"]:
                        try:
                            subprocess.run(["docker", "stop", "-t", "10", info["Id"]], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)
                        except Exception: cleanup_errors.append("container_cleanup_failed")
            except Exception: cleanup_errors.append("container_cleanup_failed")
        if cleanup_errors:
            result={"status":"failed","synthetic_only":True,"cases":cases,"error":"chatbot_lab_failed"}
            if created:
                try:
                    with (root / "cleanup.log").open("x") as log:
                        log.write("\n".join(cleanup_errors) + "\n")
                except Exception: pass
        for handle in handles:
            try: handle.close()
            except Exception: result={"status":"failed","synthetic_only":True,"cases":cases,"error":"chatbot_lab_failed"}
    if created:
        try: publish_result(root, result)
        except Exception:
            result={"status":"failed","synthetic_only":True,"cases":cases,"error":"chatbot_lab_failed"}
            try:
                with (root / "failure.log").open("a") as log: log.write("result_publication_failed\n")
            except Exception: pass
    print(json.dumps(result,sort_keys=True),file=sys.stdout if result["status"]=="passed" else sys.stderr)
    if result["status"]!="passed": raise SystemExit(2)

if __name__=="__main__": main()
