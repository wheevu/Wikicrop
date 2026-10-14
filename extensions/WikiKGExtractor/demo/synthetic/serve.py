"""Loopback HTTP relay into the owned, no-egress synthetic wiki container."""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FORWARD = """
import base64,http.client,json,sys
r=json.load(sys.stdin)
c=http.client.HTTPConnection('127.0.0.1',80,timeout=20)
c.request(r['method'],r['path'],body=base64.b64decode(r['body']),headers=r['headers'])
s=c.getresponse(); body=s.read(8_000_001)
if len(body)>8_000_000: raise ValueError('response_limit')
print(json.dumps({'status':s.status,'headers':s.getheaders(),'body':base64.b64encode(body).decode()}))
c.close()
"""
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length"}


def serve(root: Path, port: int) -> None:
    from run import private_file

    os.umask(0o077)
    if not private_file(root / ".compose-project") or not private_file(root / "synthetic-marker"):
        raise ValueError("synthetic_relay_refused")
    project = (root / ".compose-project").read_text().strip()
    if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project) \
            or (root / "synthetic-marker").read_text() != "seeded" or not 1024 <= port <= 65535:
        raise ValueError("synthetic_relay_refused")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}

    def docker(*args):
        return subprocess.check_output(["docker", *args], env=env, timeout=30, stderr=subprocess.DEVNULL)

    ids = docker("ps", "-q", "--no-trunc", "--filter", "label=com.docker.compose.project=" + project,
                 "--filter", "label=com.docker.compose.service=wiki").decode().split()
    if len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{64}", ids[0]):
        raise ValueError("synthetic_relay_refused")
    container = json.loads(docker("inspect", ids[0]))[0]
    mounts = {item["Destination"]: item for item in container["Mounts"]}
    private = mounts.get("/private/wikikg", {})
    variables = dict(item.split("=", 1) for item in container["Config"]["Env"])
    if (Path(private.get("Source", "/")).resolve() != root or not private.get("RW")
            or variables.get("WIKIKG_SYNTHETIC") != "1"
            or variables.get("WIKIKG_HTTP_PORT") != str(port)
            or variables.get("MW_CONFIG_FILE") != "/private/wikikg/LocalSettings.synthetic.php"
            or not container["HostConfig"]["ReadonlyRootfs"]):
        raise ValueError("synthetic_relay_refused")
    for network in container["NetworkSettings"]["Networks"]:
        if not json.loads(docker("network", "inspect", network))[0]["Internal"]:
            raise ValueError("synthetic_relay_refused")
    command = ["docker", "exec", "-i", "--user", f"{os.getuid()}:{os.getgid()}", ids[0],
               "python3", "-c", FORWARD]

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            self.connection.settimeout(25)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not self.path.startswith("/w/") or not 0 <= length <= 1_000_000 \
                        or self.headers.get("Transfer-Encoding"):
                    self.send_error(400)
                    return
                headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
                headers["Host"] = f"127.0.0.1:{port}"
                body = self.rfile.read(length)
                if len(body) != length:
                    self.send_error(400)
                    return
                payload = json.dumps({"method": self.command, "path": self.path, "headers": headers,
                                      "body": base64.b64encode(body).decode()}).encode()
                process = subprocess.run(command, input=payload, capture_output=True, env=env, timeout=30)
                if process.returncode or len(process.stdout) > 12_000_000:
                    raise ValueError("relay_failure")
                response = json.loads(process.stdout)
                result = base64.b64decode(response["body"], validate=True)
                self.send_response(response["status"])
                for key, value in response["headers"]:
                    if key.lower() not in HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(result)
            except (OSError, ValueError, subprocess.SubprocessError):
                self.send_error(502, "Synthetic relay unavailable")

        do_GET = handle_request
        do_POST = handle_request
        do_HEAD = handle_request

        def log_message(self, *args):
            pass  # Request URLs, login bodies, and cookies never enter the log.

    server = HTTPServer(("127.0.0.1", port), Handler)
    resource = {"pid": os.getpid(), "container_id": ids[0], "url": f"http://127.0.0.1:{port}/w/",
                "cleanup": "SIGTERM this owned relay PID; stop the wiki separately"}
    record = root / f"relay-{os.getpid()}.json"
    with record.open("x") as output:
        json.dump(resource, output, sort_keys=True)
    def stop(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    print(json.dumps({"status": "serving", "pid": os.getpid(), "url": resource["url"]}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
