"""Run the frozen rules-only engineering pilot without exposing source text."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


EXTENSION_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = EXTENSION_ROOT / "schema"
WORKER = EXTENSION_ROOT / "bin" / "kg_worker.py"
EVALUATOR = Path(__file__).resolve()
REPORT_NAME = "evaluation-report.v1.json"
PINNED_SCHEMA_NAMES = {
    "candidate-claims.v1.schema.json",
    "corpus-manifest.v1.schema.json",
    "evaluation-protocol.v1.schema.json",
    "evaluation-report.v1.schema.json",
    "graph-document.v1.schema.json",
    "raw-data.v1.schema.json",
}
DETERMINISTIC_OUTPUT_NAMES = {
    "candidate_claims.json",
    "graph_data_raw.json",
    "graph_nodes_edges.json",
    "kg_summary.json",
    "mediawiki_bang_thuoc_tinh_cay_trong.txt",
    "neo4j_import.cypher",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def repository_root(start: Path | None = None) -> Path | None:
    path = (start or Path(__file__)).resolve()
    for parent in path.parents:
        if (parent / ".git").exists():
            return parent
    return None


def ensure_output_outside_repository(output_dir: Path) -> None:
    root = repository_root()
    resolved = output_dir.expanduser().resolve()
    if root is not None and (resolved == root or root in resolved.parents):
        raise ValueError("Thư mục đánh giá phải nằm ngoài repository.")


def validate_json(instance: Any, schema_path: Path) -> None:
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError(
            "Thiếu jsonschema. Chạy: pip install -r bin/requirements.txt"
        ) from error
    try:
        jsonschema.validate(instance, load_json(schema_path))
    except jsonschema.ValidationError as error:
        raise ValueError(
            f"Dữ liệu không khớp schema {schema_path.name}."
        ) from error


def git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root() or EXTENSION_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = result.stdout.strip()
    if result.returncode != 0 or len(revision) != 40:
        raise RuntimeError("Không đọc được revision Git hiện tại.")
    return revision


def artifact_hashes_match(
    protocol: dict[str, Any],
    manifest_path: Path,
    input_path: Path,
    split: str,
) -> bool:
    artifacts = protocol["artifacts"]
    input_key = (
        "development_input_sha256"
        if split == "development"
        else "heldout_input_sha256"
    )
    if sha256_file(WORKER) != protocol["extractor"]["worker_sha256"]:
        return False
    if sha256_file(EVALUATOR) != protocol["extractor"]["evaluator_sha256"]:
        return False
    if sha256_file(manifest_path) != artifacts["manifest_sha256"]:
        return False
    if sha256_file(input_path) != artifacts[input_key]:
        return False
    if set(artifacts["schema_sha256"]) != PINNED_SCHEMA_NAMES:
        return False
    if set(protocol["evaluation"]["deterministic_outputs"]) != DETERMINISTIC_OUTPUT_NAMES:
        return False
    for name, expected in artifacts["schema_sha256"].items():
        schema_path = SCHEMA_DIR / name
        if not schema_path.is_file() or sha256_file(schema_path) != expected:
            return False
    return True


def run_worker(input_path: Path, output_dir: Path, hash_seed: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(hash_seed)
    started = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--no-ai",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=EXTENSION_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    duration = time.perf_counter() - started
    return {
        "hash_seed": hash_seed,
        "duration_seconds": round(duration, 6),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def collect_output_hashes(
    output_dir: Path, output_names: list[str]
) -> dict[str, str]:
    return {
        name: sha256_file(output_dir / name)
        for name in output_names
        if (output_dir / name).is_file()
    }


def source_records_match_manifest(
    raw_input: dict[str, Any],
    manifest: dict[str, Any],
    split: str,
) -> bool:
    manifest_split = "dev" if split == "development" else "test"
    expected = {
        (
            int(manifest["source"]["page_id"]),
            int(manifest["source"]["revision_id"]),
            str(manifest["source"]["content_hash"]),
        )
    }
    expected.update(
        (
            int(page["page_id"]),
            int(page["revision_id"]),
            str(page["content_hash"]),
        )
        for page in manifest["pages"]
        if page.get("split") == manifest_split
    )
    actual = {
        (int(page["page_id"]), int(page["revision_id"]), str(page["content_hash"]))
        for page in raw_input["pages"]
    }
    content_hashes_valid = all(
        hashlib.sha256(page["wikitext"].encode("utf-8")).hexdigest()
        == page["content_hash"]
        for page in raw_input["pages"]
    )
    input_error_count = int(raw_input.get("error_count", 0))
    return (
        actual == expected
        and len(actual) == len(raw_input["pages"])
        and content_hashes_valid
        and int(raw_input.get("page_count", len(actual))) == len(actual)
        and input_error_count == 0
        and not raw_input.get("errors", [])
    )


def output_source_records_match_input(
    raw_input: dict[str, Any],
    candidates: dict[str, Any],
    graph: dict[str, Any],
) -> bool:
    expected = {
        (
            str(page["title"]),
            int(page["page_id"]),
            int(page["revision_id"]),
            str(page["content_hash"]),
        )
        for page in raw_input["pages"]
    }
    candidate_sources = {
        (
            str(page["title"]),
            int(page["page_id"]),
            int(page["revision_id"]),
            str(page["content_hash"]),
        )
        for page in candidates["metadata"]["source_pages"]
    }
    graph_sources = {
        (
            str(page["title"]),
            int(page["page_id"]),
            int(page["revision_id"]),
            str(page["content_hash"]),
        )
        for page in graph["metadata"]["source_pages"]
    }
    return (
        candidate_sources == expected
        and graph_sources == expected
        and len(candidate_sources) == len(candidates["metadata"]["source_pages"])
        and len(graph_sources) == len(graph["metadata"]["source_pages"])
    )


def evidence_revalidates(
    raw_input: dict[str, Any], candidates: dict[str, Any]
) -> bool:
    pages = {int(page["page_id"]): page for page in raw_input["pages"]}
    complete_count = 0
    for claim in candidates["claims"]:
        claim_complete = True
        if claim.get("review") != {"status": "pending"}:
            return False
        for evidence in claim["evidence"]:
            page = pages.get(int(evidence["page_id"]))
            if page is None:
                return False
            if (
                int(evidence["revision_id"]) != int(page["revision_id"])
                or evidence["content_hash"] != page["content_hash"]
                or evidence["page_title"] != page["title"]
                or evidence["extraction_method"] != "rules"
                or evidence["extractor_version"]
                != candidates["metadata"]["extractor_version"]
            ):
                return False
            location_type = evidence["location_type"]
            if location_type == "source_span":
                start = int(evidence["source_offset_start"])
                end = int(evidence["source_offset_end"])
                if not 0 <= start < end <= len(page["wikitext"]):
                    return False
                span = page["wikitext"][start:end]
                if span != evidence["supporting_span"]:
                    return False
                if hashlib.sha256(span.encode("utf-8")).hexdigest() != evidence["span_hash"]:
                    return False
            elif location_type == "page_title":
                if evidence.get("location") != page["title"]:
                    return False
            else:
                claim_complete = False
        if bool(claim["traceability_complete"]) != claim_complete:
            return False
        complete_count += int(claim_complete)
    return complete_count == candidates["summary"]["traceability_complete_count"]


def graph_invariants_valid(
    protocol: dict[str, Any],
    candidates: dict[str, Any],
    graph: dict[str, Any],
    summary: dict[str, Any],
) -> bool:
    ontology = protocol["ontology"]
    entity_types = set(ontology["entity_types"])
    relationship_predicates = set(ontology["relationship_predicates"])
    scalar_predicates = set(ontology["scalar_predicates"])
    qualifier_keys = set(ontology["qualifier_keys"])

    node_keys = [(node["type"], node["id"]) for node in graph["nodes"]]
    if len(node_keys) != len(set(node_keys)):
        return False
    if any(node_type not in entity_types for node_type, _ in node_keys):
        return False
    node_set = set(node_keys)
    edge_keys: list[tuple[str, str, str, str, str]] = []
    for edge in graph["edges"]:
        if edge["type"] not in relationship_predicates:
            return False
        if (edge["source_type"], edge["source"]) not in node_set:
            return False
        if (edge["target_type"], edge["target"]) not in node_set:
            return False
        edge_keys.append(
            (
                edge["source_type"],
                edge["source"],
                edge["type"],
                edge["target_type"],
                edge["target"],
            )
        )
    if len(edge_keys) != len(set(edge_keys)):
        return False

    claim_ids: list[str] = []
    relationship_claim_count = 0
    relationship_claims: set[tuple[str, str, str, str, str]] = set()
    for claim in candidates["claims"]:
        claim_ids.append(claim["claim_id"])
        if claim["subject"]["type"] not in entity_types:
            return False
        if (claim["subject"]["type"], claim["subject"]["id"]) not in node_set:
            return False
        if claim["claim_type"] == "relationship":
            relationship_claim_count += 1
            if claim["predicate"] not in relationship_predicates:
                return False
            if claim["object"]["type"] not in entity_types:
                return False
            relationship_claims.add(
                (
                    claim["subject"]["type"],
                    claim["subject"]["id"],
                    claim["predicate"],
                    claim["object"]["type"],
                    claim["object"]["id"],
                )
            )
        elif claim["predicate"] not in scalar_predicates:
            return False
        if not set(claim["qualifiers"]).issubset(qualifier_keys):
            return False
    if len(claim_ids) != len(set(claim_ids)):
        return False
    if relationship_claim_count != len(edge_keys) or relationship_claims != set(edge_keys):
        return False

    if summary["node_count"] != len(graph["nodes"]):
        return False
    if summary["edge_count"] != len(graph["edges"]):
        return False
    if summary["candidate_claim_count"] != len(candidates["claims"]):
        return False
    if summary["page_count"] != len(graph["metadata"]["source_pages"]):
        return False
    if summary["nodes_by_label"] != dict(
        Counter(node["type"] for node in graph["nodes"])
    ):
        return False
    if summary["edges_by_type"] != dict(
        Counter(edge["type"] for edge in graph["edges"])
    ):
        return False
    if candidates["summary"]["claim_count"] != len(candidates["claims"]):
        return False
    if candidates["summary"].get("skipped_source_pages"):
        return False
    if candidates["summary"]["claims_by_predicate"] != dict(
        Counter(claim["predicate"] for claim in candidates["claims"])
    ):
        return False
    if candidates["metadata"]["review_state"] != "pending":
        return False
    if candidates["metadata"]["extraction_method"] != "rules":
        return False
    if candidates["metadata"]["extractor_version"] != protocol["extractor"]["version"]:
        return False
    return True


def build_summary(
    raw_input: dict[str, Any],
    candidates: dict[str, Any] | None,
    worker_summary: dict[str, Any] | None,
    worker_failure_count: int,
) -> dict[str, Any]:
    if candidates is None or worker_summary is None:
        return {
            "page_count": len(raw_input.get("pages", [])),
            "node_count": 0,
            "edge_count": 0,
            "candidate_claim_count": 0,
            "traceability_complete_count": 0,
            "traceability_coverage": 0,
            "nodes_by_label": {},
            "edges_by_type": {},
            "claims_by_predicate": {},
            "input_error_count": int(raw_input.get("error_count", 0)),
            "skipped_source_page_count": 0,
            "warning_count": 0,
            "worker_failure_count": worker_failure_count,
        }
    claim_count = int(candidates["summary"]["claim_count"])
    traceable = int(candidates["summary"]["traceability_complete_count"])
    return {
        "page_count": int(worker_summary["page_count"]),
        "node_count": int(worker_summary["node_count"]),
        "edge_count": int(worker_summary["edge_count"]),
        "candidate_claim_count": claim_count,
        "traceability_complete_count": traceable,
        "traceability_coverage": traceable / claim_count if claim_count else 0,
        "nodes_by_label": worker_summary["nodes_by_label"],
        "edges_by_type": worker_summary["edges_by_type"],
        "claims_by_predicate": candidates["summary"]["claims_by_predicate"],
        "input_error_count": int(raw_input.get("error_count", 0)),
        "skipped_source_page_count": len(
            candidates["summary"].get("skipped_source_pages", [])
        ),
        "warning_count": len(worker_summary.get("warnings", [])),
        "worker_failure_count": worker_failure_count,
    }


def evaluate(
    protocol_path: Path,
    manifest_path: Path,
    input_path: Path,
    output_dir: Path,
    split: str,
    heldout_approved: bool = False,
) -> dict[str, Any]:
    if split == "heldout" and not heldout_approved:
        raise ValueError(
            "Held-out bị khóa cho đến khi freeze packet được duyệt rõ ràng."
        )
    ensure_output_outside_repository(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Thư mục đánh giá phải mới hoặc rỗng.")

    protocol = load_json(protocol_path)
    manifest = load_json(manifest_path)
    raw_input = load_json(input_path)
    validate_json(protocol, SCHEMA_DIR / "evaluation-protocol.v1.schema.json")

    hashes_match = artifact_hashes_match(
        protocol,
        manifest_path,
        input_path,
        split,
    )
    if not hashes_match:
        raise ValueError("Hash artifact không khớp protocol đã đóng băng.")

    validate_json(manifest, SCHEMA_DIR / "corpus-manifest.v1.schema.json")
    validate_json(raw_input, SCHEMA_DIR / "raw-data.v1.schema.json")
    source_match = source_records_match_manifest(raw_input, manifest, split)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_names = list(protocol["evaluation"]["deterministic_outputs"])
    runs: list[dict[str, Any]] = []
    for seed in protocol["evaluation"]["hash_seeds"]:
        run_dir = output_dir / f"run-seed-{seed}"
        run = run_worker(input_path, run_dir, int(seed))
        run["output_hashes"] = collect_output_hashes(run_dir, output_names)
        runs.append(run)

    worker_failure_count = sum(run["exit_code"] != 0 for run in runs)
    first_dir = output_dir / "run-seed-0"
    candidates: dict[str, Any] | None = None
    graph: dict[str, Any] | None = None
    worker_summary: dict[str, Any] | None = None
    schemas_valid = False
    evidence_valid = False
    graph_valid = False
    output_sources_match = False

    if worker_failure_count == 0:
        candidates = load_json(first_dir / "candidate_claims.json")
        graph = load_json(first_dir / "graph_nodes_edges.json")
        worker_summary = load_json(first_dir / "kg_summary.json")
        validate_json(candidates, SCHEMA_DIR / "candidate-claims.v1.schema.json")
        validate_json(graph, SCHEMA_DIR / "graph-document.v1.schema.json")
        schemas_valid = True
        output_sources_match = output_source_records_match_input(
            raw_input,
            candidates,
            graph,
        )
        evidence_valid = evidence_revalidates(raw_input, candidates)
        graph_valid = graph_invariants_valid(
            protocol,
            candidates,
            graph,
            worker_summary,
        )

    deterministic = (
        worker_failure_count == 0
        and runs[0]["output_hashes"] == runs[1]["output_hashes"]
        and set(runs[0]["output_hashes"]) == set(output_names)
    )
    checks = {
        "artifact_hashes_match": hashes_match,
        "schemas_valid": schemas_valid,
        "source_records_match_manifest": source_match and output_sources_match,
        "evidence_revalidated": evidence_valid,
        "graph_invariants_valid": graph_valid,
        "outputs_deterministic": deterministic,
    }
    status = "pass" if all(checks.values()) else "fail"

    report_runs = [
        {
            "hash_seed": int(run["hash_seed"]),
            "duration_seconds": run["duration_seconds"],
            "exit_code": int(run["exit_code"]),
            "output_hashes": run["output_hashes"],
        }
        for run in runs
    ]
    report = {
        "schema_version": "1.0",
        "protocol_id": protocol["protocol_id"],
        "split": split,
        "status": status,
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "repository_revision": git_revision(),
        },
        "artifact_hashes": {
            "manifest": sha256_file(manifest_path),
            "input": sha256_file(input_path),
            "worker": sha256_file(WORKER),
            "evaluator": sha256_file(EVALUATOR),
            "protocol": sha256_file(protocol_path),
        },
        "runs": report_runs,
        "checks": checks,
        "summary": build_summary(
            raw_input,
            candidates,
            worker_summary,
            worker_failure_count,
        ),
        "limitations": [
            "No independent annotation or adjudicated reference set exists.",
            "Traceability coverage does not measure scientific correctness.",
            "Runtime values describe only the recorded local environment.",
            "Precision, recall, F1, and agreement are not reported.",
        ],
    }
    validate_json(report, SCHEMA_DIR / "evaluation-report.v1.schema.json")
    write_json(output_dir / REPORT_NAME, report)

    for run in runs:
        if run["stdout"]:
            print(run["stdout"], end="", file=sys.stderr)
        if run["stderr"]:
            print(run["stderr"], end="", file=sys.stderr)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chạy engineering pilot rules-only đã đóng băng.",
        epilog=(
            "Ví dụ: python3 evaluate_pilot.py --protocol <protocol.json> "
            "--manifest <manifest.json> --input <raw-data.json> "
            "--output-dir <private-output-dir> --split development"
        ),
    )
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--split",
        choices=("development", "heldout"),
        required=True,
    )
    parser.add_argument(
        "--confirm-heldout-approved",
        action="store_true",
        help="Xác nhận freeze packet đã được duyệt trước khi chạy held-out.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = {
        "protocol": Path(args.protocol).expanduser().resolve(),
        "manifest": Path(args.manifest).expanduser().resolve(),
        "input": Path(args.input).expanduser().resolve(),
        "output": Path(args.output_dir).expanduser().resolve(),
    }
    for name in ("protocol", "manifest", "input"):
        if not paths[name].is_file():
            print(f"Không tìm thấy {name}: {paths[name]}", file=sys.stderr)
            return 2
    try:
        report = evaluate(
            paths["protocol"],
            paths["manifest"],
            paths["input"],
            paths["output"],
            args.split,
            args.confirm_heldout_approved,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"Không chạy được evaluation: {error}", file=sys.stderr)
        return 3
    print(
        json.dumps(
            {
                "report": str(paths["output"] / REPORT_NAME),
                "status": report["status"],
                "summary": report["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
