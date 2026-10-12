"""Synthetic contracts for source-only packs, immutable intake and discrepancies."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import development_annotation as workflow  # noqa: E402 - the tools directory is not a package


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DevelopmentAnnotationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="annotation-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.directory.chmod(0o700)
        self.text = (
            "Lúa X 🌾: năng suất 6-8 tấn/ha trong vụ Đông Xuân.\r\n"
            "Chiều cao 100-110 cm. Kháng bệnh A. Đợt 1. Đợt 1.\n"
            "Giá trị chưa hiểu, chữ e\u0301. Thời gian 90-100 ngày."
        )
        self.pages = [
            self.page(1, "Lúa", "Trang cây trồng giả lập."),
            self.page(10, "Lúa X", self.text),
        ]
        self.raw = {"schema_version": "1.0", "page_count": 2, "error_count": 0, "pages": self.pages}
        self.manifest = {
            "schema_version": "1.0",
            "corpus": "synthetic-development-contract",
            "source": {**self.pin(self.pages[0]), "discovery_heading_rule": "synthetic"},
            "selection": {
                "method": "synthetic",
                "strata": "synthetic",
                "seed": "test",
                "sample_size": 2,
                "dev_size": 1,
                "test_size": 1,
                "heldout_exclusion_policy": "sealed",
                "previously_observed_titles": [],
            },
            "counts": {"discovered": 2, "statuses": {"resolved": 2}, "selected": 2},
            "pages": [
                self.manifest_page(self.pages[1], "dev"),
                {
                    "discovery_index": 1,
                    "requested_title": "SEALED-METADATA-ONLY",
                    "resolved_title": "SEALED-METADATA-ONLY",
                    "status": "resolved",
                    "eligible_for_development": True,
                    "eligible_for_heldout": True,
                    "heldout_exclusion_reason": None,
                    "selected": True,
                    "split": "test",
                    "page_id": 99,
                    "revision_id": 999,
                    "content_hash": "a" * 64,
                },
            ],
        }
        self.protocol = json.loads(workflow.PROTOCOL.read_bytes())
        self.raw_path = self.directory / "development.json"
        self.manifest_path = self.directory / "manifest.json"
        self.protocol_path = self.directory / "protocol.json"
        self.repin()
        self.pack_path = self.directory / "source pack"
        workflow.prepare(self.protocol_path, self.manifest_path, self.raw_path, self.pack_path)
        self.pack, self.source_pages, _ = workflow.load_pack(self.pack_path)
        self.submission = workflow.submission_template(self.pack)
        self.submission.update(
            {
                "submission_id": "synthetic-contract",
                "provenance": {
                    "annotator_id": "synthetic-fixture",
                    "submitted_at": "2026-10-10T08:00:00+00:00",
                    "exposure": "synthetic",
                    "notes": "Not a human annotation.",
                },
            }
        )
        self.submission["page_reviews"][1]["status"] = "complete"
        self.submission["annotations"] = [
            self.annotation("A1", "yield_amount", "6-8 tấn/ha", unit="tấn/ha")
        ]
        self.submission_path = self.directory / "working submission.json"
        self.write_submission()
        self.candidates = self.candidate_document(
            [self.candidate("C1", "yield_amount", "6-8 tấn/ha")]
        )
        self.candidate_path = self.directory / "candidates.json"
        self.write_candidates()
        self.retained = self.directory / "retained-1"

    @staticmethod
    def page(page_id: int, title: str, text: str) -> dict:
        return {
            "page_id": page_id,
            "revision_id": page_id * 10,
            "title": title,
            "content_hash": digest(text),
            "kind": "species" if page_id == 1 else "variety",
            "crop": "Lúa",
            "text": text,
            "wikitext": text,
        }

    @staticmethod
    def pin(page: dict) -> dict:
        return {key: page[key] for key in ("title", "page_id", "revision_id", "content_hash")}

    def manifest_page(self, page: dict, split: str) -> dict:
        return {
            **{key: page[key] for key in ("page_id", "revision_id", "content_hash")},
            "discovery_index": 0,
            "requested_title": page["title"],
            "resolved_title": page["title"],
            "status": "resolved",
            "eligible_for_development": True,
            "eligible_for_heldout": False,
            "heldout_exclusion_reason": "development",
            "selected": True,
            "split": split,
        }

    def repin(self) -> None:
        self.raw_path.write_bytes(workflow.encoded(self.raw))
        self.manifest_path.write_bytes(workflow.encoded(self.manifest))
        self.protocol["artifacts"]["development_input_sha256"] = workflow.sha(
            self.raw_path.read_bytes()
        )
        self.protocol["artifacts"]["manifest_sha256"] = workflow.sha(
            self.manifest_path.read_bytes()
        )
        self.protocol_path.write_bytes(workflow.encoded(self.protocol))

    def evidence(self, quote: str, *, start: int | None = None) -> dict:
        start = self.text.index(quote) if start is None else start
        return {
            "page_id": 10,
            "revision_id": 100,
            "content_hash": digest(self.text),
            "source_offset_start": start,
            "source_offset_end": start + len(quote),
            "supporting_span": quote,
            "span_hash": digest(quote),
        }

    def annotation(
        self, identifier: str, predicate: str, value: str, *, unit: str | None = None
    ) -> dict:
        return {
            "annotation_id": identifier,
            "subject": {"literal": "X", "type": "Variety", "identity_status": "resolved"},
            "predicate": predicate,
            "value_literal": value,
            "unit": {"state": "stated" if unit else "not_stated", "literal": unit},
            "qualifiers": {
                key: {"state": "not_stated", "literal": None}
                for key in self.pack["scope"]["qualifier_keys"]
            },
            "evidence": self.evidence(value),
            "judgment": "supported",
            "outside_scope": False,
            "reason": "",
        }

    def candidate(
        self, identifier: str, predicate: str, value: str, *, qualifiers: dict | None = None
    ) -> dict:
        evidence = self.evidence(value)
        evidence.update(
            {
                "evidence_id": digest(f"evidence-{identifier}"),
                "page_title": "Lúa X",
                "location_type": "source_span",
                "extraction_method": "rules",
                "extractor_version": "2.3.1",
            }
        )
        return {
            "claim_id": digest(identifier),
            "claim_type": "property",
            "subject": {"type": "Variety", "id": "X"},
            "predicate": predicate,
            "value": value,
            "qualifiers": qualifiers or {},
            "evidence": [evidence],
            "traceability_complete": True,
            "review": {"status": "pending"},
        }

    def candidate_document(self, claims: list[dict]) -> dict:
        counts = {}
        for claim in claims:
            counts[claim["predicate"]] = counts.get(claim["predicate"], 0) + 1
        return {
            "schema_version": "1.0",
            "metadata": {
                "extractor_version": "2.3.1",
                "extraction_method": "rules",
                "review_state": "pending",
                "source_pages": [self.pin(page) for page in self.pages],
            },
            "summary": {
                "claim_count": len(claims),
                "traceability_complete_count": sum(
                    claim["traceability_complete"] for claim in claims
                ),
                "claims_by_predicate": counts,
            },
            "claims": claims,
        }

    def write_submission(self) -> None:
        self.submission_path.write_bytes(workflow.encoded(self.submission))

    def write_candidates(self) -> None:
        self.candidate_path.write_bytes(workflow.encoded(self.candidates))

    def retain(self) -> str:
        return workflow.retain(self.pack_path, self.submission_path, self.retained)[
            "submission_sha256"
        ]

    def compare(self, *, output: str = "comparison") -> tuple[dict, dict]:
        submission_hash = self.retain()
        target = self.directory / output
        result = workflow.compare(
            self.pack_path,
            self.retained,
            submission_hash,
            self.candidate_path,
            workflow.sha(self.candidate_path.read_bytes()),
            target,
        )
        return result, json.loads((target / "report.json").read_bytes())

    def assert_error(self, code: str, function, *args, **kwargs) -> None:
        with self.assertRaises(workflow.AnnotationError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.result["code"])

    def rewrite_bundle_file(self, directory: Path, name: str, data: bytes) -> None:
        """Tamper with content and all local hashes, leaving an external anchor unchanged."""
        (directory / name).write_bytes(data)
        receipt = json.loads((directory / "receipt.json").read_bytes())
        receipt["files_sha256"][name] = workflow.sha(data)
        receipt["operation_sha256"] = workflow.sha(
            workflow.encoded({"kind": receipt["kind"], "files_sha256": receipt["files_sha256"]})
        )
        (directory / "receipt.json").write_bytes(workflow.encoded(receipt))

    def test_pack_is_full_source_only_and_has_blank_provenance(self) -> None:
        self.assertEqual(self.text.encode(), (self.pack_path / "source-10.txt").read_bytes())
        template = json.loads((self.pack_path / "submission-template.json").read_bytes())
        self.assertEqual([], template["annotations"])
        self.assertEqual("", template["provenance"]["annotator_id"])
        self.assertEqual("unknown", template["provenance"]["exposure"])
        combined = b"".join(file.read_bytes() for file in self.pack_path.iterdir())
        self.assertNotIn(b"SEALED-METADATA-ONLY", combined)
        self.assertNotIn(b"heldout_input_sha256", combined)
        self.assertNotIn(b"claim_id", (self.pack_path / "annotation-form.json").read_bytes())
        self.assertEqual(0o700, stat.S_IMODE(self.pack_path.stat().st_mode))
        self.assertTrue(
            all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in self.pack_path.iterdir())
        )

    def test_pack_retry_is_identical_and_does_not_touch_receipt(self) -> None:
        before = {
            file.name: (file.read_bytes(), file.stat().st_mtime_ns)
            for file in self.pack_path.iterdir()
        }
        result = workflow.prepare(
            self.protocol_path, self.manifest_path, self.raw_path, self.pack_path
        )
        self.assertEqual("unchanged", result["status"])
        self.assertEqual(
            before,
            {
                file.name: (file.read_bytes(), file.stat().st_mtime_ns)
                for file in self.pack_path.iterdir()
            },
        )

    def test_source_drift_fails_before_output(self) -> None:
        self.raw["pages"][1]["wikitext"] += "tamper"
        self.raw_path.write_bytes(workflow.encoded(self.raw))
        destination = self.directory / "drift"
        self.assert_error(
            "DEVELOPMENT_PIN",
            workflow.prepare,
            self.protocol_path,
            self.manifest_path,
            self.raw_path,
            destination,
        )
        self.assertFalse(destination.exists())

    def test_rehashed_wrong_source_revision_fails_manifest_check(self) -> None:
        self.raw["pages"][1]["revision_id"] += 1
        self.repin()
        self.assert_error(
            "SOURCE_PIN",
            workflow.prepare,
            self.protocol_path,
            self.manifest_path,
            self.raw_path,
            self.directory / "new",
        )

    def test_rehashed_title_disagreement_is_rejected(self) -> None:
        self.raw["pages"][1]["title"] = "different"
        self.repin()
        self.assert_error(
            "SOURCE_PIN",
            workflow.prepare,
            self.protocol_path,
            self.manifest_path,
            self.raw_path,
            self.directory / "new",
        )

    def test_prepare_refuses_unusable_identifiers_and_scope_before_publication(self) -> None:
        original = copy.deepcopy((self.raw, self.manifest, self.protocol))
        for case, code in (
            ("float_source_id", "SOURCE_PIN"),
            ("float_manifest_id", "SOURCE_PIN"),
            ("blank_title", "SOURCE_PIN"),
            ("overlapping_predicates", "PROTOCOL_SCOPE"),
            ("uppercase_qualifier", "PROTOCOL_SCOPE"),
            ("unsupported_entity", "PROTOCOL_SCOPE"),
            ("too_many_qualifiers", "PROTOCOL_SCOPE"),
        ):
            with self.subTest(case=case):
                self.raw, self.manifest, self.protocol = copy.deepcopy(original)
                if case == "float_source_id":
                    self.raw["pages"][1].update({"page_id": 10.0, "revision_id": 100.0})
                elif case == "float_manifest_id":
                    self.manifest["pages"][0].update({"page_id": 10.0, "revision_id": 100.0})
                elif case == "blank_title":
                    self.raw["pages"][1]["title"] = "   "
                    self.manifest["pages"][0]["resolved_title"] = "   "
                elif case == "overlapping_predicates":
                    self.protocol["ontology"]["relationship_predicates"] = ["yield_amount"]
                elif case == "uppercase_qualifier":
                    self.protocol["ontology"]["qualifier_keys"] = ["Season"]
                elif case == "unsupported_entity":
                    self.protocol["ontology"]["entity_types"] = ["Unknown"]
                else:
                    self.protocol["ontology"]["qualifier_keys"] = [f"scope{i}" for i in range(33)]
                self.repin()
                output = self.directory / case
                self.assert_error(
                    code,
                    workflow.prepare,
                    self.protocol_path,
                    self.manifest_path,
                    self.raw_path,
                    output,
                )
                self.assertFalse(output.exists())

    def test_duplicate_source_identity_is_rejected(self) -> None:
        self.raw["pages"].append(self.raw["pages"][1])
        self.raw["page_count"] = 3
        self.repin()
        self.assert_error(
            "SOURCE_PIN",
            workflow.prepare,
            self.protocol_path,
            self.manifest_path,
            self.raw_path,
            self.directory / "new",
        )

    def test_template_is_not_a_valid_submission(self) -> None:
        self.assert_error(
            "SCHEMA", workflow.validate, self.pack_path, self.pack_path / "submission-template.json"
        )

    def test_validation_is_read_only_and_does_not_certify_provenance(self) -> None:
        before = set(self.directory.iterdir())
        result = workflow.validate(self.pack_path, self.submission_path)
        self.assertEqual("valid_format_and_coordinates", result["status"])
        self.assertEqual("not_established", result["provenance_verification"])
        self.assertEqual(before, set(self.directory.iterdir()))
        self.assertNotIn("independent", result)

    def test_duplicate_annotation_ids_are_rejected(self) -> None:
        self.submission["annotations"].append(copy.deepcopy(self.submission["annotations"][0]))
        self.write_submission()
        self.assert_error("ANNOTATION_ID", workflow.validate, self.pack_path, self.submission_path)

    def test_unicode_bytes_and_utf16_offsets_do_not_validate(self) -> None:
        annotation = self.submission["annotations"][0]
        original = copy.deepcopy(annotation["evidence"])
        start = original["source_offset_start"]
        for bad_start in (
            len(self.text[:start].encode()),
            len(self.text[:start].encode("utf-16-le")) // 2,
        ):
            with self.subTest(start=bad_start):
                annotation["evidence"] = {
                    **original,
                    "source_offset_start": bad_start,
                    "source_offset_end": bad_start + len(original["supporting_span"]),
                }
                self.write_submission()
                self.assert_error(
                    "EVIDENCE_SPAN", workflow.validate, self.pack_path, self.submission_path
                )

    def test_exact_quote_range_hash_and_revision_are_required(self) -> None:
        for field, value, code in (
            ("revision_id", 101, "EVIDENCE_PIN"),
            ("content_hash", "f" * 64, "EVIDENCE_PIN"),
            ("source_offset_end", 99999, "EVIDENCE_RANGE"),
            ("span_hash", "f" * 64, "EVIDENCE_SPAN"),
            ("supporting_span", "wrong", "EVIDENCE_SPAN"),
        ):
            with self.subTest(field=field):
                original = self.submission["annotations"][0]["evidence"][field]
                self.submission["annotations"][0]["evidence"][field] = value
                self.write_submission()
                self.assert_error(code, workflow.validate, self.pack_path, self.submission_path)
                self.submission["annotations"][0]["evidence"][field] = original

    def test_qualifier_states_missing_or_uncertain_require_explicit_work(self) -> None:
        annotation = self.submission["annotations"][0]
        del annotation["qualifiers"]["season"]
        self.write_submission()
        self.assert_error(
            "QUALIFIER_STATE", workflow.validate, self.pack_path, self.submission_path
        )
        annotation["qualifiers"]["season"] = {"state": "uncertain", "literal": None}
        self.write_submission()
        self.assert_error("REASON", workflow.validate, self.pack_path, self.submission_path)
        annotation["reason"] = "Synthetic uncertainty fixture."
        self.write_submission()
        self.assertEqual(1, workflow.validate(self.pack_path, self.submission_path)["annotations"])

    def test_outside_scope_is_preserved_not_a_missing_machine_error(self) -> None:
        self.submission["annotations"][0].update(
            {"predicate": "unmodelled_stage", "outside_scope": True, "reason": "Synthetic gap."}
        )
        self.write_submission()
        _, report = self.compare()
        self.assertEqual(
            {"outside_scope": 1, "unmatched_candidates": 1}, report["counts"]["groups_by_status"]
        )

    def test_unknown_identity_must_stay_unresolved_and_explained(self) -> None:
        self.submission["annotations"][0]["subject"]["type"] = "unknown"
        self.write_submission()
        self.assert_error("IDENTITY", workflow.validate, self.pack_path, self.submission_path)
        self.submission["annotations"][0]["subject"]["identity_status"] = "unresolved"
        self.write_submission()
        self.assert_error("REASON", workflow.validate, self.pack_path, self.submission_path)
        self.submission["annotations"][0]["reason"] = "Synthetic unresolved identity."
        self.write_submission()
        _, report = self.compare()
        self.assertIn("unresolved_annotation", report["counts"]["groups_by_status"])

    def test_retention_preserves_exact_original_bytes_and_identical_retry(self) -> None:
        data = json.dumps(self.submission, ensure_ascii=False).encode() + b"\n\n"
        self.submission_path.write_bytes(data)
        first_hash = self.retain()
        before = (self.retained / "receipt.json").read_bytes()
        self.assertEqual(data, (self.retained / "submission.json").read_bytes())
        self.assertEqual(workflow.sha(data), first_hash)
        self.assertEqual(
            "unchanged",
            workflow.retain(self.pack_path, self.submission_path, self.retained)["status"],
        )
        self.assertEqual(before, (self.retained / "receipt.json").read_bytes())

    def test_correction_links_new_version_and_never_overwrites_previous(self) -> None:
        earlier_hash = self.retain()
        before = {file.name: file.read_bytes() for file in self.retained.iterdir()}
        self.submission.update({"version": 2, "previous_submission_sha256": earlier_hash})
        self.submission["annotations"][0]["reason"] = "Correction in a synthetic test."
        self.write_submission()
        corrected = self.directory / "retained-2"
        result = workflow.retain(
            self.pack_path, self.submission_path, corrected, self.retained, earlier_hash
        )
        self.assertEqual(2, result["version"])
        self.assertNotEqual(earlier_hash, result["submission_sha256"])
        self.assertEqual(before, {file.name: file.read_bytes() for file in self.retained.iterdir()})
        self.assert_error(
            "OUTPUT_EXISTS",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            self.retained,
            self.retained,
            earlier_hash,
        )

    def test_correction_refuses_missing_wrong_or_skipped_predecessors(self) -> None:
        earlier_hash = self.retain()
        self.submission.update({"version": 2, "previous_submission_sha256": earlier_hash})
        self.write_submission()
        self.assert_error(
            "PREDECESSOR",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            self.directory / "new",
        )
        self.assert_error(
            "SUBMISSION_CHANGED",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            self.directory / "new",
            self.retained,
            "f" * 64,
        )
        self.submission["version"] = 3
        self.write_submission()
        self.assert_error(
            "PREDECESSOR",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            self.directory / "new",
            self.retained,
            earlier_hash,
        )

    def test_correction_cannot_launder_annotator_or_known_exposure(self) -> None:
        earlier_hash = self.retain()
        self.submission.update({"version": 2, "previous_submission_sha256": earlier_hash})
        self.submission["provenance"]["exposure"] = "source_only_reported"
        self.write_submission()
        self.assert_error(
            "EXPOSURE",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            self.directory / "new",
            self.retained,
            earlier_hash,
        )
        self.submission["provenance"]["exposure"] = "synthetic"
        self.submission["provenance"]["annotator_id"] = "different-person"
        self.write_submission()
        self.assert_error(
            "PREDECESSOR",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            self.directory / "new",
            self.retained,
            earlier_hash,
        )

    def test_tampered_submission_is_detected_even_with_rewritten_local_receipt(self) -> None:
        earlier_hash = self.retain()
        data = (self.retained / "submission.json").read_bytes() + b" "
        self.rewrite_bundle_file(self.retained, "submission.json", data)
        self.assert_error(
            "SUBMISSION_CHANGED",
            workflow.compare,
            self.pack_path,
            self.retained,
            earlier_hash,
            self.directory / "unread-candidates.json",
            "a" * 64,
            self.directory / "new",
        )

    def test_compare_checks_retention_before_reading_machine_artifact(self) -> None:
        self.assert_error(
            "SCHEMA",
            workflow.retain,
            self.pack_path,
            self.pack_path / "submission-template.json",
            self.directory / "blank-retained",
        )
        self.assertFalse((self.directory / "blank-retained").exists())
        with self.assertRaises(FileNotFoundError):
            workflow.compare(
                self.pack_path,
                self.retained,
                "a" * 64,
                self.candidate_path,
                workflow.sha(self.candidate_path.read_bytes()),
                self.directory / "new",
            )

    def test_complete_round_trip_keeps_match_omission_qualifier_duplicates_and_uncertainty_separate(
        self,
    ) -> None:
        annotations = [
            self.submission["annotations"][0],
            self.annotation("A2", "plant_height", "100-110 cm", unit="cm"),
            self.annotation("A3", "growth_duration", "90-100 ngày", unit="ngày"),
            self.annotation("A4", "variety_type", "Giá trị chưa hiểu"),
            self.annotation("A5", "variety_type", "Giá trị chưa hiểu"),
        ]
        annotations[1]["qualifiers"]["season"] = {"state": "stated", "literal": "Đông Xuân"}
        self.submission["annotations"] = annotations
        self.write_submission()
        self.candidates = self.candidate_document(
            [
                self.candidate("C1", "yield_amount", "6-8 tấn/ha"),
                self.candidate("C2", "plant_height", "100-110 cm", qualifiers={"season": "Hè Thu"}),
                self.candidate("C4", "variety_type", "Giá trị chưa hiểu"),
            ]
        )
        self.write_candidates()
        result, report = self.compare()
        self.assertEqual(
            {"ambiguous": 1, "possible_pair": 2, "unmatched_annotations": 1},
            result["groups_by_status"],
        )
        pair = next(group for group in report["groups"] if group["annotation_ids"] == ["A1"])
        self.assertTrue(pair["comparison"]["target_or_value"]["exact_literal"])
        self.assertTrue(pair["comparison"]["evidence"]["exact_span"])
        self.assertTrue(pair["comparison"]["unit"]["exact_literal"])
        self.assertTrue(
            all(
                item["comparison"] == "both_not_stated"
                for item in pair["comparison"]["qualifiers"].values()
            )
        )
        different_qualifier = next(
            group for group in report["groups"] if group["annotation_ids"] == ["A2"]
        )
        self.assertEqual(
            "different_literal",
            different_qualifier["comparison"]["qualifiers"]["season"]["comparison"],
        )
        self.assertEqual("not_computed", result["scientific_scores"])
        self.assertTrue(all(group["human_decision"] is None for group in report["groups"]))
        self.assertCountEqual(
            ["A1", "A2", "A3", "A4", "A5"],
            [identifier for group in report["groups"] for identifier in group["annotation_ids"]],
        )
        self.assertCountEqual(
            [digest("C1"), digest("C2"), digest("C4")],
            [identifier for group in report["groups"] for identifier in group["candidate_ids"]],
        )

    def test_multiple_machine_alternatives_are_ambiguous_not_greedily_selected(self) -> None:
        self.candidates = self.candidate_document(
            [
                self.candidate("C1", "yield_amount", "6-8 tấn/ha"),
                self.candidate("C2", "yield_amount", "6-8 tấn/ha", qualifiers={"season": "Hè Thu"}),
            ]
        )
        self.write_candidates()
        result, report = self.compare()
        self.assertEqual({"ambiguous": 1}, result["groups_by_status"])
        self.assertNotIn("comparison", report["groups"][0])

    def test_alias_difference_is_unmatched_without_normalization(self) -> None:
        self.submission["annotations"][0]["subject"]["literal"] = "Lúa X"
        self.write_submission()
        result, _ = self.compare()
        self.assertEqual(
            {"unmatched_annotations": 1, "unmatched_candidates": 1}, result["groups_by_status"]
        )

    def test_values_units_and_spans_are_compared_separately(self) -> None:
        self.submission["annotations"][0]["value_literal"] = "6 đến 8 tấn/ha"
        self.submission["annotations"][0]["unit"]["literal"] = "tấn trên ha"
        self.submission["annotations"][0]["evidence"] = self.evidence(self.text.split("\r\n")[0])
        self.write_submission()
        _, report = self.compare()
        comparison = report["groups"][0]["comparison"]
        self.assertFalse(comparison["target_or_value"]["exact_literal"])
        self.assertFalse(comparison["unit"]["exact_literal"])
        self.assertFalse(comparison["evidence"]["exact_span"])
        self.assertEqual("not_judged", comparison["evidence"]["semantic_support"])

    def test_relationship_target_is_literal_not_parent_inference(self) -> None:
        annotation = self.submission["annotations"][0]
        del annotation["value_literal"]
        annotation.update(
            {
                "predicate": "RESISTANT_TO",
                "object": {"type": "Pest", "literal": "bệnh A", "identity_status": "resolved"},
                "evidence": self.evidence("Kháng bệnh A."),
            }
        )
        self.write_submission()
        candidate = self.candidate("C1", "variety_type", "Kháng bệnh A.")
        del candidate["value"]
        candidate.update(
            {
                "claim_type": "relationship",
                "predicate": "RESISTANT_TO",
                "object": {"type": "Pest", "id": "bệnh A"},
            }
        )
        self.candidates = self.candidate_document([candidate])
        self.write_candidates()
        _, report = self.compare()
        self.assertTrue(report["groups"][0]["comparison"]["target_or_value"]["exact_literal"])

    def test_candidate_hash_revision_duplicate_id_and_bad_span_are_rejected(self) -> None:
        submission_hash = self.retain()
        self.assert_error(
            "CANDIDATE_CHANGED",
            workflow.compare,
            self.pack_path,
            self.retained,
            submission_hash,
            self.candidate_path,
            "a" * 64,
            self.directory / "bad",
        )
        original = copy.deepcopy(self.candidates)
        changes = [
            lambda doc: doc["metadata"]["source_pages"][1].update({"revision_id": 101}),
            lambda doc: doc["claims"][0]["evidence"][0].update({"span_hash": "a" * 64}),
            lambda doc: doc["claims"].append(copy.deepcopy(doc["claims"][0])),
        ]
        for change, code in zip(
            changes, ["CANDIDATE_SOURCE", "CANDIDATE_SOURCE", "CANDIDATE_ACCOUNTING"]
        ):
            self.candidates = copy.deepcopy(original)
            change(self.candidates)
            self.write_candidates()
            self.assert_error(
                code,
                workflow.compare,
                self.pack_path,
                self.retained,
                submission_hash,
                self.candidate_path,
                workflow.sha(self.candidate_path.read_bytes()),
                self.directory / "bad",
            )
        self.assertFalse((self.directory / "bad").exists())

    def test_empty_partial_submission_and_empty_candidate_artifact_are_accounted(self) -> None:
        self.submission["annotations"] = []
        self.submission["page_reviews"][1]["status"] = "partial"
        self.write_submission()
        self.candidates = self.candidate_document([])
        self.write_candidates()
        result, report = self.compare()
        self.assertEqual([], report["groups"])
        self.assertEqual({"not_started": 1, "partial": 1}, result["page_reviews"])
        self.assertEqual("not_computed", result["scientific_scores"])

    def test_locate_preserves_unicode_and_line_endings_without_python_editing(self) -> None:
        quote = self.directory / "quote.txt"
        quote.write_bytes("🌾: năng suất 6-8 tấn/ha trong vụ Đông Xuân.\r\n".encode())
        output = self.directory / "quote-result"
        workflow.locate(self.pack_path, 10, quote, output)
        evidence = json.loads((output / "evidence.json").read_bytes())
        self.assertEqual(quote.read_bytes().decode(), evidence["supporting_span"])
        self.assertEqual(self.text.index("🌾"), evidence["source_offset_start"])
        workflow.check_evidence(evidence, self.source_pages, index=0)

    def test_repeated_quote_needs_explicit_start(self) -> None:
        quote = self.directory / "quote.txt"
        quote.write_bytes("Đợt 1.".encode())
        output = self.directory / "quote-result"
        self.assert_error("AMBIGUOUS_QUOTE", workflow.locate, self.pack_path, 10, quote, output)
        self.assertFalse(output.exists())
        result = workflow.locate(self.pack_path, 10, quote, output, self.text.rindex("Đợt 1."))
        self.assertEqual(self.text.rindex("Đợt 1."), result["source_offset_start"])

    def test_private_parent_symlink_and_document_root_are_refused(self) -> None:
        public = self.directory / "public"
        public.mkdir(mode=0o755)
        self.assert_error(
            "PRIVATE_DIRECTORY",
            workflow.write_bundle,
            public / "output",
            "evidence",
            {"a.json": b"{}"},
        )
        link = self.directory / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        self.assert_error(
            "SYMLINK", workflow.write_bundle, link / "output", "evidence", {"a.json": b"{}"}
        )
        self.assert_error(
            "PUBLIC_DESTINATION",
            workflow.write_bundle,
            self.directory / "output",
            "evidence",
            {"a.json": b"{}"},
            (self.directory,),
        )
        self.assert_error(
            "PUBLIC_DESTINATION",
            workflow.write_bundle,
            ROOT / "output",
            "evidence",
            {"a.json": b"{}"},
        )

    def test_symlink_hardlink_or_public_file_cannot_be_consumed(self) -> None:
        source = self.pack_path / "source-10.txt"
        source.chmod(0o644)
        self.assert_error("PRIVATE_FILE", workflow.load_pack, self.pack_path)
        source.chmod(0o600)
        linked = self.directory / "hardlink"
        os.link(source, linked)
        self.assert_error("PRIVATE_FILE", workflow.load_pack, self.pack_path)
        symlink = self.directory / "symlink.json"
        symlink.symlink_to(self.submission_path)
        with self.assertRaises(OSError):
            workflow.read_bytes(symlink)

    def test_extra_or_tampered_source_pack_files_are_refused(self) -> None:
        source = self.pack_path / "source-10.txt"
        source.write_bytes(b"tamper")
        self.assert_error("BUNDLE_CHANGED", workflow.load_pack, self.pack_path)

    def test_incomplete_output_is_kept_but_cannot_be_read_or_overwritten(self) -> None:
        output = self.directory / "interrupted"
        with patch.object(
            workflow.os, "fsync", side_effect=OSError("synthetic write interruption")
        ):
            with self.assertRaises(OSError):
                workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        self.assertTrue(output.exists())
        self.assertFalse((output / "receipt.json").exists())
        with self.assertRaises(FileNotFoundError):
            workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        self.assertEqual(b"{}\n", (output / "a.json").read_bytes())

    def test_receipt_fsync_failure_leaves_an_unpublished_bundle(self) -> None:
        output = self.directory / "receipt-interrupted"
        actual_fsync = os.fsync
        calls = 0

        def fail_receipt(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic receipt persistence failure")
            actual_fsync(descriptor)

        with patch.object(workflow.os, "fsync", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        self.assertFalse((output / "receipt.json").exists())
        before = {file.name: file.read_bytes() for file in output.iterdir()}
        with self.assertRaises(FileNotFoundError):
            workflow.load_bundle(output, "evidence")
        with self.assertRaises(FileNotFoundError):
            workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        self.assertEqual(before, {file.name: file.read_bytes() for file in output.iterdir()})

    def test_directory_and_parent_are_synced_before_receipt_publication(self) -> None:
        output = self.directory / "publication-barriers"
        actual_fsync = os.fsync
        trace = []

        def traced(descriptor: int) -> None:
            info = os.fstat(descriptor)
            trace.append((info.st_dev, info.st_ino, (output / "receipt.json").exists()))
            actual_fsync(descriptor)

        with patch.object(workflow.os, "fsync", side_effect=traced):
            workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        for path in (output, self.directory):
            info = path.stat()
            self.assertIn((info.st_dev, info.st_ino, False), trace)
            self.assertIn((info.st_dev, info.st_ino, True), trace)
        self.assertEqual({"a.json": b"{}\n"}, workflow.load_bundle(output, "evidence")[0])

    def test_identical_retry_syncs_files_and_directories_without_rewriting(self) -> None:
        output = self.directory / "retry-barriers"
        workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        before = {
            file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in output.iterdir()
        }
        actual_fsync = os.fsync
        synced = []

        def traced(descriptor: int) -> None:
            info = os.fstat(descriptor)
            synced.append((info.st_dev, info.st_ino))
            actual_fsync(descriptor)

        with patch.object(workflow.os, "fsync", side_effect=traced):
            result = workflow.write_bundle(output, "evidence", {"a.json": b"{}\n"})
        self.assertEqual("unchanged", result["status"])
        for path in (output / "a.json", output / "receipt.json", output, self.directory):
            info = path.stat()
            self.assertIn((info.st_dev, info.st_ino), synced)
        self.assertEqual(
            before,
            {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in output.iterdir()},
        )

    def test_two_processes_never_mix_identical_or_different_submissions(self) -> None:
        child = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import development_annotation as workflow
target = Path(sys.argv[2])
actual_mkdir = Path.mkdir
def gated(path, *args, **kwargs):
    if path == target:
        print('ready', flush=True)
        sys.stdin.buffer.read(1)
    return actual_mkdir(path, *args, **kwargs)
Path.mkdir = gated
raise SystemExit(workflow.main([
    'retain', '--pack', sys.argv[3], '--submission', sys.argv[4], '--output', str(target)
]))
"""
        for identical in (True, False):
            with self.subTest(identical=identical):
                target = self.directory / f"race-{identical}"
                inputs = []
                for index in range(2):
                    submission = copy.deepcopy(self.submission)
                    submission["provenance"]["notes"] = "Synthetic writer " + str(
                        0 if identical else index
                    )
                    path = self.directory / f"race-{identical}-{index}.json"
                    path.write_bytes(workflow.encoded(submission))
                    inputs.append(path)
                children = []
                try:
                    for path in inputs:
                        children.append(
                            subprocess.Popen(
                                [
                                    sys.executable,
                                    "-c",
                                    child,
                                    str(ROOT / "tools"),
                                    str(target),
                                    str(self.pack_path),
                                    str(path),
                                ],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                            )
                        )
                    with selectors.DefaultSelector() as selector:
                        for process in children:
                            selector.register(process.stdout, selectors.EVENT_READ)
                        for _ in children:
                            ready = selector.select(timeout=20)
                            self.assertTrue(ready, "Writer did not reach the controlled mkdir.")
                            stream = ready[0][0].fileobj
                            self.assertEqual(b"ready\n", stream.readline())
                            selector.unregister(stream)
                    for process in children:
                        process.stdin.write(b"G")
                        process.stdin.flush()
                    results = [process.communicate(timeout=20) for process in children]
                    self.assertEqual([0, 2], sorted(process.returncode for process in children))
                    for process, (stdout, stderr) in zip(children, results):
                        if process.returncode == 0:
                            self.assertEqual("created", json.loads(stdout)["status"])
                            self.assertEqual(b"", stderr)
                        else:
                            self.assertEqual(b"", stdout)
                            self.assertEqual("FILE_ACCESS", json.loads(stderr)["code"])
                        self.assertNotIn(b"Synthetic writer", stdout + stderr)
                    files, _ = workflow.load_bundle(target, "submission")
                    self.assertIn(files["submission.json"], [path.read_bytes() for path in inputs])
                    winner = next(
                        path for path in inputs if path.read_bytes() == files["submission.json"]
                    )
                    before = {
                        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in target.iterdir()
                    }
                    self.assertEqual(
                        "unchanged", workflow.retain(self.pack_path, winner, target)["status"]
                    )
                    self.assertEqual(
                        before,
                        {
                            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                            for path in target.iterdir()
                        },
                    )
                finally:
                    for process in children:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=20)

    def test_process_death_leaves_incomplete_or_fully_verifiable_artifacts(self) -> None:
        child = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import development_annotation as workflow
