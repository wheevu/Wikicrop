"""Regression tests for the bounded offline reviewed query interpreter."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import reviewed_query as reviewed_query
import assistant_contract


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def evidence(claim_number: int, page_id: int):
    """Claims citing one source page share the same revision and content identity."""
    return [{
        "evidence_id": digest(f"evidence-{claim_number}"),
        "page_id": page_id,
        "revision_id": page_id * 10,
        "content_hash": digest(f"source-page-{page_id}"),
        "page_title": f"Source {page_id}",
        "extraction_method": "rules",
        "extractor_version": "synthetic-test",
        "location_type": "page",
    }]


def property_claim(number, subject_type, subject_id, predicate, value, *, page, qualifiers=None):
    return {
        "snapshot_id": digest(f"snapshot-{number}"),
        "snapshot_hash": digest(f"hash-{number}"),
        "claim_id": digest(f"claim-{number}"),
        "review_version": 1,
        "claim_type": "property",
        "subject": {"type": subject_type, "id": subject_id},
        "predicate": predicate,
        "value": value,
        "qualifiers": qualifiers or {},
        "evidence": evidence(number, page),
    }


def relationship_claim(number, subject_type, subject_id, predicate, target_type, target_id,
                       *, page, resolved=None, qualifiers=None):
    qualifiers = dict(qualifiers or {})
    if resolved is not None:
        qualifiers["resolved"] = resolved
    return {
        "snapshot_id": digest(f"snapshot-{number}"),
        "snapshot_hash": digest(f"hash-{number}"),
        "claim_id": digest(f"claim-{number}"),
        "review_version": 1,
        "claim_type": "relationship",
        "subject": {"type": subject_type, "id": subject_id},
        "predicate": predicate,
        "object": {"type": target_type, "id": target_id},
        "qualifiers": qualifiers,
        "evidence": evidence(number, page),
    }


def manifest(claims):
    ordered = sorted(claims, key=lambda item: item["snapshot_id"])
    return {
        "schema_version": "wikikg.reviewed-projection.v1",
        "snapshot_count": len(ordered),
        "source_watermark": digest("synthetic-watermark"),
        "claims": ordered,
    }


def allow_all(claims):
    return copy.deepcopy(claims)


class ReviewedQueryTests(unittest.TestCase):
    def query(self, source, request, authorizer=allow_all, bindings=None):
        return reviewed_query.query(source, request, authorizer, bindings=bindings)

    def variety_request(self, **options):
        return {
            "intent": "lookup",
            "entities": [{"type": "Variety", "id": "same-id", "crop_id": "crop-a"}],
            **options,
        }

    def test_answer_lookup_blocks_unresolved_or_invalid_relationship_identity(self):
        membership = relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11)
        target_membership = relationship_claim(2, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=12)
        cases = [
            relationship_claim(3, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=11),
            relationship_claim(4, "Variety", "child", "HAS_PARENT", "Variety", "unknown", page=11, resolved=True),
            relationship_claim(5, "Variety", "child", "HAS_PARENT", "Pest", "rust", page=11, resolved=True),
        ]
        for edge in cases:
            with self.subTest(edge=edge["snapshot_id"]):
                source = manifest([membership, target_membership, edge])
                generated = []

                def generator(context):
                    generated.append(context)
                    return {"status": "answer", "answer": "Synthetic.",
                            "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

                result = reviewed_query.answer_query(
                    source, {"intent": "lookup", "purpose": "answer",
                             "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}]},
                    allow_all, generator=generator,
                )
                self.assertEqual(generated, [])
                self.assertEqual(result["query"]["status"], "unsupported")
                self.assertIn("unresolved_identity", {item["code"] for item in result["query"]["reasons"]})
                displayed = {item["claim"]["snapshot_id"] for group in result["query"]["results"] for item in group["claims"]}
                self.assertIn(edge["snapshot_id"], displayed)

    def test_answer_lookup_generates_for_resolved_same_crop_parent_and_keeps_scope_conflict_guards(self):
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            relationship_claim(2, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=12),
            relationship_claim(3, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=11, resolved=True),
        ]
        request = {"intent": "lookup", "purpose": "answer",
                   "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}]}
        generated = []

        def generator(context):
            generated.append(context)
            return {"status": "answer", "answer": "Synthetic.",
                    "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

        result = reviewed_query.answer_query(manifest(claims), request, allow_all, generator=generator)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(generated), 1)

        for index, bad_memberships in enumerate((
            [relationship_claim(4, "Crop", "crop-b", "HAS_VARIETY", "Variety", "parent", page=22)],
            [],
            [claims[1], relationship_claim(4, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=12),
             relationship_claim(5, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=13)],
        )):
            with self.subTest(membership_case=index):
                blocked_calls = []
                result = reviewed_query.answer_query(
                    manifest([claims[0], claims[2], *bad_memberships]), request, allow_all,
                    generator=lambda context: blocked_calls.append(context),
                )
                self.assertEqual(blocked_calls, [])

        scoped = relationship_claim(4, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=11,
                                    resolved=True, qualifiers={"season": "winter"})
        scoped_result = reviewed_query.answer_query(
            manifest([claims[0], claims[1], claims[2], scoped]),
            {**request, "qualifiers": {"season": "summer"}}, allow_all,
            generator=lambda context: self.fail("scope-unknown lookup must not generate"),
        )
        self.assertEqual(scoped_result["query"]["status"], "unsupported")

    def test_same_variety_local_id_stays_crop_scoped(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "same-id", page=11),
            relationship_claim(2, "Crop", "crop-b", "HAS_VARIETY", "Variety", "same-id", page=22),
            property_claim(3, "Variety", "same-id", "height", "70 cm", page=11),
            property_claim(4, "Variety", "same-id", "height", "140 cm", page=22),
        ])
        result = self.query(source, self.variety_request())
        self.assertEqual(result["status"], "ok")
        pages = {item["evidence"][0]["page_id"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(pages, {11})

    def test_variety_name_requires_a_real_same_source_alias(self):
        membership = relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "same-id", page=11)
        forged_alias = {
            "type": "Variety", "id": "same-id", "name": "Invented Label", "crop_id": "crop-a",
            "membership_snapshot_id": membership["snapshot_id"],
            "membership_snapshot_hash": membership["snapshot_hash"],
            "source_page_ids": [11],
        }
        request = {"intent": "lookup", "entities": [{"type": "Variety", "name": "Invented Label", "crop_id": "crop-a"}]}
        no_alias_claim = manifest([membership, property_claim(2, "Variety", "same-id", "height", "80 cm", page=11)])
        self.assertEqual(self.query(no_alias_claim, request, bindings=[forged_alias])["status"], "unsupported")

        alias_claim = property_claim(3, "Variety", "same-id", "ALIAS", "Invented Label", page=11)
        approved_manifest = manifest([membership, alias_claim, property_claim(2, "Variety", "same-id", "height", "80 cm", page=11)])
        self.assertEqual(self.query(approved_manifest, request, bindings=[forged_alias])["status"], "ok")

    def test_binding_cannot_expand_source_pages_or_be_malformed(self):
        membership = relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "same-id", page=11)
        source = manifest([membership, property_claim(2, "Variety", "same-id", "ALIAS", "Source 11", page=11)])
        binding = {
            "type": "Variety", "id": "same-id", "name": "Source 11", "crop_id": "crop-a",
            "membership_snapshot_id": membership["snapshot_id"],
            "membership_snapshot_hash": membership["snapshot_hash"],
            "source_page_ids": [11, 22],
        }
        request = {"intent": "lookup", "entities": [{"type": "Variety", "name": "Source 11", "crop_id": "crop-a"}]}
        self.assertEqual(self.query(source, request, bindings=[binding])["status"], "unsupported")
        self.assertEqual(self.query(source, request, bindings=[{"name": "Source 11"}])["status"], "unsupported")

    def test_path_finds_two_scoped_parent_branches_and_resistance_to_target_pest(self):
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            property_claim(2, "Variety", "child", "growth_duration", "12 ngày", page=11),
            relationship_claim(3, "Variety", "child", "HAS_PARENT", "Variety", "parent-1", page=11, resolved=True),
            relationship_claim(4, "Variety", "child", "HAS_PARENT", "Variety", "parent-2", page=11, resolved=True),
            relationship_claim(5, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent-1", page=21),
            relationship_claim(6, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent-2", page=22),
            property_claim(7, "Variety", "parent-1", "plant_height", "100 cm", page=21),
            property_claim(8, "Variety", "parent-2", "plant_height", "110 cm", page=22),
            relationship_claim(9, "Variety", "parent-1", "RESISTANT_TO", "Pest", "rust", page=21),
            relationship_claim(10, "Variety", "parent-2", "RESISTANT_TO", "Pest", "rust", page=22),
            property_claim(11, "Pest", "rust", "resistance_level", "high", page=21),
            property_claim(12, "Pest", "rust", "resistance_level", "high", page=22),
        ]
        source = manifest(claims)
        request = {
            "intent": "path",
            "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}],
            "path": ["HAS_PARENT", "RESISTANT_TO"],
            "target": {"type": "Pest", "id": "rust"},
            "filters": [{"predicate": "growth_duration", "op": "<=", "value": "12 days", "on": "root"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "ok")
        returned_claims = [item["claim"] for group in result["results"] for item in group["claims"]]
        returned_ids = {item["snapshot_id"] for item in returned_claims}
        self.assertIn(claims[2]["snapshot_id"], returned_ids)
        self.assertIn(claims[4]["snapshot_id"], returned_ids)
        self.assertIn(claims[6]["snapshot_id"], returned_ids)
        self.assertIn(claims[9]["snapshot_id"], returned_ids)
        self.assertIn(claims[10]["snapshot_id"], returned_ids)
        self.assertIn(claims[11]["snapshot_id"], returned_ids)
        self.assertEqual(len(result["results"]), 2)

    def test_unknown_or_wrongly_typed_parent_is_never_traversed(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            relationship_claim(2, "Variety", "child", "HAS_PARENT", "Variety", "Chưa rõ", page=11, resolved=True),
        ])
        request = {
            "intent": "path", "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}],
            "path": ["HAS_PARENT"], "target": {"type": "Variety", "id": "Chưa rõ"},
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertTrue(result["results"])
        parent_edge_id = next(claim["snapshot_id"] for claim in source["claims"]
                              if claim["predicate"] == "HAS_PARENT")
        self.assertTrue(any(parent_edge_id in group["diagnostic_claim_ids"]
                            for group in result["results"]))
        self.assertTrue(all(parent_edge_id not in group["eligible_claim_ids"]
                            for group in result["results"]))

        no_edge = manifest([relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11)])
        missing = self.query(no_edge, {**request, "target": {"type": "Pest", "id": "rust"}})
        self.assertEqual(missing["status"], "unsupported")
        self.assertIn("relationship_missing", {item["code"] for item in missing["reasons"]})

    def test_unknown_parent_link_makes_requested_path_unsupported(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            relationship_claim(2, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=11, resolved=False),
        ])
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}], "path": ["HAS_PARENT"]}
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])

    def test_cross_crop_edge_is_not_traversed_and_missing_path_is_unsupported(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            relationship_claim(2, "Crop", "crop-b", "HAS_VARIETY", "Variety", "child", page=22),
            relationship_claim(3, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=22, resolved=True),
            relationship_claim(4, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=33),
        ])
        request = {
            "intent": "path",
            "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}],
            "path": ["HAS_PARENT"],
            "target": {"type": "Variety", "id": "parent"},
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        self.assertIn("relationship_missing", {item["code"] for item in result["reasons"]})

    def test_unknown_season_scope_keeps_claims_diagnostic(self):
        source = manifest([
            property_claim(1, "Crop", "crop-a", "height", "80 cm", page=11,
                          qualifiers={"season": "spring", "condition": "irrigated"}),
            property_claim(2, "Crop", "crop-a", "height", "90 cm", page=12,
                          qualifiers={"season": "winter", "condition": "irrigated"}),
        ])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "qualifiers": {"season": "spring", "condition": "irrigated"},
            "filters": [{"predicate": "height", "op": "=", "value": "80 cm"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        displayed = {item["claim"]["snapshot_id"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(displayed, {claim["snapshot_id"] for claim in source["claims"]})
        self.assertTrue(all(not group["eligible_claim_ids"] for group in result["results"]))
        self.assertEqual({item for group in result["results"] for item in group["diagnostic_claim_ids"]}, displayed)

    def test_path_edges_require_exact_requested_scope(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
            relationship_claim(2, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11,
                                qualifiers={"season": "mùa", "condition": "irrigated"}),
        ])
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                   "path": ["RESISTANT_TO"], "qualifiers": {"season": "đông xuân"}}
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        request["qualifiers"] = {"season": "mùa", "condition": "irrigated"}
        matched = self.query(source, request)
        self.assertEqual(matched["status"], "ok")
        self.assertTrue(matched["complete"])
        for qualifiers in ({}, {"season": "đông xuân"}, {"condition": "irrigated"},
                           {"season": "mùa", "condition": "dry"}):
            with self.subTest(qualifiers=qualifiers):
                result = self.query(source, {**request, "qualifiers": qualifiers})
                self.assertEqual(result["status"], "unsupported")
                self.assertFalse(result["complete"])
                self.assertTrue(result["results"])
                edge_id = next(claim["snapshot_id"] for claim in source["claims"]
                               if claim["predicate"] == "RESISTANT_TO")
                self.assertTrue(all(edge_id not in group["eligible_claim_ids"]
                                    for group in result["results"]))
                self.assertTrue(any(edge_id in group.get("diagnostic_claim_ids", [])
                                    for group in result["results"]))

        unscoped = copy.deepcopy(source)
        for claim in unscoped["claims"]:
            claim["qualifiers"] = {}
        self.assertEqual(self.query(unscoped, request)["status"], "unsupported")

    def test_unknown_other_scope_remains_visible_and_blocks_completeness(self):
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
            relationship_claim(2, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11,
                               qualifiers={"season": "summer"}),
            relationship_claim(3, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11,
                               qualifiers={"season": "winter"}),
            relationship_claim(4, "Variety", "v", "SUSCEPTIBLE_TO", "Pest", "rust", page=11,
                               qualifiers={"season": "winter"}),
            relationship_claim(5, "Crop", "crop-b", "HAS_VARIETY", "Variety", "v", page=22),
            relationship_claim(6, "Variety", "v", "SUSCEPTIBLE_TO", "Pest", "rust", page=22,
                               qualifiers={"season": "summer"}),
        ]
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                   "path": ["RESISTANT_TO"], "qualifiers": {"season": "summer"}}
        result = self.query(manifest(claims), request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        returned = {item["claim"]["snapshot_id"] for group in result["results"] for item in group["claims"]}
        self.assertTrue({claim["snapshot_id"] for claim in claims[:4]} <= returned)
        self.assertTrue(any(claims[1]["snapshot_id"] in group["eligible_claim_ids"] for group in result["results"]))

    def test_scope_is_checked_at_every_hop(self):
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            relationship_claim(2, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=22),
            relationship_claim(3, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=11,
                               resolved=True, qualifiers={"season": "summer"}),
            relationship_claim(4, "Variety", "parent", "RESISTANT_TO", "Pest", "rust", page=22,
                               qualifiers={"season": "summer"}),
        ]
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}],
                   "path": ["HAS_PARENT", "RESISTANT_TO"], "qualifiers": {"season": "summer"}}
        self.assertEqual(self.query(manifest(claims), request)["status"], "ok")
        for hop in (2, 3):
            with self.subTest(hop=hop):
                changed = copy.deepcopy(claims)
                changed[hop]["qualifiers"].pop("season")
                result = self.query(manifest(changed), request)
                self.assertEqual(result["status"], "unsupported")
                self.assertFalse(result["complete"])
                self.assertTrue(result["results"])
                self.assertTrue(any(changed[hop]["snapshot_id"] in group.get("diagnostic_claim_ids", [])
                                    for group in result["results"]))

    def test_opposing_same_scope_path_claims_are_diagnostic_and_block_assistant(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
            relationship_claim(2, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11,
                               qualifiers={"season": "summer"}),
            relationship_claim(3, "Variety", "v", "SUSCEPTIBLE_TO", "Pest", "rust", page=11,
                               qualifiers={"season": "summer"}),
        ])
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                   "path": ["RESISTANT_TO"], "qualifiers": {"season": "summer"}}
        for predicate in ("RESISTANT_TO", "SUSCEPTIBLE_TO"):
            with self.subTest(predicate=predicate):
                result = self.query(source, {**request, "path": [predicate]})
                self.assertEqual(result["status"], "unsupported")
                self.assertFalse(result["complete"])
                returned = [{**item["claim"], "evidence": item["evidence"]}
                            for group in result["results"] for item in group["claims"]]
                self.assertEqual({item["snapshot_id"] for item in returned},
                                 {item["snapshot_id"] for item in source["claims"]})
                self.assertTrue(all(item in source["claims"] for item in returned))
                # Use the actual query result, even if a caller drops completeness metadata.
                composed = assistant_contract.compose_answer(
                    returned, [], allow_all, None,
                    lambda _: self.fail("opposing evidence must not reach generation"), "http://localhost",
                    graph_generation=result["generation"],
                )
                self.assertEqual(composed["status"], "conflict")

    def test_opposing_paths_keep_diagnostics_when_numeric_filter_is_uncertain(self):
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
            relationship_claim(2, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11),
            relationship_claim(3, "Variety", "v", "SUSCEPTIBLE_TO", "Pest", "rust", page=11),
            property_claim(4, "Variety", "v", "plant_height", "10 cm", page=11),
            property_claim(5, "Variety", "v", "plant_height", "30 cm", page=11),
        ]
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                   "path": ["RESISTANT_TO"],
                   "filters": [{"predicate": "plant_height", "op": "<", "value": "20 cm"}]}
        result = self.query(manifest(claims), request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        returned = {item["claim"]["snapshot_id"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(returned, {item["snapshot_id"] for item in claims})

    def test_potentially_overlapping_opposition_blocks_query_and_generation(self):
        full_scope = {"season": "summer", "condition": "irrigated"}
        cases = [
            (full_scope, {"season": "summer"}),
            (full_scope, {"condition": "irrigated"}),
            (full_scope, {}),
            ({"season": "summer"}, full_scope),
            ({"condition": "irrigated"}, full_scope),
            ({}, full_scope),
            ({"season": "summer"}, {"condition": "irrigated"}),
            ({"condition": "irrigated"}, {"season": "summer"}),
        ]
        for predicate, opposite in (("RESISTANT_TO", "SUSCEPTIBLE_TO"),
                                    ("SUSCEPTIBLE_TO", "RESISTANT_TO")):
            for scope, opposing_scope in cases:
                with self.subTest(predicate=predicate, scope=scope, opposing_scope=opposing_scope):
                    source = manifest([
                        relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
                        relationship_claim(2, "Variety", "v", predicate, "Pest", "rust", page=11,
                                           qualifiers=scope),
                        relationship_claim(3, "Variety", "v", opposite, "Pest", "rust", page=11,
                                           qualifiers=opposing_scope),
                    ])
                    request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                               "path": [predicate], "qualifiers": scope}
                    result = self.query(source, request)
                    self.assertEqual(result["status"], "unsupported")
                    self.assertFalse(result["complete"])
                    self.assertTrue(result["partial"])
                    returned = [{**item["claim"], "evidence": item["evidence"]}
                                for group in result["results"] for item in group["claims"]]
                    self.assertEqual({item["snapshot_id"] for item in returned},
                                     {item["snapshot_id"] for item in source["claims"]})
                    self.assertTrue(all(item in source["claims"] for item in returned))
                    generated = []

                    def generator(context):
                        generated.append(context)
                        return {"status": "answer", "answer": "Synthetic answer.",
                                "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

                    for honor_completeness in (True, False):
                        composed = assistant_contract.compose_answer(
                            returned, [], allow_all, None, generator, "http://localhost",
                            graph_generation=result["generation"],
                            incomplete=not result["complete"] if honor_completeness else False,
                        )
                        self.assertEqual(composed["status"], "partial" if honor_completeness else "conflict")
                        self.assertIsNone(composed["answer"])
                        self.assertEqual(generated, [])

    def test_different_qualifier_opposition_stays_diagnostic_and_blocks_generation(self):
        scope = {"season": "summer", "condition": "irrigated"}
        for predicate, opposite in (("RESISTANT_TO", "SUSCEPTIBLE_TO"),
                                    ("SUSCEPTIBLE_TO", "RESISTANT_TO")):
            for opposing_scope in ({"season": "winter"}, {"condition": "dry"},
                                   {"season": "winter", "condition": "irrigated"},
                                   {"season": "summer", "condition": "dry"}):
                with self.subTest(predicate=predicate, opposing_scope=opposing_scope):
                    membership = relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11)
                    supporting = relationship_claim(2, "Variety", "v", predicate, "Pest", "rust", page=11,
                                                    qualifiers=scope)
                    opposing = relationship_claim(3, "Variety", "v", opposite, "Pest", "rust", page=11,
                                                  qualifiers=opposing_scope)
                    request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                               "path": [predicate], "qualifiers": scope}
                    result = self.query(manifest([membership, supporting, opposing]), request)
                    self.assertEqual(result["status"], "unsupported")
                    self.assertFalse(result["complete"])
                    returned = [{**item["claim"], "evidence": item["evidence"]}
                                for group in result["results"] for item in group["claims"]]
                    returned_ids = {item["snapshot_id"] for item in returned}
                    self.assertTrue({membership["snapshot_id"], supporting["snapshot_id"],
                                     opposing["snapshot_id"]} <= returned_ids)
                    self.assertTrue(any(supporting["snapshot_id"] in group["eligible_claim_ids"]
                                        for group in result["results"]))
                    self.assertTrue(any(opposing["snapshot_id"] in group.get("diagnostic_claim_ids", [])
                                        for group in result["results"]))
                    generated = []

                    def generator(context):
                        generated.append(context)
                        return {"status": "answer", "answer": "Synthetic scoped answer.",
                                "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

                    composed = assistant_contract.compose_answer(
                        returned, [], allow_all, None, generator, "http://localhost",
                        graph_generation=result["generation"], incomplete=not result["complete"],
                    )
                    self.assertIn(composed["status"], {"partial", "conflict"})
                    self.assertIsNone(composed["answer"])
                    self.assertEqual(generated, [])

    def test_partially_scoped_opposition_survives_uncertain_numeric_filter(self):
        scope = {"season": "summer", "condition": "irrigated"}
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
            relationship_claim(2, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11,
                               qualifiers=scope),
            relationship_claim(3, "Variety", "v", "SUSCEPTIBLE_TO", "Pest", "rust", page=11,
                               qualifiers={"season": "summer"}),
            property_claim(4, "Variety", "v", "plant_height", "10 cm", page=11, qualifiers=scope),
            property_claim(5, "Variety", "v", "plant_height", "30 cm", page=11, qualifiers=scope),
        ]
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                   "path": ["RESISTANT_TO"], "qualifiers": scope,
                   "filters": [{"predicate": "plant_height", "op": "<", "value": "20 cm"}]}
        result = self.query(manifest(claims), request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        returned = {item["claim"]["snapshot_id"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(returned, {item["snapshot_id"] for item in claims})

    def test_partially_scoped_opposition_at_parent_uses_parent_provenance(self):
        scope = {"season": "summer", "condition": "irrigated"}
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "child", page=11),
            relationship_claim(2, "Variety", "child", "HAS_PARENT", "Variety", "parent", page=11,
                               resolved=True, qualifiers=scope),
            relationship_claim(3, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=22),
            relationship_claim(4, "Variety", "parent", "RESISTANT_TO", "Pest", "rust", page=22,
                               qualifiers=scope),
            relationship_claim(5, "Variety", "parent", "SUSCEPTIBLE_TO", "Pest", "rust", page=22,
                               qualifiers={"season": "summer"}),
        ]
        other_crop = [
            relationship_claim(6, "Crop", "crop-b", "HAS_VARIETY", "Variety", "parent", page=33),
            relationship_claim(7, "Variety", "parent", "SUSCEPTIBLE_TO", "Pest", "rust", page=33),
        ]
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "child", "crop_id": "crop-a"}],
                   "path": ["HAS_PARENT", "RESISTANT_TO"], "qualifiers": scope}
        result = self.query(manifest(claims + other_crop), request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        returned = {item["claim"]["snapshot_id"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(returned, {item["snapshot_id"] for item in claims})
        without_opposition = self.query(manifest(claims[:4] + other_crop), request)
        self.assertEqual(without_opposition["status"], "ok")
        self.assertTrue(without_opposition["complete"])

    def test_opposing_claims_from_distinct_pages_in_same_membership_scope_remain_visible(self):
        membership = relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11)
        membership["evidence"].extend(evidence(4, 12))
        resistant = relationship_claim(2, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11)
        susceptible = relationship_claim(3, "Variety", "v", "SUSCEPTIBLE_TO", "Pest", "rust", page=12)
        claims = [membership, resistant, susceptible]
        request = {"intent": "path", "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
                   "path": ["RESISTANT_TO"]}
        result = self.query(manifest(claims), request)
        self.assertEqual(result["status"], "unsupported")
        returned = {item["claim"]["snapshot_id"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(returned, {item["snapshot_id"] for item in claims})

    def test_conditional_property_without_matching_season_scope_is_unsupported(self):
        source = manifest([
            property_claim(1, "Crop", "crop-a", "plant_height", "80 cm", page=11,
                          qualifiers={"season": "winter"}),
        ])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "plant_height", "op": "<=", "value": "100 cm"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])

    def test_conjunction_is_per_root_and_missing_claim_is_unsupported(self):
        source = manifest([
            property_claim(1, "Crop", "crop-a", "height", "10 cm", page=11),
            property_claim(2, "Crop", "crop-b", "maturity", "20 days", page=12),
        ])
        request = {
            "intent": "filter",
            "entities": [
                {"type": "Crop", "id": "crop-a", "crop_id": "crop-a"},
                {"type": "Crop", "id": "crop-b", "crop_id": "crop-b"},
            ],
            "filters": [
                {"predicate": "height", "op": "<", "value": "15 cm"},
                {"predicate": "maturity", "op": "<", "value": "30 days"},
            ],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])

    def test_bounded_conjunction_uses_same_root_and_union_branch_witnesses(self):
        claims = [
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "v", page=11),
            relationship_claim(2, "Variety", "v", "HAS_PARENT", "Variety", "parent", page=11, resolved=True),
            relationship_claim(3, "Crop", "crop-a", "HAS_VARIETY", "Variety", "parent", page=12),
            relationship_claim(4, "Variety", "v", "RESISTANT_TO", "Pest", "rust", page=11),
        ]
        request = {
            "intent": "conjunction",
            "entities": [{"type": "Variety", "id": "v", "crop_id": "crop-a"}],
            "branches": [
                {"path": ["HAS_PARENT"], "target": {"type": "Variety", "id": "parent"}},
                {"path": ["RESISTANT_TO"], "target": {"type": "Pest", "id": "rust"}},
            ],
        }
        result = self.query(manifest(claims), request)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["results"]), 1)
        witnesses = result["results"][0]["branch_claim_ids"]
        self.assertEqual(len(witnesses), 2)
        self.assertIn(claims[1]["snapshot_id"], witnesses[0])
        self.assertIn(claims[3]["snapshot_id"], witnesses[1])

    def test_decimal_comma_interval_preserves_source_value(self):
        claim = property_claim(1, "Crop", "crop-a", "duration", "95,5-98,5 days", page=11)
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "duration", "op": "<", "value": "100 days"}],
        }
        result = self.query(manifest([claim]), request)
        self.assertEqual(result["status"], "ok")
        item = result["results"][0]["claims"][0]
        self.assertEqual(item["claim"]["value"], "95,5-98,5 days")
        self.assertIn(item["claim"]["snapshot_id"], result["results"][0]["eligible_claim_ids"])

    def test_conflicting_assertions_remain_diagnostic_alternatives(self):
        source = manifest([
            property_claim(1, "Crop", "crop-a", "height", "10 cm", page=11),
            property_claim(2, "Crop", "crop-a", "height", "30 cm", page=12),
        ])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "height", "op": "<", "value": "20 cm"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        values = {item["claim"]["value"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(values, {"10 cm", "30 cm"})

    def test_distinct_true_numeric_alternatives_are_still_unsupported(self):
        source = manifest([
            property_claim(1, "Crop", "crop-a", "plant_height", "10 cm", page=11),
            property_claim(2, "Crop", "crop-a", "plant_height", "15 cm", page=12),
        ])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "plant_height", "op": "<=", "value": "20 cm"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        values = {item["claim"]["value"] for group in result["results"] for item in group["claims"]}
        self.assertEqual(values, {"10 cm", "15 cm"})

    def test_native_units_convert_exactly_and_unknown_units_fail_closed(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "yield_amount", "1 tấn/ha", page=11)])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "yield_amount", "op": "=", "value": "1000 kg/ha"}],
        }
        self.assertEqual(self.query(source, request)["status"], "ok")
        request["filters"][0]["value"] = "1 t"
        self.assertEqual(self.query(source, request)["status"], "unsupported")

    def test_explicit_alias_filter_matches_the_frozen_property_without_rewriting_it(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "plant_height", "90 cm", page=11)])
        request = {"intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
                   "filters": [{"predicate": "height", "op": "<=", "value": "100 cm"}]}
        result = self.query(source, request)
        self.assertEqual("ok", result["status"])
        self.assertEqual("plant_height", result["results"][0]["claims"][0]["claim"]["predicate"])

    def test_real_property_predicates_enforce_their_dimensions(self):
        source = manifest([
            property_claim(1, "Crop", "crop-a", "plant_height", "1 t/ha", page=11),
        ])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "plant_height", "op": "=", "value": "1 t/ha"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")

    def test_numeric_range_uses_full_interval_for_upper_bound(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "duration", "8-12 ngày", page=11)])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "duration", "op": "<=", "value": "10 days"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["complete"])
        self.assertIn("numeric_uncertain", {item["code"] for item in result["reasons"]})
        self.assertIn(source["claims"][0]["snapshot_id"], result["results"][0]["diagnostic_claim_ids"])
        self.assertEqual(result["results"][0]["eligible_claim_ids"], [])

    def test_range_ambiguity_and_unknown_predicate_are_unsupported(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "height", "1-2 or 4 cm", page=11)])
        request = {
            "intent": "filter", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}],
            "filters": [{"predicate": "height", "op": "<", "value": "5 cm"}],
        }
        result = self.query(source, request)
        self.assertEqual(result["status"], "unsupported")
        request["filters"][0]["predicate"] = "raw expression"
        self.assertEqual(self.query(source, request)["status"], "unsupported")

    def test_authorizer_list_api_dict_api_subset_and_in_place_mutation(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "height", "10 cm", page=11)])
        request = {"intent": "lookup", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]}
        self.assertEqual(self.query(source, request, lambda claims: {"status": "ok", "claims": copy.deepcopy(claims)})["status"], "ok")
        self.assertEqual(self.query(source, request, lambda claims: [])["status"], "dependency_failure")

        def mutate_in_place(claims):
            claims[0]["value"] = "999 cm"
            return claims

        result = self.query(source, request, mutate_in_place)
        self.assertEqual(result["status"], "dependency_failure")
        self.assertFalse(result["results"])

        calls = 0

        def revoked_after_interpretation(claims):
            nonlocal calls
            calls += 1
            if calls == 1:
                return copy.deepcopy(claims)
            return []

        result = self.query(source, request, revoked_after_interpretation)
        self.assertEqual(calls, 2)
        self.assertEqual(result["status"], "dependency_failure")
        self.assertFalse(result["results"])

    def test_falsy_non_list_bindings_are_not_silently_ignored(self):
        source = manifest([
            relationship_claim(1, "Crop", "crop-a", "HAS_VARIETY", "Variety", "same-id", page=11),
        ])
        request = {"intent": "lookup", "entities": [{"type": "Variety", "id": "same-id", "crop_id": "crop-a"}]}
        self.assertEqual(self.query(source, request, bindings={})["status"], "unsupported")

    def test_answer_query_isolates_callback_mutations_of_claims_request_bindings_and_text(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "height", "80 cm", page=11)])
        request = {"intent": "lookup", "purpose": "answer", "entities": [
            {"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]}
        bindings = [{"type": "Crop", "id": "crop-a", "name": "Crop A"}]
        text = [{"page_id": 11, "revision_id": 110, "content_hash": digest("text"),
                 "page_title": "Source 11"}]
        seen = []

        def graph_authorizer(claims):
            claims[0]["value"] = "tampered"
            request["entities"][0]["id"] = "changed"
            bindings[0]["name"] = "changed"
            return copy.deepcopy(source["claims"])

        def text_authorizer(candidates):
            candidates[0]["page_title"] = "tampered"
            return [{**candidates[0], "text": "synthetic text"}]

        def generator(context):
            seen.append(json.loads(context))
            cid = seen[-1]["citation_catalog"][0]["id"]
            return {"status": "answer", "answer": "Synthetic.", "citation_ids": [cid]}

        result = reviewed_query.answer_query(source, request, graph_authorizer, text_authorizer,
                                             generator=generator, bindings=bindings,
                                             text_candidates=text)
        self.assertEqual(result["status"], "dependency_failure")
        self.assertEqual(seen, [])
        self.assertIsNone(result["query"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["citations"], [])
        self.assertEqual(request["entities"][0]["id"], "changed")
        self.assertEqual(bindings[0]["name"], "changed")
        self.assertEqual(text[0]["page_title"], "Source 11")

        text_only = [{"page_id": 11, "revision_id": 110, "content_hash": digest("text"),
                      "page_title": "Source 11"}]
        text_result = reviewed_query.answer_query(
            source, {**request, "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]},
            allow_all,
            lambda candidates: (candidates[0].__setitem__("page_title", "tampered") or
                                [{**candidates[0], "text": "synthetic text"}]),
            generator=lambda _: self.fail("mutated text authorization input must not reach generation"),
            text_candidates=text_only,
        )
        self.assertEqual(text_result["status"], "dependency_failure")
        self.assertIsNone(text_result["query"])
        self.assertIsNone(text_result["answer"])
        self.assertEqual(text_result["citations"], [])
        self.assertEqual(text_only[0]["page_title"], "Source 11")

        isolated_request = {"intent": "lookup", "purpose": "answer", "entities": [
            {"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]}
        isolated_bindings = [{"type": "Crop", "id": "crop-a", "name": "Crop A"}]
        generated_contexts = []

        def mutate_caller_references(claims):
            isolated_request["entities"][0]["id"] = "outside-change"
            isolated_bindings[0]["name"] = "outside-change"
            return copy.deepcopy(claims)

        def capture_context(context):
            generated_contexts.append(json.loads(context))
            cid = generated_contexts[-1]["citation_catalog"][0]["id"]
            return {"status": "answer", "answer": "Synthetic.", "citation_ids": [cid]}

        isolated = reviewed_query.answer_query(source, isolated_request, mutate_caller_references,
                                               generator=capture_context, bindings=isolated_bindings)
        self.assertEqual(isolated["status"], "ok")
        self.assertEqual(generated_contexts[0]["question"]["entities"][0]["id"], "crop-a")
        self.assertEqual(isolated_request["entities"][0]["id"], "outside-change")
        self.assertEqual(isolated_bindings[0]["name"], "outside-change")

    def test_answer_query_clears_all_payload_after_final_authorization_denial(self):
        source = manifest([property_claim(1, "Crop", "crop-a", "height", "80 cm", page=11)])
        request = {"intent": "lookup", "purpose": "answer", "entities": [
            {"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]}
        calls, generated = 0, []

        def authorizer(claims):
            nonlocal calls
            calls += 1
            # Query's initial/final checks plus composition's initial and pre-generator checks.
            return copy.deepcopy(claims) if calls <= 4 else []

        def generator(context):
            generated.append(context)
            return {"status": "answer", "answer": "Synthetic.",
                    "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

        result = reviewed_query.answer_query(source, request, authorizer, generator=generator)
        self.assertEqual(len(generated), 1)
        self.assertEqual(result["status"], "dependency_failure")
        self.assertIsNone(result["query"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["citations"], [])


    def test_total_evidence_limit_returns_truncated_without_payload(self):
        source = manifest([
            property_claim(number, "Crop", "crop-a", "height", f"{number} cm", page=number + 100)
            for number in range(51)
        ])
        request = {"intent": "lookup", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]}
        result = self.query(source, request)
        self.assertEqual(result["status"], "truncated")
        self.assertFalse(result["complete"])
        self.assertEqual(result["results"], [])

    def test_request_schema_runtime_and_response_schema_contract(self):
        schema = json.loads((ROOT / "schema" / "reviewed-query.v2.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "https://wikicrop.local/schema/reviewed-query.v2.schema.json")
        request = {"intent": "lookup", "entities": [{"type": "Crop", "id": "crop-a", "crop_id": "crop-a"}]}
        request_schema = {"$schema": schema["$schema"], "$defs": schema["$defs"], **schema["$defs"]["request"]}
        jsonschema.Draft202012Validator(request_schema).validate(request)
        valid = manifest([])
        result = self.query(valid, request)
        jsonschema.Draft202012Validator(schema).validate(result)
        self.assertEqual(result["semantic_version"], "wikikg.reviewed-query.v2")

        malformed = {**request, "cypher": "MATCH (n)"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(request_schema).validate(malformed)
        self.assertEqual(self.query(valid, malformed)["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
