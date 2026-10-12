"""Reproduce twelve development-annotation CLI calls using only fictional fixtures."""

from __future__ import annotations

import copy
import csv
import io
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import development_annotation as workflow

FIXTURE = workflow.ROOT / "demo" / "annotation-synthetic" / "fixture.json"
SCRIPT = workflow.ROOT / "tools" / "development_annotation.py"
MAX_REPORT_BYTES = 64 * 1024
EXPECTED_GROUPS = {
    "ambiguous": 1,
    "outside_scope": 1,
    "possible_pair": 2,
    "unmatched_annotations": 1,
    "unmatched_candidates": 1,
    "unresolved_annotation": 1,
}


def check(condition: bool) -> None:
    workflow.require(
        condition, "DEMO_CHECK", "Synthetic verification failed. Keep the artifacts for inspection."
    )


def save(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def pin(page: dict) -> dict:
    return {key: page[key] for key in ("title", "page_id", "revision_id", "content_hash")}


def inputs(fixture: dict) -> tuple[dict, dict, dict]:
    check(fixture["synthetic"] is True and len(fixture["pages"]) == 2)
    pages = [
        {
            **page,
            "text": page["wikitext"],
            "crop": "SYNTHETIC crop",
            "content_hash": workflow.sha(page["wikitext"].encode("utf-8")),
        }
        for page in fixture["pages"]
    ]
    raw = {"schema_version": "1.0", "page_count": 2, "error_count": 0, "pages": pages}
    manifest = {
        "schema_version": "1.0",
        "corpus": "synthetic-annotation-demo",
        "source": {**pin(pages[0]), "discovery_heading_rule": "synthetic fixture"},
        "selection": {
            "method": "synthetic",
            "strata": "synthetic",
            "seed": "fixture",
            "sample_size": 2,
            "dev_size": 1,
            "test_size": 1,
            "heldout_exclusion_policy": "synthetic metadata only; no held-out file exists",
            "previously_observed_titles": [],
        },
        "counts": {"discovered": 2, "statuses": {"resolved": 2}, "selected": 2},
        "pages": [
            {
                "discovery_index": 0,
                "requested_title": pages[1]["title"],
                "resolved_title": pages[1]["title"],
                "status": "resolved",
                "eligible_for_development": True,
                "eligible_for_heldout": False,
                "heldout_exclusion_reason": "synthetic development",
                "selected": True,
                "split": "dev",
                **{key: pages[1][key] for key in ("page_id", "revision_id", "content_hash")},
            },
            {
                "discovery_index": 1,
                "requested_title": "SYNTHETIC SEALED METADATA ONLY",
                "resolved_title": "SYNTHETIC SEALED METADATA ONLY",
                "status": "resolved",
                "eligible_for_development": False,
                "eligible_for_heldout": True,
                "heldout_exclusion_reason": None,
                "selected": True,
                "split": "test",
                "page_id": 99,
                "revision_id": 999,
                "content_hash": workflow.sha(b"synthetic metadata, not source content"),
            },
        ],
    }
    protocol = {
        "schema_version": "1.0",
        "protocol_id": "synthetic-annotation-demo-v1",
        "extractor": {
            "version": "synthetic-fixture",
            "method": "rules",
            "worker_sha256": workflow.sha(b"synthetic fixture; no worker invocation"),
            "evaluator_sha256": workflow.sha(b"synthetic fixture; no evaluation"),
        },
        "artifacts": {
            "manifest_sha256": workflow.sha(workflow.encoded(manifest)),
            "development_input_sha256": workflow.sha(workflow.encoded(raw)),
            "heldout_input_sha256": workflow.sha(b"synthetic placeholder; no held-out file"),
            "schema_sha256": {
                name: workflow.sha(workflow.read_bytes(workflow.ROOT / "schema" / name))
                for name in sorted(workflow.pilot.PINNED_SCHEMA_NAMES)
            },
        },
        "ontology": fixture["ontology"],
        "evaluation": {
            "hash_seeds": [0, 1],
            "deterministic_outputs": ["synthetic-fixtures-only"],
            "reported_metrics": ["none"],
            "forbidden_metrics": ["scientific_accuracy"],
        },
        "privacy": {"output_outside_repository": True, "aggregate_report_only": True},
        "rerun_policy": {
            "identical_retry_only_after_infrastructure_failure": True,
            "no_tuning_after_results": True,
        },
    }
    return raw, manifest, protocol


def evidence(page: dict, quote: str) -> dict:
    start = page["wikitext"].index(quote)
    return {
        **{key: page[key] for key in ("page_id", "revision_id", "content_hash")},
        "source_offset_start": start,
        "source_offset_end": start + len(quote),
        "supporting_span": quote,
        "span_hash": workflow.sha(quote.encode("utf-8")),
    }


def assertions(fixture: dict, raw: dict, template: dict) -> tuple[dict, dict]:
    page = raw["pages"][1]
    submission = copy.deepcopy(template)
    submission.update(
        {
            "submission_id": "synthetic-demo",
            "provenance": {
                "annotator_id": "synthetic-fixture",
                "submitted_at": "2026-10-10T08:00:00Z",
                "exposure": "synthetic",
                "notes": "Fictional software fixture, not a human annotation.",
            },
        }
    )
    submission["page_reviews"][1]["status"] = "complete"
    for item in fixture["annotations"]:
        qualifiers = {
            key: {"state": "not_stated", "literal": None}
            for key in fixture["ontology"]["qualifier_keys"]
        }
        if "season" in item:
            qualifiers["season"] = {"state": "stated", "literal": item["season"]}
        submission["annotations"].append(
            {
                "annotation_id": item["id"],
                "subject": {
                    "literal": "X",
                    "type": "Variety",
                    "identity_status": "unresolved" if item.get("unresolved") else "resolved",
                },
                "predicate": item["predicate"],
                "value_literal": item["quote"],
                "unit": {
                    "state": "stated" if "unit" in item else "not_stated",
                    "literal": item.get("unit"),
                },
                "qualifiers": qualifiers,
                "evidence": evidence(page, item["quote"]),
                "judgment": "supported",
                "outside_scope": item.get("outside_scope", False),
                "reason": item.get("reason", ""),
            }
        )
    claims = [
        {
            "claim_id": workflow.sha(item["id"].encode("utf-8")),
            "claim_type": "property",
            "subject": item.get("subject", {"type": "Variety", "id": "X"}),
            "predicate": item["predicate"],
            "value": item["quote"],
            "qualifiers": {"season": item["season"]} if "season" in item else {},
            "evidence": [
                {
                    **evidence(page, item["quote"]),
                    "evidence_id": workflow.sha(("evidence-" + item["id"]).encode("utf-8")),
                    "page_title": page["title"],
                    "location_type": "source_span",
                    "extraction_method": "rules",
                    "extractor_version": "synthetic-fixture",
                }
            ],
            "traceability_complete": True,
            "review": {"status": "pending"},
        }
        for item in fixture["candidates"]
    ]
    candidates = {
        "schema_version": "1.0",
        "metadata": {
            "extractor_version": "synthetic-fixture",
            "extraction_method": "rules",
            "review_state": "pending",
            "source_pages": [pin(page) for page in raw["pages"]],
        },
        "summary": {
            "claim_count": len(claims),
            "traceability_complete_count": len(claims),
            "claims_by_predicate": dict(Counter(claim["predicate"] for claim in claims)),
        },
        "claims": claims,
    }
    return submission, candidates


def snapshot(directory: Path) -> dict:
    return {
        path.name: (workflow.sha(workflow.read_bytes(path)), path.stat().st_mtime_ns)
        for path in directory.iterdir()
    }


def demonstrate(output: Path, document_roots: tuple[Path, ...] = ()) -> dict:
    workflow.outside_roots(output, document_roots)
    parent = workflow.private_directory(output.parent)
    output = parent / output.name
    workflow.require(
        not output.exists() and not output.is_symlink(),
        "OUTPUT_EXISTS",
        "Keep the existing demonstration and choose a new private destination.",
    )
    fixture_bytes = workflow.read_bytes(FIXTURE)
    fixture = workflow.decode(fixture_bytes)
    raw, manifest, protocol = inputs(fixture)
    output.mkdir(mode=0o700)
    workflow.private_directory(output)
    commands = []

    def run(arguments: list[str], expected_status: str, expected_code: str | None = None) -> dict:
        command = [sys.executable, str(SCRIPT), *arguments]
        for root in document_roots:
            command.extend(["--document-root", str(root)])
        result = subprocess.run(
            command,
            cwd=workflow.ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(len(result.stdout.encode()) + len(result.stderr.encode()) <= 8192)
        parsed = workflow.decode((result.stderr if expected_code else result.stdout).encode())
        commands.append({"argv": command, "exit_code": result.returncode, "result": parsed})
        check(result.returncode == (2 if expected_code else 0))
        check(parsed["status"] == expected_status)
        check(parsed.get("code") == expected_code)
        check(result.stdout == "" if expected_code else result.stderr == "")
        check("6-8" not in result.stdout + result.stderr)
        check("synthetic-fixture" not in result.stdout + result.stderr)
        return parsed

    try:
        for name, value in (
            ("input.json", raw),
            ("manifest.json", manifest),
            ("protocol.json", protocol),
        ):
            save(output / name, workflow.encoded(value))
        pack_path = output / "pack"
        preparation = [
            "prepare",
            "--protocol",
            str(output / "protocol.json"),
            "--input",
            str(output / "input.json"),
            "--manifest",
            str(output / "manifest.json"),
            "--output",
            str(pack_path),
        ]
        prepared = run(preparation, "created")
        check(
            prepared["source_pages"] == 2
            and prepared["annotations"] == prepared["machine_suggestions"] == 0
        )
        pack_before = snapshot(pack_path)
        check(run(preparation, "unchanged")["pack_id"] == prepared["pack_id"])
        check(snapshot(pack_path) == pack_before)
        template = workflow.decode(workflow.read_bytes(pack_path / "submission-template.json"))
        check(template["annotations"] == [] and template["provenance"]["annotator_id"] == "")
        check(all(page["status"] == "not_started" for page in template["page_reviews"]))
        check(
            b"SYNTHETIC SEALED METADATA ONLY"
            not in b"".join(path.read_bytes() for path in pack_path.iterdir())
        )
        submission, candidates = assertions(fixture, raw, template)
        for name, value in (("working.json", submission), ("candidates.json", candidates)):
            save(output / name, workflow.encoded(value))
        quote = fixture["annotations"][0]["quote"]
        save(output / "quote.txt", quote.encode("utf-8"))
        run(
            [
                "locate",
                "--pack",
                str(pack_path),
                "--page-id",
                "10",
                "--quote-file",
                str(output / "quote.txt"),
                "--output",
                str(output / "evidence"),
            ],
            "created",
        )
        located = workflow.decode(workflow.read_bytes(output / "evidence" / "evidence.json"))
        check(located == submission["annotations"][0]["evidence"])
        run(
            ["validate", "--pack", str(pack_path), "--submission", str(output / "working.json")],
            "valid_format_and_coordinates",
        )
        retention = [
            "retain",
            "--pack",
            str(pack_path),
            "--submission",
            str(output / "working.json"),
            "--output",
            str(output / "submission-v1"),
        ]
        retained = run(retention, "created")
        original = snapshot(output / "submission-v1")
        check(run(retention, "unchanged")["submission_sha256"] == retained["submission_sha256"])
        check(snapshot(output / "submission-v1") == original)
        check(
            (output / "submission-v1" / "submission.json").read_bytes()
            == (output / "working.json").read_bytes()
        )
        comparison = [
            "compare",
            "--pack",
            str(pack_path),
            "--retained",
            str(output / "submission-v1"),
            "--submission-sha256",
            retained["submission_sha256"],
            "--candidates",
            str(output / "candidates.json"),
            "--candidate-sha256",
            workflow.sha((output / "candidates.json").read_bytes()),
            "--output",
            str(output / "comparison"),
        ]
        compared = run(comparison, "created")
        comparison_before = snapshot(output / "comparison")
        run(comparison, "unchanged")
        check(snapshot(output / "comparison") == comparison_before)
        check(compared["groups_by_status"] == EXPECTED_GROUPS)
        check(compared["human_decisions"] == 0 and compared["scientific_scores"] == "not_computed")
        report = workflow.decode(workflow.read_bytes(output / "comparison" / "report.json"))
        check(report["provenance"]["exposure"] == "synthetic")
        check(
            Counter(item["annotation_id"] for item in submission["annotations"])
            == Counter(
                identifier for group in report["groups"] for identifier in group["annotation_ids"]
            )
        )
        check(
            Counter(item["claim_id"] for item in candidates["claims"])
            == Counter(
                identifier for group in report["groups"] for identifier in group["candidate_ids"]
            )
        )
        check(all(group["human_decision"] is None for group in report["groups"]))
        exact = next(group for group in report["groups"] if group["annotation_ids"] == ["A1"])
        check(exact["comparison"]["target_or_value"]["exact_literal"])
        check(
            exact["comparison"]["unit"]["exact_literal"]
            and exact["comparison"]["evidence"]["exact_span"]
        )
        different = next(group for group in report["groups"] if group["annotation_ids"] == ["A2"])
        check(different["comparison"]["qualifiers"]["season"]["comparison"] == "different_literal")
        decisions = csv.DictReader(
            io.StringIO((output / "comparison" / "decisions.csv").read_text())
        )
        check(
            all(
                not row[key]
                for row in decisions
                for key in ("first_view", "second_view", "adjudicator_id", "decision", "reason")
            )
        )
        bad_offsets = copy.deepcopy(submission)
        item = bad_offsets["annotations"][0]["evidence"]
        bad_start = len(raw["pages"][1]["wikitext"][: item["source_offset_start"]].encode("utf-8"))
        item.update({"source_offset_start": bad_start, "source_offset_end": bad_start + len(quote)})
        save(output / "bad-offsets.json", workflow.encoded(bad_offsets))
        run(
            [
                "validate",
                "--pack",
                str(pack_path),
                "--submission",
                str(output / "bad-offsets.json"),
            ],
            "error",
            "EVIDENCE_SPAN",
        )
        bad_revision = copy.deepcopy(submission)
        bad_revision["annotations"][0]["evidence"]["revision_id"] += 1
        save(output / "bad-revision.json", workflow.encoded(bad_revision))
        run(
            [
                "validate",
                "--pack",
                str(pack_path),
                "--submission",
                str(output / "bad-revision.json"),
            ],
            "error",
            "EVIDENCE_PIN",
        )
        save(output / "tampered-candidates.json", (output / "candidates.json").read_bytes() + b" ")
        tampered = list(comparison)
        tampered[tampered.index("--candidates") + 1] = str(output / "tampered-candidates.json")
        tampered[tampered.index("--output") + 1] = str(output / "refused-comparison")
        run(tampered, "error", "CANDIDATE_CHANGED")
        check(not (output / "refused-comparison").exists())
        corrected = copy.deepcopy(submission)
        corrected.update(
            {"version": 2, "previous_submission_sha256": retained["submission_sha256"]}
        )
        corrected["annotations"][1]["qualifiers"]["season"]["literal"] = "fixture season B"
        corrected["provenance"]["notes"] = "Synthetic correction, not a human decision."
        save(output / "corrected.json", workflow.encoded(corrected))
        correction = run(
            [
                "retain",
                "--pack",
                str(pack_path),
                "--submission",
                str(output / "corrected.json"),
                "--previous",
                str(output / "submission-v1"),
                "--previous-sha256",
                retained["submission_sha256"],
                "--output",
                str(output / "submission-v2"),
            ],
            "created",
        )
        check(
            correction["version"] == 2
            and correction["submission_sha256"] != retained["submission_sha256"]
        )
        check(snapshot(output / "submission-v1") == original)
        check(
            len(commands) == 12
            and len(submission["annotations"]) == 7
            and len(candidates["claims"]) == 4
        )
    finally:
        save(output / "commands.json", workflow.encoded(commands))
    paths = sorted(path for path in output.rglob("*") if path.is_file())
    check(len(paths) <= 64)
    verification = {
        "status": "passed",
        "synthetic": True,
        "commands": len(commands),
        "annotations_accounted": 7,
        "candidates_accounted": 4,
        "groups": EXPECTED_GROUPS,
        "human_decisions": 0,
        "scientific_scores": "not_computed",
        "version_1_preserved": True,
        "fixture_sha256": workflow.sha(fixture_bytes),
        "tool_sha256": workflow.sha(workflow.read_bytes(Path(__file__))),
        "workflow_sha256": workflow.sha(workflow.read_bytes(SCRIPT)),
        "artifact_sha256": {
            str(path.relative_to(output)): workflow.sha(workflow.read_bytes(path)) for path in paths
        },
        "command_results": commands,
    }
    report_bytes = workflow.encoded(verification)
    workflow.require(
        len(report_bytes) <= MAX_REPORT_BYTES,
        "DEMO_SIZE",
        "Keep the verification report below 64 KiB.",
    )
    save(output / "verification.json", report_bytes)
    return {
        "status": "passed",
        "synthetic": True,
        "commands": 12,
        "annotations_accounted": 7,
        "candidates_accounted": 4,
        "human_decisions": 0,
        "scientific_scores": "not_computed",
        "report": "verification.json",
        "report_sha256": workflow.sha(report_bytes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = workflow.Parser(
        description="Run twelve synthetic annotation commands. No real sources, models or evaluation.",
        epilog="Example: development_annotation_demo.py --output <private-root/demo>",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--document-root", type=Path, action="append", default=[])
    previous_umask = os.umask(0o077)
    try:
        args = parser.parse_args(argv)
        result = demonstrate(args.output, tuple(args.document_root))
        print(json.dumps(result, sort_keys=True))
        return 0
    except workflow.AnnotationError as error:
        print(json.dumps(error.result, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, StopIteration, subprocess.TimeoutExpired):
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": "DEMO_FAILED",
                    "message": "Keep the failed artifacts and check file access, fixtures and command results.",
                }
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
