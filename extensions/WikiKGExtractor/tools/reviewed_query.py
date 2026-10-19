"""Experimental, bounded offline queries over reviewed projection v1."""

from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import jsonschema
import qualifier_scope as scope_policy
import reviewed_projection as projection

SEMANTIC_VERSION = "wikikg.reviewed-query.v2"
MAX_CLAIMS = 5000
MAX_AUTHORIZED_CLAIMS = 100
MAX_PATHS = 100
MAX_RESULTS = 100
MAX_EVIDENCE = 50
MAX_RESPONSE_BYTES = 1_000_000
BLOCKING_REASONS = frozenset({
    "invalid_request", "unresolved_identity", "relationship_missing", "property_missing",
    "scope_unknown", "opposing_assertions", "scalar_alternatives", "numeric_uncertain",
    "numeric_unsupported", "partial_manifest", "limits",
})

UNITS = {
    "day": ("time", Decimal(1)),
    "days": ("time", Decimal(1)),
    "ngày": ("time", Decimal(1)),
    "cm": ("length", Decimal(1)),
    "m": ("length", Decimal(100)),
    "t/ha": ("mass_area", Decimal(1)),
    "tấn/ha": ("mass_area", Decimal(1)),
    "kg/ha": ("mass_area", Decimal("0.001")),
}

CANONICAL_PREDICATES = {
    "height": "plant_height",
    "plant_height": "plant_height",
    "maturity": "growth_duration",
    "duration": "growth_duration",
    "growth_duration": "growth_duration",
    "yield": "yield_amount",
    "yield_amount": "yield_amount",
}
CANONICAL_DIMENSIONS = {
    "plant_height": "length",
    "growth_duration": "time",
    "yield_amount": "mass_area",
}
PROPERTY_DIMENSIONS = {
    predicate: CANONICAL_DIMENSIONS[canonical]
    for predicate, canonical in CANONICAL_PREDICATES.items()
}


class QueryError(ValueError):
    """Internal, non-sensitive query contract failure."""


REASON_MESSAGES = {
    "invalid_request": "The structured request or manifest is invalid.",
    "unresolved_identity": "Entity identity is unresolved or ambiguous in the requested crop scope.",
    "relationship_missing": "A required reviewed relationship is missing.",
    "scope_unknown": "Requested applicability is not established.",
    "opposing_assertions": "Opposing reviewed assertions remain unresolved.",
    "scalar_alternatives": "Multiple incompatible scalar assertions remain.",
    "numeric_uncertain": "The source interval straddles the requested numeric threshold.",
    "numeric_unsupported": "The scalar format, unit, or dimension is not supported for this comparison.",
    "partial_manifest": "The manifest does not contain its declared complete claim set.",
    "limits": "A configured query bound was reached.",
    "authorizer_missing": "A current-evidence authorizer is required.",
    "text_authorizer_missing": "A current text authorizer is required for supplied source text.",
    "authorization_failed": "Fresh exact-payload authorization failed.",
    "property_missing": "No reviewed scalar property supports this filter.",
    "numeric_not_satisfied": "Reviewed numeric evidence confidently fails the requested comparison.",
    "empty_result": "No matching claim was found in this manifest; this is not a global negative.",
}


def _reason(code: str) -> dict[str, str]:
    return {"code": code, "message": REASON_MESSAGES[code]}


def _response(generation: str, status: str, *, results=None, complete=False, **extra):
    reason_codes = {"dependency_failure": "authorization_failed",
                    "truncated": "limits",
                    "unsupported": "invalid_request",
                    "empty": "empty_result"}
    if status in reason_codes and "reasons" not in extra:
        code = reason_codes[status]
        extra["reasons"] = [_reason(code)]
    return {
        "generation": generation,
        "semantic_version": SEMANTIC_VERSION,
        "offline_only": True,
        "completeness_scope": "manifest",
        "scope_policy_version": scope_policy.POLICY_VERSION,
        "status": status,
        "complete": complete,
        "results": results or [],
        **extra,
    }


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _is_unknown(entity: dict[str, Any]) -> bool:
    return _normalize(entity["id"]) in {
        "unknown", "unresolved", "none", "n/a", "?", "tbd", "chưa rõ", "không rõ"
    }


def _parse_number(value: Any):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*([^\d\s-][^\s]*)?\s*", value)
    if not match:
        return None
    numeric = match.group(1)
    if "," in numeric and len(numeric.rsplit(",", 1)[1]) == 3:
        return None
    unit = (match.group(2) or "").strip().casefold()
    try:
        number = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number, unit


def _parse_interval(value: Any):
    single = _parse_number(value)
    if single:
        return single[0], single[0], single[1]
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"\s*(-?\d+(?:[.,]\d+)?)\s*(?:-|–|to|đến)\s*(-?\d+(?:[.,]\d+)?)\s*([^\d\s-][^\s]*)?\s*",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    if any("," in token and len(token.rsplit(",", 1)[1]) == 3
           for token in (match.group(1), match.group(2))):
        return None
    try:
        low, high = Decimal(match.group(1).replace(",", ".")), Decimal(match.group(2).replace(",", "."))
    except InvalidOperation:
        return None
    if not low.is_finite() or not high.is_finite() or low > high:
        return None
    return low, high, (match.group(3) or "").strip().casefold()


def _evidence_pages(claim: dict[str, Any]) -> set[int]:
    return {item["page_id"] for item in claim["evidence"]}


