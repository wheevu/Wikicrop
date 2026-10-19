"""Deterministic loopback-only chatbot stand-in for synthetic browser checks."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
import threading
import time

MAX_BODY = 16_384
MAX_RECORDS = 100
ALLOWED_QUERIES = {
    "synthetic question", "synthetic failure", "synthetic malformed json",
    "synthetic invalid answer", "synthetic empty answer", "synthetic missing answer",
    "synthetic timeout late", "synthetic concurrent alpha", "synthetic concurrent beta",
}


def handler_for(origin: str, record: Path):
    records: list[dict] = []
    lock = threading.Lock()
    pair_condition = threading.Condition(lock)
    pair_queries: set[str] = set()
    beta_replied = threading.Event()
    alpha_release = threading.Event()
    late_attempted = threading.Event()
    controls: list[str] = []
    control_record = record.with_name("stub-controls.json")
    if record.exists() or record.is_symlink() or control_record.exists() or control_record.is_symlink():
        raise ValueError("record_exists")

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            if self.headers.get("Origin") != origin:
                self.send_error(403)
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_POST(self):
            length = self.headers.get("Content-Length", "")
            if self.path not in {"/synthetic-fixture", "/synthetic-control"} or self.headers.get("Origin") != origin:
                self.send_error(403)
                return
            if not length.isdigit() or int(length) > MAX_BODY:
                self.send_error(413)
                return
            try:
                data = json.loads(self.rfile.read(int(length)))
                if self.path == "/synthetic-control":
                    self._control(data)
                    return
                if not isinstance(data, dict) or set(data) != {"query"} or not isinstance(data["query"], str) or len(data["query"]) > 4000:
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_error(400)
                return
            query = data["query"]
            if query not in ALLOWED_QUERIES:
                self.send_error(404)
                return
            with lock:
                if len(records) >= MAX_RECORDS:
                    self.send_error(429)
                    return
                records.append({"query": query})
                record.write_text(json.dumps(records, ensure_ascii=False))
                os.chmod(record, 0o600)
            if query in {"synthetic concurrent alpha", "synthetic concurrent beta"}:
                with pair_condition:
                    pair_queries.add(query)
                    pair_condition.notify_all()
                    pair_condition.wait_for(lambda: len(pair_queries) == 2, timeout=10)
                if len(pair_queries) != 2:
                    self.send_error(504)
                    return
                if query.endswith("alpha"):
                    if not beta_replied.wait(timeout=10):
                        self.send_error(504)
                        return
                    if not alpha_release.wait(timeout=15):
                        self.send_error(504)
                        return
                answer = ("fixture answer for alpha" if query.endswith("alpha")
                          else "fixture answer for beta")
                try:
                    self._reply(200, {"answer": answer})
                finally:
                    if query.endswith("beta"):
                        beta_replied.set()
                return
            if query == "synthetic timeout late":
                time.sleep(32)
                try:
                    self._reply(200, {"answer": "late fixture answer"})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    late_attempted.set()
                return
            modes = {
                "synthetic malformed json": (200, b"{not json", "application/json"),
                "synthetic invalid answer": (200, json.dumps({"answer": 42}).encode(), "application/json"),
                "synthetic empty answer": (200, json.dumps({"answer": ""}).encode(), "application/json"),
                "synthetic missing answer": (200, json.dumps({"other": "no answer"}).encode(), "application/json"),
            }
            if query in modes:
                status, body, content_type = modes[query]
            else:
                answer = ("fixture failure" if query == "synthetic failure"
                          else "<img src=x onerror=alert(1)> literal synthetic answer")
                body = json.dumps({"answer": answer}).encode()
                status = 503 if query == "synthetic failure" else 200
                content_type = "application/json"
            self._reply(status, body, content_type)

        def _control(self, data):
            # Two fixed synchronization actions only, never arbitrary responses,
            # paths, network targets or application operations.
            if not isinstance(data, dict) or set(data) != {"action"} or data["action"] not in ("await_late", "release_alpha"):
                self.send_error(400)
                return
            action = data["action"]
            with lock:
                if action in controls:
                    self.send_error(409)
                    return
                controls.append(action)
                control_record.write_text(json.dumps(controls))
                os.chmod(control_record, 0o600)
            if action == "await_late":
                if not late_attempted.wait(timeout=15):
                    self.send_error(504)
                    return
            else:
                if not beta_replied.wait(timeout=10):
                    self.send_error(504)
                    return
                alpha_release.set()
            self._reply(200, {"completed": True})

        def _reply(self, status, body, content_type="application/json"):
            if isinstance(body, dict):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return Handler


def serve(port: int, origin: str, record: Path) -> None:
    if not 1024 <= port <= 65535 or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", origin):
        raise ValueError("invalid_stub_config")
    ThreadingHTTPServer(("127.0.0.1", port), handler_for(origin, record)).serve_forever()


if __name__ == "__main__":
    try:
        serve(int(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    except Exception:
        print('{"status":"refused","error":"stub_failed"}', file=sys.stderr)
        raise SystemExit(2)
