"""Load and query an offline, reviewed WikiKG projection in local Neo4j."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXTENSION_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = EXTENSION_ROOT / "schema" / "reviewed-projection.v1.schema.json"
PASSWORD_ENV = "WIKIKG_NEO4J_PASSWORD"
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_LOOKUP_RESULTS = 100
MAX_LOOKUP_EVIDENCE = 50
BATCH_SIZE = 200
MAX_NEO4J_INTEGER = 2**63 - 1
ACTIVE_KEY = "wikikg.reviewed-projection.active.v1"
ENTITY_TYPES = ("Crop", "Variety", "Pest")
CLAIM_TYPES = ("property", "relationship")

LABEL_GENERATION = "WikiKGReviewedProjectionGeneration"
LABEL_ACTIVE = "WikiKGReviewedProjectionActive"
LABEL_CLAIM = "WikiKGReviewedProjectionClaim"
LABEL_ENTITY = "WikiKGReviewedProjectionEntity"
LABEL_EVIDENCE = "WikiKGReviewedProjectionEvidence"


class ProjectionError(ValueError):
    """A safe, actionable error that can be shown by the CLI."""


class _DuplicateObjectKey(ValueError):
    pass


CONSTRAINTS = (
    """
    CREATE CONSTRAINT wikikg_reviewed_projection_generation_key IF NOT EXISTS
    FOR (node:WikiKGReviewedProjectionGeneration)
    REQUIRE node.generation IS UNIQUE
    """,
    """
    CREATE CONSTRAINT wikikg_reviewed_projection_active_key IF NOT EXISTS
    FOR (node:WikiKGReviewedProjectionActive)
    REQUIRE node.key IS UNIQUE
    """,
    """
    CREATE CONSTRAINT wikikg_reviewed_projection_claim_key IF NOT EXISTS
    FOR (node:WikiKGReviewedProjectionClaim)
    REQUIRE (node.generation, node.snapshot_id) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT wikikg_reviewed_projection_entity_key IF NOT EXISTS
    FOR (node:WikiKGReviewedProjectionEntity)
    REQUIRE (node.generation, node.entity_type, node.entity_id) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT wikikg_reviewed_projection_evidence_key IF NOT EXISTS
    FOR (node:WikiKGReviewedProjectionEvidence)
    REQUIRE (node.generation, node.snapshot_id, node.evidence_id) IS UNIQUE
    """,
)

CREATE_GENERATION_QUERY = """
OPTIONAL MATCH (active:WikiKGReviewedProjectionActive {key: $active_key})
WITH coalesce(active.epoch, 0) AS current_epoch
MERGE (generation:WikiKGReviewedProjectionGeneration {generation: $generation})
ON CREATE SET
    generation.state = 'building',
    generation.schema_version = $schema_version,
    generation.snapshot_count = $snapshot_count,
    generation.source_watermark = $source_watermark,
    generation.claim_count = $claim_count,
    generation.evidence_count = $evidence_count,
    generation.build_epoch = current_epoch
OPTIONAL MATCH (active_state:WikiKGReviewedProjectionActive {key: $active_key})
RETURN generation.state AS state,
       generation.schema_version AS schema_version,
       generation.snapshot_count AS snapshot_count,
       generation.source_watermark AS source_watermark,
       generation.claim_count AS claim_count,
       generation.evidence_count AS evidence_count,
       generation.build_epoch AS build_epoch,
       active_state.generation AS active_generation,
       coalesce(active_state.epoch, 0) AS active_epoch
"""

UPSERT_PROPERTY_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (claim:WikiKGReviewedProjectionClaim {
    generation: $generation,
    snapshot_id: row.snapshot_id
})
SET claim += row.claim_properties
MERGE (subject:WikiKGReviewedProjectionEntity {
    generation: $generation,
    entity_type: row.subject_type,
    entity_id: row.subject_id
})
MERGE (claim)-[:WIKIKG_REVIEWED_SUBJECT]->(subject)
WITH claim, row
UNWIND row.evidence AS evidence_row
MERGE (evidence:WikiKGReviewedProjectionEvidence {
    generation: $generation,
    snapshot_id: row.snapshot_id,
    evidence_id: evidence_row.evidence_id
})
SET evidence += evidence_row.properties
MERGE (claim)-[:WIKIKG_REVIEWED_EVIDENCE]->(evidence)
RETURN count(DISTINCT claim) AS claim_count,
       count(evidence) AS evidence_links
"""

