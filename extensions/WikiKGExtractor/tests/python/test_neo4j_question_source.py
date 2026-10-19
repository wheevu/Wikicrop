"""Contract tests for the read-only transaction and bounded graph adapter."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import neo4j_question_source as source
import reviewed_projection as projection
import hashlib


class Result:
    def __init__(self, rows): self.rows = rows
    def __iter__(self): return iter(self.rows)


class Transaction:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def run(self, query, **params):
        self.calls.append((query, params))
        return Result(self.rows.get(query, []))
    def commit(self): pass
    def rollback(self): pass


class Session:
    def __init__(self, tx): self.tx = tx
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def begin_transaction(self, **kwargs): self.timeout = kwargs["timeout"]; return self.tx


class Driver:
    def __init__(self, tx): self.tx = tx
    def session(self, **kwargs):
        self.access = kwargs["default_access_mode"]
        return Session(self.tx)


class Neo4jQuestionSourceTest(unittest.TestCase):
    def fixture(self):
        digest=lambda value: hashlib.sha256(value.encode()).hexdigest()
        claim={"snapshot_id":digest("snap"),"snapshot_hash":digest("hash"),"claim_id":digest("claim"),
          "review_version":1,"claim_type":"property","subject":{"type":"Crop","id":"c"},
          "predicate":"height","value":"80 cm","qualifiers":{},"evidence":[
          {"evidence_id":digest("e"),"page_id":1,"revision_id":2,"content_hash":digest("src"),
           "page_title":"source","extraction_method":"rules","extractor_version":"test","location_type":"page"}]}
        doc={"schema_version":"wikikg.reviewed-projection.v1","snapshot_count":1,
             "source_watermark":digest("watermark"),"claims":[claim]}
        generation=projection.generation_id(doc); ev=claim["evidence"][0]
        meta={"generation":generation,"epoch":2,"active_schema":doc["schema_version"],
          "active_watermark":doc["source_watermark"],"state":"ready","schema_version":doc["schema_version"],
          "source_watermark":doc["source_watermark"],"snapshot_count":1,"claim_count":1,"evidence_count":1}
        row={"snapshot_id":claim["snapshot_id"],"claim_json":projection.canonical_json(claim),
          "claim_id":claim["claim_id"],"snapshot_hash":claim["snapshot_hash"],"review_version":1,
          "claim_type":"property","predicate":"height","qualifiers_json":"{}","value":"80 cm",
          "subject_links":1,"subject_types":["Crop"],"subject_ids":["c"],"object_links":0,
          "object_types":[],"object_ids":[],"evidence_links":1,"evidence_ids":[ev["evidence_id"]],
          "evidence_snapshots":[claim["snapshot_id"]]}
        evidence={"snapshot_id":claim["snapshot_id"],"evidence_id":ev["evidence_id"],
          "evidence_json":projection.canonical_json(ev),**ev,"location":None,
          "source_offset_start":None,"source_offset_end":None,"span_hash":None}
        rows={source.ACTIVE_QUERY:[meta],source.CLAIMS_QUERY:[row],source.ENTITIES_QUERY:[{"entity_type":"Crop","entity_id":"c"}],source.EVIDENCE_QUERY:[evidence]}
        rows[source.CLAIMS_QUERY][0]["generation"] = generation
        rows[source.ENTITIES_QUERY][0]["generation"] = generation
        rows[source.EVIDENCE_QUERY][0]["generation"] = generation
        rows[source.LINKS_QUERY] = [
            {"source_generation":generation,"source_snapshot":claim["snapshot_id"],
             "source_claim":True,"source_entity":False,"source_evidence":False,
             "source_entity_type":None,"source_entity_id":None,
             "target_generation":generation,"target_snapshot":None,
             "target_claim":False,"target_entity":True,"target_evidence":False,
             "target_entity_type":"Crop","target_entity_id":"c",
             "target_evidence_id":None,"link_type":"WIKIKG_REVIEWED_SUBJECT"},
            {"source_generation":generation,"source_snapshot":claim["snapshot_id"],
             "source_claim":True,"source_entity":False,"source_evidence":False,
             "source_entity_type":None,"source_entity_id":None,
             "target_generation":generation,"target_snapshot":claim["snapshot_id"],
             "target_claim":False,"target_entity":False,"target_evidence":True,
             "target_entity_type":None,"target_entity_id":None,
             "target_evidence_id":ev["evidence_id"],"link_type":"WIKIKG_REVIEWED_EVIDENCE"},
        ]
        return doc,rows,claim

    def test_valid_graph_reconstructs_claim_and_uses_explicit_transaction_deadline(self):
        doc,rows,claim=self.fixture(); tx=Transaction(rows); driver=Driver(tx)
        loaded,generation,epoch=source.read_question_document(doc,driver,deadline=10**12)
        self.assertEqual(loaded["claims"],[claim]); self.assertEqual(epoch,2)
        expected = {source.CLAIMS_QUERY:101,source.EVIDENCE_QUERY:51,
                    source.ENTITIES_QUERY:201,source.LINKS_QUERY:251}
        for query, take in expected.items():
            params=next(params for statement,params in tx.calls if statement==query)
            self.assertEqual(params["take"],take)

    def test_rejects_wrong_epoch_metadata_and_corrupt_claim_evidence_or_links(self):
        for mutate in (
          lambda r:r[source.ACTIVE_QUERY][0].update(epoch=0),
          lambda r:r[source.ACTIVE_QUERY][0].update(source_watermark="wrong"),
          lambda r:r[source.CLAIMS_QUERY][0].update(claim_json="{}"),
          lambda r:r[source.LINKS_QUERY][0].update(target_entity_id="wrong"),
          lambda r:r[source.LINKS_QUERY].pop(),
          lambda r:r[source.EVIDENCE_QUERY][0].update(page_id=999),
        ):
            doc,rows,_=self.fixture(); mutate(rows)
            with self.subTest(mutate=mutate), self.assertRaises(source.GraphQuestionError):
                source._read_generation(Transaction(rows),doc,
                                        projection.generation_id(doc),10**12)

    def test_rejects_missing_extra_duplicate_entities_and_evidence(self):
        for table, change in ((source.CLAIMS_QUERY, lambda r:r.pop()),
                              (source.ENTITIES_QUERY, lambda r:r.append(dict(r[0]))),
                              (source.EVIDENCE_QUERY, lambda r:r.pop())):
            doc,rows,_=self.fixture(); change(rows[table])
            with self.subTest(table=table), self.assertRaises(source.GraphQuestionError):
                source._read_generation(Transaction(rows),doc,
                                        projection.generation_id(doc),10**12)

    def test_rejects_more_than_expected_links_wrong_generation_and_oversize_server_value(self):
        mutations = (
            lambda rows: rows[source.LINKS_QUERY].append(dict(rows[source.LINKS_QUERY][0])),
            lambda rows: rows[source.LINKS_QUERY][0].update(target_generation="another-generation"),
            lambda rows: rows[source.LINKS_QUERY][0].update(target_evidence=True,target_entity=False),
            lambda rows: rows[source.CLAIMS_QUERY][0].update(claim_json="x" * (source.STRING_LIMIT + 1)),
        )
        for mutate in mutations:
            document, rows, _ = self.fixture()
            mutate(rows)
            with self.subTest(mutate=mutate), self.assertRaises(source.GraphQuestionError):
                source._read_generation(Transaction(rows), document,
                                        projection.generation_id(document), 10**12)

    def test_bounds_are_applied_before_session_creation(self):
        document, _, _ = self.fixture()
        document["claims"] *= source.MAX_CLAIMS + 1
        driver = mock.Mock()
        with self.assertRaises(source.GraphQuestionError) as error:
            source.read_question_document(document, driver, deadline=10**12)
        self.assertEqual(error.exception.code, "graph_limit")
        driver.session.assert_not_called()

    def test_final_active_check_rejects_stale_generation_and_epoch(self):
        doc,_,_=self.fixture()
        tx=Transaction({source.ACTIVE_QUERY:[{"generation":"other","epoch":3,"state":"ready"}]})
        with self.assertRaises(source.GraphQuestionError) as error:
            source.recheck_active("expected",2,Driver(tx),deadline=10**12)
        self.assertEqual(error.exception.code,"graph_generation_changed")
    def test_result_bound_rejects_oversized_result(self):
        with self.assertRaises(source.GraphQuestionError):
            source._read_rows(Transaction({source.CLAIMS_QUERY:[{},{}]}),
                              source.CLAIMS_QUERY, {}, 1, 10**12)

    def test_string_payload_bound_is_enforced(self):
        with self.assertRaises(source.GraphQuestionError) as error:
            source._read_rows(Transaction({source.CLAIMS_QUERY:[{"payload":"x"*(source.MAX_ROW_TEXT+1)}]}),
                              source.CLAIMS_QUERY, {}, 1, 10**12)
        self.assertEqual(error.exception.code, "graph_limit")

    def test_cumulative_payload_bound_is_enforced(self):
        rows = [{"payload": "x" * (source.MAX_ROW_TEXT - 100)}] * 5
        with self.assertRaises(source.GraphQuestionError) as error:
            source._read_rows(Transaction({source.CLAIMS_QUERY:rows}), source.CLAIMS_QUERY,
                              {}, 5, 10**12)
        self.assertEqual(error.exception.code, "graph_limit")

    def test_transaction_is_explicit_read_and_fixed_parameterized_query(self):
        doc,_,_=self.fixture()
        tx=Transaction({source.ACTIVE_QUERY: []})
        driver=Driver(tx)
        with self.assertRaises(source.GraphQuestionError):
            source.read_question_document(doc, driver, deadline=10**12)
        self.assertEqual(driver.access, "READ")
        self.assertEqual(tx.calls[0][0], source.ACTIVE_QUERY)
        self.assertEqual(tx.calls[0][1], {"active_key": "wikikg.reviewed-projection.active.v1",
                                          "string_limit": source.STRING_LIMIT})

    def test_deadline_expires_before_row_delivery(self):
        with mock.patch.object(source.time, "monotonic", return_value=2):
            with self.assertRaises(source.GraphQuestionError) as error:
                source._read_rows(Transaction({source.CLAIMS_QUERY:[{}]}),
                                  source.CLAIMS_QUERY, {}, 1, 1)
        self.assertEqual(error.exception.code, "graph_timeout")


if __name__ == "__main__": unittest.main()
