"""CLI and private-file regressions with an explicit synthetic PHP adapter.

The adapter exercises the transport contract, not MediaWiki authorization.
The disposable-wiki workflow separately checks the real PHP evidence gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "tools" / "offline_answer.py"
sys.path.insert(0, str(ROOT / "tools"))
import offline_answer


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class OfflineAnswerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.wiki = self.root / "wiki"
        (self.wiki / "maintenance").mkdir(parents=True)
        (self.wiki / "maintenance" / "Maintenance.php").write_text("<?php\n")
        self.web = self.root / "web"
        self.web.mkdir()
        self.claim = {
            "snapshot_id": digest("snapshot"), "snapshot_hash": digest("snapshot-hash"),
            "claim_id": digest("claim"), "review_version": 1, "claim_type": "property",
            "subject": {"type": "Crop", "id": "synthetic-crop"},
            "predicate": "height", "value": "80 cm", "qualifiers": {},
            "evidence": [{
                "evidence_id": digest("evidence"), "page_id": 901, "revision_id": 902,
                "content_hash": digest("source"), "page_title": "Synthetic source",
                "extraction_method": "rules", "extractor_version": "synthetic-test",
                "location_type": "page",
            }],
        }
        self.manifest = self.write("reviewed.json", {
            "schema_version": "wikikg.reviewed-projection.v1", "snapshot_count": 1,
            "source_watermark": digest("watermark"), "claims": [self.claim],
        })
        self.request = self.write("question.json", {
            "version": "wikikg.offline-question.v1",
            "query": {"intent": "lookup", "purpose": "answer", "entities": [
                {"type": "Crop", "id": "synthetic-crop", "crop_id": "synthetic-crop"}
            ]},
        })
        self.output = self.private / "result.json"
        self.control = self.write("adapter-control.json", {"mode": "ok", "claims": [self.claim]})
        self.adapter = self.root / "synthetic-php"
        self.adapter.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys, time
control = json.loads(pathlib.Path(os.environ["WIKIKG_TEST_CONTROL"]).read_text())
options = dict(arg[2:].split("=", 1) for arg in sys.argv[1:] if arg.startswith("--") and "=" in arg)
mode = control["mode"]
if mode == "timeout":
    time.sleep(10)
if mode == "refused" or (mode == "second_refused" and control.get("calls", 0) >= 1):
    print(json.dumps({"status": "dependency_failure", "error": "offline_authorization_refused",
                      "reason": "export_watermark_changed"}, separators=(",", ":")), file=sys.stderr)
    sys.exit(1)
control["calls"] = control.get("calls", 0) + 1
pathlib.Path(os.environ["WIKIKG_TEST_CONTROL"]).write_text(json.dumps(control))
request = json.loads(pathlib.Path(options["request"]).read_text())
requested_ids = {item["snapshot_id"] for item in request["graph"]}
claims = [item for item in control["claims"] if item["snapshot_id"] in requested_ids]
if mode == "changed":
    claims[0]["value"] = "999 cm"
document = {"schema_version": "wikikg.current-evidence.v1", "status": "ok", "claims": claims, "text": []}
if mode == "malformed":
    document.pop("text")
path = pathlib.Path(options["output"])
path.write_text(json.dumps(document))
os.chmod(path, 0o600)
''', encoding="utf-8")
        os.chmod(self.adapter, 0o700)

    def write(self, name: str, document: object) -> Path:
        path = self.private / name
        path.write_text(json.dumps(document), encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def invoke(self, *extra: str) -> subprocess.CompletedProcess:
        args = [sys.executable, str(COMMAND), "--manifest", str(self.manifest),
                "--request", str(self.request), "--output", str(self.output),
                "--actor", "Synthetic reviewer", "--wiki-root", str(self.wiki),
                "--document-root", str(self.web), "--synthetic-only", "--offline-only",
                "--audience", "actor-only", "--php", str(self.adapter), *extra]
        return subprocess.run(args, capture_output=True, text=True, timeout=15,
                              env={**os.environ, "DOCUMENT_ROOT": str(self.web),
                                   "WIKIKG_TEST_CONTROL": str(self.control),
                                   "PYTHONDONTWRITEBYTECODE": "1"})

    def result(self) -> dict:
        return json.loads(self.output.read_text())

    def test_write_new_removes_only_its_staging_after_success(self):
        unrelated = self.private / ".question-write-unrelated"
        unrelated.write_bytes(b"keep me")
        os.chmod(unrelated, 0o600)
        before = {p.name for p in self.private.iterdir()}
        offline_answer._write_new(self.output, b"complete", 100, self.web)
        self.assertEqual(self.output.read_bytes(), b"complete")
        self.assertEqual({p.name for p in self.private.iterdir()}, before | {self.output.name})
        self.assertEqual(unrelated.read_bytes(), b"keep me")
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_write_new_link_failure_retains_complete_staging_and_existing_target(self):
        def conflicting_link(staging, target, **kwargs):
            target.write_bytes(b"original")
            raise FileExistsError
        with mock.patch.object(offline_answer.os, "link", side_effect=conflicting_link):
            with self.assertRaises(FileExistsError):
                offline_answer._write_new(self.output, b"private complete bytes", 100, self.web)
        self.assertEqual(self.output.read_bytes(), b"original")
        staging = [p for p in self.private.glob(".question-write-*")]
        self.assertEqual(len(staging), 1)
        self.assertEqual(staging[0].read_bytes(), b"private complete bytes")
        self.assertEqual(stat.S_IMODE(staging[0].stat().st_mode), 0o600)

    def test_write_new_interrupted_write_retains_recoverable_staging(self):
        with mock.patch.object(offline_answer.os, "fsync", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                offline_answer._write_new(self.output, b"recoverable", 100, self.web)
        self.assertFalse(self.output.exists())
        staging = [p for p in self.private.glob(".question-write-*")]
        self.assertEqual(len(staging), 1)
        self.assertEqual(staging[0].read_bytes(), b"recoverable")

    def test_write_new_failed_install_directory_sync_retains_staging(self):
        real_sync = offline_answer.os.fsync
        calls = 0
        def fail_directory_sync(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic directory sync failure")
            real_sync(fd)
        with mock.patch.object(offline_answer.os, "fsync", side_effect=fail_directory_sync):
            with self.assertRaises(OSError):
                offline_answer._write_new(self.output, b"recoverable", 100, self.web)
        staging = list(self.private.glob(".question-write-*"))
        self.assertEqual(len(staging), 1)
        self.assertEqual(staging[0].read_bytes(), self.output.read_bytes())

    def test_default_command_returns_evidence_without_generation_and_rechecks_authority(self):
        process = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        result = self.result()
        self.assertEqual(result["status"], "evidence")
        self.assertIsNone(result["answer"])
        self.assertFalse(result["execution"]["generated"])
        self.assertEqual(result["execution"]["authorization_calls"], 2)
        self.assertEqual(result["citations"][0]["revision_id"], 902)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        summary = json.loads(process.stdout)
        self.assertTrue(summary["result_written"])
        for private_value in (str(self.private), "synthetic-crop", "80 cm", "Synthetic source"):
            self.assertNotIn(private_value, process.stdout + process.stderr)

    def test_existing_result_is_never_overwritten(self):
        self.assertEqual(self.invoke().returncode, 0)
        original = self.output.read_bytes()
        process = self.invoke()
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertFalse(json.loads(process.stdout)["result_written"])

    def test_authority_failures_clear_payload_and_keep_content_free_reasons(self):
        for mode, reason in (("refused", "export_watermark_changed"),
                             ("malformed", "malformed_authorization_response"),
                             ("changed", "source_evidence_changed")):
            with self.subTest(mode=mode):
                self.control.write_text(json.dumps({"mode": mode, "claims": [self.claim]}))
                self.output = self.private / f"result-{mode}.json"
                process = self.invoke()
                self.assertEqual(process.returncode, 2, process.stderr + process.stdout)
                result = self.result()
                self.assertIsNone(result["query"])
                self.assertIsNone(result["answer"])
                self.assertEqual(result["citations"], [])
                self.assertEqual(result["reasons"][0]["code"], reason)

    def test_second_transport_authorization_denial_clears_answer_and_citations(self):
        self.control.write_text(json.dumps({"mode": "second_refused", "calls": 0,
                                            "claims": [self.claim]}))
        process = self.invoke()
        self.assertEqual(process.returncode, 2, process.stderr + process.stdout)
        result = self.result()
        self.assertIsNone(result["query"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["reasons"][0]["code"], "export_watermark_changed")
        self.assertEqual(json.loads(process.stdout)["authorization_calls"], 2)

    def test_private_symlinks_and_public_permissions_are_refused(self):
        original = self.request
        alias = self.private / "alias.json"
        alias.symlink_to(original)
        self.request = alias
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.result()["reasons"][0]["code"], "unsafe_private_path")
        self.request = original
        self.output = self.private / "bad-permissions.json"
        os.chmod(self.manifest, 0o644)
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.result()["reasons"][0]["code"], "unsafe_private_path")

    def test_duplicate_question_keys_are_refused_before_authorization(self):
        self.request.write_text('{"version":"wikikg.offline-question.v1","query":{},"query":{}}')
        process = self.invoke()
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.result()["reasons"][0]["code"], "invalid_request")
        self.assertEqual(json.loads(process.stdout)["authorization_calls"], 0)

    def test_missing_php_is_a_dependency_failure(self):
        process = self.invoke("--php", str(self.root / "absent-php"))
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.result()["reasons"][0]["code"], "runtime_missing")

    def test_timeout_stops_only_the_owned_bridge_and_writes_no_evidence(self):
        self.control.write_text(json.dumps({"mode": "timeout", "claims": [self.claim]}))
        process = self.invoke("--timeout", "1")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.result()["reasons"][0]["code"], "authorization_timeout")
        self.assertEqual(self.result()["citations"], [])

    def test_graph_transport_reads_private_file_and_never_passes_password_as_argv(self):
        from argparse import Namespace
        password = self.private / "graph-password"
        password.write_text("not-an-argv-secret\n")
        os.chmod(password, 0o600)
        args = Namespace(graph_uri="bolt://127.0.0.1:7687", graph_user="neo4j",
                         graph_password_file=str(password), document_root=str(self.web))
        driver = mock.Mock()
        neo4j = mock.Mock(GraphDatabase=mock.Mock(driver=mock.Mock(return_value=driver)))
        with mock.patch.dict(sys.modules, {"neo4j": neo4j, "neo4j_question_source": mock.Mock(
                 QUERY_TIMEOUT=8, read_question_document=mock.Mock(return_value=({"claims": []}, "a" * 64, 4)))}):
            _, returned_driver, document, token = offline_answer._graph_source(args, {}, deadline=10**12)
        neo4j.GraphDatabase.driver.assert_called_once_with(args.graph_uri,
            auth=("neo4j", "not-an-argv-secret"), connection_timeout=8)
        self.assertIs(returned_driver, driver)
        self.assertEqual(token, {"source": "neo4j", "generation": "a" * 64, "epoch": 4})
        self.assertNotIn("not-an-argv-secret", repr(args))

    def test_graph_transport_clears_failed_payload_and_closes_owned_driver(self):
        from argparse import Namespace
        password = self.private / "graph-password-failed"
        password.write_text("secret")
        os.chmod(password, 0o600)
        args = Namespace(graph_uri="bolt://127.0.0.1:7687", graph_user="neo4j",
                         graph_password_file=str(password), document_root=str(self.web))
        driver = mock.Mock()
        graph_module = mock.Mock(QUERY_TIMEOUT=8, read_question_document=mock.Mock(
            side_effect=type("Failure", (ValueError,), {"code": "graph_corrupt"})()))
        neo4j = mock.Mock(GraphDatabase=mock.Mock(driver=mock.Mock(return_value=driver)))
        with mock.patch.dict(sys.modules, {"neo4j": neo4j, "neo4j_question_source": graph_module}):
            with self.assertRaises(offline_answer.OfflineError) as error:
                offline_answer._graph_source(args, {"claims": []}, deadline=10**12)
        self.assertEqual(error.exception.code, "graph_corrupt")
        driver.close.assert_called_once_with()
        result = offline_answer._failure(error.exception.code)
        self.assertIsNone(result["query"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["citations"], [])

    def test_graph_flags_require_private_credentials_and_loopback_uri(self):
        from argparse import Namespace
        args = Namespace(graph_uri="bolt://example.com", graph_user="neo4j",
                         graph_password_file=str(self.private / "absent"), document_root=str(self.web))
        with self.assertRaises(offline_answer.OfflineError) as error:
            offline_answer._graph_source(args, {}, deadline=10**12)
        self.assertEqual(error.exception.code, "invalid_arguments")

    def test_final_graph_identity_failure_clears_previous_answer_and_citations(self):
        source = mock.Mock()
        source.recheck_active.side_effect = type(
            "GraphChanged", (ValueError,), {"code": "graph_generation_changed"})()
        with self.assertRaises(offline_answer.OfflineError) as error:
            offline_answer._recheck_graph(source, "a" * 64, 4, mock.Mock(), deadline=10**12)
        result = offline_answer._failure(error.exception.code)
        self.assertEqual(result["status"], "dependency_failure")
        self.assertIsNone(result["query"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["citations"], [])


if __name__ == "__main__":
    unittest.main()