UPSERT_RELATIONSHIP_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (claim:WikiKGReviewedProjectionClaim {
    generation: $generation,
    snapshot_id: row.snapshot_id
})
SET claim += row.claim_properties
MERGE (subject:WikiKGReviewedProjectionEntity {
    generation: $generation,
    entity_type: row.subject_type,
    entity_id: row.subject_id
})
MERGE (claim)-[:WIKIKG_REVIEWED_SUBJECT]->(subject)
MERGE (object:WikiKGReviewedProjectionEntity {
    generation: $generation,
    entity_type: row.object_type,
    entity_id: row.object_id
})
MERGE (claim)-[:WIKIKG_REVIEWED_OBJECT]->(object)
WITH claim, row
UNWIND row.evidence AS evidence_row
MERGE (evidence:WikiKGReviewedProjectionEvidence {
    generation: $generation,
    snapshot_id: row.snapshot_id,
    evidence_id: evidence_row.evidence_id
})
SET evidence += evidence_row.properties
MERGE (claim)-[:WIKIKG_REVIEWED_EVIDENCE]->(evidence)
RETURN count(DISTINCT claim) AS claim_count,
       count(evidence) AS evidence_links
"""

VERIFY_GENERATION_QUERY = """
MATCH (generation:WikiKGReviewedProjectionGeneration {generation: $generation})
OPTIONAL MATCH (claim:WikiKGReviewedProjectionClaim {generation: $generation})
WITH generation, count(claim) AS claim_nodes
OPTIONAL MATCH (evidence:WikiKGReviewedProjectionEvidence {generation: $generation})
RETURN generation.state AS state,
       generation.schema_version AS schema_version,
       generation.snapshot_count AS snapshot_count,
       generation.source_watermark AS source_watermark,
       generation.claim_count AS expected_claim_count,
       generation.evidence_count AS expected_evidence_count,
       claim_nodes,
       count(evidence) AS evidence_nodes
"""

VERIFY_CLAIMS_QUERY = """
MATCH (generation:WikiKGReviewedProjectionGeneration {generation: $generation})
OPTIONAL MATCH (claim:WikiKGReviewedProjectionClaim {generation: $generation})
CALL (claim) {
    OPTIONAL MATCH (claim)-[link:WIKIKG_REVIEWED_SUBJECT]->
        (entity:WikiKGReviewedProjectionEntity {generation: $generation})
    RETURN count(link) AS subject_links,
           collect(entity.entity_type) AS subject_types,
           collect(entity.entity_id) AS subject_ids
}
CALL (claim) {
    OPTIONAL MATCH (claim)-[link:WIKIKG_REVIEWED_OBJECT]->
        (entity:WikiKGReviewedProjectionEntity {generation: $generation})
    RETURN count(link) AS object_links,
           collect(entity.entity_type) AS object_types,
           collect(entity.entity_id) AS object_ids
}
CALL (claim) {
    OPTIONAL MATCH (claim)-[link:WIKIKG_REVIEWED_EVIDENCE]->
        (evidence:WikiKGReviewedProjectionEvidence {generation: $generation})
    RETURN count(link) AS evidence_links,
           collect(evidence.evidence_id) AS evidence_ids,
           collect(evidence.snapshot_id) AS evidence_snapshot_ids
}
RETURN claim.snapshot_id AS snapshot_id,
       claim.claim_id AS claim_id,
       claim.snapshot_hash AS snapshot_hash,
       claim.review_version AS review_version,
       claim.claim_type AS claim_type,
       claim.predicate AS predicate,
       claim.qualifiers_json AS qualifiers_json,
       claim.value AS value,
       claim.claim_json AS claim_json,
       subject_links,
       subject_types,
       subject_ids,
       object_links,
       object_types,
       object_ids,
       evidence_links,
       evidence_ids,
       evidence_snapshot_ids
"""

VERIFY_ENTITIES_QUERY = """
MATCH (entity:WikiKGReviewedProjectionEntity {generation: $generation})
RETURN entity.entity_type AS entity_type, entity.entity_id AS entity_id
"""

VERIFY_EVIDENCE_QUERY = """
MATCH (evidence:WikiKGReviewedProjectionEvidence {generation: $generation})
RETURN evidence.snapshot_id AS snapshot_id,
       evidence.evidence_id AS evidence_id,
       evidence.evidence_json AS evidence_json,
       evidence.page_id AS page_id,
       evidence.revision_id AS revision_id,
       evidence.content_hash AS content_hash,
       evidence.page_title AS page_title,
       evidence.extraction_method AS extraction_method,
       evidence.extractor_version AS extractor_version,
       evidence.location_type AS location_type,
       properties(evidence)['location'] AS location,
       evidence.source_offset_start AS source_offset_start,
       evidence.source_offset_end AS source_offset_end,
       evidence.span_hash AS span_hash
