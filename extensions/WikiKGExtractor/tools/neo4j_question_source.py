"""Bounded read-only source for connected synthetic question execution."""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

import reviewed_projection as projection

MAX_CLAIMS = 100
MAX_EVIDENCE = 50
MAX_CLAIM_ROWS = MAX_CLAIMS + 1
MAX_EVIDENCE_ROWS = MAX_EVIDENCE + 1
MAX_ENTITY_ROWS = 201
MAX_LINK_ROWS = 251
MAX_ROW_TEXT = 2_000_000
MAX_TOTAL_TEXT = 8_000_000
QUERY_TIMEOUT = 8.0
STRING_LIMIT = MAX_ROW_TEXT

ACTIVE_QUERY = """
MATCH (a:WikiKGReviewedProjectionActive {key: $active_key})
MATCH (g:WikiKGReviewedProjectionGeneration {generation: a.generation})
RETURN substring(toString(a.generation), 0, $string_limit + 1) AS generation,
       a.epoch AS epoch,
       substring(toString(a.schema_version), 0, $string_limit + 1) AS active_schema,
       substring(toString(a.source_watermark), 0, $string_limit + 1) AS active_watermark,
       substring(toString(g.state), 0, $string_limit + 1) AS state,
       substring(toString(g.schema_version), 0, $string_limit + 1) AS schema_version,
       substring(toString(g.source_watermark), 0, $string_limit + 1) AS source_watermark,
       g.snapshot_count AS snapshot_count,
       g.claim_count AS claim_count, g.evidence_count AS evidence_count
"""

# The expressions cap text in Neo4j before it crosses the driver boundary.
CLAIMS_QUERY = """
MATCH (c:WikiKGReviewedProjectionClaim {generation: $generation})
RETURN substring(toString(c.generation), 0, $string_limit + 1) AS generation,
       substring(toString(c.snapshot_id), 0, $string_limit + 1) AS snapshot_id,
       substring(toString(c.claim_json), 0, $string_limit + 1) AS claim_json,
       substring(toString(c.claim_id), 0, $string_limit + 1) AS claim_id,
       substring(toString(c.snapshot_hash), 0, $string_limit + 1) AS snapshot_hash,
       c.review_version AS review_version,
       substring(toString(c.claim_type), 0, $string_limit + 1) AS claim_type,
       substring(toString(c.predicate), 0, $string_limit + 1) AS predicate,
       substring(toString(c.qualifiers_json), 0, $string_limit + 1) AS qualifiers_json,
       CASE WHEN c.value IS NULL THEN NULL ELSE substring(toString(c.value), 0, $string_limit + 1) END AS value
ORDER BY c.snapshot_id LIMIT $take
"""

ENTITIES_QUERY = """
MATCH (e:WikiKGReviewedProjectionEntity {generation: $generation})
RETURN substring(toString(e.generation), 0, $string_limit + 1) AS generation,
       substring(toString(e.entity_type), 0, $string_limit + 1) AS entity_type,
       substring(toString(e.entity_id), 0, $string_limit + 1) AS entity_id
LIMIT $take
"""

EVIDENCE_QUERY = """
MATCH (e:WikiKGReviewedProjectionEvidence {generation: $generation})
RETURN substring(toString(e.generation), 0, $string_limit + 1) AS generation,
       substring(toString(e.snapshot_id), 0, $string_limit + 1) AS snapshot_id,
       substring(toString(e.evidence_id), 0, $string_limit + 1) AS evidence_id,
       substring(toString(e.evidence_json), 0, $string_limit + 1) AS evidence_json,
       e.page_id AS page_id, e.revision_id AS revision_id,
       substring(toString(e.content_hash), 0, $string_limit + 1) AS content_hash,
       substring(toString(e.page_title), 0, $string_limit + 1) AS page_title,
       substring(toString(e.extraction_method), 0, $string_limit + 1) AS extraction_method,
       substring(toString(e.extractor_version), 0, $string_limit + 1) AS extractor_version,
       substring(toString(e.location_type), 0, $string_limit + 1) AS location_type,
       CASE WHEN properties(e)['location'] IS NULL THEN NULL
            ELSE substring(toString(properties(e)['location']), 0, $string_limit + 1) END AS location,
       e.source_offset_start AS source_offset_start,
       e.source_offset_end AS source_offset_end,
       substring(toString(e.span_hash), 0, $string_limit + 1) AS span_hash
ORDER BY e.snapshot_id, e.evidence_id LIMIT $take
"""

