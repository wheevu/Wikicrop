"""Contract and independent-oracle tests for connected synthetic questions."""
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
import reviewed_query

SCENARIOS = ROOT / "demo" / "synthetic" / "questions" / "scenarios.json"
REQUEST_SCHEMA = ROOT / "schema" / "reviewed-query.v2.schema.json"
CATALOG_SCHEMA = ROOT / "schema" / "synthetic-questions.v1.schema.json"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def claim(n, subject_type, subject, predicate, *, obj=None, value=None, qualifiers=None):
    page = 1200 + n
    text = f"Nguồn tổng hợp {n}: {subject} {predicate} {obj or value}."
    span = f"{subject} {predicate} {obj or value}"
    start = len(f"Nguồn tổng hợp {n}: ")
    ev = {"evidence_id": digest(f"evidence-{n}"), "page_id": page, "revision_id": page * 10,
          "content_hash": digest(text), "page_title": f"Synthetic source {n}",
          "extraction_method": "rules", "extractor_version": "synthetic-direct-fixture.v1",
          "location_type": "source_span", "source_offset_start": start,
          "source_offset_end": start + len(span), "supporting_span": span, "span_hash": digest(span)}
    result = {"snapshot_id": digest(f"snapshot-{n}"), "snapshot_hash": digest(f"hash-{n}"),
              "claim_id": digest(f"claim-{n}"), "review_version": 1,
              "claim_type": "relationship" if obj else "property",
              "subject": {"type": subject_type, "id": subject}, "predicate": predicate,
              "qualifiers": qualifiers or {}, "evidence": [ev]}
    if obj:
        typ = "Pest" if predicate in ("RESISTANT_TO", "SUSCEPTIBLE_TO") else "Variety"
        result["object"] = {"type": typ, "id": obj}
    else:
        result["value"] = value
    return result


def manifest(claims):
    ordered = sorted(claims, key=lambda c: c["snapshot_id"])
    return {"schema_version": "wikikg.reviewed-projection.v1", "snapshot_count": len(ordered),
            "source_watermark": digest("synthetic-question-watermark"), "claims": ordered}


class SyntheticQuestionCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(SCENARIOS.read_text())
        cls.scenarios = {s["id"]: s for s in cls.data["scenarios"]}
        cls.fixture_claims = {}
        cls.claim_sources = {}
        counter = 0
        for article in cls.data["fixtures"]["articles"]:
            page = 1200 + len(cls.claim_sources) + 1
            for spec in article["claims"]:
                counter += 1
                item = claim(counter, spec["subject"]["type"], spec["subject"]["id"],
                             spec["predicate"], value=spec.get("value"),
                             obj=spec.get("object", {}).get("id"), qualifiers=spec["qualifiers"])
                text = article["text"]
                span = spec["span"]
                start = len(text[:text.index(span)])
                item["evidence"] = [{"evidence_id":digest("evidence-"+spec["label"]),"page_id":page,
                    "revision_id":page*10,"content_hash":digest(text),"page_title":article["title"],
                    "extraction_method":"rules","extractor_version":"synthetic-direct-fixture.v1",
                    "location_type":"source_span","source_offset_start":start,"source_offset_end":start+len(span),
                    "supporting_span":span,"span_hash":digest(span)}]
                item["_fixture_label"] = spec["label"]
                cls.fixture_claims[spec["label"]] = item
                cls.claim_sources[spec["label"]] = article["key"]

    def test_scenarios_are_explicit_complete_and_schema_valid(self):
        expected_ids = {"supported-compound","split-roots","cross-crop","unresolved-parent",
                        "numeric-uncertain","scope-unknown","opposing-claims","missing-relationship",
                        "partial-manifest","rejected-claim","source-changed","permission-loss","graph-unavailable"}
        self.assertEqual(set(self.scenarios), expected_ids)
        schema = json.loads(REQUEST_SCHEMA.read_text())
        request_schema = {"$schema":schema["$schema"],"$defs":schema["$defs"],"$ref":"#/$defs/request"}
        for item in self.data["scenarios"]:
            with self.subTest(id=item["id"]):
                self.assertEqual(set(item), {"id","label","query","expected"} | ({"mutation"} if "mutation" in item else set()))
                self.assertTrue(item["label"])
                jsonschema.validate(item["query"], request_schema)
                oracle = item["expected"]
                self.assertEqual(set(oracle), {"exit","status","query_state","reason","required_claim_labels","forbidden_claim_labels"})
                self.assertIsInstance(oracle["required_claim_labels"], list)
                self.assertIsInstance(oracle["forbidden_claim_labels"], list)

    def test_independent_static_claim_oracles(self):
        expected = {"supported-compound":("ok",None),"split-roots":("unsupported","relationship_missing"),
                    "cross-crop":("unsupported","relationship_missing"),"unresolved-parent":("unsupported","unresolved_identity"),
                    "numeric-uncertain":("unsupported","numeric_uncertain"),"scope-unknown":("unsupported","scope_unknown"),
                    "opposing-claims":("unsupported","opposing_assertions"),"missing-relationship":("unsupported","relationship_missing")}
        by_label=self.fixture_claims
        for key,(status,reason) in expected.items():
            item=self.scenarios[key]
            with self.subTest(id=key):
                # Explicit fixture-level truth table, deliberately independent of
                # the interpreter: same-root conjunction, scoped edge, membership.
                self.assertEqual(item["expected"]["status"], status)
                self.assertEqual(item["expected"]["reason"], reason)
                labels={x["label"] for a in self.data["fixtures"]["articles"] for x in a["claims"]}
                self.assertTrue(set(item["expected"]["required_claim_labels"]) <= labels)
                chosen=[c for c in by_label.values() if self.claim_sources[c["_fixture_label"]] not in ("changed",)]
                # Distinct requests share one complete graph; source pages enforce root scope.
                result=reviewed_query.query(manifest([{k:v for k,v in c.items() if not k.startswith("_")} for c in chosen]),
                                            item["query"],lambda current:copy.deepcopy(current))
                self.assertEqual(result["status"],status)
                self.assertIn(reason,{r["code"] for r in result.get("reasons",[])}) if reason else self.assertTrue(result["complete"])
                returned={c["claim"]["snapshot_id"] for group in result["results"] for c in group["claims"]}
                label_ids={label:c["snapshot_id"] for label,c in by_label.items()}
                for required in item["expected"]["required_claim_labels"]:
                    self.assertIn(label_ids[required],returned)
                for forbidden in item["expected"]["forbidden_claim_labels"]:
                    if forbidden in label_ids: self.assertNotIn(label_ids[forbidden],returned)
                if key == "supported-compound":
                    branches=result["results"][0]["branch_claim_ids"]
                    self.assertEqual(len(branches),2)
                    self.assertIn(label_ids["alpha-parent"],branches[0])
                    self.assertIn(label_ids["alpha-resistance"],branches[1])
                self.assertEqual(item["expected"]["exit"],0 if status == "ok" else 3)

    def test_public_fixture_budget_deduplicates_sources_and_keeps_requests_distinct(self):
        fixture=self.data["fixtures"]
        articles=fixture["articles"]
        labels=[claim["label"] for article in articles for claim in article["claims"]]
        self.assertEqual(len(articles),12)
        self.assertEqual(len(labels),37)
        self.assertEqual(len(set(labels)),37)
        self.assertEqual(fixture["counts"],{"direct_claims":37,"unique_sources":12,
                         "evidence_entries":37,"frozen_claims":9,"generation_claims":46})
        source_keys={self.claim_sources[label] for label in labels}
        self.assertEqual(len(source_keys),12)
        static=[self.scenarios[key]["query"] for key in
                ("supported-compound","split-roots","cross-crop","unresolved-parent",
                 "numeric-uncertain","scope-unknown","opposing-claims","missing-relationship")]
        self.assertGreaterEqual(len({json.dumps(query,sort_keys=True) for query in static}),8)

    def test_catalog_schema_bounds_catalog_to_real_static_questions(self):
        catalog_schema=json.loads(CATALOG_SCHEMA.read_text())
        questions=[{"id":s["id"],"label":s["label"],"query":s["query"]}
                   for s in self.data["scenarios"] if "mutation" not in s]
        questions.append({"id":"supported-review-sensitive","label":"Review-sensitive synthetic compound example",
                          "query":self.scenarios["supported-compound"]["query"]})
        document={"version":"wikikg.synthetic-questions.v1","questions":questions}
        jsonschema.validate(document,catalog_schema)
        self.assertLessEqual(len(questions),20)
        self.assertFalse(any(q["id"] in {"graph-unavailable","permission-loss","source-changed"} for q in questions))

    def test_state_scenarios_use_identical_real_request_and_named_mutations(self):
        ids=("partial-manifest","rejected-claim","source-changed","permission-loss","graph-unavailable")
        query=self.scenarios[ids[0]]["query"]
        for scenario_id in ids:
            item=self.scenarios[scenario_id]
            if scenario_id != "source-changed":
                self.assertEqual(item["query"],query)
            self.assertEqual(item["mutation"],scenario_id)
        changed=self.scenarios["source-changed"]
        changed_claims=[self.fixture_claims[label] for label in ("source-change-membership","source-change-target")]
        canonical=[{key:value for key,value in claim.items() if not key.startswith("_")} for claim in changed_claims]
        result=reviewed_query.query(manifest(canonical),changed["query"],lambda current:copy.deepcopy(current))
        self.assertEqual(result["status"],"ok")
        self.assertIn(changed_claims[1]["snapshot_id"],result["results"][0]["eligible_claim_ids"])
        self.assertEqual({self.scenarios[key]["expected"]["reason"] for key in
                          ("partial-manifest","rejected-claim","source-changed","permission-loss","graph-unavailable")},
                         {"graph_generation_changed","export_watermark_changed","source_revision_changed",
                          "offline_authorization_refused","graph_unavailable"})

    def test_cli_exit_contract_partitions_supported_blocked_and_operational(self):
        zero={"supported-compound"}
        blocked={"split-roots","cross-crop","unresolved-parent","numeric-uncertain",
                 "scope-unknown","opposing-claims","missing-relationship"}
        operational={"partial-manifest","rejected-claim","source-changed","permission-loss","graph-unavailable"}
        self.assertEqual({key for key,item in self.scenarios.items() if item["expected"]["exit"] == 0},zero)
        self.assertEqual({key for key,item in self.scenarios.items() if item["expected"]["exit"] == 3},blocked)
        self.assertEqual({key for key,item in self.scenarios.items() if item["expected"]["exit"] == 2},operational)
        for key in zero | blocked | operational:
            self.assertEqual(self.scenarios[key]["expected"]["exit"],
                             0 if key in zero else 3 if key in blocked else 2)

    def test_connected_seeder_preserves_frozen_marker_and_uses_separate_ready_marker(self):
        source=(ROOT / "demo" / "synthetic" / "questions" / "fixture.php").read_text()
        self.assertIn("trim( file_get_contents( $root . '/synthetic-marker' ) ) !== 'seeded'",source)
        self.assertNotIn("file_put_contents( $root . '/synthetic-marker'",source)
        self.assertIn("$root . '/connected/fixture-ready'",source)


if __name__ == "__main__":
    unittest.main()