"""

# The dummy property write acquires the active-pointer write lock before the
# epoch is read, so a competing activation either commits first or fails the
# epoch check. Without it, two readers can both observe one epoch and both
# report success, and the later writer silently replaces the earlier one.
SWITCH_ACTIVE_QUERY = """
MATCH (generation:WikiKGReviewedProjectionGeneration {generation: $generation})
MERGE (active:WikiKGReviewedProjectionActive {key: $active_key})
ON CREATE SET active.epoch = 0
SET active._wikikg_lock = true
REMOVE active._wikikg_lock
WITH generation, active, coalesce(active.epoch, 0) AS active_epoch
WHERE active_epoch = $expected_epoch
SET generation.state = 'ready',
    active.generation = generation.generation,
    active.schema_version = generation.schema_version,
    active.source_watermark = generation.source_watermark,
    active.epoch = active_epoch + 1
RETURN active.generation AS active_generation,
       active.epoch AS active_epoch
"""

# Re-verify an already-active generation without advancing the epoch, so a
# repeated load of unchanged content cannot invalidate an unrelated build.
CONFIRM_ACTIVE_QUERY = """
MATCH (generation:WikiKGReviewedProjectionGeneration {
    generation: $generation,
    state: 'ready'
})
MATCH (active:WikiKGReviewedProjectionActive {key: $active_key})
SET active._wikikg_lock = true
REMOVE active._wikikg_lock
WITH generation, active
WHERE active.generation = $generation
RETURN active.generation AS active_generation
"""

LOOKUP_QUERY = """
MATCH (active:WikiKGReviewedProjectionActive {key: $active_key})
MATCH (generation:WikiKGReviewedProjectionGeneration {
    generation: active.generation,
    state: 'ready'
})
MATCH (claim:WikiKGReviewedProjectionClaim {
    generation: generation.generation,
    claim_type: $claim_type,
    predicate: $predicate
})
MATCH (claim)-[:WIKIKG_REVIEWED_SUBJECT]->
    (subject:WikiKGReviewedProjectionEntity {
        generation: generation.generation,
        entity_type: $subject_type,
        entity_id: $subject_id
    })
WITH generation, claim
ORDER BY claim.snapshot_id ASC
LIMIT $take
OPTIONAL MATCH (claim)-[:WIKIKG_REVIEWED_EVIDENCE]->
    (evidence:WikiKGReviewedProjectionEvidence {
        generation: generation.generation,
        snapshot_id: claim.snapshot_id
    })
WITH claim, evidence
ORDER BY claim.snapshot_id ASC, evidence.evidence_id ASC
WITH claim,
     count(evidence) AS evidence_count,
     collect(evidence {
         .evidence_id,
         .page_id,
         .revision_id,
         .content_hash,
         .page_title,
         .location_type,
         location: properties(evidence)['location'],
         .source_offset_start,
         .source_offset_end,
         .span_hash
     }) AS all_evidence
RETURN claim.claim_json AS claim_json,
       evidence_count,
       all_evidence[0..$evidence_limit] AS evidence
