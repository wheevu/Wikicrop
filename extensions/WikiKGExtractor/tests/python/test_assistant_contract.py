import hashlib
import importlib.util
import itertools
import json
import pathlib
import unittest
from decimal import Decimal


MODULE = pathlib.Path(__file__).parents[2] / "tools" / "assistant_contract.py"
spec = importlib.util.spec_from_file_location("assistant_contract", MODULE)
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)


def make_claim(key="one", value="140 days", text="Rice grows well in flooded fields.",
               revision_id=11):
    span = "Rice grows"
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "snapshot_id": hashlib.sha256(("snapshot:" + key).encode()).hexdigest(),
        "snapshot_hash": hashlib.sha256(("snapshot-hash:" + key).encode()).hexdigest(),
        "claim_id": hashlib.sha256(("claim:" + key).encode()).hexdigest(),
        "review_version": 2, "claim_type": "property",
        "subject": {"type": "Crop", "id": "Rice"}, "predicate": "GROWTH_DURATION",
        "value": value, "qualifiers": {},
        "evidence": [{
            "evidence_id": hashlib.sha256(("evidence:" + key).encode()).hexdigest(),
            "page_id": 7, "revision_id": revision_id, "content_hash": digest,
            "page_title": "Rice guide", "extraction_method": "rules",
            "extractor_version": "phase6-test", "location_type": "source_span",
            "source_offset_start": 0, "source_offset_end": len(span), "supporting_span": span,
            "span_hash": hashlib.sha256(span.encode()).hexdigest(),
        }],
    }


def text_identity(claim):
    evidence = claim["evidence"][0]
    return {key: evidence[key] for key in ("page_id", "revision_id", "content_hash")}


def text_response(identity, source="Rice grows well in flooded fields."):
    return {**identity, "text": source}


def make_response_claim(key, predicate, qualifiers):
    claim = make_claim(key)
    claim.pop("value")
    claim.update(claim_type="relationship", subject={"type": "Variety", "id": "v"},
                 predicate=predicate, object={"type": "Pest", "id": "rust"},
                 qualifiers=qualifiers)
    return claim