def _relation_transition(predicate: str, source: dict[str, Any], target: dict[str, Any]) -> bool:
    source_type, target_type = source["type"], target["type"]
    allowed = {
        "HAS_VARIETY": ("Crop", "Variety"),
        "HAS_PARENT": ("Variety", "Variety"),
        "PARENT_OF": ("Variety", "Variety"),
        "RESISTANT_TO": ("Variety", "Pest"),
        "SUSCEPTIBLE_TO": ("Variety", "Pest"),
        "AFFECTED_BY": ("Crop", "Pest"),
    }
    return allowed.get(predicate) == (source_type, target_type)


def _relationship_identity_eligible(claim: dict[str, Any]) -> bool:
    target = claim.get("object")
    source = claim.get("subject")
    predicate = claim.get("predicate")
    if not isinstance(source, dict) or not isinstance(target, dict):
        return False
    if not _relation_transition(predicate, source, target) or _is_unknown(target):
        return False
    resolved = claim.get("qualifiers", {}).get("resolved")
    if predicate in {"HAS_PARENT", "PARENT_OF"}:
        return resolved is True
    return resolved is not False


def _validate_request(request: Any, schema: dict[str, Any]) -> None:
    request_schema = {
        "$schema": schema["$schema"],
        "$id": schema["$id"],
        "$defs": schema["$defs"],
        **schema["$defs"]["request"],
    }
    jsonschema.Draft202012Validator(request_schema).validate(request)
    if len(request.get("filters", [])) > 20:
        raise QueryError("too many filters")
    for entity in request["entities"]:
        for field in ("id", "name", "crop_id"):
            if field in entity and len(entity[field]) > 512:
                raise QueryError("entity text too long")
    for item in request.get("path", []):
        if item not in {
            "HAS_VARIETY", "HAS_PARENT", "PARENT_OF", "RESISTANT_TO",
            "SUSCEPTIBLE_TO", "AFFECTED_BY",
        }:
            raise QueryError("unknown path predicate")
    if request.get("intent") == "path" and len(request["path"]) > request.get("max_depth", len(request["path"])):
        raise QueryError("path exceeds maximum depth")
    if request.get("intent") == "conjunction" and any(len(branch["path"]) > 3 for branch in request["branches"]):
        raise QueryError("branch exceeds maximum depth")
    if any(item.get("on", "root") == "target" for item in request.get("filters", [])):
        if request.get("intent") != "path" or "target" not in request:
            raise QueryError("target filters require a typed path target")
    for item in request.get("filters", []):
        if item["predicate"] not in PROPERTY_DIMENSIONS:
            raise QueryError("unknown property predicate")


def _authorize(
    claims: list[dict[str, Any]], authorizer: Callable | None
) -> dict[str, dict[str, Any]] | None:
    if authorizer is None:
        return None
    originals = {claim["snapshot_id"]: projection.canonical_json(claim) for claim in claims}
    try:
        response = authorizer(copy.deepcopy(claims))
        if isinstance(response, dict):
            if response.get("status") != "ok":
                return None
            response = response.get("claims")
        if not isinstance(response, list) or len(response) != len(claims):
            return None
        current = {}
        for claim in response:
            if not isinstance(claim, dict):
                return None
            snapshot_id = claim.get("snapshot_id")
            if snapshot_id not in originals or snapshot_id in current:
                return None
            if projection.canonical_json(claim) != originals[snapshot_id]:
                return None
            current[snapshot_id] = claim
        if set(current) != set(originals):
            return None
        return current
    except Exception:
        return None


def _membership_claims(claims, crop_id: str, variety_id: str):
    return [
        claim for claim in claims
        if claim["claim_type"] == "relationship"
        and claim["predicate"] == "HAS_VARIETY"
        and claim["subject"] == {"type": "Crop", "id": crop_id}
        and claim.get("object") == {"type": "Variety", "id": variety_id}
    ]


def _scope_root(entity, claims, bindings):
    if entity["type"] == "Crop":
        if entity.get("id") != entity.get("crop_id"):
            raise QueryError("crop identity mismatch")
        return {"entity": ("Crop", entity["id"]), "crop_id": entity["id"], "membership": None, "pages": None}

    if entity["type"] != "Variety":
        raise QueryError("Pest roots require an explicit path context")

    variety_id = entity.get("id")
    if entity.get("name") is not None:
        matches = []
        if bindings is None:
            bindings = []
        if not isinstance(bindings, list):
            raise QueryError("malformed identity bindings")
        allowed_binding_keys = {
            "type", "id", "name", "crop_id", "membership_snapshot_id",
            "membership_snapshot_hash", "source_page_ids",
        }
        required_binding_keys = allowed_binding_keys
        for binding in bindings:
            if (not isinstance(binding, dict)
                    or set(binding) - allowed_binding_keys
                    or not required_binding_keys.issubset(binding)
                    or binding.get("type") != "Variety"
                    or any(not isinstance(binding.get(key), str) or not binding[key]
                           for key in ("id", "name", "crop_id", "membership_snapshot_id", "membership_snapshot_hash"))
                    or not isinstance(binding.get("source_page_ids"), list)
                    or any(type(page) is not int or page < 1 for page in binding["source_page_ids"])):
                raise QueryError("malformed identity binding")
        for binding in bindings:
            if binding.get("type") != "Variety" or binding.get("crop_id") != entity["crop_id"]:
                continue
            if binding.get("name") != entity["name"]:
                continue
            bound_id = binding.get("id")
            if not isinstance(bound_id, str) or not bound_id:
                raise QueryError("malformed identity binding")
            proofs = _membership_claims(claims, entity["crop_id"], bound_id)
            if binding.get("membership_snapshot_id"):
                proofs = [p for p in proofs if p["snapshot_id"] == binding["membership_snapshot_id"]]
            if binding.get("membership_snapshot_hash"):
                proofs = [p for p in proofs if p["snapshot_hash"] == binding["membership_snapshot_hash"]]
            for proof in proofs:
                proof_pages = _evidence_pages(proof)
                source_pages = binding.get("source_page_ids")
                if (not isinstance(source_pages, list) or len(source_pages) != len(proof_pages)
                        or set(source_pages) != proof_pages):
                    continue
                canonical_alias = _normalize(bound_id) == _normalize(binding["name"])
                title_alias = any(_normalize(e["page_title"]) == _normalize(binding["name"]) for e in proof["evidence"])
                alias_claim = next((
                    claim for claim in claims
                    if claim["claim_type"] == "property"
                    and claim["predicate"] == "ALIAS"
                    and claim["subject"] == {"type": "Variety", "id": bound_id}
                    and _normalize(claim.get("value", "")) == _normalize(binding["name"])
                    and _evidence_pages(claim) == proof_pages
                ), None)
                approved_alias = alias_claim is not None
                if canonical_alias or title_alias or approved_alias:
                    matches.append((bound_id, proof, alias_claim if approved_alias else None))
        unique = {(item_id, proof["snapshot_id"]): (item_id, proof, alias_claim)
                  for item_id, proof, alias_claim in matches}
        if len(unique) != 1:
            raise QueryError("ambiguous_or_unapproved_alias")
        variety_id, membership, alias_claim = next(iter(unique.values()))
    else:
        memberships = _membership_claims(claims, entity["crop_id"], variety_id)
        source_sets = {tuple(sorted(_evidence_pages(proof))) for proof in memberships}
        if not memberships or len(source_sets) != 1:
            raise QueryError("unresolved_or_ambiguous_crop_membership")
        membership = memberships[0]

    return {
        "entity": ("Variety", variety_id),
        "crop_id": entity["crop_id"],
        "membership": membership,
        "alias_claim": alias_claim if entity.get("name") is not None else None,
        "pages": _evidence_pages(membership),
    }


