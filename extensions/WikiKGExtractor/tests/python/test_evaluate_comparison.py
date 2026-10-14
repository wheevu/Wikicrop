import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "evaluate_comparison.py"
spec = importlib.util.spec_from_file_location("evaluate_comparison", SCRIPT)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.q = [{"question_id":"q1", "reference":"real reference", "reference_status":"confirmed_independent", "split":"heldout", "split_hand_checked":True, "prior_exposure":"seen", "retrieval_annotation_status":"independent_hand_checked", "citation_annotation_status":"independent_hand_checked"}]
        self.b = {"source_snapshot":"snapshot", "source_revision_digest":"a"*64, "model":"model", "prompt_hash":"b"*64, "decoding":{}, "context_budget":10, "audience":"reviewer", "common_controls":{}, "artifact_hashes":{k:"c"*64 for k in ev.ARM_HASH_KEYS}, "results":[{"question_id":"q1", "extraction_valid":True, "retrieval_relevant":False, "human_correct":True, "citation_supported":False, "abstained":False, "runtime_seconds":1.0}]}
        self.a = {**self.b, "artifact_hashes":{k:"d"*64 for k in ev.ARM_HASH_KEYS}, "results":[{**self.b["results"][0], "human_correct":False, "retrieval_relevant":True}]}
        self.protocol = {"human_approved_frozen":True,"question_set_sha256":ev.question_hash(self.q),"hand_checked_split_hash":ev.reference_split_hash(self.q),"shared_controls":{k:self.b[k] for k in ev.CONTROL_KEYS},"unanswered_counts_wrong":True}
        self.protocol["protocol_hash"] = ev.sha(self.protocol)

    def test_independent_metrics_split_and_pair_data(self):
        r=ev.evaluate(self.q,self.b,self.a,self.protocol)
        self.assertEqual(r["status"],"ready")
        self.assertEqual(r["arms"]["before"]["answer_correctness"]["denominator"],1)
        self.assertEqual(r["arms"]["before"]["retrieval_relevance"]["no"],1)
        self.assertEqual(r["arms"]["after"]["answer_correctness"]["wrong"],1)
        self.assertEqual(r["counts"]["prior_exposure_recorded"],1)

    def test_unconfirmed_protocol_zero_scientific_denominators(self):
        r=ev.evaluate(self.q,self.b,self.a,{})
        self.assertEqual(r["status"],"engineering_only")
        self.assertEqual(r["arms"]["before"]["answer_correctness"]["denominator"],0)
        self.assertEqual(r["arms"]["before"]["retrieval_relevance"]["adjudicated"],0)

    def test_unconfirmed_no_reference_stays_engineering_only(self):
        questions = [{**self.q[0], "reference": "Dữ liệu chờ cập nhật."}]
        result = ev.evaluate(questions, self.b, self.a, {})
        self.assertEqual("engineering_only", result["status"])
        self.assertEqual(0, result["counts"]["eligible_independent_references"])

    def test_shared_controls_distinguish_boolean_from_integer(self):
        self.b["context_budget"] = True
        self.a["context_budget"] = 1
        result = ev.evaluate(self.q, self.b, self.a, self.protocol)
        self.assertEqual("invalid", result["status"])
        self.assertEqual(0, result["arms"]["after"]["answer_correctness"]["denominator"])

    def test_missing_verdict_is_excluded_from_both_paired_arms(self):
        self.b["results"][0]["human_correct"] = None
        result = ev.evaluate(self.q, self.b, self.a, self.protocol)
        for arm in result["arms"].values():
            self.assertEqual(0, arm["answer_correctness"]["denominator"])
        self.assertEqual(ev.sha(self.b), result["receipts"]["before_sha256"])

    def test_retrieval_and_citation_use_the_same_paired_denominator(self):
        self.a["results"][0]["retrieval_relevant"] = None
        self.b["results"][0]["citation_supported"] = None
        result = ev.evaluate(self.q, self.b, self.a, self.protocol)
        for arm in result["arms"].values():
            self.assertEqual(0, arm["retrieval_relevance"]["adjudicated"])
            self.assertEqual(0, arm["citation_support"]["adjudicated"])
        self.assertEqual({"development": 0, "heldout": 1}, result["counts"]["reference_splits"])
        self.assertEqual(1, result["counts"]["prior_exposure"]["seen"])

    def test_protocol_must_bind_exact_questions_and_controls(self):
        protocol={**self.protocol,"question_set_sha256":"0"*64}
        protocol["protocol_hash"]=ev.sha({k:v for k,v in protocol.items() if k!="protocol_hash"})
        r=ev.evaluate(self.q,self.b,self.a,protocol)
        self.assertEqual(r["status"],"engineering_only")
        self.assertEqual(r["arms"]["before"]["answer_correctness"]["denominator"],0)

    def test_missing_exposure_disclosure_blocks_scoring_even_with_a_bound_protocol(self):
        del self.q[0]["prior_exposure"]
        self.protocol["question_set_sha256"] = ev.question_hash(self.q)
        self.protocol["protocol_hash"] = ev.sha({key: value for key, value in self.protocol.items()
                                                if key != "protocol_hash"})
        result = ev.evaluate(self.q, self.b, self.a, self.protocol)
        self.assertEqual("invalid", result["status"])
        self.assertIn("missing_or_invalid_prior_exposure", result["issues"])
        self.assertEqual(0, result["arms"]["before"]["answer_correctness"]["denominator"])

    def test_placeholder_null_and_disputed_are_not_references(self):
        for value,status in ((" Dữ liệu   chờ cập nhật ","confirmed_independent"),(None,"confirmed_independent"),("real","disputed")):
            q=[{**self.q[0],"reference":value,"reference_status":status}]
            self.assertEqual(ev.evaluate(q,self.b,self.a,{})["counts"]["eligible_independent_references"],0)

    def test_no_eligible_references_is_engineering_only(self):
        q=[{**self.q[0],"reference_status":"candidate"}]
        protocol={**self.protocol,"question_set_sha256":ev.question_hash(q),"hand_checked_split_hash":ev.reference_split_hash(q)}
        protocol["protocol_hash"]=ev.sha({k:v for k,v in protocol.items() if k!="protocol_hash"})
        r=ev.evaluate(q,self.b,self.a,protocol)
        self.assertEqual(r["status"],"engineering_only")
        self.assertEqual(r["arms"]["after"]["answer_correctness"]["denominator"],0)

    def test_missing_independent_annotation_excludes_retrieval_and_citation(self):
        q=[{**self.q[0],"retrieval_annotation_status":"candidate","citation_annotation_status":"disputed"}]
        protocol={**self.protocol,"question_set_sha256":ev.question_hash(q),"hand_checked_split_hash":ev.reference_split_hash(q)}
        protocol["protocol_hash"]=ev.sha({k:v for k,v in protocol.items() if k!="protocol_hash"})
        r=ev.evaluate(q,self.b,self.a,protocol)
        self.assertEqual(r["arms"]["before"]["retrieval_relevance"]["adjudicated"],0)
        self.assertEqual(r["arms"]["before"]["citation_support"]["adjudicated"],0)

    def test_mismatched_frozen_controls_and_rows_invalid(self):
        after={**self.a,"context_budget":11,"results":[]}
        r=ev.evaluate(self.q,self.b,after,self.protocol)
        self.assertEqual(r["status"],"invalid")
        self.assertEqual(r["arms"]["before"]["answer_correctness"]["denominator"],0)
        self.assertIn("control_mismatch_context_budget",r["issues"])
        self.assertIn("missing_or_extra_paired_questions",r["issues"])

    def test_integer_verdict_is_invalid_not_bool(self):
        self.b["results"][0]["human_correct"]=1
        r=ev.evaluate(self.q,self.b,self.a,self.protocol)
        self.assertEqual(r["arms"]["before"]["answer_correctness"]["denominator"],0)

    def test_invalid_metric_and_nonfinite_runtime(self):
        self.b["results"][0]["runtime_seconds"]=float("nan")
        r=ev.evaluate(self.q,self.b,self.a,self.protocol)
        self.assertIn("invalid_runtime_seconds",r["issues"])
        self.assertEqual(r["status"],"invalid")

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"bad.json"; p.write_text('{"a":1,"a":2}')
            with self.assertRaisesRegex(ValueError,"duplicate_json_key"): ev.load(p)
            p.write_text('{"x":NaN}')
            with self.assertRaisesRegex(ValueError,"nonfinite_json_number"): ev.load(p)

    def test_csv_rows_remain_strings_and_not_boolean_scores(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"rows.csv"; p.write_text("question_id,human_correct\nq1,true\n")
            rows=ev.load(p)
            self.assertEqual(rows[0]["human_correct"],"true")

    def test_csv_reference_split_boolean_is_explicit_and_headers_are_unique(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"questions.csv"
            path.write_text("question_id,split_hand_checked\nq1,true\n")
            self.assertIs(ev.load(path)[0]["split_hand_checked"], True)
            path.write_text("question_id,question_id\nq1,q2\n")
            with self.assertRaises(ValueError):
                ev.load(path)

    def test_private_input_mode_and_normalized_repository_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d).resolve()
            parent.chmod(0o700)
            web = parent/"web"
            web.mkdir()
            data = parent/"questions.json"
            data.write_text("[]")
            data.chmod(0o600)
            self.assertEqual(data, ev.private_input(data, web))
            data.chmod(0o644)
            with self.assertRaises(ValueError):
                ev.private_input(data, web)
            with self.assertRaises(ValueError):
                ev.safe_output(parent/".."/parent.name/"web"/"new", web)

    def test_private_output_parent_boundary_and_existing_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            parent=Path(d).resolve()/"private"; parent.mkdir(mode=0o700); parent.chmod(0o700)
            web=(Path(d).resolve()/"web"); web.mkdir()
            ev.safe_output(parent/"new",web)
            with self.assertRaises(ValueError): ev.safe_output(web/"new",web)
            existing=parent/"exists"; existing.mkdir()
            with self.assertRaises(ValueError): ev.safe_output(existing,web)
            with self.assertRaises(ValueError): ev.safe_output(ev.ROOT/"bad",web)


if __name__ == "__main__": unittest.main()