ORDER BY claim.snapshot_id ASC
"""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ProjectionError("manifest contains a non-standard JSON number")


def _parse_manifest_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateObjectKey as error:
        raise ProjectionError("manifest contains a duplicate object key") from error
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, ProjectionError):
            raise
        raise ProjectionError("manifest is not valid UTF-8 JSON") from error


def read_manifest_file(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        manifest_path.relative_to(REPOSITORY_ROOT)
    except ValueError:
        pass
    else:
        raise ProjectionError("manifest must be stored outside the repository")

    try:
        if manifest_path.stat().st_mode & 0o077 or manifest_path.parent.stat().st_mode & 0o077:
            raise ProjectionError("manifest and its directory must be owner-only")
    except OSError as error:
        raise ProjectionError("cannot inspect manifest permissions") from error

    try:
        with manifest_path.open("rb") as manifest_file:
            raw = manifest_file.read(MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise ProjectionError("cannot read manifest file") from error
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ProjectionError("manifest exceeds the 64 MiB input limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectionError("manifest must use UTF-8 encoding") from error
    document = _parse_manifest_json(text)
    if not isinstance(document, dict):
        raise ProjectionError("manifest root must be a JSON object")
    return document


def _json_pointer(path: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(parts) if parts else "/"


def _require_neo4j_integer(value: Any, field: str, minimum: int) -> int:
    if type(value) is not int or not minimum <= value <= MAX_NEO4J_INTEGER:
        raise ProjectionError(f"{field} must be an integer in Neo4j's supported range")
    return value


def validate_manifest_document(document: Any) -> dict[str, Any]:
    try:
        import jsonschema
    except ImportError as error:
        raise ProjectionError(
            "jsonschema is required; install requirements-reviewed-projection.txt"
        ) from error

    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(document)
    except jsonschema.ValidationError as error:
        raise ProjectionError(
            f"manifest does not match the reviewed-projection schema at "
            f"{_json_pointer(error.absolute_path)}"
        ) from error
    except OSError as error:
        raise ProjectionError("reviewed-projection schema is unavailable") from error
    except json.JSONDecodeError as error:
        raise ProjectionError("reviewed-projection schema is invalid JSON") from error

    _require_neo4j_integer(document["snapshot_count"], "snapshot_count", 0)
    claims = document["claims"]
    if document["snapshot_count"] < len(claims):
        raise ProjectionError("snapshot_count cannot be smaller than the claim count")

    seen_snapshots: set[str] = set()
    previous_snapshot_id: str | None = None
    for claim in claims:
        snapshot_id = claim["snapshot_id"]
        if snapshot_id in seen_snapshots:
            raise ProjectionError("manifest contains a duplicate snapshot_id")
        seen_snapshots.add(snapshot_id)
        if previous_snapshot_id is not None and snapshot_id < previous_snapshot_id:
            raise ProjectionError("claims must be sorted by snapshot_id ascending")
        previous_snapshot_id = snapshot_id
        _require_neo4j_integer(claim["review_version"], "review_version", 1)

        seen_evidence: set[str] = set()
        for evidence in claim["evidence"]:
            _require_neo4j_integer(evidence["page_id"], "page_id", 1)
            _require_neo4j_integer(evidence["revision_id"], "revision_id", 1)
            evidence_id = evidence["evidence_id"]
            if evidence_id in seen_evidence:
                raise ProjectionError("claim contains a duplicate evidence_id")
            seen_evidence.add(evidence_id)
            if evidence["location_type"] == "source_span":
                start = _require_neo4j_integer(
                    evidence["source_offset_start"], "source_offset_start", 0
                )
                end = _require_neo4j_integer(
                    evidence["source_offset_end"], "source_offset_end", 1
                )
                span = evidence["supporting_span"]
                if end <= start or end - start != len(span):
                    raise ProjectionError("source-span coordinates do not match the span")
                actual_span_hash = hashlib.sha256(span.encode("utf-8")).hexdigest()
                if evidence["span_hash"] != actual_span_hash:
                    raise ProjectionError("source-span hash does not match the span")
            elif evidence["location_type"] == "page_title":
                if evidence["location"] != evidence["page_title"]:
                    raise ProjectionError("page-title evidence must name the cited page")
                if any(key in evidence for key in (
                    "source_offset_start", "source_offset_end", "supporting_span", "span_hash"
                )):
                    raise ProjectionError("page-title evidence must not contain a source span")
            elif any(key in evidence for key in (
                "location", "source_offset_start", "source_offset_end", "supporting_span", "span_hash"
            )):
                raise ProjectionError("page-only evidence must not contain a location or span")
    return document


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ProjectionError("manifest contains a non-finite JSON number")
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        return "-0" if sign else "0"
    coefficient = "".join(str(digit) for digit in digits)
    scientific_exponent = exponent + len(coefficient) - 1
    mantissa = coefficient[0]
    if len(coefficient) > 1:
        mantissa += "." + coefficient[1:]
    prefix = "-" if sign else ""
    if scientific_exponent == 0:
        return prefix + mantissa
    return prefix + mantissa + "e" + str(scientific_exponent)


def canonical_json(value: Any) -> str:
    """Encode JSON data deterministically without rounding decimal qualifiers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProjectionError("manifest contains a non-finite JSON number")
        return _canonical_decimal(Decimal(repr(value)))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ProjectionError("JSON object keys must be strings")
        parts = (
            canonical_json(key) + ":" + canonical_json(value[key])
            for key in sorted(value)
        )
        return "{" + ",".join(parts) + "}"
    raise ProjectionError("manifest contains a value that is not valid JSON")