def _filter_value(claim, filter_spec):
    actual = _parse_interval(claim.get("value"))
    threshold = _parse_number(filter_spec["value"])
    if not actual or not threshold:
        return None
    if actual[2] not in UNITS or threshold[1] not in UNITS:
        return None
    actual_unit, threshold_unit = UNITS[actual[2]], UNITS[threshold[1]]
    expected_dimension = PROPERTY_DIMENSIONS[filter_spec["predicate"]]
    if (actual_unit[0] != threshold_unit[0]
            or actual_unit[0] != expected_dimension
            or threshold_unit[0] != expected_dimension):
        return None
    low = actual[0] * actual_unit[1]
    high = actual[1] * actual_unit[1]
    target = threshold[0] * threshold_unit[1]
    operation = filter_spec["op"]
    if operation == "<":
        return True if high < target else (False if low >= target else None)
    if operation == "<=":
        return True if high <= target else (False if low > target else None)
    if operation == ">":
        return True if low > target else (False if high <= target else None)
    if operation == ">=":
        return True if low >= target else (False if high < target else None)
    return True if low == high == target else (False if target < low or target > high else None)


def _path_edges(claims, node, predicate, current_pages, requested_qualifiers):
    result, diagnostics, reason_codes = [], [], set()
    for claim in claims:
        if claim["claim_type"] != "relationship" or claim["predicate"] != predicate:
            continue
        if claim["subject"] != {"type": node["type"], "id": node["id"]}:
            continue
        if current_pages is not None and not (_evidence_pages(claim) & current_pages):
            continue
        relation = scope_policy.compare_scopes(claim.get("qualifiers", {}), requested_qualifiers)
        if relation is scope_policy.ScopeRelation.DISJOINT:
            continue
        scope_eligible = relation in (scope_policy.ScopeRelation.MATCH, scope_policy.ScopeRelation.COVERS)
        if not scope_eligible:
            diagnostics.append(claim)
            reason_codes.add("scope_unknown")
        if not _relationship_identity_eligible(claim):
            if claim not in diagnostics:
                diagnostics.append(claim)
            reason_codes.add("unresolved_identity")
            continue
        if not scope_eligible:
            continue
        result.append(claim)
    if not result and not reason_codes:
        reason_codes.add("relationship_missing")
    return result, diagnostics, reason_codes