LINKS_QUERY = """
MATCH (s)-[r]->(t)
WHERE type(r) IN ['WIKIKG_REVIEWED_SUBJECT', 'WIKIKG_REVIEWED_OBJECT', 'WIKIKG_REVIEWED_EVIDENCE']
  AND ((s:WikiKGReviewedProjectionClaim AND s.generation = $generation)
       OR (t:WikiKGReviewedProjectionClaim AND t.generation = $generation))
RETURN substring(toString(s.generation), 0, $string_limit + 1) AS source_generation,
       substring(toString(s.snapshot_id), 0, $string_limit + 1) AS source_snapshot,
       s:WikiKGReviewedProjectionClaim AS source_claim,
       s:WikiKGReviewedProjectionEntity AS source_entity,
       s:WikiKGReviewedProjectionEvidence AS source_evidence,
       substring(toString(s.entity_type), 0, $string_limit + 1) AS source_entity_type,
       substring(toString(s.entity_id), 0, $string_limit + 1) AS source_entity_id,
       substring(toString(t.generation), 0, $string_limit + 1) AS target_generation,
       substring(toString(t.snapshot_id), 0, $string_limit + 1) AS target_snapshot,
       t:WikiKGReviewedProjectionClaim AS target_claim,
       t:WikiKGReviewedProjectionEntity AS target_entity,
       t:WikiKGReviewedProjectionEvidence AS target_evidence,
       substring(toString(t.entity_type), 0, $string_limit + 1) AS target_entity_type,
       substring(toString(t.entity_id), 0, $string_limit + 1) AS target_entity_id,
       substring(toString(t.evidence_id), 0, $string_limit + 1) AS target_evidence_id,
       type(r) AS link_type
LIMIT $take
"""


class GraphQuestionError(ValueError):
    """A safe, stable graph dependency failure."""

    def __init__(self, code: str = "graph_corrupt"):
        self.code = code
        super().__init__(code)


def _expected_counts(document: dict[str, Any]) -> tuple[int, int]:
    """Check immutable export bounds before opening a Neo4j session."""
    claims = document.get("claims")
    if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
        raise GraphQuestionError("graph_limit")
    evidence_count = 0
    for claim in claims:
        evidence = claim.get("evidence") if isinstance(claim, dict) else None
        if not isinstance(evidence, list):
            raise GraphQuestionError("graph_corrupt")
        evidence_count += len(evidence)
        if evidence_count > MAX_EVIDENCE:
            raise GraphQuestionError("graph_limit")
    return len(claims), evidence_count


def _read_rows(tx: Any, query: str, params: dict[str, Any], row_limit: int,
               deadline: float) -> list[dict[str, Any]]:
    result = tx.run(query, **params)
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for record in result:
        if time.monotonic() >= deadline:
            raise GraphQuestionError("graph_timeout")
        if len(rows) >= row_limit:
            raise GraphQuestionError("graph_limit")
        row = dict(record)
        for value in row.values():
            if isinstance(value, str):
                size = len(value.encode("utf-8"))
                if size > MAX_ROW_TEXT:
                    raise GraphQuestionError("graph_limit")
                total_bytes += size
        if total_bytes > MAX_TOTAL_TEXT:
            raise GraphQuestionError("graph_limit")
        rows.append(row)
    return rows


def _single_row(tx: Any, query: str, params: dict[str, Any], deadline: float) -> dict[str, Any]:
    rows = _read_rows(tx, query, params, 1, deadline)
    if len(rows) != 1:
        raise GraphQuestionError()
    return rows[0]


def _check_active(document: dict[str, Any], generation: str, meta: dict[str, Any]) -> int:
    epoch = meta.get("epoch")
    if (meta.get("generation") != generation
            or meta.get("state") != "ready"
            or meta.get("schema_version") != document["schema_version"]
            or meta.get("active_schema") != document["schema_version"]
            or meta.get("source_watermark") != document["source_watermark"]
            or meta.get("active_watermark") != document["source_watermark"]
            or type(epoch) is not int or epoch < 1):
        raise GraphQuestionError("graph_generation_changed")
    return epoch


def _claim_json(row: dict[str, Any]) -> dict[str, Any]:
    try:
        claim = json.loads(row["claim_json"], object_pairs_hook=projection._unique_object,
                           parse_float=projection.Decimal)
    except Exception as error:
        raise GraphQuestionError() from error
    if not isinstance(claim, dict):
        raise GraphQuestionError()
    return claim


def _evidence_json(row: dict[str, Any]) -> dict[str, Any]:
    try:
        evidence = json.loads(row["evidence_json"], object_pairs_hook=projection._unique_object,
                              parse_float=projection.Decimal)
    except Exception as error:
        raise GraphQuestionError() from error
    if not isinstance(evidence, dict):
        raise GraphQuestionError()
    return evidence


