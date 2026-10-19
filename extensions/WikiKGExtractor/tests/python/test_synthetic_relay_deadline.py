"""The connected page's own deadline must fit within its loopback transport."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[2] / "demo/synthetic"
SPEC = importlib.util.spec_from_file_location("synthetic_relay_deadline", PACKAGE / "serve.py")
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)


class RelayDeadlineTests(unittest.TestCase):
    def test_invalid_transport_deadline_refuses_before_docker_or_bind(self):
        lifecycle = types.SimpleNamespace(private_file=lambda path: self.fail("must validate deadline first"))
        with patch.dict(sys.modules, {"run": lifecycle}), patch.object(relay.subprocess, "check_output") as docker:
            for value in (0, -1, 61, True, "40", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    relay.serve(Path("/unused"), 8787, request_timeout=value)
            docker.assert_not_called()

    def test_forwarder_uses_only_parent_supplied_upstream_deadline(self):
        import http.client
        class Response:
            status = 200
            def read(self, bound):
                self.bound = bound
                return b"synthetic response"
            def getheaders(self):
                return []
        response = Response()
        connection = types.SimpleNamespace(request=lambda *args, **kwargs: None,
                                           getresponse=lambda: response, close=lambda: None)
        payload = {"method": "POST", "path": "/w/index.php", "body": "",
                   "headers": {"X-Timeout": "999999"}, "timeout": 40}
        output = io.StringIO()
        with patch.object(http.client, "HTTPConnection", return_value=connection) as client, \
                patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(output):
            exec(compile(relay.FORWARD, "owned-relay-forwarder", "exec"), {})
        client.assert_called_once_with("127.0.0.1", 80, timeout=40)
        self.assertEqual(response.bound, 8_000_001)
        self.assertEqual(json.loads(output.getvalue())["status"], 200)


if __name__ == "__main__":
    unittest.main()