def _find_paths(claims, scope, predicates, target_entity, requested_qualifiers):
    # Supports, diagnostic claims, and display context are deliberately separate.
    states = [{"node": {"type": scope["entity"][0], "id": scope["entity"][1]},
               "claims": [], "diagnostic_claims": [], "context_claims": [],
               "pages": scope["pages"], "conflict": False}]
    reason_codes = set()
    dead_ends = []
    for predicate in predicates:
        next_states = []
        for state in states:
            edges, edge_diagnostics, edge_reasons = _path_edges(
                claims, state["node"], predicate, state["pages"], requested_qualifiers)
            reason_codes.update(edge_reasons)
            diagnostics = [*state["diagnostic_claims"], *edge_diagnostics]
            if not edges:
                if state["claims"]:
                    diagnostics.extend(state["claims"])
                dead_ends.append({**state, "diagnostic_claims": diagnostics,
                                  "claims": [], "dead_end": True})
                continue
            for edge in edges:
                target = edge["object"]
                supports = [*state["claims"], edge]
                edge_diagnostics = list(diagnostics)
                opposing = []
                if edge["predicate"] in {"RESISTANT_TO", "SUSCEPTIBLE_TO"}:
                    opposite = "SUSCEPTIBLE_TO" if edge["predicate"] == "RESISTANT_TO" else "RESISTANT_TO"
                    # Keep opponents unless the shared policy proves exclusion.
                    opposing = [
                        claim for claim in claims
                        if claim["claim_type"] == "relationship" and claim["predicate"] == opposite
                        and claim["subject"] == edge["subject"] and claim.get("object") == target
                        and scope_policy.compare_scopes(claim["qualifiers"], edge["qualifiers"])
                        is not scope_policy.ScopeRelation.DISJOINT
                        and (state["pages"] is None or _evidence_pages(claim) & state["pages"])
                    ]
                    edge_diagnostics.extend(opposing)
                    if opposing:
                        reason_codes.add("opposing_assertions")
                pages = _evidence_pages(edge)
                context_claims = list(state["context_claims"])
                if target["type"] == "Variety":
                    memberships = _membership_claims(claims, scope["crop_id"], target["id"])
                    page_sets = {tuple(sorted(_evidence_pages(item))) for item in memberships}
                    if not memberships or len(page_sets) != 1:
                        edge_diagnostics.append(edge)
                        reason_codes.add("unresolved_identity")
                        dead_ends.append({"node": target, "claims": [], "diagnostic_claims": [*edge_diagnostics, *supports],
                                          "context_claims": context_claims, "pages": pages,
                                          "conflict": bool(opposing), "dead_end": True})
                        continue
                    supports.extend(memberships)
                    pages = _evidence_pages(memberships[0])
                    context_claims.extend(
                        claim for claim in claims
                        if claim["claim_type"] == "property"
                        and claim["subject"] == target
                        and _evidence_pages(claim) & pages
                    )
                elif target["type"] == "Pest":
                    context_claims.extend(
                        claim for claim in claims
                        if claim["claim_type"] == "property"
                        and claim["subject"] == target
                        and _evidence_pages(claim) & pages
                    )
                next_states.append({"node": target, "claims": supports,
                                     "diagnostic_claims": edge_diagnostics,
                                    "context_claims": context_claims, "pages": pages,
                                    "conflict": state["conflict"] or bool(opposing)})
                if len(next_states) > MAX_PATHS:
                    raise OverflowError
        states = next_states
        if not states:
            break
    if target_entity:
        unmatched = [state for state in states if state["node"] != target_entity]
        for state in unmatched:
            dead_ends.append({**state, "diagnostic_claims": [*state["diagnostic_claims"], *state["claims"]],
                               "claims": [], "dead_end": True})
        states = [state for state in states if state["node"] == target_entity]
        if unmatched and not states:
            reason_codes.add("relationship_missing")
    if states:
        # A conclusive dead end does not negate another existential witness.
        reason_codes.discard("relationship_missing")
    return states, dead_ends, reason_codes


def _evaluate_filters(claims, scope, state, request):
    filters = request.get("filters", [])
    if not filters:
        return True, set(), [], []
    supports, diagnostics = [], []
    reason_codes = set()
    all_match = True
    for filter_spec in filters:
        if filter_spec.get("on", "root") == "root":
            node = scope["entity"]
            pages = scope["pages"]
        else:
            node = (state["node"]["type"], state["node"]["id"])
            pages = state["pages"]
        source_candidates = [
            claim for claim in claims
            if claim["subject"] == {"type": node[0], "id": node[1]}
            and (pages is None or _evidence_pages(claim) & pages)
            and claim["claim_type"] == "property"
            and CANONICAL_PREDICATES.get(claim["predicate"]) == CANONICAL_PREDICATES[filter_spec["predicate"]]
        ]
        requested_qualifiers = request.get("qualifiers", {})
        candidates = []
        uncertain_candidates = []
        for claim in source_candidates:
            relation = scope_policy.compare_scopes(claim.get("qualifiers", {}), requested_qualifiers)
            if relation in (scope_policy.ScopeRelation.MATCH, scope_policy.ScopeRelation.COVERS):
                candidates.append(claim)
            elif relation is not scope_policy.ScopeRelation.DISJOINT:
                uncertain_candidates.append(claim)
        if not candidates:
            if source_candidates:
                reason_codes.add("scope_unknown")
                diagnostics.extend(uncertain_candidates)
            else:
                reason_codes.add("property_missing")
            all_match = False
            continue
        outcomes = [_filter_value(claim, filter_spec) for claim in candidates]
        if uncertain_candidates:
            reason_codes.add("scope_unknown")
        if any(value is None for value in outcomes):
            reason_codes.add(_numeric_reason(candidates, filter_spec))
        if any(value is True for value in outcomes) and any(value is False for value in outcomes):
            reason_codes.add("scalar_alternatives")
        if len({claim.get("value") for claim in candidates}) > 1:
            reason_codes.add("scalar_alternatives")
        if outcomes and all(value is False for value in outcomes):
            reason_codes.add("numeric_not_satisfied")
        if (all(value is True for value in outcomes) and not uncertain_candidates
                and "scalar_alternatives" not in reason_codes):
            supports.extend(candidates)
        else:
            diagnostics.extend(candidates)
            diagnostics.extend(uncertain_candidates)
        if not all(value is True for value in outcomes):
            all_match = False
    return all_match, reason_codes, supports, diagnostics


def _numeric_reason(claims, filter_spec):
    threshold = _parse_number(filter_spec.get("value"))
    if threshold is None or threshold[1] not in UNITS:
        return "numeric_unsupported"
    expected = PROPERTY_DIMENSIONS[filter_spec["predicate"]]
    if UNITS[threshold[1]][0] != expected:
        return "numeric_unsupported"
    for claim in claims:
        actual = _parse_interval(claim.get("value"))
        if actual is None or actual[2] not in UNITS or UNITS[actual[2]][0] != expected:
            return "numeric_unsupported"
    return "numeric_uncertain"


