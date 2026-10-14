"""Exercise only the explicitly selected disposable graph/tools package.

Run initial before wiki mutations, then rebuilt after verify_authorization.py.
These are export-time projection checks, not current MediaWiki authorization.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path

EXTENSION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXTENSION / "tools"))
import reviewed_projection as projection


def require(condition: bool) -> None:
    if not condition:
        raise ValueError("synthetic_graph_contract_failed")


def private_path(path: Path, *, directory: bool = False) -> Path:
    require(path.is_absolute() and ".." not in path.parts)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        require(not current.is_symlink())
    info = path.stat()
    require(info.st_uid == os.geteuid())
    require(stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    repository = EXTENSION.parents[1]
    require(path != repository and repository not in path.parents)
    require(path != Path("/var/www/html") and Path("/var/www/html") not in path.parents)
    if not directory:
        private_path(path.parent, directory=True)
        require(info.st_size <= 8_000_000)
    return path


def fixture_manifest(path: Path, seeds: dict[int, dict]) -> dict:
    private_path(path)
    document = projection.validate_manifest_document(projection.read_manifest_file(path))
    # snapshot_count covers all stored review states, not just eligible claims.
    # The projection validator enforces that it cannot be smaller than claims.
    require(bool(document["claims"]))
    for claim in document["claims"]:
        for evidence in claim["evidence"]:
            seed = seeds.get(evidence["page_id"])
            require(seed is not None)
            require(all(evidence[key] == seed[key] for key in ("revision_id", "content_hash")))
            require(evidence["page_title"] == seed["title"])
    return document


def lookup(driver, claim: dict) -> dict:
    result = projection.lookup_projection(
        driver, claim_type=claim["claim_type"], subject_type=claim["subject"]["type"],
        subject_id=claim["subject"]["id"], predicate=claim["predicate"], limit=100,
    )
    require(not result["truncated"])
    return result


def check_lookup(driver, document: dict) -> int:
    checked = set()
    for claim in document["claims"]:
        result = lookup(driver, claim)
        actual = next((item for item in result["results"]
                       if item["snapshot_id"] == claim["snapshot_id"]), None)
        require(actual is not None and not actual["evidence_truncated"])
        for key in ("snapshot_hash", "review_version", "claim_type", "subject", "predicate", "qualifiers"):
            require(projection.canonical_json(actual[key]) == projection.canonical_json(claim[key]))
        value_key = "value" if claim["claim_type"] == "property" else "object"
        require(projection.canonical_json(actual[value_key]) == projection.canonical_json(claim[value_key]))
        expected_evidence = {item["evidence_id"]: item for item in claim["evidence"]}
        actual_evidence = {item["evidence_id"]: item for item in actual["evidence"]}
        require(len(actual_evidence) == len(actual["evidence"]) and set(actual_evidence) == set(expected_evidence))
        for evidence_id, expected in expected_evidence.items():
            # The fixed lookup omits source text and extractor fields. Check
            # every identity it exposes, not a fictitious full-payload API.
            identity_keys = ("page_id", "revision_id", "content_hash", "page_title", "location_type",
                             "location", "source_offset_start", "source_offset_end", "span_hash")
            require(all(actual_evidence[evidence_id].get(key) == expected.get(key) for key in identity_keys))
        checked.add(claim["snapshot_id"])
    return len(checked)


def write_new(path: Path, document: dict) -> None:
    private_path(path.parent, directory=True)
    require(not path.exists() and not path.is_symlink())
    raw = (projection.canonical_json(document) + "\n").encode()
    require(len(raw) < 2_000_000)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: verify_graph.py --phase initial --private-root /private/wikikg --synthetic-only. "
        "Use the reviewed-offline tools container sharing its graph network namespace."))
    parser.add_argument("--phase", choices=("initial", "rebuilt"), required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    args = parser.parse_args()
    driver = None
    try:
        require(os.environ.get("WIKIKG_GRAPH_SYNTHETIC") == "1")
        root = private_path(args.private_root, directory=True)
        password = os.environ.get("WIKIKG_NEO4J_PASSWORD")
        require(bool(password))
        seed_rows = json.loads(private_path(root / "seed-identities.json").read_text())
        seeds = {row["page_id"]: row for row in seed_rows}
        require(len(seeds) == 7 and all(page_id >= 1_000_000 for page_id in seeds))
        original = fixture_manifest(root / "evidence-2" / "reviewed.json", seeds)
        require(original["snapshot_count"] == 9 and len(original["claims"]) == 9)
        output = root / f"graph-{args.phase}-results.json"
        require(not output.exists())
        uri = projection.validate_local_bolt_uri("bolt://127.0.0.1:7687")
        driver = projection.create_neo4j_driver(uri, "neo4j", password)
        driver.verify_connectivity()
        if args.phase == "initial":
            with driver.session() as session:
                require(session.run("MATCH (n) RETURN count(n) AS nodes").single()["nodes"] == 0)
            from neo4j.exceptions import AuthError

            bad_driver = projection.create_neo4j_driver(uri, "neo4j", secrets.token_hex(32))
            try:
                try:
                    bad_driver.verify_connectivity()
                except AuthError:
                    pass
                else:
                    raise ValueError("synthetic_graph_authentication_not_enforced")
            finally:
                bad_driver.close()
            first = projection.load_projection(original, driver)
            repeated = projection.load_projection(original, driver)
            require(first == repeated)
            checked = check_lookup(driver, original)
            results = {"synthetic_only": True, "phase": "initial", "authentication_enforced": True,
                       "load": first, "reload": repeated, "idempotent": True,
                       "exact_lookup_claims": checked, "current_authorization": "not provided by Neo4j"}
        else:
            initial = json.loads(private_path(root / "graph-initial-results.json").read_text())
            require(initial["load"]["generation"] == projection.generation_id(original))
            # A source-change case can remove stale snapshots without rewriting
            # their old identities. The new export contains only unchanged seeds.
            rebuilt = fixture_manifest(root / "evidence-rejected" / "reviewed.json", seeds)
            require(rebuilt["snapshot_count"] == original["snapshot_count"])
            prior = {claim["snapshot_id"]: claim for claim in original["claims"]}
            current = {claim["snapshot_id"]: claim for claim in rebuilt["claims"]}
            require(set(current) < set(prior))
            removed = set(prior) - set(current)
            activated = projection.load_projection(rebuilt, driver)
            checked = check_lookup(driver, rebuilt)
            for snapshot_id in removed:
                require(snapshot_id not in {item["snapshot_id"] for item in lookup(driver, prior[snapshot_id])["results"]})
            try:
                projection.load_projection(original, driver)
            except projection.ProjectionError as error:
                require("retired generation" in str(error))
            else:
                raise ValueError("synthetic_graph_retired_export_reactivated")
            require(check_lookup(driver, rebuilt) == checked)
            results = {"synthetic_only": True, "phase": "rebuilt", "load": activated,
                       "removed_snapshot_ids": sorted(removed), "exact_lookup_claims": checked,
                       "retired_export_refused": True, "current_authorization": "separate PHP bridge checks"}
        write_new(output, results)
        print(json.dumps({"status": "passed", "phase": args.phase,
                          "checked_claims": checked, "synthetic_only": True}, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "failed", "error": "synthetic_graph_verification_failed"}), file=sys.stderr)
        return 2
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