def _check_claim_content(row: dict[str, Any], claim: dict[str, Any], expected: dict[str, Any]) -> None:
    if (projection.canonical_json(claim) != row.get("claim_json")
            or projection.canonical_json(claim) != projection.canonical_json(expected)):
        raise GraphQuestionError()
    scalar_fields = {
        "generation": None,
        "claim_id": claim["claim_id"],
        "snapshot_hash": claim["snapshot_hash"],
        "review_version": claim["review_version"],
        "claim_type": claim["claim_type"],
        "predicate": claim["predicate"],
        "qualifiers_json": projection.canonical_json(claim["qualifiers"]),
        "value": claim.get("value") if claim["claim_type"] == "property" else None,
    }
    if any(row.get(field) != value for field, value in scalar_fields.items() if value is not None):
        raise GraphQuestionError()


def _expected_entities(claims: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    entities: Counter[tuple[str, str]] = Counter()
    for claim in claims:
        subject = claim["subject"]
        entities[(subject["type"], subject["id"])] += 1
        if claim["claim_type"] == "relationship":
            target = claim["object"]
            entities[(target["type"], target["id"])] += 1
    return Counter(set(entities))


def _check_links(rows: list[dict[str, Any]], generation: str,
                 claims: dict[str, dict[str, Any]]) -> None:
    expected: Counter[tuple[Any, ...]] = Counter()
    for snapshot_id, claim in claims.items():
        subject = claim["subject"]
        expected[(snapshot_id, "WIKIKG_REVIEWED_SUBJECT", subject["type"], subject["id"], None)] += 1
        if claim["claim_type"] == "relationship":
            target = claim["object"]
            expected[(snapshot_id, "WIKIKG_REVIEWED_OBJECT", target["type"], target["id"], None)] += 1
        for evidence in claim["evidence"]:
            expected[(snapshot_id, "WIKIKG_REVIEWED_EVIDENCE", None, None,
                      evidence["evidence_id"])] += 1

    actual: Counter[tuple[Any, ...]] = Counter()
    for row in rows:
        # Canonical stored direction is claim -> subject/object/evidence.
        if (row.get("source_claim") is not True or row.get("source_entity") is True
                or row.get("source_evidence") is True):
            raise GraphQuestionError()
        if row.get("source_generation") != generation or row.get("source_snapshot") not in claims:
            raise GraphQuestionError()
        if row.get("target_generation") != generation:
            raise GraphQuestionError()
        if row.get("target_claim") is True:
            raise GraphQuestionError()
        link_type = row.get("link_type")
        if (link_type == "WIKIKG_REVIEWED_SUBJECT"
                and row.get("target_entity") is True and row.get("target_evidence") is not True):
            identity = (row.get("source_snapshot"), link_type,
                        row.get("target_entity_type"), row.get("target_entity_id"), None)
        elif (link_type == "WIKIKG_REVIEWED_OBJECT"
              and row.get("target_entity") is True and row.get("target_evidence") is not True):
            identity = (row.get("source_snapshot"), link_type,
                        row.get("target_entity_type"), row.get("target_entity_id"), None)
        elif (link_type == "WIKIKG_REVIEWED_EVIDENCE"
              and row.get("target_evidence") is True and row.get("target_entity") is not True):
            if row.get("target_snapshot") != row.get("source_snapshot"):
                raise GraphQuestionError()
            identity = (row.get("source_snapshot"), link_type, None, None,
                        row.get("target_evidence_id"))
        else:
            raise GraphQuestionError()
        actual[identity] += 1
    if actual != expected:
        raise GraphQuestionError()


def _read_generation(tx: Any, document: dict[str, Any], generation: str,
                     deadline: float) -> tuple[dict[str, Any], int]:
    claim_count, evidence_count = _expected_counts(document)
    meta = _single_row(tx, ACTIVE_QUERY,
                       {"active_key": projection.ACTIVE_KEY, "string_limit": STRING_LIMIT}, deadline)
    epoch = _check_active(document, generation, meta)
    if (meta.get("claim_count") != claim_count
            or meta.get("snapshot_count") != document["snapshot_count"]
            or meta.get("evidence_count") != evidence_count):
        raise GraphQuestionError()

    params = {"generation": generation, "string_limit": STRING_LIMIT}
    claim_rows = _read_rows(tx, CLAIMS_QUERY, {**params, "take": MAX_CLAIM_ROWS},
                            MAX_CLAIM_ROWS, deadline)
    evidence_rows = _read_rows(tx, EVIDENCE_QUERY, {**params, "take": MAX_EVIDENCE_ROWS},
                               MAX_EVIDENCE_ROWS, deadline)
    entity_rows = _read_rows(tx, ENTITIES_QUERY, {**params, "take": MAX_ENTITY_ROWS},
                             MAX_ENTITY_ROWS, deadline)
    link_rows = _read_rows(tx, LINKS_QUERY, {**params, "take": MAX_LINK_ROWS},
                           MAX_LINK_ROWS, deadline)
    if len(claim_rows) != claim_count or len(evidence_rows) != evidence_count:
        raise GraphQuestionError()

    expected_claims = {claim["snapshot_id"]: claim for claim in document["claims"]}
    claims: dict[str, dict[str, Any]] = {}
    for row in claim_rows:
        snapshot_id = row.get("snapshot_id")
        if row.get("generation") != generation or snapshot_id not in expected_claims or snapshot_id in claims:
            raise GraphQuestionError()
        claim = _claim_json(row)
        _check_claim_content(row, claim, expected_claims[snapshot_id])
        claims[snapshot_id] = claim
    if set(claims) != set(expected_claims):
        raise GraphQuestionError()

    expected_entity_ids = _expected_entities(list(claims.values()))
    actual_entity_ids: Counter[tuple[str, str]] = Counter()
    for row in entity_rows:
        if row.get("generation") != generation:
            raise GraphQuestionError()
        actual_entity_ids[(row.get("entity_type"), row.get("entity_id"))] += 1
    if actual_entity_ids != expected_entity_ids:
        raise GraphQuestionError()

    expected_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot_id, claim in claims.items():
        for evidence in claim["evidence"]:
            expected_evidence[(snapshot_id, evidence["evidence_id"])] = evidence
    actual_evidence: set[tuple[str, str]] = set()
    evidence_scalar_fields = (
        "page_id", "revision_id", "content_hash", "page_title", "extraction_method",
        "extractor_version", "location_type", "location", "source_offset_start",
        "source_offset_end", "span_hash",
    )
    for row in evidence_rows:
        key = (row.get("snapshot_id"), row.get("evidence_id"))
        if row.get("generation") != generation or key not in expected_evidence or key in actual_evidence:
            raise GraphQuestionError()
        evidence = _evidence_json(row)
        if (projection.canonical_json(evidence) != row.get("evidence_json")
                or projection.canonical_json(evidence) != projection.canonical_json(expected_evidence[key])
                or any(row.get(field) != evidence.get(field) for field in evidence_scalar_fields)):
            raise GraphQuestionError()
        actual_evidence.add(key)
    if actual_evidence != set(expected_evidence):
        raise GraphQuestionError()

    _check_links(link_rows, generation, claims)
    reconstructed = {
        "schema_version": document["schema_version"],
        "snapshot_count": document["snapshot_count"],
        "source_watermark": document["source_watermark"],
        "claims": [claims[claim["snapshot_id"]] for claim in document["claims"]],
    }
    return reconstructed, epoch


def read_question_document(document: dict[str, Any], driver: Any, *, deadline: float):
    """Read one complete generation in an explicit bounded read transaction."""
    _expected_counts(document)
    generation = projection.generation_id(document)
    try:
        with driver.session(default_access_mode="READ") as session:
            timeout = min(QUERY_TIMEOUT, deadline - time.monotonic())
            if timeout <= 0:
                raise GraphQuestionError("graph_timeout")
            try:
                transaction = session.begin_transaction(timeout=timeout)
            except Exception as error:
                raise GraphQuestionError("graph_unavailable") from error
            try:
                reconstructed, epoch = _read_generation(transaction, document, generation, deadline)
                transaction.commit()
                return reconstructed, generation, epoch
            except Exception:
                transaction.rollback()
                raise
    except GraphQuestionError:
        raise
    except Exception as error:
        raise GraphQuestionError("graph_unavailable") from error


def recheck_active(generation: str, epoch: int, driver: Any, *, deadline: float) -> None:
    """Use a fresh transaction to ensure interpretation used the still-active token."""
    try:
        with driver.session(default_access_mode="READ") as session:
            timeout = min(QUERY_TIMEOUT, deadline - time.monotonic())
            if timeout <= 0:
                raise GraphQuestionError("graph_timeout")
            transaction = session.begin_transaction(timeout=timeout)
            try:
                row = _single_row(transaction, ACTIVE_QUERY,
                                  {"active_key": projection.ACTIVE_KEY,
                                   "string_limit": STRING_LIMIT}, deadline)
                if (row.get("generation") != generation or row.get("epoch") != epoch
                        or row.get("state") != "ready"):
                    raise GraphQuestionError("graph_generation_changed")
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
    except GraphQuestionError:
        raise
    except Exception as error:
        raise GraphQuestionError("graph_unavailable") from error
