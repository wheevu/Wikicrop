"""Pure offline contracts for current-evidence composition and ingestion receipts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

CONTRACT_VERSION = "assistant-ingestion.v1"
PROJECTION_VERSION = "wikikg.reviewed-projection.v1"
MAX_CANDIDATES = 100
MAX_EVIDENCE = 500
MAX_SOURCE_BYTES = 100_000
MAX_ANSWER_BYTES = 8_000
MAX_CITATIONS = 500
DEFAULT_BUDGET = 20_000
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
GRAPH_IDENTITY = ("snapshot_id", "snapshot_hash", "review_version")
TEXT_IDENTITY = ("page_id", "revision_id", "content_hash")


def _valid_wiki_base(value: Any) -> bool:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "localhost", "::1")
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and parsed.path in ("", "/")
            and bool(parsed.netloc)
            and (port is None or 1 <= port <= 65535))


def _valid_wiki_script_path(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*index\.php", value) is not None


def _hash(value: Any) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def _json_bytes(value: Any) -> bytes:
    return _projection_module().canonical_json(value).encode("utf-8")


def _identity(value: dict[str, Any]) -> dict[str, Any]:
    required = ("page_id", "revision_id", "content_hash", "audience")
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError("invalid ingestion identity fields")
    if any(type(value[k]) is not int or value[k] < 1 for k in required[:2]):
        raise ValueError("invalid page or revision identity")
    if not _hash(value["content_hash"]):
        raise ValueError("invalid content hash")
    if value["audience"] != "actor-only":
        raise ValueError("ingestion audience must be actor-only")
    return {key: value[key] for key in required}


def idempotence_key(identity: dict[str, Any]) -> str:
    encoded = _json_bytes(_identity(identity))
    return hashlib.sha256((CONTRACT_VERSION + "\0").encode() + encoded).hexdigest()


def _current_text(text_authorizer: Callable, candidate: dict[str, Any]) -> dict[str, Any]:
    result = text_authorizer([copy.deepcopy(candidate)])
    if isinstance(result, dict):
        if result.get("status") != "ok" or set(result) != {"status", "text"}:
            raise ValueError("current text authorization failed")
        result = result["text"]
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError("current text authorization is incomplete")
    resolved = copy.deepcopy(result[0])
    if (set(resolved) != {*TEXT_IDENTITY, "text"}
            or _json_bytes({key: resolved[key] for key in TEXT_IDENTITY})
            != _json_bytes(candidate)):
        raise ValueError("current text identity differs")
    text = resolved["text"]
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("current source text is invalid or too large")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != candidate["content_hash"]:
        raise ValueError("current source text hash differs")
    return resolved


def _authorized_text(candidates: list[dict[str, Any]], authorizer: Callable) -> list[dict[str, Any]]:
    if not callable(authorizer):
        raise ValueError("current text authorizer unavailable")
    result = authorizer(copy.deepcopy(candidates))
    if isinstance(result, dict):
        if result.get("status") != "ok" or set(result) != {"status", "text"}:
            raise ValueError("current text authorization failed")
        result = result["text"]
    if not isinstance(result, list) or len(result) != len(candidates):
        raise ValueError("current text authorization is incomplete")
    resolved_by_identity = {}
    for item in copy.deepcopy(result):
        if not isinstance(item, dict):
            raise ValueError("current text item is malformed")
        key = tuple(item.get(field) for field in TEXT_IDENTITY)
        if key in resolved_by_identity:
            raise ValueError("duplicate current text identity")
        resolved_by_identity[key] = item
    expected_keys = [tuple(candidate[key] for key in TEXT_IDENTITY) for candidate in candidates]
    if set(resolved_by_identity) != set(expected_keys):
        raise ValueError("current text identity differs")
    return [_current_text(lambda _: [resolved_by_identity[key]], candidate)
            for candidate, key in zip(candidates, expected_keys)]


def build_ingestion_request(identity: dict[str, Any], content: str,
                            text_authorizer: Callable) -> dict[str, Any]:
    """Build a verified actor-only request; never transfers it to a backend."""
    identity = _identity(identity)
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("ingestion content is invalid or too large")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if digest != identity["content_hash"]:
        raise ValueError("ingestion content hash differs")
    authorized = _current_text(text_authorizer, {key: identity[key] for key in TEXT_IDENTITY})
    if authorized["text"] != content:
        raise ValueError("ingestion content is not current source text")
    return {
        "version": CONTRACT_VERSION,
        "identity": identity,
        "idempotence_key": idempotence_key(identity),
        "content": content,
    }


def ingestion_ack(request: dict[str, Any], http_status: int,
                  response: dict[str, Any] | None) -> dict[str, Any]:
    """Return a content-free receipt only for an exact explicit success ack."""
    failure = {"status": "failure", "version": CONTRACT_VERSION}
    try:
        if (not isinstance(request, dict) or set(request) !=
                {"version", "identity", "idempotence_key", "content"}
                or request["version"] != CONTRACT_VERSION
                or not isinstance(request["content"], str)
                or hashlib.sha256(request["content"].encode("utf-8")).hexdigest()
                != request["identity"]["content_hash"]
                or request["idempotence_key"] != idempotence_key(request["identity"])):
            return failure
        if type(http_status) is not int or not 200 <= http_status < 300:
            return failure
        if not isinstance(response, dict) or set(response) != {
                "version", "identity", "idempotence_key", "status"}:
            return failure
        if (response["version"] != CONTRACT_VERSION
                or _json_bytes(response["identity"]) != _json_bytes(request["identity"])
                or response["idempotence_key"] != request["idempotence_key"]
                or response["status"] != "success"):
            return failure
        return {"status": "success", "version": CONTRACT_VERSION,
                "identity": copy.deepcopy(request["identity"]),
                "idempotence_key": request["idempotence_key"]}
    except (KeyError, TypeError, ValueError, UnicodeError):
        return failure


@lru_cache(maxsize=1)
def _projection_module():
    path = Path(__file__).with_name("reviewed_projection.py")
    spec = importlib.util.spec_from_file_location("_wikikg_reviewed_projection_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("reviewed projection validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _scope_module():
    path = Path(__file__).with_name("qualifier_scope.py")
    spec = importlib.util.spec_from_file_location("_wikikg_qualifier_scope_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("qualifier scope policy is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_claim(claim: Any) -> bool:
    if not isinstance(claim, dict) or not isinstance(claim.get("evidence"), list) \
            or len(claim["evidence"]) > MAX_EVIDENCE:
        return False
    wrapper = {
        "schema_version": PROJECTION_VERSION, "snapshot_count": 1,
        "source_watermark": hashlib.sha256(_json_bytes([claim])).hexdigest(), "claims": [claim],
    }
    try:
        _projection_module().validate_manifest_document(wrapper)
        return True
    except Exception:
        return False


def _text_candidate(candidate: Any) -> bool:
    return (isinstance(candidate, dict) and set(candidate) == set(TEXT_IDENTITY)
            and type(candidate["page_id"]) is int and candidate["page_id"] > 0
            and type(candidate["revision_id"]) is int and candidate["revision_id"] > 0
            and _hash(candidate["content_hash"]))


def _call_authorizer(candidates: list[dict[str, Any]], authorizer: Callable,
                     output_key: str, validator: Callable) -> list[dict[str, Any]]:
    if not callable(authorizer):
        raise ValueError("current authorizer unavailable")
    expected = copy.deepcopy(candidates)
    request = copy.deepcopy(candidates)
    result = authorizer(request)
    if isinstance(result, dict):
        if result.get("status") != "ok" or set(result) != {"status", output_key}:
            raise ValueError("current authorization failed")
        result = result[output_key]
    if not isinstance(result, list) or len(result) != len(expected):
        raise ValueError("current authorizer returned incomplete results")
    result = copy.deepcopy(result)
    if not all(validator(item) for item in result):
        raise ValueError("current authorizer returned malformed data")
    # Enforce immutable exact payload, not just a matching identity tuple.
    expected_by_key = {tuple(item[key] for key in (GRAPH_IDENTITY if output_key == "claims" else TEXT_IDENTITY)): item
                       for item in expected}
    result_by_key = {tuple(item[key] for key in (GRAPH_IDENTITY if output_key == "claims" else TEXT_IDENTITY)): item
                     for item in result}
    if len(expected_by_key) != len(expected) or len(result_by_key) != len(result):
        raise ValueError("duplicate current identities")
    if any(_json_bytes(result_by_key[key]) != _json_bytes(expected_by_key[key])
           for key in expected_by_key):
        raise ValueError("current canonical payload changed")
    return [result_by_key[tuple(item[key] for key in
            (GRAPH_IDENTITY if output_key == "claims" else TEXT_IDENTITY))] for item in expected]


def _conflicted(claims: list[dict[str, Any]]) -> bool:
    scope_policy = _scope_module()
    alternatives: dict[bytes, list[dict[str, Any]]] = {}
    opposing: dict[bytes, list[dict[str, Any]]] = {}
    for claim in claims:
        if claim["claim_type"] == "property":
            key = _json_bytes([claim["subject"], claim["predicate"]])
            previous = alternatives.setdefault(key, [])
            if any(_json_bytes(other["value"]) != _json_bytes(claim["value"])
                   and scope_policy.compare_scopes(other["qualifiers"], claim["qualifiers"])
                   is not scope_policy.ScopeRelation.DISJOINT
                   for other in previous):
                return True
            previous.append(claim)
        elif claim["predicate"] in ("RESISTANT_TO", "SUSCEPTIBLE_TO"):
            key = _json_bytes([claim["subject"], claim["object"]])
            previous = opposing.setdefault(key, [])
            # Missing or different strings are not proof of scope exclusion.
            if any(other["predicate"] != claim["predicate"]
                   and scope_policy.compare_scopes(other["qualifiers"], claim["qualifiers"])
                   is not scope_policy.ScopeRelation.DISJOINT
                   for other in previous):
                return True
            previous.append(claim)
    return False


def _citation_id(claim: dict[str, Any], evidence: dict[str, Any]) -> str:
    return ":".join(("graph", claim["snapshot_id"], str(claim["review_version"]),
                     evidence["evidence_id"], str(evidence["page_id"]),
                     str(evidence["revision_id"]), evidence["content_hash"]))


def _failure(status: str = "dependency_failure") -> dict[str, Any]:
    return {"status": status, "answer": None, "citations": [], "claims": [], "text": []}


def compose_answer(graph_candidates: list[dict[str, Any]], text_candidates: list[dict[str, Any]],
                   graph_authorizer: Callable | None, text_authorizer: Callable | None,
                   generator: Callable[[str], dict[str, Any]] | None, wiki_base: str,
                   *, graph_generation: str | None = None, context_budget: int = DEFAULT_BUDGET,
                   incomplete: bool = False, conflicts: bool = False,
                   wiki_script_path: str = "/w/index.php",
                   question: dict[str, Any] | None = None) -> dict[str, Any]:
    """Authorize exact canonical payloads before and after bounded local generation."""
    try:
        if (not isinstance(graph_candidates, list) or not isinstance(text_candidates, list)
                or len(graph_candidates) + len(text_candidates) > MAX_CANDIDATES
                or type(context_budget) is not int or not 1 <= context_budget <= DEFAULT_BUDGET
                or incomplete or not _valid_wiki_base(wiki_base)):
            return _failure("partial" if incomplete else "dependency_failure")
        if not _valid_wiki_script_path(wiki_script_path):
            return _failure()
        if graph_candidates and (not _hash(graph_generation)):
            return _failure()
        if not graph_candidates and graph_generation is not None:
            return _failure()
        graph = copy.deepcopy(graph_candidates)
        text_ids = copy.deepcopy(text_candidates)
        question = copy.deepcopy(question)
        if (not all(_validate_claim(claim) for claim in graph)
                or not all(_text_candidate(item) for item in text_ids)
                or len({claim["snapshot_id"] for claim in graph}) != len(graph)
                or len({item["page_id"] for item in text_ids}) != len(text_ids)):
            return _failure()
        total_evidence = sum(len(claim["evidence"]) for claim in graph)
        if total_evidence > MAX_EVIDENCE:
            return _failure("partial")

        def fresh() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            authorized_graph = (_call_authorizer(graph, graph_authorizer, "claims", _validate_claim)
                                if graph else [])
            resolved_text = _authorized_text(text_ids, text_authorizer) if text_ids else []
            return authorized_graph, resolved_text

        authorized_graph, authorized_text = fresh()
        text_by_source = {(item["page_id"], item["revision_id"], item["content_hash"]): item["text"]
                          for item in authorized_text}
        for claim in authorized_graph:
            for evidence in claim["evidence"]:
                key = (evidence["page_id"], evidence["revision_id"], evidence["content_hash"])
                source_text = text_by_source.get(key)
                if source_text is not None and evidence["location_type"] == "source_span":
                    if source_text[evidence["source_offset_start"]:evidence["source_offset_end"]] \
                            != evidence["supporting_span"]:
                        return _failure()
        source_map: dict[tuple[int, int, str], dict[str, Any]] = {}
        for claim in authorized_graph:
            for evidence in claim["evidence"]:
                key = (evidence["page_id"], evidence["revision_id"], evidence["content_hash"])
                source_map.setdefault(key, {"page_id": key[0], "revision_id": key[1],
                                            "content_hash": key[2], "page_title": evidence["page_title"]})
        for source in authorized_text:
            key = (source["page_id"], source["revision_id"], source["content_hash"])
            item = source_map.setdefault(key, {key_name: source[key_name] for key_name in TEXT_IDENTITY})
            item["text"] = source["text"]
        source_list = list(source_map.values())
        context_obj = {"claims": authorized_graph, "sources": source_list}
        if question is not None:
            if not isinstance(question, dict):
                return _failure()
            context_obj["question"] = copy.deepcopy(question)
        if not context_obj["claims"] and not authorized_text:
            return _failure("abstention")

        citation_records: dict[str, dict[str, Any]] = {}
        base = wiki_base.rstrip("/")
        for claim in authorized_graph:
            for evidence in claim["evidence"]:
                cid = _citation_id(claim, evidence)
                citation_records[cid] = {
                    "id": cid, "snapshot_id": claim["snapshot_id"],
                    "snapshot_hash": claim["snapshot_hash"],
                    "review_version": claim["review_version"], "evidence_id": evidence["evidence_id"],
                    "page_id": evidence["page_id"], "revision_id": evidence["revision_id"],
                    "content_hash": evidence["content_hash"],
                    "url": base + wiki_script_path + "?oldid=" + str(evidence["revision_id"]),
                }
        for source in authorized_text:
            cid = f"text:{source['page_id']}:{source['revision_id']}:{source['content_hash']}"
            citation_records[cid] = {
                "id": cid, "page_id": source["page_id"], "revision_id": source["revision_id"],
                "content_hash": source["content_hash"],
                "url": base + wiki_script_path + "?oldid=" + str(source["revision_id"]),
            }
        if len(citation_records) > MAX_CITATIONS:
            return _failure("partial")
        context_obj["citation_catalog"] = list(citation_records.values())
        packed = _json_bytes(context_obj)
        if len(packed) > context_budget:
            return _failure("partial")

        # Last full current check immediately before the callback.
        graph_check, text_check = fresh()
        if graph_check != authorized_graph or text_check != authorized_text:
            return _failure()
        if conflicts or _conflicted(authorized_graph):
            return {**_failure("conflict"), "claims": authorized_graph,
                    "text": [{k: v for k, v in item.items() if k != "text"} for item in authorized_text],
                    **_source_metadata(authorized_graph, authorized_text, graph_generation)}
        if not callable(generator):
            return _failure()
        raw_response = generator(packed.decode("utf-8"))
        response = copy.deepcopy(raw_response)
        if not isinstance(response, dict) or set(response) != {"status", "answer", "citation_ids"}:
            return _failure()
        # Reauthorize after model output; never publish a stale answer.
        graph_final, text_final = fresh()
        if graph_final != authorized_graph or text_final != authorized_text:
            return _failure()

        if response["status"] == "abstention":
            if response["answer"] not in (None, "") or response["citation_ids"] != []:
                return _failure()
            return {"status": "abstention", "answer": None, "citations": [],
                    "claims": authorized_graph, "text": authorized_text,
                    **_source_metadata(authorized_graph, authorized_text, graph_generation)}
        answer = response["answer"]
        cited = response["citation_ids"]
        if (response["status"] != "answer" or not isinstance(answer, str) or not answer.strip()
                or len(answer.encode("utf-8")) > MAX_ANSWER_BYTES or not isinstance(cited, list)
                or not cited or len(cited) > MAX_CITATIONS
                or len(set(cited)) != len(cited) or any(not isinstance(cid, str)
                                                        or cid not in citation_records for cid in cited)):
            return _failure()
        return {"status": "ok", "answer": answer,
                "citations": [citation_records[cid] for cid in cited],
                "claims": authorized_graph, "text": authorized_text,
                **_source_metadata(authorized_graph, authorized_text, graph_generation),
                "generation": {"adapter": "callback", "semantic_citation_check": False}}
    except Exception:
        return _failure()


def _source_metadata(graph: list[dict[str, Any]], text: list[dict[str, Any]],
                     graph_generation: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"review": "fresh-authorizer-v1", "source": "authorized-current"}
    if graph:
        result["graph_generation"] = graph_generation
    if text:
        receipts = sorted(({key: item[key] for key in TEXT_IDENTITY} for item in text),
                          key=lambda item: (item["page_id"], item["revision_id"], item["content_hash"]))
        result["text_source_digest"] = hashlib.sha256(_json_bytes(receipts)).hexdigest()
    return result