def _assertion_reason_codes(claims):
    reasons = set()
    diagnostic_ids = set()
    properties = {}
    for claim in claims:
        if claim.get("claim_type") == "property":
            predicate = CANONICAL_PREDICATES.get(claim.get("predicate"), claim.get("predicate"))
            key = (projection.canonical_json(claim.get("subject")), predicate)
            previous = properties.setdefault(key, [])
            alternatives = [other for other in previous
                            if projection.canonical_json(other.get("value")) != projection.canonical_json(claim.get("value"))
                            and scope_policy.compare_scopes(other.get("qualifiers", {}), claim.get("qualifiers", {}))
                            is not scope_policy.ScopeRelation.DISJOINT]
            if alternatives:
                reasons.add("scalar_alternatives")
                diagnostic_ids.add(claim["snapshot_id"])
                diagnostic_ids.update(other["snapshot_id"] for other in alternatives)
            previous.append(claim)
        if claim.get("predicate") in {"RESISTANT_TO", "SUSCEPTIBLE_TO"}:
            opposite = "SUSCEPTIBLE_TO" if claim["predicate"] == "RESISTANT_TO" else "RESISTANT_TO"
            opponents = [other for other in claims
                         if other.get("predicate") == opposite
                         and other.get("subject") == claim.get("subject")
                         and other.get("object") == claim.get("object")
                         and scope_policy.compare_scopes(other.get("qualifiers", {}), claim.get("qualifiers", {}))
                         is not scope_policy.ScopeRelation.DISJOINT]
            if opponents:
                reasons.add("opposing_assertions")
                diagnostic_ids.add(claim["snapshot_id"])
                diagnostic_ids.update(other["snapshot_id"] for other in opponents)
    return reasons, diagnostic_ids