phase = sys.argv[3]
actual_open, actual_fdopen = os.open, os.fdopen
actual_fsync, actual_link, actual_unlink = os.fsync, os.link, os.unlink
names = {}
def tracked_open(path, *args, **kwargs):
    fd = actual_open(path, *args, **kwargs)
    names[fd] = str(path)
    if str(path) == 'a.json' and phase == 'payload-created':
        os._exit(73)
    if str(path) == '.receipt.pending' and phase == 'receipt-created':
        os._exit(73)
    return fd
def fdopen(fd, *args, **kwargs):
    stream = actual_fdopen(fd, *args, **kwargs)
    if names.get(fd) != '.receipt.pending' or phase != 'receipt-partial':
        return stream
    class Partial:
        def __enter__(self): return self
        def __exit__(self, *args): stream.close()
        def write(self, data):
            stream.write(data[:len(data)//2])
            stream.flush()
            os._exit(73)
    return Partial()
def fsync(fd):
    if names.get(fd) == '.receipt.pending' and phase == 'receipt-before-fsync':
        os._exit(73)
    return actual_fsync(fd)
def link(*args, **kwargs):
    actual_link(*args, **kwargs)
    if phase == 'receipt-linked': os._exit(73)
def unlink(*args, **kwargs):
    actual_unlink(*args, **kwargs)
    if phase == 'receipt-published': os._exit(73)
os.open, os.fdopen, os.fsync, os.link, os.unlink = tracked_open, fdopen, fsync, link, unlink
workflow.write_bundle(Path(sys.argv[2]), 'evidence', {'a.json': b'{}\\n'})
"""
        for phase in (
            "payload-created",
            "receipt-created",
            "receipt-partial",
            "receipt-before-fsync",
            "receipt-linked",
            "receipt-published",
        ):
            with self.subTest(phase=phase):
                target = self.directory / phase
                result = subprocess.run(
                    [sys.executable, "-c", child, str(ROOT / "tools"), str(target), phase],
                    capture_output=True,
                    timeout=20,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertEqual(73, result.returncode, result.stderr)
                before = {path.name: path.read_bytes() for path in target.iterdir()}
                if phase == "receipt-published":
                    self.assertEqual(
                        {"a.json": b"{}\n"}, workflow.load_bundle(target, "evidence")[0]
                    )
                    self.assertEqual(
                        "unchanged",
                        workflow.write_bundle(target, "evidence", {"a.json": b"{}\n"})["status"],
                    )
                elif phase == "receipt-linked":
                    self.assert_error("PRIVATE_FILE", workflow.load_bundle, target, "evidence")
                    self.assert_error(
                        "PRIVATE_FILE",
                        workflow.write_bundle,
                        target,
                        "evidence",
                        {"a.json": b"{}\n"},
                    )
                else:
                    with self.assertRaises(FileNotFoundError):
                        workflow.load_bundle(target, "evidence")
                    with self.assertRaises(FileNotFoundError):
                        workflow.write_bundle(target, "evidence", {"a.json": b"{}\n"})
                self.assertEqual(
                    before, {path.name: path.read_bytes() for path in target.iterdir()}
                )

    def test_each_persistence_barrier_preserves_a_safe_retry_state(self) -> None:
        actual_fsync = os.fsync
        for failure in range(1, 7):
            with self.subTest(failure=failure):
                target = self.directory / f"barrier-{failure}"
                calls = 0

                def interrupted(descriptor: int) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == failure:
                        raise OSError("Synthetic persistence interruption.")
                    actual_fsync(descriptor)

                with patch.object(workflow.os, "fsync", side_effect=interrupted):
                    with self.assertRaises(OSError):
                        workflow.write_bundle(target, "evidence", {"a.json": b"{}\n"})
                before = {
                    path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in target.iterdir()
                }
                if failure <= 4:
                    self.assertFalse((target / "receipt.json").exists())
                    with self.assertRaises(FileNotFoundError):
                        workflow.load_bundle(target, "evidence")
                    with self.assertRaises(FileNotFoundError):
                        workflow.write_bundle(target, "evidence", {"a.json": b"{}\n"})
                else:
                    self.assertEqual(
                        {"a.json": b"{}\n"}, workflow.load_bundle(target, "evidence")[0]
                    )
                    self.assertEqual(
                        "unchanged",
                        workflow.write_bundle(target, "evidence", {"a.json": b"{}\n"})["status"],
                    )
                self.assertEqual(
                    before,
                    {
                        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in target.iterdir()
                    },
                )

    def test_receipt_publication_never_replaces_an_existing_file(self) -> None:
        target = self.directory / "receipt-collision"
        actual_link = os.link

        def collision(*args, **kwargs) -> None:
            descriptor = os.open(
                "receipt.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"Synthetic earlier receipt evidence.")
            actual_link(*args, **kwargs)

        with patch.object(workflow.os, "link", side_effect=collision):
            with self.assertRaises(FileExistsError):
                workflow.write_bundle(target, "evidence", {"a.json": b"{}\n"})
        before = {path.name: path.read_bytes() for path in target.iterdir()}
        self.assertEqual(b"Synthetic earlier receipt evidence.", before["receipt.json"])
        self.assert_error("BAD_JSON", workflow.load_bundle, target, "evidence")
        self.assert_error(
            "BAD_JSON", workflow.write_bundle, target, "evidence", {"a.json": b"{}\n"}
        )
        self.assertEqual(before, {path.name: path.read_bytes() for path in target.iterdir()})

    def test_retention_revalidates_changes_after_read_only_validation(self) -> None:
        validated = workflow.validate(self.pack_path, self.submission_path)
        self.submission["annotations"][0]["evidence"]["revision_id"] += 1
        self.write_submission()
        self.assert_error(
            "EVIDENCE_PIN", workflow.retain, self.pack_path, self.submission_path, self.retained
        )
        self.assertFalse(self.retained.exists())
        self.submission["annotations"][0]["evidence"]["revision_id"] -= 1
        self.submission["provenance"]["notes"] = "Synthetic valid change after validation."
        self.write_submission()
        retained = workflow.retain(self.pack_path, self.submission_path, self.retained)
        self.assertNotEqual(validated["submission_sha256"], retained["submission_sha256"])
        self.assertEqual(
            workflow.sha(self.submission_path.read_bytes()), retained["submission_sha256"]
        )
        self.assertEqual(
            self.submission_path.read_bytes(), (self.retained / "submission.json").read_bytes()
        )

    def test_correction_refuses_substituted_or_rebound_predecessors(self) -> None:
        original_hash = self.retain()
        original = {path.name: path.read_bytes() for path in self.retained.iterdir()}
        substitute = copy.deepcopy(self.submission)
        substitute["submission_id"] = "different-synthetic-submission"
        substitute_path = self.directory / "substitute.json"
        substitute_path.write_bytes(workflow.encoded(substitute))
        substitute_bundle = self.directory / "substitute-bundle"
        substitute_hash = workflow.retain(self.pack_path, substitute_path, substitute_bundle)[
            "submission_sha256"
        ]
        self.submission.update({"version": 2, "previous_submission_sha256": substitute_hash})
        self.write_submission()
        output = self.directory / "refused-correction"
        self.assert_error(
            "PREDECESSOR",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            output,
            substitute_bundle,
            substitute_hash,
        )
        self.submission["previous_submission_sha256"] = original_hash
        self.write_submission()
        with self.assertRaises(FileNotFoundError):
            workflow.retain(
                self.pack_path,
                self.submission_path,
                output,
                self.directory / "missing-predecessor",
                original_hash,
            )
        tampered = self.directory / "altered-predecessor"
        shutil.copytree(self.retained, tampered)
        self.rewrite_bundle_file(
            tampered, "submission.json", (tampered / "submission.json").read_bytes() + b" "
        )
        self.assert_error(
            "SUBMISSION_CHANGED",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            output,
            tampered,
            original_hash,
        )
        altered = self.directory / "rebound-predecessor"
        shutil.copytree(self.retained, altered)
        binding = json.loads((altered / "binding.json").read_bytes())
        binding["pack_sha256"] = "f" * 64
        self.rewrite_bundle_file(altered, "binding.json", workflow.encoded(binding))
        self.submission["previous_submission_sha256"] = original_hash
        self.write_submission()
        self.assert_error(
            "SUBMISSION_PACK",
            workflow.retain,
            self.pack_path,
            self.submission_path,
            output,
            altered,
            original_hash,
        )
        self.assertFalse(output.exists())
        self.assertEqual(
            original, {path.name: path.read_bytes() for path in self.retained.iterdir()}
        )

    def test_rehashed_modified_pack_sources_forms_and_metadata_are_refused(self) -> None:
        for case, code in (
            ("source", "SOURCE_PIN"),
            ("form", "PACK_CHANGED"),
            ("metadata", "PACK_CHANGED"),
        ):
            with self.subTest(case=case):
                target = self.directory / f"modified-pack-{case}"
                shutil.copytree(self.pack_path, target)
                if case == "source":
                    name, data = "source-10.txt", b"Synthetic replaced source."
                elif case == "form":
                    name, data = "annotation-form.json", b"{}\n"
                else:
                    pack = copy.deepcopy(self.pack)
                    pack["pages"][1]["revision_id"] += 1
                    name, data = "pack.json", workflow.encoded(pack)
                self.rewrite_bundle_file(target, name, data)
                self.assert_error(code, workflow.validate, target, self.submission_path)

    def test_rehashed_candidate_missing_or_extra_sources_cannot_create_output(self) -> None:
        submission_hash = self.retain()
        original = copy.deepcopy(self.candidates)
        for extra in (False, True):
            with self.subTest(extra=extra):
                self.candidates = copy.deepcopy(original)
                if extra:
                    page = self.pin(self.pages[0])
                    page.update(
                        {"page_id": 99, "revision_id": 990, "title": "Synthetic extra source"}
                    )
                    self.candidates["metadata"]["source_pages"].append(page)
                else:
                    self.candidates["metadata"]["source_pages"] = self.candidates["metadata"][
                        "source_pages"
                    ][1:]
                self.write_candidates()
                output = self.directory / f"mismatched-{extra}"
                self.assert_error(
                    "CANDIDATE_SOURCE",
                    workflow.compare,
                    self.pack_path,
                    self.retained,
                    submission_hash,
                    self.candidate_path,
                    workflow.sha(self.candidate_path.read_bytes()),
                    output,
                )
                self.assertFalse(output.exists())

    def test_large_file_and_many_schema_errors_have_bounded_source_free_output(self) -> None:
        large = self.directory / "large-private-sentinel.json"
        with large.open("wb") as stream:
            stream.truncate(workflow.MAX_BYTES + 1)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = workflow.main(
                ["validate", "--pack", str(self.pack_path), "--submission", str(large)]
            )
        self.assertEqual(2, result)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("TOO_LARGE", json.loads(stderr.getvalue())["code"])
        self.assertNotIn("large-private-sentinel", stderr.getvalue())
        self.submission["annotations"] = [
            copy.deepcopy(self.submission["annotations"][0]) for _ in range(30)
        ]
        for index, annotation in enumerate(self.submission["annotations"]):
            annotation["annotation_id"] = f"A{index}"
            annotation["subject"]["type"] = "PRIVATE-VALUE-SENTINEL"
        self.write_submission()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = workflow.main(
                [
                    "validate",
                    "--pack",
                    str(self.pack_path),
                    "--submission",
                    str(self.submission_path),
                ]
            )
        self.assertEqual(2, result)
        message = stderr.getvalue()
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("SCHEMA", json.loads(message)["code"])
        self.assertEqual(20, len(json.loads(message)["issues"]))
        self.assertTrue(json.loads(message)["truncated"])
        self.assertLess(len(message.encode()), 8192)
        self.assertNotIn("PRIVATE-VALUE-SENTINEL", message)
        self.assertNotIn("6-8", message)

    def test_duplicate_keys_nonfinite_and_deep_json_are_rejected(self) -> None:
        for data in (
            b'{"secret":"one","secret":"two"}',
            b'{"x": NaN}',
            b'{"x": 1e999}',
            b'"\\ud800"',
            b"[" * 40 + b"0" + b"]" * 40,
        ):
            with self.subTest(data=data[:20]):
                self.assert_error("BAD_JSON", workflow.decode, data)

    def test_scientific_approval_fields_and_malformed_values_are_not_accepted(self) -> None:
        original = copy.deepcopy(self.submission)
        for change in (
            lambda doc: doc.update({"human_approved_frozen": True}),
            lambda doc: doc.update({"split": "heldout"}),
            lambda doc: doc["annotations"][0].update({"value_literal": 7}),
            lambda doc: doc["annotations"][0]["evidence"].update({"source_offset_start": True}),
            lambda doc: doc["provenance"].update({"submitted_at": "not a date"}),
        ):
            self.submission = copy.deepcopy(original)
            change(self.submission)
            self.write_submission()
            self.assert_error("SCHEMA", workflow.validate, self.pack_path, self.submission_path)

    def test_every_page_requires_one_review_and_annotations_cannot_use_unread_pages(self) -> None:
        reviews = copy.deepcopy(self.submission["page_reviews"])
        self.submission["page_reviews"] = reviews[:1]
        self.write_submission()
        self.assert_error("PAGE_REVIEWS", workflow.validate, self.pack_path, self.submission_path)
        self.submission["page_reviews"] = reviews + [reviews[0]]
        self.write_submission()
        self.assert_error("PAGE_REVIEWS", workflow.validate, self.pack_path, self.submission_path)
        self.submission["page_reviews"] = reviews
        self.submission["page_reviews"][1]["status"] = "not_started"
        self.write_submission()
        self.assert_error("PAGE_REVIEWS", workflow.validate, self.pack_path, self.submission_path)

    def test_multisource_machine_claim_is_accounted_once_and_kept_unresolved(self) -> None:
        first = self.pages[0]
        self.candidates["claims"][0]["evidence"].append(
            {
                "evidence_id": digest("multisource"),
                "page_id": first["page_id"],
                "revision_id": first["revision_id"],
                "content_hash": first["content_hash"],
                "page_title": first["title"],
                "extraction_method": "rules",
                "extractor_version": "2.3.1",
                "location_type": "page_title",
                "location": first["title"],
            }
        )
        self.write_candidates()
        result, report = self.compare()
        self.assertEqual(
            {"unmatched_annotations": 1, "unresolved_candidate_sources": 1},
            result["groups_by_status"],
        )
        self.assertEqual(1, sum(len(group["candidate_ids"]) for group in report["groups"]))

    def test_page_level_candidate_evidence_is_visible_without_semantic_support(self) -> None:
        item = self.candidates["claims"][0]
        evidence = item["evidence"][0]
        for key in ("source_offset_start", "source_offset_end", "supporting_span", "span_hash"):
            del evidence[key]
        evidence["location_type"] = "page"
        item["traceability_complete"] = False
        self.candidates["summary"]["traceability_complete_count"] = 0
        self.write_candidates()
        _, report = self.compare()
        self.assertEqual(
            ["page"], report["groups"][0]["comparison"]["evidence"]["candidate_locations"]
        )
        self.assertFalse(report["groups"][0]["comparison"]["evidence"]["exact_span"])

    def test_comparison_retry_and_input_permutation_do_not_change_grouping(self) -> None:
        result, report = self.compare()
        before = {
            file.name: file.read_bytes() for file in (self.directory / "comparison").iterdir()
        }
        again, _ = self.compare()
        self.assertEqual("unchanged", again["status"])
        self.assertEqual(
            before,
            {file.name: file.read_bytes() for file in (self.directory / "comparison").iterdir()},
        )
        self.assertEqual(report["groups"], workflow.discrepancies(self.submission, self.candidates))
        shuffled = copy.deepcopy(self.candidates)
        shuffled["claims"].reverse()
        self.assertEqual(report["groups"], workflow.discrepancies(self.submission, shuffled))
        self.assertEqual("created", result["status"])

    def test_file_and_bundle_size_limits_fail_before_writing(self) -> None:
        with patch.object(workflow, "MAX_BYTES", 4):
            self.assert_error("TOO_LARGE", workflow.read_bytes, self.raw_path)
            self.assert_error(
                "BUNDLE_SIZE",
                workflow.write_bundle,
                self.directory / "large",
                "evidence",
                {"a.json": b"12345"},
            )
        with patch.object(workflow, "MAX_BUNDLE_BYTES", 4):
            self.assert_error(
                "BUNDLE_SIZE",
                workflow.write_bundle,
                self.directory / "large",
                "evidence",
                {"a.json": b"123", "b.json": b"456"},
            )
        self.assertFalse((self.directory / "large").exists())

    def test_non_posix_private_storage_is_refused(self) -> None:
        with patch.object(workflow.os, "name", "nt"):
            self.assert_error("PLATFORM", workflow.read_bytes, self.raw_path)

    def test_timestamp_validation_does_not_depend_on_optional_format_extras(self) -> None:
        for timestamp in ("2026-10-10T08:00:00Z", "2026-10-10t08:00:00.123+07:00"):
            self.assertTrue(workflow.valid_timestamp(timestamp))
        for timestamp in (
            "not a date",
            "2026-02-30T08:00:00Z",
            "2026-10-10T08:00:00",
            "2026-10-10T08:00:00+01:99",
            "2026-10-10T23:59:60Z",
            None,
        ):
            with self.subTest(timestamp=timestamp):
                self.assertFalse(workflow.valid_timestamp(timestamp))
        self.submission["provenance"]["submitted_at"] = "not a date"
        self.write_submission()
        self.assert_error("SCHEMA", workflow.validate, self.pack_path, self.submission_path)

    def test_schema_errors_do_not_echo_private_values(self) -> None:
        self.submission["provenance"]["exposure"] = "PRIVATE-SENTINEL"
        self.write_submission()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = workflow.main(
                [
                    "validate",
                    "--pack",
                    str(self.pack_path),
                    "--submission",
                    str(self.submission_path),
                ]
            )
        self.assertEqual(2, result)
        self.assertEqual("", stdout.getvalue())
        self.assertNotIn("PRIVATE-SENTINEL", stderr.getvalue())
        self.assertNotIn("6-8", stderr.getvalue())
        self.assertEqual("SCHEMA", json.loads(stderr.getvalue())["code"])

    def test_custom_qualifier_names_are_not_echoed_in_schema_errors(self) -> None:
        self.submission["annotations"][0]["qualifiers"]["private_source_sentinel"] = {
            "state": "bad",
            "literal": None,
        }
        self.write_submission()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = workflow.main(
                [
                    "validate",
                    "--pack",
                    str(self.pack_path),
                    "--submission",
                    str(self.submission_path),
                ]
            )
        self.assertEqual(2, code)
        self.assertNotIn("private_source_sentinel", stderr.getvalue())
        self.assertIn("<field>", stderr.getvalue())

    def test_cli_flags_help_and_source_free_success_summary(self) -> None:
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        script = ROOT / "tools" / "development_annotation.py"
        for flags in (["--help"], ["prepare", "--help"], ["compare", "--help"]):
            result = subprocess.run(
                [sys.executable, str(script), *flags],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("--help", result.stdout)
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "validate",
                "--pack",
                str(self.pack_path),
                "--submission",
                str(self.submission_path),
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("valid_format_and_coordinates", json.loads(result.stdout)["status"])
        self.assertNotIn("synthetic-fixture", result.stdout)
        self.assertNotIn("6-8", result.stdout)
        for flags in (
            [],
            ["heldout"],
            ["prepare", "--confirm-heldout-approved"],
            ["validate", "--unknown"],
            ["validate", "--pac", str(self.pack_path), "--submission", str(self.submission_path)],
        ):
            result = subprocess.run(
                [sys.executable, str(script), *flags],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertEqual("ARGUMENTS", json.loads(result.stderr)["code"])


if __name__ == "__main__":
    unittest.main()
