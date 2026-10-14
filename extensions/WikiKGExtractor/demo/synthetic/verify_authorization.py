#!/usr/bin/env python3
"""Exercise the real offline PHP authorization bridge against one disposable synthetic wiki."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
EXTENSION_ROOT = SCRIPT.parents[2]
REPOSITORY_ROOT = EXTENSION_ROOT.parents[1]
EXPORT_SCRIPT = EXTENSION_ROOT / "maintenance" / "exportReviewedProjection.php"
REVIEW_SCRIPT = EXTENSION_ROOT / "maintenance" / "reviewOfflineClaim.php"
FIXTURE_SCRIPT = SCRIPT.with_name("authorization_fixture.php")
OFFLINE_ANSWER = EXTENSION_ROOT / "tools" / "offline_answer.py"
REASON_PERMISSION = {"authorization_refused", "offline_authorization_refused"}


class VerificationError(RuntimeError):
    pass


def secure_file(path: Path, *, required: bool = True) -> None:
    if path.is_symlink():
        raise VerificationError(f"Unsafe symlink: {path.name}")
    if not path.exists():
        if required:
            raise VerificationError(f"Required private input is missing: {path.name}")
        return
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise VerificationError(f"Private file must be owner-owned mode 0600: {path.name}")


def private_dir(path: Path, private_root: Path) -> None:
    if not path.resolve().is_relative_to(private_root):
        raise VerificationError("A case directory escaped the private root")
    current = private_root
    for part in path.relative_to(private_root).parts:
        current = current / part
        if current.is_symlink():
            raise VerificationError(f"Unsafe directory symlink: {current.name}")
        current.mkdir(mode=0o700, exist_ok=True)
        info = current.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() \
                or stat.S_IMODE(info.st_mode) != 0o700:
            raise VerificationError(f"Private directory must be owner-owned mode 0700: {current.name}")


def write_private(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def run_logged(args: list[str], *, env: dict[str, str], log: Path, input_data: bytes | None = None) -> subprocess.CompletedProcess:
    process = subprocess.run(args, input=input_data, capture_output=True, env=env, check=False, timeout=180)
    write_private(log, b"stdout:\n" + process.stdout + b"\nstderr:\n" + process.stderr)
    return process


def read_json(path: Path, *, array: bool = False):
    secure_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"Invalid private JSON input: {path.name}") from error
    if not isinstance(value, list if array else dict):
        raise VerificationError(f"Unexpected JSON input shape: {path.name}")
    return value


def marker_write(marker: Path, content: str) -> None:
    if marker.is_symlink():
        raise VerificationError("Permission marker must not be a symlink")
    if marker.exists():
        secure_file(marker)
    write_private(marker, content.encode("ascii"))


def parse_summary(stdout: bytes) -> dict:
    try:
        value = json.loads(stdout)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError("Command returned no valid content-free JSON summary") from error
    if not isinstance(value, dict):
        raise VerificationError("Command returned an invalid summary")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: verify_authorization.py --wiki-root /var/www/html/w "
        "--private-root /private/wikikg --document-root /var/www/html --synthetic-only"))
    parser.add_argument("--wiki-root", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--document-root", required=True, type=Path)
    parser.add_argument("--actor", default="Synthetic reviewer")
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--php", default="php")
    parser.add_argument("--case-directory", default="authorization-cases",
                        help="new authorization-cases[-suffix] directory; never overwrite a prior case run")
    args = parser.parse_args()
    if not re.fullmatch(r"authorization-cases(?:-[a-z0-9]+)?", args.case_directory):
        raise VerificationError("Invalid new case directory name")
    root = args.private_root
    if root != Path("/private/wikikg") or root.is_symlink():
        raise VerificationError("Use the configured /private/wikikg synthetic root")
    root = root.resolve(strict=True)
    root_info = root.stat()
    if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
        raise VerificationError("Private root must be owner-owned mode 0700")
    wiki = args.wiki_root.resolve(strict=True)
    web = args.document_root.resolve(strict=True)
    if not (wiki / "maintenance" / "run.php").is_file() or not web.is_dir():
        raise VerificationError("Wiki or document root is invalid")
    if args.actor != "Synthetic reviewer":
        raise VerificationError("Only the named synthetic reviewer is permitted")
    for public in (wiki, web, REPOSITORY_ROOT.resolve()):
        if root == public or root.is_relative_to(public):
            raise VerificationError("Private root overlaps a public/runtime tree")

    package = root / "evidence-2"
    manifest = package / "reviewed.json"
    question = package / "lookup-question.json"
    exported = read_json(manifest)
    wrapper = read_json(question)
    secure_file(root / "seed-identities.json")
    if (root / args.case_directory).exists() or (root / "evidence-after-source-change").exists() \
            or (root / "evidence-rejected").exists() or (root / "authorization-results.json").exists():
        raise VerificationError("Refusing to overwrite prior authorization-verification outputs")
    if exported.get("snapshot_count") != 9 or len(exported.get("claims", [])) != 9:
        raise VerificationError("Expected exactly nine reviewed fixture claims")
    query = wrapper.get("query")
    if wrapper.get("version") != "wikikg.offline-question.v1" or not isinstance(query, dict) \
            or query.get("intent") != "lookup" or not isinstance(query.get("entities"), list):
        raise VerificationError("Lookup question has an unexpected contract")
    seeds = read_json(root / "seed-identities.json", array=True)
    swift = next((item for item in seeds if item.get("title") == "Verification crop Swift"), None)
    if not swift:
        raise VerificationError("Swift seed identity is missing")
    if not any(
        claim.get("predicate") == "HAS_VARIETY"
        and any(ev.get("page_title") == swift["title"] and ev.get("page_id") == swift["page_id"]
                for ev in claim.get("evidence", []) if isinstance(ev, dict))
        for claim in exported["claims"] if isinstance(claim, dict)
    ):
        raise VerificationError("Export lacks the Swift-sourced variety membership fixture")

    marker = root / "permission-denied"
    if marker.exists():
        secure_file(marker)
        if marker.read_text(encoding="ascii") not in ("allow", "deny"):
            raise VerificationError("Permission marker must contain allow or deny")
    marker_write(marker, "allow")
    secure_file(root / "LocalSettings.synthetic.php")
    env = {
        **os.environ,
        "DOCUMENT_ROOT": str(web),
        "MW_INSTALL_PATH": str(wiki),
        "MW_CONFIG_FILE": str(root / "LocalSettings.synthetic.php"),
        "WIKIKG_SYNTHETIC": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    results: dict[str, object] = {"version": "wikikg.synthetic-authorization-results.v1",
                                "case_directory": args.case_directory, "cases": []}

    def case_dir(name: str) -> Path:
        path = root / args.case_directory / name
        private_dir(path, root)
        return path

    def answer_case(name: str, current_manifest: Path, expected: str) -> dict:
        folder = case_dir(name)
        output = folder / "answer.json"
        cmd = [sys.executable, str(OFFLINE_ANSWER), "--manifest", str(current_manifest),
               "--request", str(question), "--output", str(output), "--actor", args.actor,
               "--wiki-root", str(wiki), "--document-root", str(web), "--synthetic-only",
               "--offline-only", "--audience", "actor-only", "--php", args.php,
               "--wiki-base", "http://127.0.0.1:" + os.environ.get("WIKIKG_HTTP_PORT", "8787")]
        process = run_logged(cmd, env=env, log=folder / "offline-answer.log")
        summary = parse_summary(process.stdout)
        result = read_json(output)
        if expected in ("success", "partial"):
            query = result.get("query")
            if not isinstance(query, dict) \
                    or not query.get("results") or not result.get("citations") \
                    or summary.get("authorization_calls") != 2 or result.get("answer") is not None \
                    or result.get("execution", {}).get("generated") is not False:
                raise VerificationError(f"Expected successful evidence-only bridge: {name}")
            if expected == "success" and (process.returncode != 0 or result.get("status") != "evidence"
                                           or query.get("status") != "ok" or query.get("complete") is not True):
                raise VerificationError(f"Expected complete authorized evidence: {name}")
            if expected == "partial" and (process.returncode != 3 or result.get("status") != "blocked"
                                           or query.get("complete") is not False or query.get("partial") is not True
                                           or {item.get("code") for item in result.get("reasons", [])}
                                           != {"partial_manifest"}):
                raise VerificationError(f"Expected authorized but incomplete evidence: {name}")
        else:
            if process.returncode == 0 or result.get("status") != "dependency_failure" \
                    or result.get("query") is not None or result.get("answer") is not None \
                    or result.get("citations") != []:
                raise VerificationError(f"Expected fail-closed bridge response: {name}")
            reasons = [item.get("code") for item in result.get("reasons", []) if isinstance(item, dict)]
            if expected == "permission" and not set(reasons) & REASON_PERMISSION:
                raise VerificationError("Permission-loss case failed for a reason other than authorization")
            if expected == "source" and (not set(reasons) & {"source_revision_changed", "source_content_changed",
                                                               "source_evidence_changed"}
                                          or "export_watermark_changed" in reasons
                                          or set(reasons) & REASON_PERMISSION):
                raise VerificationError("Source-change case was hidden by watermark or permission failure")
            if expected == "review" and not set(reasons) & {"export_watermark_changed", "claim_review_changed"}:
                raise VerificationError("Rejection case did not fail due to export/review state")
        source = read_json(current_manifest)
        query = result.get("query") or {}
        ids = sorted({item["claim"]["snapshot_id"] for group in query.get("results", [])
                      for item in group.get("claims", []) if isinstance(item, dict)
                      and isinstance(item.get("claim"), dict) and isinstance(item["claim"].get("snapshot_id"), str)})
        return {"case": name, "return_code": process.returncode,
                "status": result.get("status"), "query_status": (result.get("query") or {}).get("status"),
                "reason_codes": [item.get("code") for item in result.get("reasons", [])
                                 if isinstance(item, dict)],
                "authorization_calls": summary.get("authorization_calls"),
                "citation_count": len(result.get("citations", [])),
                "export_snapshot_count": source["snapshot_count"],
                "exported_claim_count": len(source["claims"]),
                "returned_snapshot_ids": ids}

    def export_to(name: str) -> Path:
        directory = root / name
        private_dir(directory, root)
        target = directory / "reviewed.json"
        cmd = [args.php, str(wiki / "maintenance" / "run.php"), str(EXPORT_SCRIPT),
               "--actor", args.actor, "--synthetic-only", "--document-root", str(web), "--output", str(target)]
        process = run_logged(cmd, env=env, log=directory / "export.log")
        if process.returncode != 0:
            raise VerificationError(f"Synthetic reviewed export failed: {name}")
        document = read_json(target)
        if document.get("snapshot_count") != exported["snapshot_count"] \
                or not isinstance(document.get("claims"), list) \
                or not 0 < len(document["claims"]) <= document["snapshot_count"]:
            raise VerificationError("Export snapshot count is inconsistent")
        return target

    cases = results["cases"]
    cases.append(answer_case("baseline", manifest, "success"))

    marker_write(marker, "deny")
    try:
        cases.append(answer_case("permission-denied", manifest, "permission"))
    finally:
        marker_write(marker, "allow")
    cases.append(answer_case("permission-restored", manifest, "success"))

    fixture = [args.php, str(wiki / "maintenance" / "run.php"), str(FIXTURE_SCRIPT),
               "--mode", "source-edit", "--private-root", str(root), "--actor", args.actor]
    edit_folder = case_dir("source-edit")
    edit = run_logged(fixture, env=env, log=edit_folder / "fixture.log")
    if edit.returncode != 0 or parse_summary(edit.stdout).get("status") != "edited":
        raise VerificationError("Guarded synthetic source revision edit failed")
    cases.append(answer_case("stale-after-source-change", manifest, "source"))

    fresh_manifest = export_to("evidence-after-source-change")
    # Stored snapshots remain in the denominator after stale claims are removed.
    # Fresh surviving evidence is authorized, but completeness stays blocked.
    cases.append(answer_case("fresh-after-source-change", fresh_manifest, "partial"))
    source_export = read_json(fresh_manifest)
    later = next((item for item in seeds if item.get("title") == "Other crop Later"), None)
    expected_ids = {claim["snapshot_id"] for claim in exported["claims"]
                    if later and all(ev["page_id"] != later["page_id"] for ev in claim["evidence"])}
    if not later or {claim["snapshot_id"] for claim in source_export["claims"]} != expected_ids \
            or source_export["source_watermark"] != exported["source_watermark"]:
        raise VerificationError("Source edit did not remove exactly the stale fixture evidence")
    swift_identities = read_json(root / "seed-identities.json", array=True)
    swift = next((item for item in swift_identities if item.get("title") == "Verification crop Swift"), None)
    if not swift:
        raise VerificationError("Swift seed identity is missing")
    candidates = [claim for claim in source_export["claims"] if claim.get("claim_type") == "property"
                  if any(ev.get("page_title") == swift["title"] and ev.get("page_id") == swift["page_id"]
                         for ev in claim.get("evidence", []))]
    if not candidates:
        raise VerificationError("Fresh export contains no Swift-sourced claim to reject")
    selected = candidates[0]
    reject_folder = case_dir("reject")
    reject_cmd = [args.php, str(wiki / "maintenance" / "run.php"), str(REVIEW_SCRIPT),
                  "--actor", args.actor, "--snapshot", selected["snapshot_id"],
                  "--status", "rejected", "--expected-version", str(selected["review_version"]),
                  "--audience", "actor-only", "--reason", "Synthetic authorization verifier rejection",
                  "--offline-only", "--synthetic-only"]
    rejection = run_logged(reject_cmd, env=env, log=reject_folder / "fixture.log")
    rejection_summary = parse_summary(rejection.stdout)
    if rejection.returncode != 0 or rejection_summary.get("status") != "recorded" \
            or rejection_summary.get("snapshot_id") != selected["snapshot_id"] \
            or rejection_summary.get("decision_status") != "rejected":
        raise VerificationError("Versioned synthetic claim rejection failed")
    results["rejected_snapshot_id"] = selected["snapshot_id"]
    results["rejection_review_version"] = rejection_summary.get("review_version")
    results["rejection_reason"] = "synthetic_authorization_verifier"
    cases.append(answer_case("stale-after-rejection", fresh_manifest, "review"))

    rejected_manifest = export_to("evidence-rejected")
    rejected = read_json(rejected_manifest)
    before_ids = {claim["snapshot_id"] for claim in source_export["claims"]}
    after_ids = {claim["snapshot_id"] for claim in rejected["claims"]}
    removed = sorted(before_ids - after_ids)
    if removed != [selected["snapshot_id"]] or not after_ids <= before_ids:
        raise VerificationError("Rejection did not remove exactly its selected snapshot")
    results["export_after_source_change"] = {"snapshot_count": source_export["snapshot_count"],
                                            "claim_count": len(source_export["claims"])}
    results["export_after_rejection"] = {"snapshot_count": rejected["snapshot_count"],
                                           "claim_count": len(rejected["claims"]),
                                          "removed_snapshot_ids": removed,
                                          "removed_count": len(removed)}
    output = root / "authorization-results.json"
    write_private(output, (json.dumps(results, sort_keys=True, separators=(",", ":")) + "\n").encode())
    print(json.dumps({"status": "ok", "case_count": len(cases),
                      "authorization_results_written": True,
                      "rejected_export_snapshot_count": rejected["snapshot_count"],
                      "rejected_export_claim_count": len(rejected["claims"]),
                      "removed_snapshot_count": len(removed)}, sort_keys=True))
    return 0


if __name__ == "__main__":
	try:
		sys.exit(main())
	except (OSError, VerificationError, subprocess.SubprocessError) as error:
		print(json.dumps({"status": "failed", "reason": "synthetic_authorization_verification_failed",
			"detail": str(error)}, sort_keys=True), file=sys.stderr)
		sys.exit(2)