def query(document: dict[str, Any], request: dict[str, Any], authorizer: Callable | None, *, bindings=None):
    try:
        # Freeze caller-owned data before callbacks or interpretation can observe it.
        doc = projection.validate_manifest_document(copy.deepcopy(document))
        generation = projection.generation_id(doc)
    except Exception:
        return _response("", "unsupported", reasons=[_reason("invalid_request")])

    if isinstance(request, dict):
        request = copy.deepcopy(request)
        request.setdefault("purpose", "evidence")
    schema_path = Path(__file__).resolve().parents[1] / "schema" / "reviewed-query.v2.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        _validate_request(request, schema)
    except Exception:
        return _response(generation, "unsupported", reasons=[_reason("invalid_request")])

    claims = doc["claims"]
    if bindings is not None and not isinstance(bindings, list):
        return _response(generation, "unsupported", partial=True, reasons=[_reason("invalid_request")])
    try:
        bindings = copy.deepcopy(bindings)
    except Exception:
        return _response(generation, "unsupported", partial=True, reasons=[_reason("invalid_request")])
    if len(claims) > MAX_CLAIMS:
        return _response(generation, "truncated", claims_scanned=0)
    if authorizer is None:
        return _response(generation, "dependency_failure", reasons=[_reason("authorizer_missing")])
    if len(claims) > MAX_AUTHORIZED_CLAIMS:
        return _response(generation, "truncated", claims_scanned=MAX_AUTHORIZED_CLAIMS)

    first_authorization = _authorize(claims, authorizer)
    if first_authorization is None:
        return _response(generation, "dependency_failure")

    try:
        scopes = [_scope_root(entity, claims, [] if bindings is None else bindings) for entity in request["entities"]]
    except QueryError as error:
        code = "invalid_request" if str(error) in {
            "malformed identity binding", "Pest roots require an explicit path context"
        } else "unresolved_identity"
        return _response(generation, "unsupported", partial=True, reasons=[_reason(code)])
    except (TypeError, KeyError):
        return _response(generation, "unsupported", partial=True, reasons=[_reason("invalid_request")])
    except Exception:
        return _response(generation, "unsupported", partial=True, reasons=[_reason("unresolved_identity")])

    result_groups = []
    eligible_result_count = 0
    used_claims = {}
    unsupported = False
    diagnostic_codes = set()
    path_overflow = False
    for scope in scopes:
        if scope["membership"] is not None:
            used_claims[scope["membership"]["snapshot_id"]] = scope["membership"]
        if scope.get("alias_claim") is not None:
            used_claims[scope["alias_claim"]["snapshot_id"]] = scope["alias_claim"]
        properties = [
            claim for claim in claims
            if claim["subject"] == {"type": scope["entity"][0], "id": scope["entity"][1]}
            and claim["claim_type"] == "property"
            and (scope["pages"] is None or _evidence_pages(claim) & scope["pages"])
        ]

        path_sets = [{"node": {"type": scope["entity"][0], "id": scope["entity"][1]},
                      "claims": [], "diagnostic_claims": [], "context_claims": [],
                      "pages": scope["pages"], "conflict": False}]
        path_diagnostics = []
        if request["intent"] in {"path", "conjunction"}:
            try:
                if request["intent"] == "conjunction":
                    branches = []
                    branch_reasons = set()
                    for branch in request["branches"]:
                        branch_target = branch.get("target")
                        branch_ref = {"type": branch_target["type"], "id": branch_target["id"]} if branch_target else None
                        try:
                            states, dead_ends, reasons = _find_paths(
                                claims, scope, branch["path"], branch_ref, request.get("qualifiers", {}))
                        except OverflowError:
                            raise
                        except QueryError:
                            states, dead_ends, reasons = [], [], {"unresolved_identity"}
                        except Exception:
                            states, dead_ends, reasons = [], [], {"invalid_request"}
                        branches.append((states, dead_ends))
                        branch_reasons.update(reasons)
                    state_lists = [item[0] for item in branches]
                    branch_missing = any(not states for states in state_lists)
                    if branch_missing and not branch_reasons:
                        branch_reasons.add("relationship_missing")
                    # Each branch is existential from this exact root. Union every eligible
                    # witness per branch; never select a preferred assertion or cross-join.
                    branch_claim_ids = []
                    all_branch_claims = {}
                    all_diagnostics = {}
                    all_context = {}
                    branch_conflict = "opposing_assertions" in branch_reasons
                    for states, dead_ends in branches:
                        ids = {claim["snapshot_id"] for claim in
                               (scope.get("membership"), scope.get("alias_claim")) if claim is not None and states}
                        for state in states:
                            branch_conflict |= state.get("conflict", False)
                            for claim in state["claims"]:
                                ids.add(claim["snapshot_id"])
                                all_branch_claims[claim["snapshot_id"]] = claim
                            for claim in state.get("diagnostic_claims", []):
                                all_diagnostics[claim["snapshot_id"]] = claim
                            for claim in state.get("context_claims", []):
                                all_context[claim["snapshot_id"]] = claim
                        for dead_end in dead_ends:
                            branch_conflict |= dead_end.get("conflict", False)
                            for claim in dead_end.get("diagnostic_claims", []):
                                all_diagnostics[claim["snapshot_id"]] = claim
                            for claim in dead_end.get("context_claims", []):
                                all_context[claim["snapshot_id"]] = claim
                        branch_claim_ids.append(sorted(ids))
                    path_sets = [{"node": {"type": scope["entity"][0], "id": scope["entity"][1]},
                                  "claims": list(all_branch_claims.values()),
                                  "diagnostic_claims": list(all_diagnostics.values()),
                                  "context_claims": list(all_context.values()),
                                  "branch_claim_ids": branch_claim_ids,
                                  "pages": scope["pages"], "conflict": branch_conflict,
                                  "branch_missing": branch_missing}]
                    diagnostic_codes.update(branch_reasons)
                    unsupported |= bool(branch_reasons & BLOCKING_REASONS)
                    if len(path_sets[0]["claims"]) > MAX_PATHS:
                        raise OverflowError
                else:
                    target = request.get("target")
                    target_ref = {"type": target["type"], "id": target["id"]} if target else None
                    path_sets, dead_ends, path_reasons = _find_paths(claims, scope, request["path"], target_ref, request.get("qualifiers", {}))
                    path_diagnostics = dead_ends
                    diagnostic_codes.update(path_reasons)
                    unsupported |= bool(path_reasons & BLOCKING_REASONS)
            except OverflowError:
                path_overflow = True
                break
            except QueryError:
                unsupported = True
                diagnostic_codes.add("unresolved_identity")
                continue
            except Exception:
                unsupported = True
                diagnostic_codes.add("invalid_request")
                continue

        for path_state in path_sets:
            unsupported |= path_state.get("conflict", False)
            if path_state.get("conflict"):
                diagnostic_codes.add("opposing_assertions")
            try:
                matched, filter_reasons, filter_claims, filter_diagnostics = _evaluate_filters(claims, scope, path_state, request)
            except Exception:
                unsupported = True
                diagnostic_codes.add("invalid_request")
                continue
            unsupported |= bool(filter_reasons & BLOCKING_REASONS)
            diagnostic_codes.update(filter_reasons)
            if not matched:
                if filter_reasons or path_state.get("branch_missing"):
                    diagnostic_claims = [*filter_diagnostics, *path_state.get("diagnostic_claims", []),
                                         *path_state.get("context_claims", [])]
                    identity_claims = []
                    if scope["membership"] is not None:
                        identity_claims.append(scope["membership"])
                    if scope.get("alias_claim") is not None:
                        identity_claims.append(scope["alias_claim"])
                    displayed = [*diagnostic_claims, *identity_claims, *path_state["claims"], *filter_claims]
                    if displayed:
                        unique_displayed = {item["snapshot_id"]: item for item in displayed}
                        for claim in unique_displayed.values():
                            used_claims[claim["snapshot_id"]] = claim
                        result_groups.append({"subject": {"type": scope["entity"][0], "id": scope["entity"][1], "crop_id": scope["crop_id"]},
                                               "_claims": list(unique_displayed.values()),
                                               "eligible_claim_ids": [],
                                              "diagnostic_claim_ids": sorted({claim["snapshot_id"] for claim in diagnostic_claims}),
                                              "branch_claim_ids": path_state.get("branch_claim_ids")})
                continue
            group_claims = []
            if scope["membership"] is not None:
                group_claims.append(scope["membership"])
            if scope.get("alias_claim") is not None:
                group_claims.append(scope["alias_claim"])
            group_claims.extend(path_state["claims"])
            group_claims.extend(path_state.get("context_claims", []))
            group_claims.extend(path_state.get("diagnostic_claims", []))
            group_claims.extend(filter_claims)
            group_claims.extend(filter_diagnostics)
            group_claims.extend(properties if not request.get("filters") else [])

            # Lookup returns root relationships and properties, not properties alone.
            if request["intent"] == "lookup":
                group_claims.extend(
                    claim for claim in claims
                    if claim["subject"] == {"type": scope["entity"][0], "id": scope["entity"][1]}
                    and claim["claim_type"] == "relationship"
                    and (scope["pages"] is None or _evidence_pages(claim) & scope["pages"])
                )
                group_claims.extend(properties)

            if request.get("purpose", "evidence") == "answer" and request["intent"] == "lookup":
                scope_eligible = []
                scope_diagnostic_ids = set()
                identity_diagnostic_ids = set()
                for claim in group_claims:
                    applicability = scope_policy.compare_scopes(claim.get("qualifiers", {}), request.get("qualifiers", {}))
                    scope_eligible.append(claim)
                    if applicability not in (scope_policy.ScopeRelation.MATCH, scope_policy.ScopeRelation.COVERS):
                        unsupported = True
                        diagnostic_codes.add("scope_unknown")
                        scope_diagnostic_ids.add(claim["snapshot_id"])
                    if (claim["claim_type"] == "relationship"
                            and claim["subject"] == {"type": scope["entity"][0], "id": scope["entity"][1]}
                            and not _relationship_identity_eligible(claim)):
                        diagnostic_codes.add("unresolved_identity")
                        unsupported = True
                        identity_diagnostic_ids.add(claim["snapshot_id"])
                    elif (claim["claim_type"] == "relationship"
                          and claim["subject"] == {"type": scope["entity"][0], "id": scope["entity"][1]}
                          and claim.get("object", {}).get("type") == "Variety"):
                        memberships = _membership_claims(claims, scope["crop_id"], claim["object"]["id"])
                        page_sets = {tuple(sorted(_evidence_pages(item))) for item in memberships}
                        if not memberships or len(page_sets) != 1:
                            diagnostic_codes.add("unresolved_identity")
                            unsupported = True
                            identity_diagnostic_ids.add(claim["snapshot_id"])
                group_claims = scope_eligible
                assertion_reasons, assertion_diag_ids = _assertion_reason_codes(group_claims)
                diagnostic_codes.update(assertion_reasons)
                unsupported |= bool(assertion_reasons)
            else:
                assertion_reasons = set()
                assertion_diag_ids = set()
                scope_diagnostic_ids = set()
                identity_diagnostic_ids = set()

            unique_group = {claim["snapshot_id"]: claim for claim in group_claims}
            group_claims = list(unique_group.values())
            context_claim_ids = set()
            if request["intent"] != "lookup":
                context_claim_ids.update(claim["snapshot_id"] for claim in path_state.get("context_claims", []))
                if not request.get("filters"):
                    context_claim_ids.update(claim["snapshot_id"] for claim in properties)
            diagnostic_claim_ids = ({claim["snapshot_id"] for claim in
                [*path_state.get("diagnostic_claims", []), *filter_diagnostics]}
                | assertion_diag_ids | scope_diagnostic_ids | identity_diagnostic_ids)
            if request["intent"] == "lookup":
                eligible_claim_ids = set(unique_group) - diagnostic_claim_ids
            elif path_state.get("branch_missing"):
                eligible_claim_ids = set()
            else:
                eligible_claim_ids = {claim["snapshot_id"] for claim in
                    [*path_state["claims"], *filter_claims]}
                eligible_claim_ids.update(claim["snapshot_id"] for claim in
                    (scope.get("membership"), scope.get("alias_claim")) if claim is not None)
            for claim in group_claims:
                used_claims[claim["snapshot_id"]] = claim
            if group_claims:
                result_groups.append({"subject": {"type": scope["entity"][0], "id": scope["entity"][1], "crop_id": scope["crop_id"]},
                                       "_claims": group_claims,
                                       "eligible_claim_ids": sorted(eligible_claim_ids),
                                       "context_claim_ids": sorted(context_claim_ids),
                                       "diagnostic_claim_ids": sorted(diagnostic_claim_ids),
                                      "branch_claim_ids": path_state.get("branch_claim_ids")})
                if (not assertion_reasons and not filter_reasons
                        and not path_state.get("branch_missing")
                        and not path_state.get("conflict")):
                    eligible_result_count += 1
            if len(result_groups) > MAX_RESULTS:
                return _response(generation, "truncated", claims_scanned=len(claims))

        for dead_end in path_diagnostics:
            diagnostic_claims = list(dead_end.get("diagnostic_claims", []))
            diagnostic_claims.extend(dead_end.get("context_claims", []))
            identity_claims = [scope["membership"]] if scope["membership"] is not None else []
            unique_diagnostics = {claim["snapshot_id"]: claim for claim in diagnostic_claims}
            claims_for_group = {claim["snapshot_id"]: claim for claim in [*identity_claims, *diagnostic_claims]}
            if claims_for_group:
                result_groups.append({
                    "subject": {"type": scope["entity"][0], "id": scope["entity"][1], "crop_id": scope["crop_id"]},
                    "_claims": list(claims_for_group.values()),
                    "eligible_claim_ids": [],
                    "diagnostic_claim_ids": sorted(unique_diagnostics),
                    "branch_claim_ids": None,
                })
                if len(result_groups) > MAX_RESULTS:
                    return _response(generation, "truncated", claims_scanned=len(claims))

    if path_overflow:
        return _response(generation, "truncated", claims_scanned=len(claims))

    results = []
    for group in result_groups:
        serialized_claims = []
        for claim in group["_claims"]:
            public_claim = {key: value for key, value in claim.items() if key != "evidence"}
            serialized_claims.append({
                "claim": public_claim,
                "citation": {key: claim[key] for key in ("snapshot_id", "snapshot_hash", "review_version")},
                "evidence": claim["evidence"],
            })
        result = {"subject": group["subject"], "claims": serialized_claims,
                  "eligible_claim_ids": group.get("eligible_claim_ids", [])}
        if group.get("branch_claim_ids") is not None:
            result["branch_claim_ids"] = group["branch_claim_ids"]
        if group.get("diagnostic_claim_ids"):
            result["diagnostic_claim_ids"] = group["diagnostic_claim_ids"]
        if group.get("context_claim_ids"):
            result["context_claim_ids"] = group["context_claim_ids"]
        results.append(result)

    returned_evidence_count = sum(
        len(item["evidence"])
        for group in results
        for item in group["claims"]
    )
    if returned_evidence_count > MAX_EVIDENCE:
        return _response(generation, "truncated", claims_scanned=len(claims))

    coverage_complete = doc["snapshot_count"] == len(claims)
    status = "unsupported" if unsupported or not coverage_complete else ("ok" if eligible_result_count else "empty")
    if not coverage_complete:
        diagnostic_codes.add("partial_manifest")
    if status == "empty":
        diagnostic_codes.add("empty_result")
    response = _response(generation, status, results=results,
                         complete=coverage_complete and not unsupported,
                         partial=not coverage_complete or unsupported,
                         reasons=[_reason(code) for code in sorted(diagnostic_codes)])
    if len(projection.canonical_json(response).encode("utf-8")) > MAX_RESPONSE_BYTES:
        return _response(generation, "truncated", claims_scanned=len(claims))

    # Recheck after interpretation to catch revocation or revision changes during the query.
    final_authorization = _authorize(claims, authorizer)
    if final_authorization is None or any(
        projection.canonical_json(final_authorization[snapshot_id])
        != projection.canonical_json(claim)
        for snapshot_id, claim in first_authorization.items()
    ):
        return _response(generation, "dependency_failure")
    return response