class AssistantContractTest(unittest.TestCase):
    def setUp(self):
        self.source = "Rice grows well in flooded fields."
        self.claim = make_claim(text=self.source)
        self.graph_generation = hashlib.sha256(b"exported exact generation").hexdigest()
        self.wiki = "http://127.0.0.1:8080"

    def graph_authorizer(self, claims):
        return claims

    def text_authorizer(self, identities):
        return [text_response(identity) for identity in identities]

    def invoke(self, claims=None, text_ids=None, graph_auth=None, text_auth=None,
               generator=None, **kwargs):
        claims = [self.claim] if claims is None else claims
        text_ids = [] if text_ids is None else text_ids
        graph_auth = self.graph_authorizer if graph_auth is None else graph_auth
        text_auth = self.text_authorizer if text_auth is None else text_auth
        wiki_base = kwargs.pop("wiki_base", self.wiki)
        generator = (lambda _: {"status": "answer", "answer": "Supported.",
                                "citation_ids": [self.citation_id(self.claim)]}) if generator is None else generator
        return assistant.compose_answer(
            claims, text_ids, graph_auth if claims else None, text_auth if text_ids else None,
            generator, wiki_base, graph_generation=self.graph_generation if claims else None,
            **kwargs,
        )

    def citation_id(self, claim, evidence=None):
        evidence = claim["evidence"][0] if evidence is None else evidence
        return assistant._citation_id(claim, evidence)

    def test_full_v1_graph_and_current_text_answer_cite_identity(self):
        identity = text_identity(self.claim)
        result = self.invoke(text_ids=[identity])
        self.assertEqual("ok", result["status"])
        self.assertEqual(self.graph_generation, result["graph_generation"])
        self.assertEqual(hashlib.sha256(assistant._json_bytes([identity])).hexdigest(),
                         result["text_source_digest"])
        self.assertEqual(self.claim["evidence"][0]["evidence_id"], result["citations"][0]["evidence_id"])
        self.assertEqual(11, result["citations"][0]["revision_id"])
        self.assertFalse(result["generation"]["semantic_citation_check"])
        self.assertEqual(self.claim["snapshot_hash"], result["citations"][0]["snapshot_hash"])
        self.assertEqual(self.wiki + "/w/index.php?oldid=11", result["citations"][0]["url"])

    def test_generator_can_select_citations_from_the_supplied_catalog(self):
        def generator(context):
            catalog = json.loads(context)["citation_catalog"]
            return {"status": "answer", "answer": "Supported by this source.",
                    "citation_ids": [catalog[0]["id"]]}
        self.assertEqual("ok", self.invoke(generator=generator)["status"])

    def test_direct_composition_freezes_question_before_authorizer_callbacks(self):
        question = {"intent": "lookup", "entities": [{"type": "Crop", "id": "Rice"}]}
        contexts = []

        def authorizer(claims):
            question["entities"][0]["id"] = "changed-by-callback"
            return claims

        def generator(context):
            contexts.append(json.loads(context))
            return {"status": "answer", "answer": "Synthetic.",
                    "citation_ids": [contexts[-1]["citation_catalog"][0]["id"]]}

        result = self.invoke(graph_auth=authorizer, generator=generator, question=question)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(contexts[0]["question"]["entities"][0]["id"], "Rice")
        self.assertEqual(question["entities"][0]["id"], "changed-by-callback")

    def test_projection_decimal_qualifiers_remain_serializable(self):
        claim = {**self.claim, "qualifiers": {"ratio": Decimal("1.25")}}
        self.assertEqual("ok", self.invoke(claims=[claim])["status"])

    def test_opposing_resistance_requires_abstention_but_two_memberships_do_not(self):
        def relationship(key, predicate, target):
            claim = make_claim(key, text=self.source)
            claim.pop("value")
            claim.update(claim_type="relationship", predicate=predicate,
                         object={"type": "Pest", "id": target})
            return claim
        resistant = relationship("resistant", "RESISTANT_TO", "rust")
        susceptible = relationship("susceptible", "SUSCEPTIBLE_TO", "rust")
        self.assertEqual("conflict", self.invoke(claims=[resistant, susceptible],
            generator=lambda _: self.fail("opposing evidence must not reach generation"))["status"])
        first = relationship("first-member", "HAS_VARIETY", "v1")
        second = relationship("second-member", "HAS_VARIETY", "v2")
        for claim in (first, second):
            claim["object"]["type"] = "Variety"
        self.assertEqual("ok", self.invoke(claims=[first, second], generator=lambda context: {
            "status": "answer", "answer": "Two varieties are listed.",
            "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]})["status"])

    def test_partially_scoped_opposition_blocks_generation_without_caller_flags(self):
        scope = {"season": "summer", "condition": "irrigated"}
        for opposing_scope in ({}, {"season": "summer"}, {"condition": "irrigated"}):
            resistant = make_response_claim("resistant", "RESISTANT_TO", scope)
            susceptible = make_response_claim("susceptible", "SUSCEPTIBLE_TO", opposing_scope)
            for claims in ([resistant, susceptible], [susceptible, resistant]):
                with self.subTest(opposing_scope=opposing_scope, first=claims[0]["predicate"]):
                    generated = []

                    def generator(context):
                        generated.append(context)
                        return {"status": "answer", "answer": "Synthetic answer.",
                                "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

                    result = self.invoke(claims=claims, generator=generator)
                    self.assertEqual(result["status"], "conflict")
                    self.assertIsNone(result["answer"])
                    self.assertEqual(result["claims"], claims)
                    self.assertEqual(generated, [])

    def test_different_scope_literals_do_not_prove_opposing_claims_disjoint(self):
        scope = {"season": "summer", "condition": "irrigated"}
        for opposing_scope in ({"season": "winter"}, {"condition": "dry"},
                               {"season": "winter", "condition": "irrigated"},
                               {"season": "summer", "condition": "dry"}):
            resistant = make_response_claim("resistant", "RESISTANT_TO", scope)
            susceptible = make_response_claim("susceptible", "SUSCEPTIBLE_TO", opposing_scope)
            for claims in ([resistant, susceptible], [susceptible, resistant]):
                with self.subTest(opposing_scope=opposing_scope, first=claims[0]["predicate"]):
                    generated = []

                    def generator(context):
                        generated.append(context)
                        return {"status": "answer", "answer": "Synthetic separate scopes.",
                                "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

                    result = self.invoke(claims=claims, generator=generator)
                    self.assertEqual(result["status"], "conflict")
                    self.assertIsNone(result["answer"])
                    self.assertEqual(result["claims"], claims)
                    self.assertEqual(generated, [])

    def test_different_scope_opponent_does_not_hide_another_overlapping_pair(self):
        claims = [
            make_response_claim("winter", "RESISTANT_TO", {"season": "winter"}),
            make_response_claim("summer", "RESISTANT_TO", {"season": "summer", "condition": "irrigated"}),
            make_response_claim("susceptible", "SUSCEPTIBLE_TO", {"season": "summer"}),
        ]
        for ordered in itertools.permutations(claims):
            with self.subTest(order=[claim["snapshot_id"] for claim in ordered]):
                generated = []

                def generator(context):
                    generated.append(context)
                    return {"status": "answer", "answer": "Synthetic answer.",
                            "citation_ids": [json.loads(context)["citation_catalog"][0]["id"]]}

                result = self.invoke(claims=list(ordered), generator=generator)
                self.assertEqual(result["status"], "conflict")
                self.assertEqual(generated, [])

    def test_text_only_answer_has_text_receipt_not_graph_generation(self):
        identity = text_identity(self.claim)
        citation = f"text:{identity['page_id']}:{identity['revision_id']}:{identity['content_hash']}"
        result = self.invoke(claims=[], text_ids=[identity], generator=lambda _: {
            "status": "answer", "answer": "Current source says this.", "citation_ids": [citation]
        })
        self.assertEqual("ok", result["status"])
        self.assertNotIn("graph_generation", result)
        self.assertTrue(assistant.HASH_RE.fullmatch(result["text_source_digest"]))

    def test_invalid_v1_claim_shapes_hashes_and_unicode_are_denied(self):
        variants = []
        bad = {**self.claim, "value": "bad", "extra": True}
        variants.append(bad)
        bad = {**self.claim, "evidence": [{**self.claim["evidence"][0], "content_hash": "x" * 64}]}
        variants.append(bad)
        bad = {**self.claim, "evidence": [{**self.claim["evidence"][0], "supporting_span": "⚡"}]}
        variants.append(bad)
        for claim in variants:
            result = self.invoke(claims=[claim])
            self.assertEqual("dependency_failure", result["status"])

    def test_same_identity_changed_body_and_authorizer_mutation_fail_closed(self):
        changed = {**self.claim, "value": "tampered"}
        result = self.invoke(graph_auth=lambda _: [changed])
        self.assertEqual("dependency_failure", result["status"])

        def mutates_input(items):
            items[0]["value"] = "mutated in place"
            return items

        result = self.invoke(graph_auth=mutates_input)
        self.assertEqual("dependency_failure", result["status"])
        self.assertEqual("140 days", self.claim["value"])

    def test_failed_text_hash_and_wrong_current_body_never_enter_context(self):
        identity = text_identity(self.claim)
        result = self.invoke(text_ids=[identity], text_auth=lambda ids: [text_response(ids[0], "stale")])
        self.assertEqual("dependency_failure", result["status"])
        result = self.invoke(claims=[], text_ids=[identity], text_auth=lambda _: [
            {**text_response(identity), "page_id": True}
        ])
        self.assertEqual("dependency_failure", result["status"])

    def test_supported_answer_requires_nonblank_answer_and_valid_citations(self):
        for response in (
            {"status": "answer", "answer": "  ", "citation_ids": [self.citation_id(self.claim)]},
            {"status": "answer", "answer": "yes", "citation_ids": []},
            {"status": "answer", "answer": "yes", "citation_ids": ["forged"]},
        ):
            result = self.invoke(generator=lambda _: response)
            self.assertEqual("dependency_failure", result["status"])
        abstain = self.invoke(generator=lambda _: {"status": "abstention", "answer": None,
                                                  "citation_ids": []})
        self.assertEqual("abstention", abstain["status"])

    def test_duplicate_source_is_packed_once_and_total_context_is_bounded(self):
        identity = text_identity(self.claim)
        captured = []

        def generator(context):
            captured.append(context)
            return {"status": "answer", "answer": "supported",
                    "citation_ids": [self.citation_id(self.claim)]}

        result = self.invoke(text_ids=[identity], generator=generator)
        self.assertEqual("ok", result["status"])
        context = json.loads(captured[0])
        self.assertEqual(1, len(context["sources"]))
        self.assertEqual(self.source, context["sources"][0]["text"])
        result = self.invoke(context_budget=True)
        self.assertEqual("dependency_failure", result["status"])
        result = self.invoke(context_budget=100)
        self.assertEqual("partial", result["status"])

    def test_competing_values_are_detected_without_caller_conflict_flag(self):
        other = make_claim("two", "180 days", self.source)
        result = self.invoke(claims=[self.claim, other],
                             generator=lambda _: self.fail("conflict must suppress generator"))
        self.assertEqual("conflict", result["status"])
        self.assertEqual(2, len(result["claims"]))

    def test_authorization_is_rechecked_before_and_after_generation(self):
        calls = 0

        def changes_before(claims):
            nonlocal calls
            calls += 1
            if calls == 2:
                return [{**claims[0], "value": "changed"}]
            return claims

        result = self.invoke(graph_auth=changes_before,
                             generator=lambda _: self.fail("must not generate stale context"))
        self.assertEqual("dependency_failure", result["status"])

        calls = 0

        def changes_after(claims):
            nonlocal calls
            calls += 1
            if calls == 3:
                return [{**claims[0], "value": "changed"}]
            return claims

        result = self.invoke(graph_auth=changes_after)
        self.assertEqual("dependency_failure", result["status"])
        self.assertIsNone(result["answer"])

    def test_current_text_body_change_before_or_after_generation_discards_result(self):
        identity = text_identity(self.claim)
        calls = 0

        def changes_before(ids):
            nonlocal calls
            calls += 1
            body = "Changed before generation." if calls == 2 else self.source
            return [text_response(ids[0], body)]

        result = self.invoke(claims=[], text_ids=[identity], text_auth=changes_before,
                             generator=lambda _: self.fail("must not generate stale text"))
        self.assertEqual("dependency_failure", result["status"])

        calls = 0

        def changes_after(ids):
            nonlocal calls
            calls += 1
            body = "Changed after generation." if calls == 3 else self.source
            return [text_response(ids[0], body)]

        result = self.invoke(claims=[], text_ids=[identity], text_auth=changes_after)
        self.assertEqual("dependency_failure", result["status"])
        self.assertIsNone(result["answer"])

    def test_incomplete_and_truncated_results_never_generate(self):
        generator = lambda _: self.fail("must not generate")
        self.assertEqual("partial", self.invoke(incomplete=True, generator=generator)["status"])
        self.assertEqual("partial", self.invoke(context_budget=100, generator=generator)["status"])

    def test_ingestion_request_and_ack_bind_body_identity_version_and_key(self):
        digest = hashlib.sha256(self.source.encode()).hexdigest()
        identity = {"page_id": 7, "revision_id": 11, "content_hash": digest,
                    "audience": "actor-only"}
        request = assistant.build_ingestion_request(identity, self.source, self.text_authorizer)
        self.assertNotIn("content", assistant.ingestion_ack(request, 503, None))
        self.assertEqual("failure", assistant.ingestion_ack(request, 503, None)["status"])
        good = {"version": assistant.CONTRACT_VERSION, "identity": identity,
                "idempotence_key": request["idempotence_key"], "status": "success"}
        self.assertEqual("success", assistant.ingestion_ack(request, 200, good)["status"])
        for response in (
            {**good, "version": "wrong"},
            {**good, "identity": {**identity, "revision_id": 12}},
            {**good, "identity": {**identity, "page_id": True}},
            {**good, "idempotence_key": "0" * 64},
        ):
            self.assertEqual("failure", assistant.ingestion_ack(request, 200, response)["status"])
        self.assertEqual("failure", assistant.ingestion_ack(
            {**request, "content": "tampered"}, 200, good)["status"])
        with self.assertRaises(ValueError):
            assistant.build_ingestion_request(identity, "altered body", self.text_authorizer)

    def test_ingestion_refuses_noncurrent_authority_or_non_actor_audience(self):
        identity = {"page_id": 7, "revision_id": 11,
                    "content_hash": hashlib.sha256(self.source.encode()).hexdigest(),
                    "audience": "actor-only"}
        with self.assertRaises(ValueError):
            assistant.build_ingestion_request(identity, self.source,
                                              lambda _: {"status": "dependency_failure", "text": []})
        with self.assertRaises(ValueError):
            assistant.build_ingestion_request({**identity, "audience": "public"}, self.source,
                                              self.text_authorizer)

    def test_loopback_base_rejects_userinfo_queries_fragments_and_host_suffix(self):
        for base in ("http://user@127.0.0.1", "http://127.0.0.1?x=1",
                      "http://127.0.0.1/#x", "http://127.0.0.1.attacker.test",
                      "http://127.0.0.1\nheader", "http://localhost:evil"):
            self.assertEqual("dependency_failure", self.invoke(wiki_base=base)["status"])


if __name__ == "__main__":
    unittest.main()
