"""Check the synthetic graph verifier's oracle without a graph service."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "demo" / "synthetic" / "verify_graph.py"
SPEC = importlib.util.spec_from_file_location("synthetic_graph_verification", SCRIPT)
graph_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(graph_check)


class SyntheticGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        os.chmod(self.root, 0o700)
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        self.seed = {"title": "Verification crop Swift", "page_id": 1000001,
                     "revision_id": 1000002, "content_hash": digest("synthetic source")}
        self.claim = {"snapshot_id": digest("snapshot"), "snapshot_hash": digest("snapshot payload"),
                      "claim_id": digest("claim"), "review_version": 1, "claim_type": "property",
                      "subject": {"type": "Crop", "id": "synthetic-crop"},
                      "predicate": "growth_duration", "value": "95-98 days", "qualifiers": {},
                      "evidence": [{"evidence_id": digest("evidence"),
                                    "page_id": self.seed["page_id"], "revision_id": self.seed["revision_id"],
                                    "page_title": self.seed["title"], "content_hash": self.seed["content_hash"],
                                    "location_type": "page", "extraction_method": "rules",
                                    "extractor_version": "synthetic-test"}]}
        self.manifest = {"schema_version": "wikikg.reviewed-projection.v1", "snapshot_count": 1,
                         "source_watermark": digest("watermark"), "claims": [self.claim]}

    def write_manifest(self):
        path = self.root / "reviewed.json"
        path.write_text(json.dumps(self.manifest))
        os.chmod(path, 0o600)
        return path

    def lookup_result(self):
        claim = copy.deepcopy(self.claim)
        evidence = claim["evidence"][0]
        # The real fixed lookup deliberately does not return source text or
        # extractor fields, and returns null for absent optional identities.
        evidence.pop("extraction_method")
        evidence.pop("extractor_version")
        evidence.update(location=None, source_offset_start=None, source_offset_end=None, span_hash=None)
        claim["evidence_truncated"] = False
        return {"results": [claim], "truncated": False}

    def test_manifest_is_bound_to_unchanged_private_seed_identities(self):
        path = self.write_manifest()
        seeds = {self.seed["page_id"]: self.seed}
        self.assertEqual(graph_check.fixture_manifest(path, seeds), self.manifest)
        altered_seed = {**self.seed, "revision_id": self.seed["revision_id"] + 1}
        with self.assertRaises(ValueError):
            graph_check.fixture_manifest(path, {self.seed["page_id"]: altered_seed})
        os.chmod(path, 0o644)
        with self.assertRaises(ValueError):
            graph_check.fixture_manifest(path, seeds)

    def test_lookup_checks_real_exposed_identities_and_claim_value(self):
        result = self.lookup_result()
        with patch.object(graph_check.projection, "lookup_projection", return_value=result):
            self.assertEqual(graph_check.check_lookup(object(), self.manifest), 1)
        for altered in ("revision", "value", "truncated"):
            with self.subTest(altered=altered):
                result = self.lookup_result()
                if altered == "revision":
                    result["results"][0]["evidence"][0]["revision_id"] += 1
                elif altered == "value":
                    result["results"][0]["value"] = "140 days"
                else:
                    result["results"][0]["evidence_truncated"] = True
                with patch.object(graph_check.projection, "lookup_projection", return_value=result):
                    with self.assertRaises(ValueError):
                        graph_check.check_lookup(object(), self.manifest)

    def test_store_count_can_include_snapshots_excluded_from_export(self):
        # A source edit or rejection keeps snapshots in MediaWiki but removes
        # them from the eligible claims, without changing the stored count.
        self.manifest["snapshot_count"] = 9
        path = self.write_manifest()
        seeds = {self.seed["page_id"]: self.seed}
        self.assertEqual(graph_check.fixture_manifest(path, seeds), self.manifest)
        self.manifest["snapshot_count"] = 0
        path = self.write_manifest()
        with self.assertRaises(graph_check.projection.ProjectionError):
            graph_check.fixture_manifest(path, seeds)

    def test_verification_record_never_overwrites_previous_evidence(self):
        path = self.root / "graph-results.json"
        graph_check.write_new(path, {"synthetic_only": True})
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            graph_check.write_new(path, {"synthetic_only": False})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
