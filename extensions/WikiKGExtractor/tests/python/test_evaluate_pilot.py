"""Regression tests for the frozen engineering-pilot evaluator."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tools" / "evaluate_pilot.py"
SPEC = importlib.util.spec_from_file_location("evaluate_pilot", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
evaluate_pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate_pilot)


class EvaluatePilotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = "Năng suất 6-8 tấn/ha."
        self.content_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        self.page = {
            "title": "Lúa X",
            "kind": "variety",
            "crop": "Lúa",
            "page_id": 10,
            "revision_id": 20,
            "content_hash": self.content_hash,
            "text": self.text,
            "wikitext": self.text,
        }
        self.raw_input = {"schema_version": "1.0", "pages": [self.page]}
        self.manifest = {
            "source": {
                "page_id": 1,
                "revision_id": 2,
                "content_hash": "a" * 64,
            },
            "pages": [
                {
                    "page_id": 10,
                    "revision_id": 20,
                    "content_hash": self.content_hash,
                    "split": "dev",
                }
            ],
        }

    def test_output_directory_inside_repository_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ngoài repository"):
            evaluate_pilot.ensure_output_outside_repository(
                evaluate_pilot.EXTENSION_ROOT / "private-results"
            )

    def test_source_records_require_exact_manifest_split(self) -> None:
        source_page = {
            "title": "Lúa",
            "kind": "species",
            "crop": "Lúa",
            "page_id": 1,
            "revision_id": 2,
            "content_hash": "a" * 64,
            "text": "Lúa",
            "wikitext": "Lúa",
        }
        source_page["content_hash"] = hashlib.sha256(
            source_page["wikitext"].encode("utf-8")
        ).hexdigest()
        self.manifest["source"]["content_hash"] = source_page["content_hash"]
        raw_input = {"pages": [source_page, self.page]}
        self.assertTrue(
            evaluate_pilot.source_records_match_manifest(
                raw_input,
                self.manifest,
                "development",
            )
        )
        raw_input["pages"][1]["revision_id"] = 21
        self.assertFalse(
            evaluate_pilot.source_records_match_manifest(
                raw_input,
                self.manifest,
                "development",
            )
        )

    def test_evidence_offsets_are_revalidated_against_wikitext(self) -> None:
        start = self.text.index("6-8")
        span = "6-8 tấn/ha"
        candidates = {
            "metadata": {"extractor_version": "2.3.0"},
            "summary": {"traceability_complete_count": 1},
            "claims": [
                {
                    "review": {"status": "pending"},
                    "traceability_complete": True,
                    "evidence": [
                        {
                            "page_id": 10,
                            "revision_id": 20,
                            "content_hash": self.content_hash,
                            "page_title": "Lúa X",
                            "extraction_method": "rules",
                            "extractor_version": "2.3.0",
                            "location_type": "source_span",
                            "source_offset_start": start,
                            "source_offset_end": start + len(span),
                            "supporting_span": span,
                            "span_hash": hashlib.sha256(
                                span.encode("utf-8")
                            ).hexdigest(),
                        }
                    ],
                }
            ],
        }
        self.assertTrue(
            evaluate_pilot.evidence_revalidates(self.raw_input, candidates)
        )
        candidates["claims"][0]["evidence"][0]["source_offset_start"] += 1
        self.assertFalse(
            evaluate_pilot.evidence_revalidates(self.raw_input, candidates)
        )

    def test_output_source_records_reject_duplicates(self) -> None:
        source = {
            key: self.page[key]
            for key in ("title", "kind", "crop", "page_id", "revision_id", "content_hash")
        }
        candidates = {"metadata": {"source_pages": [source, source]}}
        graph = {"metadata": {"source_pages": [source]}}
        self.assertFalse(
            evaluate_pilot.output_source_records_match_input(
                self.raw_input,
                candidates,
                graph,
            )
        )

    def test_new_empty_output_directory_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluate_pilot.ensure_output_outside_repository(Path(directory))

    def test_heldout_evaluation_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(ValueError, "Held-out bị khóa"):
                evaluate_pilot.evaluate(
                    missing,
                    missing,
                    missing,
                    Path(directory) / "output",
                    "heldout",
                )

    def test_artifact_hash_check_rejects_unlisted_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            raw_input = root / "raw-input.json"
            manifest.write_text("{}\n", encoding="utf-8")
            raw_input.write_text("{}\n", encoding="utf-8")
            protocol = {
                "extractor": {
                    "worker_sha256": evaluate_pilot.sha256_file(
                        evaluate_pilot.WORKER
                    ),
                    "evaluator_sha256": evaluate_pilot.sha256_file(
                        evaluate_pilot.EVALUATOR
                    ),
                },
                "artifacts": {
                    "manifest_sha256": evaluate_pilot.sha256_file(manifest),
                    "development_input_sha256": evaluate_pilot.sha256_file(
                        raw_input
                    ),
                    "heldout_input_sha256": "a" * 64,
                    "schema_sha256": {
                        name: evaluate_pilot.sha256_file(
                            evaluate_pilot.SCHEMA_DIR / name
                        )
                        for name in evaluate_pilot.PINNED_SCHEMA_NAMES
                    },
                },
                "evaluation": {
                    "deterministic_outputs": sorted(
                        evaluate_pilot.DETERMINISTIC_OUTPUT_NAMES
                    )
                },
            }
            self.assertTrue(
                evaluate_pilot.artifact_hashes_match(
                    protocol,
                    manifest,
                    raw_input,
                    "development",
                )
            )
            protocol["evaluation"]["deterministic_outputs"].append(
                "../unexpected.json"
            )
            self.assertFalse(
                evaluate_pilot.artifact_hashes_match(
                    protocol,
                    manifest,
                    raw_input,
                    "development",
                )
            )


if __name__ == "__main__":
    unittest.main()
