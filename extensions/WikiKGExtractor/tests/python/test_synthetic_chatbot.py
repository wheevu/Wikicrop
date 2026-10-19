"""Fast safety and contract checks for the synthetic chatbot fixture."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import contextlib
import io
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

EXTENSION = Path(__file__).resolve().parents[2]
STUB = EXTENSION / "demo/synthetic/chatbot/stub.py"
spec = importlib.util.spec_from_file_location("chatbot_stub", STUB)
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)


class ChatbotStubTests(unittest.TestCase):
    @staticmethod
    def run_module():
        run_path = EXTENSION / "demo/synthetic/chatbot/run.py"
        namespace = {"__name__": "chatbot_run_test", "__file__": str(run_path)}
        exec(compile(run_path.read_text(), str(run_path), "exec"), namespace)
        return namespace

    def test_stop_reaps_child_exiting_between_poll_and_signal(self):
        run = self.run_module()
        class ExitingProcess:
            def __init__(self): self.polled = False; self.reaped = False
            def poll(self):
                if not self.polled:
                    self.polled = True
                    return None
                return 0
            def terminate(self): raise ProcessLookupError()
            def wait(self, timeout=None): self.reaped = True; return 0
        child = ExitingProcess()
        run["stop"](child)
        self.assertTrue(child.reaped)

    def test_stop_uses_owned_handles_and_does_not_kill_sentinel(self):
        run = self.run_module()
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            for round_number in range(10):
                children = [subprocess.Popen([sys.executable, "-c", "pass"]) for _ in range(2)]
                children += [subprocess.Popen([sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.readline()"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(2)]
                try:
                    for child in children[2:]:
                        self.assertEqual(child.stdout.readline().strip(), "ready")
                    for child in (children if round_number % 2 else reversed(children)):
                        run["stop"](child)
                        self.assertIsNotNone(child.returncode)
                    self.assertIsNone(sentinel.poll())
                finally:
                    for child in children:
                        if child.poll() is None: child.kill()
                        child.wait(timeout=3)
                        if child.stdin: child.stdin.close()
                        if child.stdout: child.stdout.close()
        finally:
            sentinel.terminate(); sentinel.wait(timeout=3)

    def exercise_lifecycle_adapter(self, *, cleanup_failure=False, publication_failure=False):
        """Model process boundaries only; the parent separately runs real MediaWiki."""
        run = self.run_module()
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "lab"
            manifest = parent / "manifest.json"
            manifest.write_text("{}"); manifest.chmod(0o600)
            package = parent / "playwright"
            package.mkdir(mode=0o700)
            cli = package / "cli.js"; cli.write_text("// adapter only")
            (package / "package.json").write_text(json.dumps({"name":"@playwright/test","version":"1.63.0"}))
            relay, stub_process = object(), object()
            processes = iter([relay, stub_process])
            stopped, containers = [], []
            class Response:
                def __init__(self, status): self.status = status
                def __enter__(self): return self
                def __exit__(self, *args): pass
            class Opener:
                def open(self, request, timeout=None):
                    return Response(204 if isinstance(request, Request) else 200)
            class Process:
                def __init__(self, identity): self.identity = identity; self.pid = 123
                def poll(self): return None
            def fake_popen(*args, **kwargs): return Process(next(processes))
            def fake_run(command, **kwargs):
                if "setup" in command:
                    wiki = root / "wiki"; wiki.mkdir(mode=0o700)
                    for name, text in ((".compose-project","wikikg-synthetic-" + "a" * 16), ("LocalSettings.synthetic.php","<?php\n")):
                        path = wiki / name; path.write_text(text); path.chmod(0o600)
                elif command[0] == "node":
                    queries = ["synthetic question", "synthetic failure", "synthetic question",
                        "synthetic malformed json", "synthetic question", "synthetic invalid answer",
                        "synthetic question", "synthetic empty answer", "synthetic question",
                        "synthetic missing answer", "synthetic question", "synthetic timeout late",
                        "synthetic question", "synthetic concurrent alpha", "synthetic concurrent beta"]
                    (root / "stub-requests.json").write_text(json.dumps([{"query": query} for query in queries]))
                    (root / "stub-controls.json").write_text(json.dumps(["await_late", "release_alpha"]))
                elif command[:2] == ["docker", "stop"]:
                    containers.append(command[-1])
                    if cleanup_failure and command[-1] == "first":
                        raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)
            def fake_stop(process):
                stopped.append(process.identity)
                if cleanup_failure and process.identity is stub_process:
                    raise OSError("synthetic process cleanup failure")
            stdout, stderr = io.StringIO(), io.StringIO()
            arguments = ["run.py", "--private-root", str(root), "--corpus-manifest", str(manifest),
                "--playwright-cli", str(cli), "--synthetic-only"]
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(run["sys"], "argv", arguments))
                stack.enter_context(patch.object(run["subprocess"], "run", side_effect=fake_run))
                stack.enter_context(patch.object(run["subprocess"], "Popen", side_effect=fake_popen))
                stack.enter_context(patch.object(run["urllib"].request, "build_opener", return_value=Opener()))
                stack.enter_context(patch.dict(run, {"stop":fake_stop, "owned_containers":lambda *a: [
                    {"State":{"Running":True},"Id":"first"}, {"State":{"Running":True},"Id":"second"}]}))
                if publication_failure:
                    stack.enter_context(patch.dict(run, {"publish_result":lambda *a: (_ for _ in ()).throw(OSError("synthetic write failure"))}))
                stack.enter_context(contextlib.redirect_stdout(stdout))
                stack.enter_context(contextlib.redirect_stderr(stderr))
                with self.assertRaises(SystemExit) as failure:
                    run["main"]()
            self.assertNotEqual(failure.exception.code, 0)
            self.assertEqual(stopped, [stub_process, relay])
            self.assertEqual(containers, ["first", "second"])
            self.assertNotIn('"status": "passed"', stdout.getvalue())
            self.assertEqual(json.loads(stderr.getvalue())["status"], "failed")
            if cleanup_failure:
                self.assertEqual(json.loads((root / "results.json").read_text())["status"], "failed")
                self.assertTrue((root / "cleanup.log").is_file())
            if publication_failure:
                self.assertFalse((root / "results.json").exists())

    def test_cleanup_failure_still_attempts_other_process_and_each_container(self):
        self.exercise_lifecycle_adapter(cleanup_failure=True)

    def test_result_write_failure_after_completed_adapter_never_prints_success(self):
        self.exercise_lifecycle_adapter(publication_failure=True)

    def test_result_publication_is_exclusive_and_private(self):
        run = self.run_module()
        result = {"status": "passed"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run["publish_result"](root, result)
            path = root / "results.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError): run["publish_result"](root, {"status": "failed"})
            self.assertEqual(path.read_bytes(), original)

    def test_result_publish_failure_preserves_existing_and_stage_for_recovery(self):
        run = self.run_module()
        for failed_call in ("link", "fsync"):
            with self.subTest(failed_call=failed_call), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); target = root / "results.json"; target.write_text("foreign")
                original = target.read_bytes()
                with patch.object(run["os"], failed_call, side_effect=OSError("disk fault")):
                    with self.assertRaises(OSError): run["publish_result"](root, {"status": "passed"})
                self.assertEqual(target.read_bytes(), original)
                stages = list(root.glob(".results-*"))
                self.assertTrue(stages)
                self.assertLessEqual(stages[0].stat().st_size, 65536)

    def test_fixture_request_cors_and_bounded_sanitized_record(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "requests.json"
            origin = "http://127.0.0.1:8080"
            server = ThreadingHTTPServer(("127.0.0.1", 0), stub.handler_for(origin, record))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/synthetic-fixture"
            try:
                with urlopen(Request(url, method="OPTIONS", headers={"Origin": origin})) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(Request(url, method="OPTIONS", headers={"Origin": "http://evil.test"}))
                self.assertEqual(rejected.exception.code, 403)
                for query, expected in (("synthetic question", 200), ("synthetic failure", 503)):
                    request = Request(url, data=json.dumps({"query": query}).encode(), method="POST",
                                      headers={"Origin": origin, "Content-Type": "application/json"})
                    try:
                        response = urlopen(request)
                        code = response.status
                        self.assertEqual(json.loads(response.read())["answer"], "<img src=x onerror=alert(1)> literal synthetic answer")
                        response.close()
                    except HTTPError as response:
                        code = response.code
                        response.close()
                    self.assertEqual(code, expected)
                for query, expected_body in (
                    ("synthetic malformed json", b"{not json"),
                    ("synthetic invalid answer", b'{"answer": 42}'),
                    ("synthetic empty answer", b'{"answer": ""}'),
                    ("synthetic missing answer", b'{"other": "no answer"}'),
                ):
                    response = urlopen(Request(url, data=json.dumps({"query": query}).encode(), method="POST",
                        headers={"Origin": origin, "Content-Type": "application/json"}))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), expected_body)
                    response.close()
                with self.assertRaises(HTTPError) as unknown:
                    urlopen(Request(url, data=b'{"query":"unbounded query"}', method="POST",
                        headers={"Origin": origin, "Content-Type": "application/json"}))
                self.assertEqual(unknown.exception.code, 404)
                control_url = url.rsplit("/", 1)[0] + "/synthetic-control"
                for data, request_origin, expected in ((b'{"action":"arbitrary"}', origin, 400),
                        (b'{"action":"release_alpha"}', "http://evil.test", 403),
                        (b'{"action":"release_alpha","path":"other"}', origin, 400)):
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(Request(control_url, data=data, method="POST",
                            headers={"Origin": request_origin, "Content-Type": "application/json"}))
                    self.assertEqual(rejected.exception.code, expected)
                self.assertFalse(record.with_name("stub-controls.json").exists())
                self.assertEqual(json.loads(record.read_text()), [
                    {"query": "synthetic question"}, {"query": "synthetic failure"},
                    {"query": "synthetic malformed json"}, {"query": "synthetic invalid answer"},
                    {"query": "synthetic empty answer"}, {"query": "synthetic missing answer"},
                ])
                self.assertEqual(record.stat().st_mode & 0o777, 0o600)
                oversized = Request(url, data=b"x" * (stub.MAX_BODY + 1), method="POST",
                                    headers={"Origin": origin, "Content-Length": str(stub.MAX_BODY + 1)})
                with self.assertRaises(HTTPError) as limited:
                    urlopen(oversized)
                self.assertEqual(limited.exception.code, 413)
                for malformed in (b"null", b"[]", b"{}", b'{"query":42}'):
                    with self.subTest(malformed=malformed), self.assertRaises(HTTPError) as invalid:
                        urlopen(Request(url, data=malformed, method="POST",
                            headers={"Origin": origin, "Content-Type": "application/json"}))
                    self.assertEqual(invalid.exception.code, 400)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_stub_rejects_nonloopback_config_before_binding(self):
        for port, origin in ((80, "http://127.0.0.1:8080"), (8081, "http://example.test:8080")):
            with self.subTest(port=port, origin=origin), patch.object(stub, "ThreadingHTTPServer") as server:
                with self.assertRaises(ValueError):
                    stub.serve(port, origin, Path("unused-record"))
                server.assert_not_called()

    def test_concurrent_fixture_requests_are_recorded_and_replied_out_of_order(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "requests.json"
            origin = "http://127.0.0.1:8080"
            server = ThreadingHTTPServer(("127.0.0.1", 0), stub.handler_for(origin, record))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/synthetic-fixture"
            def send(query):
                request = Request(url, data=json.dumps({"query": query}).encode(), method="POST",
                    headers={"Origin": origin, "Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    return json.loads(response.read())
            results = {}
            callers = [threading.Thread(target=lambda query=query: results.update({query: send(query)}))
                for query in ("synthetic concurrent alpha", "synthetic concurrent beta")]
            try:
                for caller in callers: caller.start()
                callers[1].join(timeout=5)
                self.assertEqual(results, {"synthetic concurrent beta": {"answer": "fixture answer for beta"}})
                self.assertTrue(callers[0].is_alive())
                control_url = url.rsplit("/", 1)[0] + "/synthetic-control"
                with urlopen(Request(control_url, data=b'{"action":"release_alpha"}', method="POST",
                        headers={"Origin": origin, "Content-Type": "application/json"})) as response:
                    self.assertEqual(json.loads(response.read()), {"completed": True})
                for caller in callers: caller.join(timeout=6)
                self.assertTrue(all(not caller.is_alive() for caller in callers))
                self.assertEqual(results, {
                    "synthetic concurrent beta": {"answer": "fixture answer for beta"},
                    "synthetic concurrent alpha": {"answer": "fixture answer for alpha"},
                })
                self.assertEqual(sorted(item["query"] for item in json.loads(record.read_text())),
                    ["synthetic concurrent alpha", "synthetic concurrent beta"])
                self.assertEqual(json.loads(record.with_name("stub-controls.json").read_text()), ["release_alpha"])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_unsafe_paths_refused_without_subprocess(self):
        run_path = EXTENSION / "demo/synthetic/chatbot/run.py"
        namespace = {"__name__": "chatbot_run_test", "__file__": str(run_path)}
        exec(compile(run_path.read_text(), str(run_path), "exec"), namespace)
        for value in ("relative", str(EXTENSION), str(EXTENSION / "new"), "/var/www/html/new", "/tmp/../unsafe"):
            with self.subTest(value=value), self.assertRaises(namespace["Refusal"]):
                namespace["safe_path"](value)


if __name__ == "__main__":
    unittest.main()