def answer_query(document, request, graph_authorizer, text_authorizer=None, generator=None,
                 wiki_base="http://127.0.0.1", *, bindings=None, text_candidates=None,
                 context_budget=20000, wiki_script_path="/w/index.php") -> dict:
    """Run the bounded query and compose only its eligible, freshly authorized evidence."""
    output = {"version": "wikikg.reviewed-answer.v1", "offline_only": True,
              "status": "blocked", "query": None, "answer": None,
              "citations": [], "reasons": [],
              "text_behavior": "disabled; text cannot substitute for unsupported graph evidence"}
    try:
        # Interpretation and composition must see the same caller-independent question.
        request = copy.deepcopy(request)
        bindings = copy.deepcopy(bindings)
        text_candidates = copy.deepcopy(text_candidates)
        result = query(document, request, graph_authorizer, bindings=bindings)
        output["query"] = result
        if result.get("status") == "dependency_failure":
            output["reasons"] = result.get("reasons") or [_reason("authorization_failed")]
            return output
        import assistant_contract

        def attach_query_citations():
            if not any(group.get("claims") for group in result.get("results", [])):
                return True
            if (not assistant_contract._valid_wiki_base(wiki_base)
                    or not assistant_contract._valid_wiki_script_path(wiki_script_path)):
                output["reasons"] = [*result.get("reasons", []), _reason("invalid_request")]
                return False
            for group in result.get("results", []):
                for item in group.get("claims", []):
                    for evidence in item.get("evidence", []):
                        citation = dict(item["citation"])
                        citation.update(page_id=evidence["page_id"], revision_id=evidence["revision_id"],
                                        content_hash=evidence["content_hash"],
                                        url=wiki_base.rstrip("/") + wiki_script_path + "?oldid=" + str(evidence["revision_id"]))
                        output["citations"].append(citation)
            return True

        citations_valid = attach_query_citations()
        if result.get("status") == "empty":
            output["status"] = "evidence" if citations_valid else "blocked"
            output["reasons"] = output["reasons"] or result.get("reasons", []) or [_reason("empty_result")]
            return output
        if result.get("status") != "ok":
            output["reasons"] = output["reasons"] or result.get("reasons") or [_reason("invalid_request")]
            return output
        if not citations_valid:
            output["status"] = "blocked"
            return output
        purpose = request.get("purpose", "evidence")
        if purpose != "answer" or generator is None:
            output["status"] = "evidence"
            output["reasons"] = result.get("reasons", [])
            return output
        if not result.get("complete") or result.get("partial") or result.get("reasons"):
            output["reasons"] = result.get("reasons") or [{"code": "scope_unknown", "message": "Interpretation is incomplete or restricted."}]
            return output
        if text_candidates and text_authorizer is None:
            output["status"] = "blocked"
            output["reasons"] = [_reason("text_authorizer_missing")]
            return output
        candidates, seen = [], set()
        for group in result["results"]:
            eligible_ids = set(group.get("eligible_claim_ids", []))
            for item in group["claims"]:
                claim = item["claim"]
                if claim["snapshot_id"] not in seen and claim["snapshot_id"] in eligible_ids:
                    candidates.append({**claim, "evidence": item["evidence"]})
                    seen.add(claim["snapshot_id"])
        composed = assistant_contract.compose_answer(
            candidates, text_candidates or [], graph_authorizer, text_authorizer, generator,
            wiki_base, graph_generation=result.get("generation"), context_budget=context_budget,
            incomplete=False, conflicts=False, wiki_script_path=wiki_script_path, question=request)
        output.update(status=composed.get("status", "blocked"), answer=composed.get("answer"),
                      citations=composed.get("citations", []))
        if composed.get("status") == "dependency_failure":
            output.update(query=None, citations=[], reasons=[_reason("authorization_failed")])
        elif composed.get("status") == "conflict":
            output["reasons"] = [_reason("opposing_assertions")]
        elif composed.get("status") == "partial":
            output["reasons"] = [_reason("limits")]
        elif composed.get("status") == "abstention":
            output["reasons"] = [_reason("relationship_missing")]
        return output
    except Exception:
        output.update(status="blocked", query=None, answer=None, citations=[])
        output["reasons"] = [{"code": "invalid_request", "message": "Request or authorized query could not be interpreted."}]
        return output


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate an exported snapshot only; no query or current-user authorization."
    )
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        document = projection.read_manifest_file(args.manifest)
        projection.validate_manifest_document(document)
        print(json.dumps({
            "status": "valid",
            "offline_only": True,
            "mode": "exported-snapshot-only",
            "generation": projection.generation_id(document),
        }, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "invalid", "offline_only": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
