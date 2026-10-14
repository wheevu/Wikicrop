"""Tests for the offline reviewed-projection manifest and Neo4j boundary."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any


TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "reviewed_projection.py"
SPEC = importlib.util.spec_from_file_location("reviewed_projection", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
reviewed_projection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewed_projection)
ProjectionError = reviewed_projection.ProjectionError
SCHEMA_ORACLE = Path(__file__).with_name("validate_reviewed_projection_schema.py")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _evidence(index: int, span: str = "good yield") -> dict[str, Any]:
    start = index * 20
    return {
        "evidence_id": _hash(f"evidence:{index}"),
        "page_id": 100 + index,
        "revision_id": 200 + index,
        "content_hash": _hash(f"content:{index}"),
        "page_title": f"Synthetic page {index}",
        "extraction_method": "rules",
        "extractor_version": "3.0.0",
        "location_type": "source_span",
        "source_offset_start": start,
        "source_offset_end": start + len(span),
        "supporting_span": span,
        "span_hash": _hash(span),
    }


def _property_claim(index: int, *, review_version: int = 1, value: str | None = None) -> dict[str, Any]:
    return {
        "snapshot_id": f"{index:064x}",
        "snapshot_hash": _hash(f"snapshot:{index}"),
        "claim_id": _hash(f"claim:{index}"),
        "review_version": review_version,
        "claim_type": "property",
        "subject": {"type": "Crop", "id": "crop:rice"},
        "predicate": "yield",
        "value": value if value is not None else f"{index} tonnes/ha",
        "qualifiers": {
            "season": {"name": "winter", "ranges": [Decimal("1.25")]},
            "verified": True,
        },
        "evidence": [_evidence(index)],
    }


def _relationship_claim(index: int) -> dict[str, Any]:
    return {
        "snapshot_id": f"{index:064x}",
        "snapshot_hash": _hash(f"snapshot:{index}"),
        "claim_id": _hash(f"claim:{index}"),
        "review_version": 1,
        "claim_type": "relationship",
        "subject": {"type": "Crop", "id": "crop:rice"},
        "predicate": "affected_by",
        "object": {"type": "Pest", "id": "pest:stem-borer"},
        "qualifiers": {"region": ["north", "central"]},
        "evidence": [_evidence(index)],
    }


def _document(claims: list[dict[str, Any]], snapshot_count: int | None = None) -> dict[str, Any]:
    return {
        "schema_version": "wikikg.reviewed-projection.v1",
        "snapshot_count": len(claims) if snapshot_count is None else snapshot_count,
        "source_watermark": _hash("watermark"),
        "claims": claims,
    }


class FakeResult:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []

    def single(self, strict: bool = False) -> dict[str, Any] | None:
        if strict and len(self.records) != 1:
            raise AssertionError(f"expected one record, got {len(self.records)}")
        return self.records[0] if self.records else None

    def __iter__(self):
        return iter(self.records)

    def consume(self) -> None:
        return None


class FakeTransaction:
    """Small query adapter with copy-on-write transaction rollback semantics."""

    def __init__(self, state: dict[str, Any], owner: "FakeDriver", read_only: bool = False) -> None:
        self.state = state
        self.owner = owner
        self.read_only = read_only

    def run(self, query: str, **parameters: Any) -> FakeResult:
        self.owner.queries.append((query, copy.deepcopy(parameters)))
        if query == reviewed_projection.CREATE_GENERATION_QUERY:
            generation = parameters["generation"]
            record = self.state["generations"].get(generation)
            if record is None:
                record = {
                    "state": "building",
                    "schema_version": parameters["schema_version"],
                    "snapshot_count": parameters["snapshot_count"],
                    "source_watermark": parameters["source_watermark"],
                    "claim_count": parameters["claim_count"],
                    "evidence_count": parameters["evidence_count"],
                    "build_epoch": self.state["active_epoch"],
                }
                self.state["generations"][generation] = record
            return FakeResult(
                [
                    {
                        **record,
                        "active_generation": self.state["active_generation"],
                        "active_epoch": self.state["active_epoch"],
                    }
                ]
            )

        if query in (
            reviewed_projection.UPSERT_PROPERTY_BATCH_QUERY,
            reviewed_projection.UPSERT_RELATIONSHIP_BATCH_QUERY,
        ):
            if self.read_only:
                raise AssertionError("write query used in execute_read")
            self.owner.batch_attempts += 1
            for row in parameters["rows"]:
                key = (parameters["generation"], row["snapshot_id"])
                claim = {
                    **row["claim_properties"],
                    "generation": parameters["generation"],
                    "snapshot_id": row["snapshot_id"],
                }
                self.state["claims"][key] = claim
                self.state["subject_links"][key] = [
                    (row["subject_type"], row["subject_id"])
                ]
                if "object_type" in row:
                    self.state["object_links"][key] = [
                        (row["object_type"], row["object_id"])
                    ]
                else:
                    self.state["object_links"].pop(key, None)
                link_targets: list[tuple[str, str]] = []
                for evidence_row in row["evidence"]:
                    evidence_id = evidence_row["evidence_id"]
                    evidence_key = (parameters["generation"], row["snapshot_id"], evidence_id)
                    self.state["evidence"][evidence_key] = copy.deepcopy(
                        evidence_row["properties"]
                    )
                    link_targets.append((row["snapshot_id"], evidence_id))
                self.state["evidence_links"][key] = link_targets
            if self.owner.fail_batch_attempt == self.owner.batch_attempts:
                raise RuntimeError("injected batch failure")
            return FakeResult(
                [
                    {
                        "claim_count": len(parameters["rows"]),
                        "evidence_links": sum(
                            len(row["evidence"]) for row in parameters["rows"]
                        ),
                    }
                ]
            )

        if query == reviewed_projection.VERIFY_GENERATION_QUERY:
            generation = parameters["generation"]
            metadata = self.state["generations"].get(generation)
            if metadata is None:
                return FakeResult()
            claim_nodes = sum(key[0] == generation for key in self.state["claims"])
            evidence_nodes = sum(key[0] == generation for key in self.state["evidence"])
            return FakeResult(
                [
                    {
                        "state": metadata["state"],
                        "schema_version": metadata["schema_version"],
                        "snapshot_count": metadata["snapshot_count"],
                        "source_watermark": metadata["source_watermark"],
                        "expected_claim_count": metadata["claim_count"],
                        "expected_evidence_count": metadata["evidence_count"],
                        "claim_nodes": claim_nodes,
                        "evidence_nodes": evidence_nodes,
                    }
                ]
            )

        if query == reviewed_projection.VERIFY_CLAIMS_QUERY:
            generation = parameters["generation"]
            records = []
            for key, claim in self.state["claims"].items():
                if key[0] != generation:
                    continue
                subjects = self.state["subject_links"].get(key, [])
                objects = self.state["object_links"].get(key, [])
                link_targets = self.state["evidence_links"].get(key, [])
                records.append(
                    {
                        "snapshot_id": key[1],
                        "claim_id": claim["claim_id"],
                        "snapshot_hash": claim.get("snapshot_hash"),
                        "review_version": claim.get("review_version"),
                        "claim_type": claim.get("claim_type"),
                        "predicate": claim.get("predicate"),
                        "qualifiers_json": claim.get("qualifiers_json"),
                        "value": claim.get("value"),
                        "claim_json": claim["claim_json"],
                        "subject_links": len(subjects),
                        "subject_types": [item[0] for item in subjects],
                        "subject_ids": [item[1] for item in subjects],
                        "object_links": len(objects),
                        "object_types": [item[0] for item in objects],
                        "object_ids": [item[1] for item in objects],
                        "evidence_links": len(link_targets),
                        "evidence_ids": [evidence_id for _, evidence_id in link_targets],
                        "evidence_snapshot_ids": [
                            snapshot_id for snapshot_id, _ in link_targets
                        ],
                    }
                )
            if not records:
                records.append(
                    {
                        "snapshot_id": None,
                        "claim_id": None,
                        "snapshot_hash": None,
                        "review_version": None,
                        "claim_type": None,
                        "predicate": None,
                        "qualifiers_json": None,
                        "value": None,
                        "claim_json": None,
                        "subject_links": 0,
                        "subject_types": [],
                        "subject_ids": [],
                        "object_links": 0,
                        "object_types": [],
                        "object_ids": [],
                        "evidence_links": 0,
                        "evidence_ids": [],
                        "evidence_snapshot_ids": [],
                    }
                )
            return FakeResult(records)

        if query == reviewed_projection.VERIFY_ENTITIES_QUERY:
            generation = parameters["generation"]
            entities = {
                entity
                for key, links in self.state["subject_links"].items()
                if key[0] == generation
                for entity in links
            }
            entities.update(
                entity
                for key, links in self.state["object_links"].items()
                if key[0] == generation
                for entity in links
            )
            entities.update(self.state["entities"].get(generation, set()))
            return FakeResult([
                {"entity_type": kind, "entity_id": identifier}
                for kind, identifier in sorted(entities)
            ])

        if query == reviewed_projection.VERIFY_EVIDENCE_QUERY:
            generation = parameters["generation"]
            return FakeResult(
                [
                    {
                        "snapshot_id": key[1],
                        "evidence_id": key[2],
                        "evidence_json": value["evidence_json"],
                        **{
                            field: value.get(field)
                            for field in (
                                "page_id",
                                "revision_id",
                                "content_hash",
                                "page_title",
                                "extraction_method",
                                "extractor_version",
                                "location_type",
                                "location",
                                "source_offset_start",
                                "source_offset_end",
                                "span_hash",
                            )
                        },
                    }
                    for key, value in self.state["evidence"].items()
                    if key[0] == generation
                ]
            )

        if query == reviewed_projection.SWITCH_ACTIVE_QUERY:
            if self.read_only:
                raise AssertionError("write query used in execute_read")
            generation = parameters["generation"]
            if self.owner.advance_epoch_before_switch:
                self.owner.advance_epoch_before_switch = False
                self.state["active_epoch"] += 1
            if self.state["active_epoch"] != parameters["expected_epoch"]:
                return FakeResult()
            self.state["generations"][generation]["state"] = "ready"
            self.state["active_generation"] = generation
            self.state["active_epoch"] = parameters["expected_epoch"] + 1
            if self.owner.fail_after_switch_write:
                self.owner.fail_after_switch_write = False
                raise RuntimeError("injected active-pointer failure")
            return FakeResult(
                [
                    {
                        "active_generation": generation,
                        "active_epoch": self.state["active_epoch"],
                    }
                ]
            )

        if query == reviewed_projection.CONFIRM_ACTIVE_QUERY:
            if self.read_only:
                raise AssertionError("write query used in execute_read")
            generation = parameters["generation"]
            if self.owner.advance_before_confirm is not None:
                self.state["active_generation"] = self.owner.advance_before_confirm
                self.owner.advance_before_confirm = None
            record = self.state["generations"].get(generation)
            if (
                record is None
                or record.get("state") != "ready"
                or self.state["active_generation"] != generation
            ):
                return FakeResult()
            return FakeResult([{"active_generation": generation}])

        if query == reviewed_projection.LOOKUP_QUERY:
            generation = self.state["active_generation"]
            if generation is None:
                return FakeResult()
            matching = []
            for key, claim in self.state["claims"].items():
                if key[0] != generation:
                    continue
                if (
                    claim["claim_type"] != parameters["claim_type"]
                    or claim["predicate"] != parameters["predicate"]
                    or self.state["subject_links"].get(key)
                    != [(parameters["subject_type"], parameters["subject_id"])]
                ):
                    continue
                link_targets = self.state["evidence_links"].get(key, [])
                evidence_ids = sorted(
                    evidence_id
                    for snapshot_id, evidence_id in link_targets
                    if snapshot_id == key[1]
                )
                evidence = []
                for evidence_id in evidence_ids:
                    evidence_props = self.state["evidence"][
                        (generation, key[1], evidence_id)
                    ]
                    evidence.append(
                        {
                            field: evidence_props.get(field)
                            for field in (
                                "evidence_id",
                                "page_id",
                                "revision_id",
                                "content_hash",
                                "page_title",
                                "location_type",
                                "location",
                                "source_offset_start",
                                "source_offset_end",
                                "span_hash",
                            )
                        }
                    )
                matching.append(
                    {
                        "claim_json": claim["claim_json"],
                        "evidence_count": len(evidence),
                        "evidence": evidence[: parameters["evidence_limit"]],
                    }
                )
            matching.sort(
                key=lambda record: json.loads(record["claim_json"])["snapshot_id"]
            )
            return FakeResult(matching[: parameters["take"]])

        raise AssertionError("unexpected Cypher statement")


class FakeSession:
    def __init__(self, driver: "FakeDriver", read_only: bool = False) -> None:
        self.driver = driver
        self.read_only = read_only

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def run(self, query: str) -> FakeResult:
        if query not in reviewed_projection.CONSTRAINTS:
            raise AssertionError("unexpected schema statement")
        self.driver.queries.append((query, {}))
        return FakeResult()

    def execute_write(self, callback: Any, *args: Any) -> Any:
        working_state = copy.deepcopy(self.driver.state)
        transaction = FakeTransaction(working_state, self.driver)
        result = callback(transaction, *args)
        self.driver.state = working_state
        return result

    def execute_read(self, callback: Any, *args: Any) -> Any:
        transaction = FakeTransaction(self.driver.state, self.driver, read_only=True)
        return callback(transaction, *args)


class FakeDriver:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "generations": {},
            "claims": {},
            "entities": {},
            "evidence": {},
            "subject_links": {},
            "object_links": {},
            "evidence_links": {},
            "active_generation": None,
            "active_epoch": 0,
        }
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.batch_attempts = 0
        self.fail_batch_attempt: int | None = None
        self.fail_after_switch_write = False
        self.advance_epoch_before_switch = False
        self.advance_before_confirm: str | None = None
        self.closed = False

    def session(self) -> FakeSession:
        return FakeSession(self)

    def close(self) -> None:
        self.closed = True


class ReviewedProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = FakeDriver()

    def write_manifest(self, directory: str, document: dict[str, Any]) -> Path:
        path = Path(directory) / "manifest.json"
        path.write_text(reviewed_projection.canonical_json(document), encoding="utf-8")
        path.chmod(0o600)
        return path

    def assert_schema_oracle(self, document: dict[str, Any], valid: bool) -> None:
        result = subprocess.run(
            [sys.executable, str(SCHEMA_ORACLE)],
            input=reviewed_projection.canonical_json(document),
            text=True,
            capture_output=True,
            check=False,
        )
        expected = 0 if valid else 1
        self.assertEqual(result.returncode, expected, result.stderr)

    def test_schema_oracle_reports_malformed_input_and_missing_dependency_as_errors(self) -> None:
        malformed = subprocess.run(
            [sys.executable, str(SCHEMA_ORACLE)],
            input="{not-json",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(malformed.returncode, 2, malformed.stderr)
        self.assertIn("configuration/input error", malformed.stderr)

        without_site_packages = subprocess.run(
            [sys.executable, "-S", str(SCHEMA_ORACLE)],
            input="{}",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(without_site_packages.returncode, 2, without_site_packages.stderr)
        self.assertIn("jsonschema is required", without_site_packages.stderr)

    def test_draft_2020_12_schema_oracle_accepts_all_evidence_and_claim_shapes(self) -> None:
        for location_type in ("source_span", "page_title", "page"):
            evidence = _evidence(1)
            if location_type != "source_span":
                for field in ("source_offset_start", "source_offset_end", "supporting_span", "span_hash"):
                    evidence.pop(field)
            evidence["location_type"] = location_type
            if location_type == "page_title":
                evidence["location"] = evidence["page_title"]
            with self.subTest(location_type=location_type):
                for claim in (_property_claim(1), _relationship_claim(1)):
                    candidate = _document([claim])
                    candidate["claims"][0]["evidence"] = [evidence]
                    self.assert_schema_oracle(candidate, True)

    def test_draft_2020_12_schema_oracle_rejects_const_and_conditional_violations(self) -> None:
        valid = _document([_property_claim(1)])
        cases: list[tuple[str, dict[str, Any]]] = []
        wrong_version = copy.deepcopy(valid)
        wrong_version["schema_version"] = "wikikg.reviewed-projection.v2"
        cases.append(("schema_version const", wrong_version))
        for claim_type, required in (("property", "value"), ("relationship", "object")):
            missing_required = copy.deepcopy(valid)
            missing_required["claims"][0]["claim_type"] = claim_type
            if claim_type == "relationship":
                missing_required["claims"][0].pop("value", None)
            missing_required["claims"][0].pop(required, None)
            cases.append((f"{claim_type} missing {required}", missing_required))
        property_with_object = copy.deepcopy(valid)
        property_with_object["claims"][0]["object"] = {"type": "Pest", "id": "pest"}
        cases.append(("property forbids object", property_with_object))
        relationship_with_value = copy.deepcopy(valid)
        relationship_with_value["claims"][0]["claim_type"] = "relationship"
        relationship_with_value["claims"][0]["object"] = {"type": "Pest", "id": "pest"}
        relationship_with_value["claims"][0]["value"] = "forbidden"
        cases.append(("relationship forbids value", relationship_with_value))

        missing_span = copy.deepcopy(valid)
        missing_span["claims"][0]["evidence"][0].pop("source_offset_start")
        cases.append(("source_span required coordinate", missing_span))
        title_missing_location = copy.deepcopy(valid)
        title_evidence = title_missing_location["claims"][0]["evidence"][0]
        title_evidence["location_type"] = "page_title"
        for field in ("source_offset_start", "source_offset_end", "supporting_span", "span_hash"):
            title_evidence.pop(field)
        cases.append(("page_title missing location", title_missing_location))
        title_with_span = copy.deepcopy(title_missing_location)
        title_with_span["claims"][0]["evidence"][0]["location"] = "Synthetic page 1"
        title_with_span["claims"][0]["evidence"][0]["span_hash"] = _hash("bad")
        cases.append(("page_title forbids span", title_with_span))
        page_with_location = copy.deepcopy(valid)
        page_evidence = page_with_location["claims"][0]["evidence"][0]
        page_evidence["location_type"] = "page"
        page_evidence["location"] = "Synthetic page 1"
        for field in ("source_offset_start", "source_offset_end", "supporting_span", "span_hash"):
            page_evidence.pop(field)
        cases.append(("page forbids location", page_with_location))
        page_with_span = copy.deepcopy(page_with_location)
        page_with_span["claims"][0]["evidence"][0].pop("location")
        page_with_span["claims"][0]["evidence"][0]["span_hash"] = _hash("bad")
        cases.append(("page forbids span", page_with_span))

        for label, document in cases:
            with self.subTest(case=label):
                self.assert_schema_oracle(document, False)

    def test_manifest_requires_exact_schema_and_sorted_unique_snapshots(self) -> None:
        valid = _document([_property_claim(1), _property_claim(2)])
        self.assertIs(
            reviewed_projection.validate_manifest_document(valid), valid
        )

        duplicate = _document(
            [_property_claim(1), {**_property_claim(1), "claim_id": _hash("other")}]
        )
        with self.assertRaisesRegex(ProjectionError, "duplicate snapshot_id"):
            reviewed_projection.validate_manifest_document(duplicate)

        unsorted = _document([_property_claim(2), _property_claim(1)])
        with self.assertRaisesRegex(ProjectionError, "sorted by snapshot_id"):
            reviewed_projection.validate_manifest_document(unsorted)

        extra = _document([_property_claim(1)])
        extra["unexpected"] = True
        with self.assertRaisesRegex(ProjectionError, "schema"):
            reviewed_projection.validate_manifest_document(extra)

    def test_hashes_require_exactly_sixty_four_characters_before_any_database_write(self) -> None:
        valid = _document([_property_claim(1)])
        self.assertIs(reviewed_projection.validate_manifest_document(valid), valid)
        fields = [
            ((), "source_watermark"),
            (("claims", 0), "snapshot_id"),
            (("claims", 0), "snapshot_hash"),
            (("claims", 0), "claim_id"),
            (("claims", 0, "evidence", 0), "evidence_id"),
            (("claims", 0, "evidence", 0), "content_hash"),
            (("claims", 0, "evidence", 0), "span_hash"),
        ]
        for path, field in fields:
            for length in (63, 65):
                document = copy.deepcopy(valid)
                target = document
                for part in path:
                    target = target[part]
                target[field] = target[field][:-1] if length == 63 else target[field] + "\n"
                with self.subTest(field=field, length=length):
                    self.assert_schema_oracle(document, False)
                    with self.assertRaisesRegex(ProjectionError, "schema"):
                        reviewed_projection.load_projection(document, self.driver)
                    self.assertEqual(self.driver.state["generations"], {})

    def test_rejects_malformed_file_duplicate_json_keys_and_bad_span_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            duplicate_key = Path(directory) / "duplicate.json"
            duplicate_key.write_text('{"claims": [], "claims": []}', encoding="utf-8")
            duplicate_key.chmod(0o600)
            with self.assertRaisesRegex(ProjectionError, "duplicate object key"):
                reviewed_projection.read_manifest_file(duplicate_key)

            malformed = Path(directory) / "malformed.json"
            malformed.write_text("{not-json", encoding="utf-8")
            malformed.chmod(0o600)
            with self.assertRaisesRegex(ProjectionError, "valid UTF-8 JSON"):
                reviewed_projection.read_manifest_file(malformed)

        invalid_span = _document([_property_claim(1)])
        invalid_span["claims"][0]["evidence"][0]["source_offset_end"] += 1
        with self.assertRaisesRegex(ProjectionError, "coordinates"):
            reviewed_projection.validate_manifest_document(invalid_span)

    def test_refuses_world_readable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.write_manifest(directory, _document([_property_claim(1)]))
            manifest.chmod(0o644)
            with self.assertRaisesRegex(ProjectionError, "owner-only"):
                reviewed_projection.read_manifest_file(manifest)

    def test_page_only_and_page_title_evidence_have_no_fabricated_spans(self) -> None:
        document = _document([_property_claim(1)])
        evidence = document["claims"][0]["evidence"][0]
        evidence["location_type"] = "page"
        with self.assertRaisesRegex(ProjectionError, "schema"):
            reviewed_projection.validate_manifest_document(document)
        for key in (
            "source_offset_start", "source_offset_end", "supporting_span", "span_hash"
        ):
            evidence.pop(key)
        reviewed_projection.validate_manifest_document(document)
        evidence["location_type"] = "page_title"
        evidence["location"] = "Different page"
        with self.assertRaisesRegex(ProjectionError, "must name the cited page"):
            reviewed_projection.validate_manifest_document(document)

    def test_span_hash_and_decimal_qualifiers_are_preserved(self) -> None:
        document = _document([_property_claim(1)])
        evidence = document["claims"][0]["evidence"][0]
        evidence["span_hash"] = _hash("different")
        with self.assertRaisesRegex(ProjectionError, "hash does not match"):
            reviewed_projection.validate_manifest_document(document)

        claim = _property_claim(2)
        claim["qualifiers"]["precise"] = Decimal("1.2345678901234567890123456789")
        encoded = reviewed_projection.canonical_json(claim["qualifiers"])
        self.assertIn("1.2345678901234567890123456789", encoded)

    def test_generation_changes_when_review_version_changes(self) -> None:
        first = _document([_property_claim(1, review_version=1)])
        revised = _document([_property_claim(1, review_version=2)])
        self.assertNotEqual(
            reviewed_projection.generation_id(first),
            reviewed_projection.generation_id(revised),
        )

    def test_conflicts_and_qualifiers_evidence_remain_separate_and_lossless(self) -> None:
        first = _property_claim(1, value="6 tonnes/ha")
        second = _property_claim(2, value="8 tonnes/ha")
        second["qualifiers"] = {"region": {"north": True}, "range": [Decimal("1.75")]}
        document = _document([first, second])
        result = reviewed_projection.load_projection(document, self.driver)

        self.assertEqual(result["claim_count"], 2)
        self.assertEqual(len(self.driver.state["claims"]), 2)
        stored = self.driver.state["claims"][(result["generation"], second["snapshot_id"])]
        self.assertEqual(json.loads(stored["qualifiers_json"], parse_float=Decimal), second["qualifiers"])
        stored_evidence = self.driver.state["evidence"][(
            result["generation"],
            second["snapshot_id"],
            second["evidence"][0]["evidence_id"],
        )]
        self.assertEqual(
            json.loads(stored_evidence["evidence_json"]),
            second["evidence"][0],
        )

    def test_repeated_load_is_idempotent_and_stale_claims_leave_active_generation(self) -> None:
        complete = _document([_property_claim(1), _property_claim(2)])
        first_result = reviewed_projection.load_projection(complete, self.driver)
        node_counts = (
            len(self.driver.state["claims"]),
            len(self.driver.state["evidence"]),
            len(self.driver.state["generations"]),
        )
        second_result = reviewed_projection.load_projection(complete, self.driver)
        self.assertEqual(first_result["generation"], second_result["generation"])
        self.assertEqual(
            node_counts,
            (
                len(self.driver.state["claims"]),
                len(self.driver.state["evidence"]),
                len(self.driver.state["generations"]),
            ),
        )

        reduced = _document([_property_claim(1, review_version=2)], snapshot_count=2)
        new_result = reviewed_projection.load_projection(reduced, self.driver)
        lookup = reviewed_projection.lookup_projection(
            self.driver,
            claim_type="property",
            subject_type="Crop",
            subject_id="crop:rice",
            predicate="yield",
        )
        self.assertEqual(self.driver.state["active_generation"], new_result["generation"])
        self.assertEqual(len(self.driver.state["generations"]), 2)
        self.assertEqual(len(self.driver.state["claims"]), 3)
        self.assertEqual(len(lookup["results"]), 1)
        self.assertEqual(lookup["results"][0]["review_version"], 2)
        self.assertTrue(lookup["offline_only"])
        with self.assertRaisesRegex(ProjectionError, "retired generation"):
            reviewed_projection.load_projection(complete, self.driver)
        self.assertEqual(self.driver.state["active_generation"], new_result["generation"])

    def test_interrupted_build_resumes_when_no_newer_generation_was_activated(self) -> None:
        document = _document([_property_claim(1)])
        generation = reviewed_projection.generation_id(document)
        self.driver.session().execute_write(
            reviewed_projection._ensure_generation,
            generation,
            reviewed_projection._generation_metadata(document),
        )

        result = reviewed_projection.load_projection(document, self.driver)

        self.assertEqual(result["generation"], generation)
        self.assertEqual(self.driver.state["active_generation"], generation)
        self.assertEqual(self.driver.state["generations"][generation]["state"], "ready")

    def test_superseded_build_cannot_replace_a_newer_activation(self) -> None:
        older = _document([_property_claim(1)])
        older_generation = reviewed_projection.generation_id(older)
        self.driver.session().execute_write(
            reviewed_projection._ensure_generation,
            older_generation,
            reviewed_projection._generation_metadata(older),
        )

        newer = _document([], snapshot_count=1)
        newer["source_watermark"] = _hash("newer empty export")
        newer_result = reviewed_projection.load_projection(newer, self.driver)
        self.assertNotEqual(newer_result["generation"], older_generation)

        with self.assertRaisesRegex(ProjectionError, "superseded by a newer activation"):
            reviewed_projection.load_projection(older, self.driver)

        self.assertEqual(self.driver.state["active_generation"], newer_result["generation"])
        self.assertEqual(
            self.driver.state["generations"][older_generation]["state"],
            "building",
        )
        lookup = reviewed_projection.lookup_projection(
            self.driver,
            claim_type="property",
            subject_type="Crop",
            subject_id="crop:rice",
            predicate="yield",
        )
        self.assertEqual(lookup["results"], [])

    def test_activation_race_blocks_the_switch_without_changing_the_active_generation(self) -> None:
        first = reviewed_projection.load_projection(
            _document([_property_claim(1)]), self.driver
        )
        competing = _document([], snapshot_count=1)
        competing["source_watermark"] = _hash("competing activation")
        competing_generation = reviewed_projection.generation_id(competing)

        self.driver.advance_epoch_before_switch = True
        with self.assertRaisesRegex(ProjectionError, "newer generation was activated"):
            reviewed_projection.load_projection(competing, self.driver)

        self.assertEqual(self.driver.state["active_generation"], first["generation"])
        self.assertEqual(
            self.driver.state["generations"][competing_generation]["state"],
            "building",
        )

    def test_reloading_the_active_export_keeps_partial_builds_alive(self) -> None:
        active_document = _document([_property_claim(1)])
        active = reviewed_projection.load_projection(active_document, self.driver)
        epoch_before_reload = self.driver.state["active_epoch"]

        building_document = _document([_property_claim(2)])
        building_generation = reviewed_projection.generation_id(building_document)
        self.driver.session().execute_write(
            reviewed_projection._ensure_generation,
            building_generation,
            reviewed_projection._generation_metadata(building_document),
        )

        reloaded = reviewed_projection.load_projection(active_document, self.driver)
        self.assertEqual(reloaded["generation"], active["generation"])
        self.assertEqual(self.driver.state["active_epoch"], epoch_before_reload)

        result = reviewed_projection.load_projection(building_document, self.driver)
        self.assertEqual(result["generation"], building_generation)
        self.assertEqual(self.driver.state["active_generation"], building_generation)

    def test_active_pointer_change_during_verification_is_rejected(self) -> None:
        document = _document([_property_claim(1)])
        reviewed_projection.load_projection(document, self.driver)
        epoch_before_confirm = self.driver.state["active_epoch"]
        self.driver.advance_before_confirm = reviewed_projection.generation_id(
            _document([_property_claim(2)])
        )

        with self.assertRaisesRegex(ProjectionError, "active generation changed"):
            reviewed_projection.load_projection(document, self.driver)

        self.assertEqual(self.driver.state["active_epoch"], epoch_before_confirm)

    def test_staged_lookup_fields_are_verified_against_the_manifest(self) -> None:
        document = _document([_property_claim(1)])
        loaded = reviewed_projection.load_projection(document, self.driver)
        claim = document["claims"][0]
        claim_key = (loaded["generation"], claim["snapshot_id"])
        evidence_key = (
            loaded["generation"],
            claim["snapshot_id"],
            claim["evidence"][0]["evidence_id"],
        )

        self.driver.state["claims"][claim_key]["predicate"] = "wrong_predicate"
        with self.assertRaisesRegex(ProjectionError, "lookup fields"):
            reviewed_projection.load_projection(document, self.driver)
        self.driver.state["claims"][claim_key]["predicate"] = claim["predicate"]

        self.driver.state["claims"][claim_key]["value"] = "wrong value"
        with self.assertRaisesRegex(ProjectionError, "lookup fields"):
            reviewed_projection.load_projection(document, self.driver)
        self.driver.state["claims"][claim_key]["value"] = claim["value"]

        self.driver.state["evidence"][evidence_key]["page_title"] = "Wrong title"
        with self.assertRaisesRegex(ProjectionError, "evidence fields"):
            reviewed_projection.load_projection(document, self.driver)

        self.assertEqual(self.driver.state["active_generation"], loaded["generation"])

    def test_evidence_links_must_target_their_claim_snapshot(self) -> None:
        first = _property_claim(1)
        second = _property_claim(2)
        shared_evidence_id = first["evidence"][0]["evidence_id"]
        second["evidence"] = [
            dict(second["evidence"][0], evidence_id=shared_evidence_id)
        ]
        document = _document([first, second])
        loaded = reviewed_projection.load_projection(document, self.driver)

        self.driver.state["evidence_links"][(loaded["generation"], first["snapshot_id"])] = [
            (second["snapshot_id"], shared_evidence_id)
        ]

        with self.assertRaisesRegex(ProjectionError, "targets another snapshot"):
            reviewed_projection.load_projection(document, self.driver)

        self.assertEqual(self.driver.state["active_generation"], loaded["generation"])

    def test_relationship_claim_links_typed_object_and_returns_bounded_coordinates(self) -> None:
        claim = _relationship_claim(3)
        claim["evidence"] = [_evidence(3, "one"), _evidence(4, "two")]
        document = _document([claim])
        reviewed_projection.load_projection(document, self.driver)
        response = reviewed_projection.lookup_projection(
            self.driver,
            claim_type="relationship",
            subject_type="Crop",
            subject_id="crop:rice",
            predicate="affected_by",
        )
        result = response["results"][0]
        self.assertEqual(result["object"], claim["object"])
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(result["evidence"][0]["revision_id"], 203)
        self.assertEqual(result["evidence"][0]["span_hash"], _hash("one"))
        self.assertEqual(result["evidence"][0]["page_title"], "Synthetic page 3")
        self.assertNotIn("supporting_span", result["evidence"][0])
        self.assertTrue(response["offline_only"])

    def test_lookup_bounds_claims_and_evidence(self) -> None:
        first = _property_claim(1)
        first["evidence"] = [_evidence(index) for index in range(51)]
        second = _property_claim(2)
        reviewed_projection.load_projection(_document([first, second]), self.driver)

        response = reviewed_projection.lookup_projection(
            self.driver,
            claim_type="property",
            subject_type="Crop",
            subject_id="crop:rice",
            predicate="yield",
            limit=1,
        )
        self.assertTrue(response["truncated"])
        self.assertEqual(len(response["results"]), 1)
        self.assertTrue(response["results"][0]["evidence_truncated"])
        self.assertEqual(len(response["results"][0]["evidence"]), 50)

    def test_batch_failure_keeps_previous_generation_active(self) -> None:
        reviewed_projection.load_projection(_document([_property_claim(1)]), self.driver)
        previous_generation = self.driver.state["active_generation"]
        many_claims = [_property_claim(index + 10) for index in range(201)]
        self.driver.fail_batch_attempt = self.driver.batch_attempts + 2
        with self.assertRaisesRegex(RuntimeError, "injected batch failure"):
            reviewed_projection.load_projection(_document(many_claims), self.driver)

        self.assertEqual(self.driver.state["active_generation"], previous_generation)
        failed_generations = set(self.driver.state["generations"]) - {previous_generation}
        self.assertEqual(len(failed_generations), 1)
        self.assertEqual(
            self.driver.state["generations"][next(iter(failed_generations))]["state"],
            "building",
        )

    def test_switch_transaction_rolls_back_if_pointer_write_fails(self) -> None:
        reviewed_projection.load_projection(_document([_property_claim(1)]), self.driver)
        previous_generation = self.driver.state["active_generation"]
        self.driver.fail_after_switch_write = True
        with self.assertRaisesRegex(RuntimeError, "active-pointer failure"):
            reviewed_projection.load_projection(
                _document([_property_claim(2)]), self.driver
            )
        self.assertEqual(self.driver.state["active_generation"], previous_generation)
        new_generation = set(self.driver.state["generations"]) - {previous_generation}
        self.assertEqual(len(new_generation), 1)
        self.assertEqual(
            self.driver.state["generations"][next(iter(new_generation))]["state"],
            "building",
        )

    def test_export_with_no_approved_claims_activates_an_empty_generation(self) -> None:
        reviewed_projection.load_projection(_document([_property_claim(1)]), self.driver)

        empty = _document([], snapshot_count=3)
        result = reviewed_projection.load_projection(empty, self.driver)

        self.assertEqual(result["claim_count"], 0)
        self.assertEqual(result["evidence_count"], 0)
        self.assertEqual(self.driver.state["active_generation"], result["generation"])
        response = reviewed_projection.lookup_projection(
            self.driver,
            claim_type="property",
            subject_type="Crop",
            subject_id="crop:rice",
            predicate="yield",
        )
        self.assertEqual(response["results"], [])

    def test_orphan_entity_blocks_generation_activation(self) -> None:
        baseline = reviewed_projection.load_projection(_document([_property_claim(1)]), self.driver)
        document = _document([_property_claim(2)])
        generation = reviewed_projection.generation_id(document)
        self.driver.state["entities"][generation] = {("Crop", "unexpected orphan")}
        with self.assertRaisesRegex(ProjectionError, "orphan nodes"):
            reviewed_projection.load_projection(document, self.driver)
        self.assertEqual(self.driver.state["active_generation"], baseline["generation"])

    def test_malicious_lookup_input_stays_a_parameter_and_never_becomes_cypher(self) -> None:
        malicious = "crop' }) MATCH (n) DETACH DELETE n //"
        before = len(self.driver.queries)
        response = reviewed_projection.lookup_projection(
            self.driver,
            claim_type="property",
            subject_type="Crop",
            subject_id=malicious,
            predicate=malicious,
        )
        self.assertEqual(response["results"], [])
        query, parameters = self.driver.queries[-1]
        self.assertEqual(query, reviewed_projection.LOOKUP_QUERY)
        self.assertNotIn(malicious, query)
        self.assertEqual(parameters["subject_id"], malicious)
        self.assertEqual(parameters["predicate"], malicious)
        self.assertEqual(len(self.driver.queries), before + 1)
        self.assertTrue(
            all(
                query in reviewed_projection.CONSTRAINTS
                or query
                in {
                    reviewed_projection.CREATE_GENERATION_QUERY,
                    reviewed_projection.UPSERT_PROPERTY_BATCH_QUERY,
                    reviewed_projection.UPSERT_RELATIONSHIP_BATCH_QUERY,
                    reviewed_projection.VERIFY_GENERATION_QUERY,
                    reviewed_projection.VERIFY_CLAIMS_QUERY,
                    reviewed_projection.VERIFY_ENTITIES_QUERY,
                    reviewed_projection.VERIFY_EVIDENCE_QUERY,
                    reviewed_projection.SWITCH_ACTIVE_QUERY,
                    reviewed_projection.CONFIRM_ACTIVE_QUERY,
                    reviewed_projection.LOOKUP_QUERY,
                }
                for query, _ in self.driver.queries
            )
        )
        for query, _ in self.driver.queries:
            self.assertNotRegex(query, r"\(\s*\w+\s*:\s*(Crop|Variety|Pest)\b")

    def test_remote_uri_and_credentials_in_uri_are_rejected(self) -> None:
        for uri in (
            "bolt://example.com:7687",
            "neo4j://127.0.0.1:7687",
            "bolt://user:secret@localhost:7687",
            "bolt://localhost:7687/path",
        ):
            with self.subTest(uri=uri), self.assertRaises(ProjectionError):
                reviewed_projection.validate_local_bolt_uri(uri)
        self.assertEqual(
            reviewed_projection.validate_local_bolt_uri("bolt://127.0.0.1:7687"),
            "bolt://127.0.0.1:7687",
        )

    def test_cli_help_has_placeholders_and_no_password_argument(self) -> None:
        parser = reviewed_projection.build_parser()
        help_text = parser.format_help()
        self.assertIn("validate --manifest <manifest.json>", help_text)
        self.assertIn("--subject-id <entity-id>", help_text)
        self.assertNotIn("--password", help_text)


@unittest.skipUnless(
    os.getenv("WIKIKG_TEST_NEO4J_ISOLATED") == "1"
    and os.getenv("WIKIKG_TEST_NEO4J_URI"),
    "requires an explicitly selected disposable local Neo4j instance",
)
class ReviewedProjectionNeo4jIntegrationTest(unittest.TestCase):
    def _driver(self) -> Any:
        from neo4j import GraphDatabase

        uri = reviewed_projection.validate_local_bolt_uri(
            os.environ["WIKIKG_TEST_NEO4J_URI"]
        )
        password = os.getenv("WIKIKG_TEST_NEO4J_PASSWORD")
        return GraphDatabase.driver(uri, auth=("neo4j", password) if password else None)

    def setUp(self) -> None:
        # The instance is explicitly disposable; each test starts from an empty projection.
        driver = self._driver()
        try:
            driver.verify_connectivity()
            with driver.session(database="neo4j") as session:
                session.run(
                    """
                    MATCH (node)
                    WHERE node:WikiKGReviewedProjectionClaim
                       OR node:WikiKGReviewedProjectionEntity
                       OR node:WikiKGReviewedProjectionEvidence
                       OR node:WikiKGReviewedProjectionGeneration
                       OR node:WikiKGReviewedProjectionActive
                    DETACH DELETE node
                    """
                ).consume()
        finally:
            driver.close()

    def test_real_generation_switch_retrieval_and_failed_staging(self) -> None:
        driver = self._driver()
        try:
            driver.verify_connectivity()
            with driver.session(database="neo4j") as session:
                count = session.execute_read(
                    lambda tx: tx.run(
                        "MATCH (a:WikiKGReviewedProjectionActive) RETURN count(a) AS n"
                    ).single(strict=True)["n"]
                )
            self.assertEqual(count, 0, "test Neo4j must be a fresh disposable database")

            first = _property_claim(1, value="100 days")
            conflicting = _property_claim(2, value="180 days")
            relationship = _relationship_claim(3)
            complete = _document([first, conflicting, relationship], snapshot_count=4)
            active = reviewed_projection.load_projection(complete, driver)
            self.assertEqual(active["claim_count"], 3)
            self.assertEqual(
                reviewed_projection.load_projection(complete, driver)["generation"],
                active["generation"],
            )
            with driver.session(database="neo4j") as session:
                epoch = session.run(
                    "MATCH (active:WikiKGReviewedProjectionActive) RETURN active.epoch AS epoch"
                ).single(strict=True)["epoch"]
            self.assertEqual(
                epoch, 1, "reloading unchanged content must not advance the activation epoch"
            )
            rows = reviewed_projection.lookup_projection(
                driver,
                claim_type="property",
                subject_type="Crop",
                subject_id="crop:rice",
                predicate="yield",
            )["results"]
            self.assertEqual({row["value"] for row in rows}, {"100 days", "180 days"})
            self.assertEqual(rows[0]["evidence"][0]["revision_id"], 201)

            reduced = _document(
                [_property_claim(1, review_version=2, value="100 days"), relationship],
                snapshot_count=4,
            )
            reduced["source_watermark"] = _hash("conflict rejected after review")
            new_active = reviewed_projection.load_projection(reduced, driver)
            self.assertNotEqual(new_active["generation"], active["generation"])
            rows = reviewed_projection.lookup_projection(
                driver,
                claim_type="property",
                subject_type="Crop",
                subject_id="crop:rice",
                predicate="yield",
            )["results"]
            self.assertEqual([row["value"] for row in rows], ["100 days"])
            self.assertEqual(rows[0]["review_version"], 2)

            invalid_stage = _document([], snapshot_count=4)
            invalid_stage["source_watermark"] = _hash("later empty generation")
            stage_id = reviewed_projection.generation_id(invalid_stage)
            with driver.session(database="neo4j") as session:
                session.execute_write(
                    reviewed_projection._ensure_generation,
                    stage_id,
                    reviewed_projection._generation_metadata(invalid_stage),
                )
                session.execute_write(
                    lambda tx: tx.run(
                        "CREATE (:WikiKGReviewedProjectionEvidence "
                        "{generation: $generation, snapshot_id: $snapshot_id, evidence_id: $evidence_id})",
                        generation=stage_id,
                        snapshot_id=_hash("unexpected snapshot"),
                        evidence_id=_hash("unexpected evidence"),
                    ).consume()
                )
            with self.assertRaisesRegex(ProjectionError, "staged generation counts"):
                reviewed_projection.load_projection(invalid_stage, driver)
            rows = reviewed_projection.lookup_projection(
                driver,
                claim_type="property",
                subject_type="Crop",
                subject_id="crop:rice",
                predicate="yield",
            )["results"]
            self.assertEqual([row["value"] for row in rows], ["100 days"])
        finally:
            driver.close()

    def test_evidence_link_redirected_to_another_snapshot_is_rejected(self) -> None:
        driver = self._driver()
        try:
            first = _property_claim(1)
            second = _property_claim(2)
            shared_evidence_id = first["evidence"][0]["evidence_id"]
            second["evidence"] = [
                dict(second["evidence"][0], evidence_id=shared_evidence_id)
            ]
            document = _document([first, second])
            loaded = reviewed_projection.load_projection(document, driver)
            with driver.session(database="neo4j") as session:
                session.run(
                    """
                    MATCH (claim:WikiKGReviewedProjectionClaim
                        {generation: $generation, snapshot_id: $snapshot_id})
                        -[old:WIKIKG_REVIEWED_EVIDENCE]->
                        (:WikiKGReviewedProjectionEvidence)
                    MATCH (other:WikiKGReviewedProjectionEvidence
                        {generation: $generation, snapshot_id: $other_snapshot_id})
                    DELETE old
                    CREATE (claim)-[:WIKIKG_REVIEWED_EVIDENCE]->(other)
                    """,
                    generation=loaded["generation"],
                    snapshot_id=first["snapshot_id"],
                    other_snapshot_id=second["snapshot_id"],
                ).consume()

            with self.assertRaisesRegex(ProjectionError, "targets another snapshot"):
                reviewed_projection.load_projection(document, driver)

            with driver.session(database="neo4j") as session:
                active_generation = session.run(
                    "MATCH (active:WikiKGReviewedProjectionActive) "
                    "RETURN active.generation AS generation"
                ).single(strict=True)["generation"]
            self.assertEqual(active_generation, loaded["generation"])
        finally:
            driver.close()

    def test_concurrent_activation_cannot_lose_an_update(self) -> None:
        driver = self._driver()
        try:
            baseline = reviewed_projection.load_projection(
                _document([_property_claim(1)]), driver
            )
            winner = reviewed_projection.generation_id(_document([_property_claim(2)]))
            loser = reviewed_projection.generation_id(_document([_property_claim(3)]))
            with driver.session(database="neo4j") as session:
                expected_epoch = session.run(
                    "MATCH (active:WikiKGReviewedProjectionActive) "
                    "RETURN active.epoch AS epoch"
                ).single(strict=True)["epoch"]
                for generation in (winner, loser):
                    session.run(
                        """
                        MERGE (generation:WikiKGReviewedProjectionGeneration {
                            generation: $generation
                        })
                        SET generation.state = 'building',
                            generation.schema_version = 'wikikg.reviewed-projection.v1',
                            generation.source_watermark = $watermark
                        """,
                        generation=generation,
                        watermark=_hash("concurrent activation"),
                    ).consume()

            outcomes: dict[str, Any] = {}

            def competing_switch() -> None:
                try:
                    with driver.session(database="neo4j") as session:
                        outcomes["record"] = session.run(
                            reviewed_projection.SWITCH_ACTIVE_QUERY,
                            generation=loser,
                            active_key=reviewed_projection.ACTIVE_KEY,
                            expected_epoch=expected_epoch,
                        ).single()
                except Exception as error:  # pragma: no cover - diagnostic capture
                    outcomes["error"] = error

            with driver.session(database="neo4j") as session:
                transaction = session.begin_transaction()
                record = transaction.run(
                    reviewed_projection.SWITCH_ACTIVE_QUERY,
                    generation=winner,
                    active_key=reviewed_projection.ACTIVE_KEY,
                    expected_epoch=expected_epoch,
                ).single()
                self.assertIsNotNone(record)
                self.assertEqual(record["active_generation"], winner)

                thread = threading.Thread(target=competing_switch)
                thread.start()
                # Give the competing transaction time to reach the locked pointer.
                time.sleep(1.0)
                transaction.commit()
                thread.join(timeout=30)

            self.assertFalse(thread.is_alive(), "the competing switch did not finish")
            self.assertNotIn("error", outcomes, outcomes.get("error"))
            self.assertIsNone(
                outcomes.get("record"),
                "a stale activation epoch must not report success",
            )
            with driver.session(database="neo4j") as session:
                state = session.run(
                    "MATCH (active:WikiKGReviewedProjectionActive) "
                    "RETURN active.generation AS generation, active.epoch AS epoch, "
                    "keys(active) AS keys"
                ).single(strict=True)
                loser_state = session.run(
                    "MATCH (generation:WikiKGReviewedProjectionGeneration "
                    "{generation: $generation}) RETURN generation.state AS state",
                    generation=loser,
                ).single(strict=True)["state"]
            self.assertEqual(state["generation"], winner)
            self.assertEqual(state["epoch"], expected_epoch + 1)
            self.assertNotEqual(state["generation"], baseline["generation"])
            self.assertEqual(loser_state, "building")
            self.assertNotIn("_wikikg_lock", state["keys"])
        finally:
            driver.close()


if __name__ == "__main__":
    unittest.main()