def generation_id(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def validate_local_bolt_uri(uri: str) -> str:
    if len(uri) > 512:
        raise ProjectionError("Bolt URI is too long")
    try:
        parsed = urlsplit(uri)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ProjectionError("Bolt URI is invalid") from error
    if (
        parsed.scheme != "bolt"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ProjectionError("Neo4j URI must be a local bolt:// loopback address")
    if port is not None and not 1 <= port <= 65535:
        raise ProjectionError("Bolt URI port is invalid")
    is_loopback = hostname.lower() == "localhost"
    if not is_loopback and "%" not in hostname:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ProjectionError("Neo4j URI must be a local bolt:// loopback address")
    return uri


def _require_lookup_text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProjectionError(f"{field} must be a non-empty string of at most {maximum} characters")
    return value


def _single_record(result: Any) -> dict[str, Any]:
    record = result.single(strict=True)
    if record is None:
        raise ProjectionError("Neo4j returned no verification record")
    return dict(record)


def _record_list(result: Any) -> list[dict[str, Any]]:
    return [dict(record) for record in result]


def _generation_metadata(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": document["schema_version"],
        "snapshot_count": document["snapshot_count"],
        "source_watermark": document["source_watermark"],
        "claim_count": len(document["claims"]),
        "evidence_count": sum(len(claim["evidence"]) for claim in document["claims"]),
    }


def _ensure_generation(
    transaction: Any,
    generation: str,
    metadata: dict[str, Any],
) -> tuple[str, int]:
    record = _single_record(
        transaction.run(
            CREATE_GENERATION_QUERY,
            generation=generation,
            active_key=ACTIVE_KEY,
            **metadata,
        )
    )
    for field, expected in metadata.items():
        if record.get(field) != expected:
            raise ProjectionError("generation metadata does not match its content hash")
    state = record.get("state")
    build_epoch = record.get("build_epoch")
    active_epoch = record.get("active_epoch")
    if state not in ("building", "ready"):
        raise ProjectionError("generation has an invalid state")
    if (
        type(build_epoch) is not int
        or type(active_epoch) is not int
        or build_epoch < 0
        or active_epoch < 0
    ):
        raise ProjectionError("generation build epoch is invalid")
    if state == "building" and record.get("active_generation") == generation:
        raise ProjectionError("active generation cannot be in the building state")
    if state == "ready" and record.get("active_generation") != generation:
        raise ProjectionError("retired generation cannot be reactivated by loading an old export")
    if state == "building":
        if build_epoch != active_epoch:
            raise ProjectionError("generation build was superseded by a newer activation")
        return state, build_epoch
    return state, active_epoch


def _prepare_rows(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {kind: [] for kind in CLAIM_TYPES}
    for claim in document["claims"]:
        claim_properties: dict[str, Any] = {
            "snapshot_hash": claim["snapshot_hash"],
            "claim_id": claim["claim_id"],
            "review_version": claim["review_version"],
            "claim_type": claim["claim_type"],
            "predicate": claim["predicate"],
            "qualifiers_json": canonical_json(claim["qualifiers"]),
            "claim_json": canonical_json(claim),
        }
        row: dict[str, Any] = {
            "snapshot_id": claim["snapshot_id"],
            "claim_properties": claim_properties,
            "subject_type": claim["subject"]["type"],
            "subject_id": claim["subject"]["id"],
            "evidence": [],
        }
        if claim["claim_type"] == "property":
            claim_properties["value"] = claim["value"]
        else:
            row["object_type"] = claim["object"]["type"]
            row["object_id"] = claim["object"]["id"]

        for evidence in claim["evidence"]:
            row["evidence"].append(
                {
                    "evidence_id": evidence["evidence_id"],
                    "properties": {
                        **evidence,
                        "evidence_json": canonical_json(evidence),
                    },
                }
            )
        result[claim["claim_type"]].append(row)
    return result


def _write_batch(
    transaction: Any,
    generation: str,
    rows: list[dict[str, Any]],
    query: str,
) -> None:
    expected_evidence_links = sum(len(row["evidence"]) for row in rows)
    record = _single_record(
        transaction.run(query, generation=generation, rows=rows)
    )
    if (
        record.get("claim_count") != len(rows)
        or record.get("evidence_links") != expected_evidence_links
    ):
        raise ProjectionError("Neo4j did not write the expected batch identities")


def _verify_and_switch(
    transaction: Any,
    generation: str,
    document: dict[str, Any],
    metadata: dict[str, Any],
    expected_epoch: int,
    is_active: bool,
) -> None:
    """Verify staged content, then either confirm the active generation or switch to it."""
    generation_record = _single_record(
        transaction.run(VERIFY_GENERATION_QUERY, generation=generation)
    )
    if generation_record.get("state") not in ("building", "ready"):
        raise ProjectionError("generation cannot be activated from its current state")
    expected_generation = {
        "schema_version": metadata["schema_version"],
        "snapshot_count": metadata["snapshot_count"],
        "source_watermark": metadata["source_watermark"],
        "expected_claim_count": metadata["claim_count"],
        "expected_evidence_count": metadata["evidence_count"],
        "claim_nodes": metadata["claim_count"],
        "evidence_nodes": metadata["evidence_count"],
    }
    if any(generation_record.get(key) != value for key, value in expected_generation.items()):
        raise ProjectionError("staged generation counts or metadata do not match the manifest")

    claim_records = _record_list(
        transaction.run(VERIFY_CLAIMS_QUERY, generation=generation)
    )
    expected_claims = {claim["snapshot_id"]: claim for claim in document["claims"]}
    actual_claims: dict[str, dict[str, Any]] = {}
    for record in claim_records:
        snapshot_id = record.get("snapshot_id")
        if snapshot_id is None:
            continue
        if snapshot_id in actual_claims:
            raise ProjectionError("staged generation contains duplicate claim nodes")
        actual_claims[snapshot_id] = record
    if set(actual_claims) != set(expected_claims):
        raise ProjectionError("staged claim identities do not match the manifest")

    expected_entities = {
        (claim["subject"]["type"], claim["subject"]["id"])
        for claim in document["claims"]
    }
    expected_entities.update(
        (claim["object"]["type"], claim["object"]["id"])
        for claim in document["claims"] if claim["claim_type"] == "relationship"
    )
    entity_records = _record_list(
        transaction.run(VERIFY_ENTITIES_QUERY, generation=generation)
    )
    actual_entities = [
        (record["entity_type"], record["entity_id"]) for record in entity_records
    ]
    if len(actual_entities) != len(expected_entities) or set(actual_entities) != expected_entities:
        raise ProjectionError("staged entity identities or orphan nodes do not match the manifest")

    expected_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot_id, claim in expected_claims.items():
        record = actual_claims[snapshot_id]
        if (
            record.get("claim_id") != claim["claim_id"]
            or record.get("claim_json") != canonical_json(claim)
        ):
            raise ProjectionError("staged claim content does not match the manifest")
        expected_properties = {
            "snapshot_hash": claim["snapshot_hash"],
            "review_version": claim["review_version"],
            "claim_type": claim["claim_type"],
            "predicate": claim["predicate"],
            "qualifiers_json": canonical_json(claim["qualifiers"]),
            "value": claim.get("value") if claim["claim_type"] == "property" else None,
        }
        if any(record.get(field) != expected for field, expected in expected_properties.items()):
            raise ProjectionError("staged claim lookup fields do not match the manifest")
        subject = claim["subject"]
        if (
            record.get("subject_links") != 1
            or record.get("subject_types") != [subject["type"]]
            or record.get("subject_ids") != [subject["id"]]
        ):
            raise ProjectionError("staged subject identity does not match the manifest")
        expected_object = claim.get("object")
        expected_object_types = [expected_object["type"]] if expected_object else []
        expected_object_ids = [expected_object["id"]] if expected_object else []
        if (
            record.get("object_links") != len(expected_object_types)
            or record.get("object_types") != expected_object_types
            or record.get("object_ids") != expected_object_ids
        ):
            raise ProjectionError("staged object identity does not match the manifest")

        evidence_ids = [evidence["evidence_id"] for evidence in claim["evidence"]]
        if (
            record.get("evidence_links") != len(evidence_ids)
            or len(record.get("evidence_ids", [])) != len(evidence_ids)
            or set(record.get("evidence_ids", [])) != set(evidence_ids)
        ):
            raise ProjectionError("staged evidence links do not match the manifest")
        linked_snapshot_ids = record.get("evidence_snapshot_ids") or []
        if linked_snapshot_ids and set(linked_snapshot_ids) != {snapshot_id}:
            raise ProjectionError("a staged evidence link targets another snapshot")
        for evidence in claim["evidence"]:
            expected_evidence[(snapshot_id, evidence["evidence_id"])] = evidence

    evidence_records = _record_list(
        transaction.run(VERIFY_EVIDENCE_QUERY, generation=generation)
    )
    actual_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for record in evidence_records:
        key = (record.get("snapshot_id"), record.get("evidence_id"))
        if key in actual_evidence:
            raise ProjectionError("staged generation contains duplicate evidence nodes")
        actual_evidence[key] = record
    if set(actual_evidence) != set(expected_evidence):
        raise ProjectionError("staged evidence identities do not match the manifest")
    evidence_scalar_fields = (
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
    for key, expected in expected_evidence.items():
        record = actual_evidence[key]
        if record.get("evidence_json") != canonical_json(expected):
            raise ProjectionError("staged evidence content does not match the manifest")
        if any(record.get(field) != expected.get(field) for field in evidence_scalar_fields):
            raise ProjectionError("staged evidence fields do not match the manifest")

    if is_active:
        confirmed = transaction.run(
            CONFIRM_ACTIVE_QUERY,
            generation=generation,
            active_key=ACTIVE_KEY,
        ).single()
        if confirmed is None or confirmed.get("active_generation") != generation:
            raise ProjectionError(
                "the active generation changed while this export was being verified"
            )
        return

    active_record = transaction.run(
        SWITCH_ACTIVE_QUERY,
        generation=generation,
        active_key=ACTIVE_KEY,
        expected_epoch=expected_epoch,
    ).single()
    if active_record is None:
        raise ProjectionError("a newer generation was activated while this build was in progress")
    if active_record.get("active_generation") != generation:
        raise ProjectionError("Neo4j did not switch to the verified generation")


def load_projection(document: Any, driver: Any) -> dict[str, Any]:
    """Build an inactive generation, verify it, then switch the active pointer.

    A build records the activation epoch it started from. If another generation
    is activated while the build is running, the build cannot become active.
    Reloading the generation that is already active re-verifies it and leaves
    the activation epoch unchanged.
    """
    validated = validate_manifest_document(document)
    generation = generation_id(validated)
    metadata = _generation_metadata(validated)
    rows_by_type = _prepare_rows(validated)

    with driver.session() as session:
        for statement in CONSTRAINTS:
            session.run(statement).consume()

        state, expected_epoch = session.execute_write(
            _ensure_generation,
            generation,
            metadata,
        )
        if state == "building":
            for claim_type, query in (
                ("property", UPSERT_PROPERTY_BATCH_QUERY),
                ("relationship", UPSERT_RELATIONSHIP_BATCH_QUERY),
            ):
                rows = rows_by_type[claim_type]
                for start in range(0, len(rows), BATCH_SIZE):
                    session.execute_write(
                        _write_batch,
                        generation,
                        rows[start : start + BATCH_SIZE],
                        query,
                    )
        session.execute_write(
            _verify_and_switch,
            generation,
            validated,
            metadata,
            expected_epoch,
            state == "ready",
        )

    return {
        "offline_only": True,
        "generation": generation,
        "active": True,
        "claim_count": metadata["claim_count"],
        "evidence_count": metadata["evidence_count"],
    }


def _lookup_records(transaction: Any, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    result = transaction.run(LOOKUP_QUERY, **parameters)
    return _record_list(result)


def lookup_projection(
    driver: Any,
    *,
    claim_type: str,
    subject_type: str,
    subject_id: str,
    predicate: str,
    limit: int = 20,
) -> dict[str, Any]:
    if claim_type not in CLAIM_TYPES:
        raise ProjectionError("claim_type must be property or relationship")
    if subject_type not in ENTITY_TYPES:
        raise ProjectionError("subject_type must be Crop, Variety, or Pest")
    subject_id = _require_lookup_text(subject_id, "subject_id", 4096)
    predicate = _require_lookup_text(predicate, "predicate", 1024)
    if type(limit) is not int or not 1 <= limit <= MAX_LOOKUP_RESULTS:
        raise ProjectionError(f"limit must be between 1 and {MAX_LOOKUP_RESULTS}")

    parameters = {
        "active_key": ACTIVE_KEY,
        "claim_type": claim_type,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "predicate": predicate,
        "take": limit + 1,
        "evidence_limit": MAX_LOOKUP_EVIDENCE,
    }
    with driver.session() as session:
        records = session.execute_read(_lookup_records, parameters)

    truncated = len(records) > limit
    results: list[dict[str, Any]] = []
    for record in records[:limit]:
        claim = _parse_manifest_json(record["claim_json"])
        evidence = record.get("evidence") or []
        evidence_count = int(record.get("evidence_count", len(evidence)))
        result: dict[str, Any] = {
            "claim_id": claim["claim_id"],
            "snapshot_id": claim["snapshot_id"],
            "snapshot_hash": claim["snapshot_hash"],
            "review_version": claim["review_version"],
            "claim_type": claim["claim_type"],
            "subject": claim["subject"],
            "predicate": claim["predicate"],
            "qualifiers": claim["qualifiers"],
            "evidence": evidence,
            "evidence_truncated": evidence_count > len(evidence),
        }
        if claim_type == "property":
            result["value"] = claim["value"]
        else:
            result["object"] = claim["object"]
        results.append(result)
    return {
        "offline_only": True,
        "results": results,
        "truncated": truncated,
    }


def create_neo4j_driver(uri: str, username: str, password: str) -> Any:
    try:
        from neo4j import GraphDatabase
    except ImportError as error:
        raise ProjectionError(
            "neo4j driver is required; install requirements-reviewed-projection.txt"
        ) from error
    return GraphDatabase.driver(uri, auth=(username, password))


def _add_database_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--uri",
        required=True,
        metavar="bolt://127.0.0.1:7687",
        help="local loopback Bolt URI only",
    )
    parser.add_argument(
        "--username",
        default="neo4j",
        metavar="<username>",
        help="Neo4j username; password comes from WIKIKG_NEO4J_PASSWORD",
    )


def _parse_lookup_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 1 <= limit <= MAX_LOOKUP_RESULTS:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_LOOKUP_RESULTS}"
        )
    return limit


def build_parser() -> argparse.ArgumentParser:
    examples = """Examples:
  python3 reviewed_projection.py validate --manifest <manifest.json>
  python3 reviewed_projection.py load --manifest <manifest.json> --uri bolt://127.0.0.1:7687
  python3 reviewed_projection.py lookup --uri bolt://127.0.0.1:7687 --claim-type property --subject-type Crop --subject-id <entity-id> --predicate <predicate>
"""
    parser = argparse.ArgumentParser(
        description="Validate, load, or look up the offline reviewed projection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate a manifest without connecting to Neo4j",
        description="Validate the complete manifest without database writes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: python3 reviewed_projection.py validate --manifest <manifest.json>",
    )
    validate_parser.add_argument("--manifest", required=True, metavar="<manifest.json>")

    load_parser = subparsers.add_parser(
        "load",
        help="build and activate a verified local generation",
        description="Load only into a local loopback Neo4j Bolt server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: python3 reviewed_projection.py load --manifest <manifest.json> --uri bolt://127.0.0.1:7687",
    )
    load_parser.add_argument("--manifest", required=True, metavar="<manifest.json>")
    _add_database_arguments(load_parser)

    lookup_parser = subparsers.add_parser(
        "lookup",
        help="look up approved property or relationship claims offline",
        description="Run one fixed, parameterized offline lookup query.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example: python3 reviewed_projection.py lookup --uri "
            "bolt://127.0.0.1:7687 --claim-type property --subject-type Crop "
            "--subject-id <entity-id> --predicate <predicate>"
        ),
    )
    _add_database_arguments(lookup_parser)
    lookup_parser.add_argument("--claim-type", choices=CLAIM_TYPES, required=True)
    lookup_parser.add_argument("--subject-type", choices=ENTITY_TYPES, required=True)
    lookup_parser.add_argument("--subject-id", required=True, metavar="<entity-id>")
    lookup_parser.add_argument("--predicate", required=True, metavar="<predicate>")
    lookup_parser.add_argument(
        "--limit",
        type=_parse_lookup_limit,
        default=20,
        help=f"maximum results (1-{MAX_LOOKUP_RESULTS}; default: 20)",
    )
    return parser


def _database_credentials(args: argparse.Namespace) -> tuple[str, str, str]:
    uri = validate_local_bolt_uri(args.uri)
    username = _require_lookup_text(args.username, "username", 128)
    if any(ord(character) < 32 for character in username):
        raise ProjectionError("username contains a control character")
    password = os.environ.get(PASSWORD_ENV)
    if not password:
        raise ProjectionError(f"set {PASSWORD_ENV} in the environment")
    return uri, username, password


def _write_json(value: Any) -> None:
    print(canonical_json(value))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            document = read_manifest_file(args.manifest)
            validate_manifest_document(document)
            _write_json(
                {
                    "status": "valid",
                    "schema_version": document["schema_version"],
                    "snapshot_count": document["snapshot_count"],
                    "claim_count": len(document["claims"]),
                    "generation": generation_id(document),
                }
            )
            return 0

        uri, username, password = _database_credentials(args)
        driver = create_neo4j_driver(uri, username, password)
        try:
            if args.command == "load":
                document = read_manifest_file(args.manifest)
                result = load_projection(document, driver)
            else:
                result = lookup_projection(
                    driver,
                    claim_type=args.claim_type,
                    subject_type=args.subject_type,
                    subject_id=args.subject_id,
                    predicate=args.predicate,
                    limit=args.limit,
                )
        finally:
            driver.close()
        _write_json(result)
        return 0
    except ProjectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception:
        print("error: offline Neo4j operation failed; database details withheld", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
