#!/usr/bin/env python3
"""Run the connected fixture's explicit CLI and real-browser verification cases."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = HERE.parent
STATIC_IDS = ("supported-compound", "split-roots", "cross-crop", "unresolved-parent",
              "numeric-uncertain", "scope-unknown", "opposing-claims", "missing-relationship")
STATE_IDS = ("partial-manifest", "rejected-claim", "source-changed", "permission-loss", "graph-unavailable")


def private_json(path: Path) -> dict:
    import stat
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("private_input_refused")
    return json.loads(path.read_text())


def private_directory(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("private_directory_refused")


def write_private_new(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def owned_candidates(root: Path, *, include_stopped: bool = False,
                     tolerate_invalid: bool = False) -> dict[str, str] | tuple[dict[str, str], list[str]]:
    project_file = root / "wiki" / ".compose-project"
    info = project_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("private_project_refused")
    project = project_file.read_text().strip()
    if not re.fullmatch(r"wikikg-synthetic-[0-9a-f]{16}", project):
        raise ValueError("private_project_refused")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    ps = ["docker", "ps"] + (["-a"] if include_stopped else [])
    ids = subprocess.check_output([*ps, "-q", "--no-trunc", "--filter",
        "label=com.docker.compose.project=" + project, "--filter", "label=wikikg.synthetic.questions=1"],
        env=env, timeout=20, stderr=subprocess.DEVNULL).decode().split()
    if not ids or any(not re.fullmatch(r"[a-f0-9]{64}", item) for item in ids):
        raise ValueError("owned_stack_missing")
    records = {}
    errors = []
    for container_id in ids:
        try:
            container = json.loads(subprocess.check_output(["docker", "inspect", container_id], env=env,
                timeout=20, stderr=subprocess.DEVNULL))[0]
            service = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
            labels = container["Config"].get("Labels", {})
            mounts = {mount["Destination"]: mount for mount in container["Mounts"]}
            name = labels.get("com.docker.compose.service")
            if labels.get("wikikg.synthetic.owner") != project or labels.get("wikikg.synthetic.questions") != "1":
                raise ValueError("foreign_container")
            if name == "wiki" and container["HostConfig"].get("ReadonlyRootfs") is not True:
                raise ValueError("unsafe_container")
            if service.get("WIKIKG_SYNTHETIC") != "1":
                raise ValueError("unsafe_container")
            if name == "wiki":
                private_mount = mounts.get("/private/wikikg", {})
                code_mount = mounts.get("/var/www/html/w/extensions/WikiKGExtractor", {})
                if (Path(private_mount.get("Source", "/")).resolve() != (root / "wiki").resolve()
                        or private_mount.get("RW") is not True
                        or Path(code_mount.get("Source", "/")).resolve() != REPO
                        or code_mount.get("RW") is not False or "NEO4J_AUTH" in service):
                    raise ValueError("foreign_mount")
            elif name == "graph":
                marker_mount = mounts.get("/private/wikikg/synthetic-marker", {})
                if (Path(marker_mount.get("Source", "/")).resolve() != (root / "wiki" / "synthetic-marker").resolve()
                        or marker_mount.get("RW") is not False or not service.get("NEO4J_AUTH")):
                    raise ValueError("foreign_mount")
            else:
                raise ValueError("foreign_container")
            if not include_stopped:
                for network in container.get("NetworkSettings", {}).get("Networks", {}):
                    data = json.loads(subprocess.check_output(["docker", "network", "inspect", network], env=env,
                        timeout=20, stderr=subprocess.DEVNULL))[0]
                    if data.get("Internal") is not True:
                        raise ValueError("external_network")
            if name in records:
                raise ValueError("duplicate_owned_service")
            records[name] = container_id
        except Exception as exc:
            errors.append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    if errors and not tolerate_invalid:
        raise ValueError("owned_candidate_invalid:" + ",".join(errors))
    return (records, errors) if tolerate_invalid else records


def owned_containers(root: Path, *, include_stopped: bool = False) -> tuple[str, str]:
    records = owned_candidates(root, include_stopped=include_stopped)
    if set(records) != {"graph", "wiki"}:
        raise ValueError("owned_stack_missing")
    return records["graph"], records["wiki"]


def stop_owned(root: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    try:
        initial, inspection_errors = owned_candidates(root, include_stopped=True, tolerate_invalid=True)
    except Exception as exc:
        raise ValueError("cleanup_inspection_failed") from exc
    errors = ["candidate_inspection_failed"] if inspection_errors else []
    for service in ("wiki", "graph"):
        candidate = initial.get(service)
        if not candidate:
            continue
        try:
            current, current_errors = owned_candidates(root, include_stopped=True, tolerate_invalid=True)
            if current_errors:
                errors.append("candidate_revalidation_failed")
            if current.get(service) != candidate:
                errors.append("owned_candidate_changed")
                continue
            state = json.loads(subprocess.check_output(["docker", "inspect", candidate], env=env,
                stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20))[0].get("State", {})
            if state.get("Running"):
                subprocess.run(["docker", "stop", "--time", "10", candidate], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30, check=True)
        except Exception:
            errors.append(service + "_stop_failed")
    if errors:
        raise ValueError("cleanup_failed:" + ",".join(errors))


def _inside(root: Path, wiki_id: str, *argv: str, stdin: bytes | None = None, timeout: int = 40):
    current = owned_candidates(root)
    if current.get("wiki") != wiki_id:
        raise ValueError("owned_wiki_changed")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    command = ["docker", "exec", "-i", "--user", f"{os.getuid()}:{os.getgid()}", wiki_id, *argv]
    return subprocess.run(command, env=env, input=stdin, capture_output=True, timeout=timeout)


def cli_case(root: Path, wiki: str, case: dict, manifest: str, suffix: str) -> tuple[int, dict]:
    query = case["query"]
    attempts = root / "wiki" / "connected" / "attempts"
    private_directory(attempts)
    request = attempts / (suffix + ".request.json")
    result_path = attempts / (suffix + ".result.json")
    write_private_new(request, json.dumps({"version": "wikikg.offline-question.v1", "query": query},
        ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    cli = "/var/www/html/w/extensions/WikiKGExtractor/tools/offline_answer.py"
    process = _inside(root, wiki, "python3", "-B", cli,
        "--manifest", manifest, "--request", "/private/wikikg/connected/attempts/" + request.name,
        "--output", "/private/wikikg/connected/attempts/" + result_path.name,
        "--actor", "Synthetic reviewer", "--wiki-root", "/var/www/html/w",
        "--document-root", "/var/www/html", "--graph-uri", "bolt://127.0.0.1:7687",
        "--graph-user", "neo4j", "--graph-password-file", "/private/wikikg/connected/graph-password",
        "--timeout", "30", "--synthetic-only", "--offline-only", "--audience", "actor-only", timeout=40)
    data = json.loads(result_path.read_text()) if result_path.is_file() else {}
    return process.returncode, data


def permission_loss_case(root: Path, wiki: str, case: dict, report: dict, manifest: Path) -> None:
    marker = root / "wiki" / "permission-denied"
    retained = root / "wiki" / "permission-denied.connected-verify"
    if marker.exists() or retained.exists():
        raise ValueError("permission_fixture_marker_exists")
    attempts = root / "wiki" / "connected" / "attempts"
    private_directory(attempts)
    request = attempts / "permission-loss.request.json"
    result_path = attempts / "permission-loss.result.json"
    write_private_new(request, json.dumps({"version": "wikikg.offline-question.v1", "query": case["query"]},
        ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    wrapper = r'''import os,sys
sys.path.insert(0,"/var/www/html/w/extensions/WikiKGExtractor/tools")
import offline_answer as app
original=app.EvidenceBridge._call
state={"calls":0,"authorized":False}
def wrapped(self,graph,text):
    state["calls"]+=1
    if not state["authorized"]:
        result=original(self,graph,text)
        state["authorized"]=True
        return result
    marker="/private/wikikg/permission-denied"
    if not os.path.exists(marker):
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as out: out.write("deny")
    return original(self,graph,text)
app.EvidenceBridge._call=wrapped
sys.argv=["offline_answer.py","--manifest","/private/wikikg/connected/'''+manifest.name+r'''",
"--request","/private/wikikg/connected/attempts/permission-loss.request.json",
"--output","/private/wikikg/connected/attempts/permission-loss.result.json",
"--actor","Synthetic reviewer","--wiki-root","/var/www/html/w","--document-root","/var/www/html",
"--graph-uri","bolt://127.0.0.1:7687","--graph-user","neo4j","--graph-password-file","/private/wikikg/connected/graph-password",
"--timeout","30","--synthetic-only","--offline-only","--audience","actor-only"]
try: code=app.main()
finally:
    if state["calls"]<2 or not state["authorized"]: code=9
    marker="/private/wikikg/permission-denied"
    retained="/private/wikikg/permission-denied.connected-verify"
    if os.path.exists(marker): os.replace(marker,retained)
sys.exit(code)
'''
    process = _inside(root, wiki, "python3", "-B", "-c", wrapper, timeout=40)
    if process.returncode != case["expected"]["exit"] or not retained.is_file():
        raise ValueError("permission_loss_oracle_mismatch")
    data = json.loads(result_path.read_text()) if result_path.is_file() else {}
    _assert_expected(case, process.returncode, data)
    reasons = [item.get("code") for item in data.get("reasons", []) if isinstance(item, dict)]
    if case["expected"]["reason"] not in reasons:
        raise ValueError("permission_loss_reason_mismatch")
    report["permission-loss"] = {"exit": process.returncode, "reason_codes": reasons,
                                  "retained_marker": retained.name}


def _query_cases(root: Path, wiki: str, manifest: Path, scenarios: dict, report: dict) -> None:
    catalog = private_json(root / "wiki" / "connected" / "questions.json")
    ids = {question["id"] for question in catalog["questions"]}
    cases = scenarios.get("scenarios", [])
    by_id = {case["id"]: case for case in cases}
    if set(by_id) != set(STATIC_IDS + STATE_IDS):
        raise ValueError("scenario_inventory_invalid")
    requests = root / "wiki" / "connected" / "attempts"
    private_directory(requests)
    cli = "/var/www/html/w/extensions/WikiKGExtractor/tools/offline_answer.py"
    pwd = "/private/wikikg/connected/graph-password"
    for case_id in STATIC_IDS:
        case = by_id[case_id]
        if case_id not in ids:
            raise ValueError("catalog_case_missing")
        request = {"version": "wikikg.offline-question.v1", "query": case["query"]}
        req_path = requests / (case_id + ".request.json")
        result_path = requests / (case_id + ".result.json")
        write_private_new(req_path, json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        request_container = "/private/wikikg/connected/attempts/" + case_id + ".request.json"
        result_container = "/private/wikikg/connected/attempts/" + case_id + ".result.json"
        manifest_container = "/private/wikikg/connected/" + manifest.name
        result = _inside(root, wiki, "python3", "-B", cli, "--manifest", manifest_container,
                         "--request", request_container, "--output", result_container,
                         "--actor", "Synthetic reviewer", "--wiki-root", "/var/www/html/w",
                         "--document-root", "/var/www/html",
                          "--graph-uri", "bolt://127.0.0.1:7687", "--graph-user", "neo4j",
                          "--graph-password-file", pwd, "--synthetic-only", "--offline-only",
                          "--timeout", "30", "--audience", "actor-only", timeout=40)
        expected = case["expected"]
        _assert_expected(case, result.returncode,
                         json.loads(result_path.read_text()) if result_path.exists() else {})
        if result.returncode != expected["exit"]:
            raise ValueError("scenario_exit_mismatch")
        data = json.loads(result_path.read_text()) if result_path.exists() else {}
        query = data.get("query") or {}
        reasons = data.get("reasons", []) if data.get("status") == "dependency_failure" else query.get("reasons", [])
        reason_codes = [item.get("code") for item in reasons if isinstance(item, dict)]
        actual_status = data.get("status") if data.get("status") == "dependency_failure" else query.get("status")
        if "status" in expected and actual_status != expected["status"]:
            raise ValueError("scenario_status_mismatch")
        if "query_state" in expected:
            actual_state = ("partial" if "partial_manifest" in reason_codes else "refused"
                            if data.get("status") == "dependency_failure" else
                            "complete" if query.get("complete") is True else "incomplete")
            if actual_state != expected["query_state"]:
                raise ValueError("scenario_state_mismatch")
        if expected.get("reason") and expected["reason"] not in reason_codes:
            raise ValueError("scenario_reason_mismatch")
        identities = private_json(root / "wiki" / "connected" / "fixture-identities.json")
        claim_labels = identities.get("claim_labels", {})
        all_labels = set(expected.get("required_claim_labels", [])) | set(expected.get("forbidden_claim_labels", []))
        if not all_labels.issubset(claim_labels):
            raise ValueError("scenario_claim_identity_missing")
        observed = {claim_labels[label] for label in expected.get("required_claim_labels", []) if label in claim_labels}
        forbidden = {claim_labels[label] for label in expected.get("forbidden_claim_labels", []) if label in claim_labels}
        evidence_text = json.dumps(data, sort_keys=True)
        if any(snapshot not in evidence_text for snapshot in observed) or any(snapshot in evidence_text for snapshot in forbidden):
            raise ValueError("scenario_claim_set_mismatch")
        report[case_id] = {"exit": result.returncode, "status": actual_status,
                           "query_state": actual_state if "query_state" in expected else
                           ("complete" if query.get("complete") is True else "incomplete"),
                           "reason_codes": reason_codes}


def _assert_expected(case: dict, code: int, data: dict) -> None:
    expected = case["expected"]
    if code != expected["exit"]:
        raise ValueError("scenario_exit_mismatch:" + case["id"])
    query = data.get("query") or {}
    reasons = data.get("reasons", []) if data.get("status") == "dependency_failure" else query.get("reasons", [])
    reason_codes = [item.get("code") for item in reasons if isinstance(item, dict)]
    status = data.get("status") if data.get("status") == "dependency_failure" else query.get("status")
    if status != expected.get("status"):
        raise ValueError("scenario_status_mismatch:" + case["id"])
    if status == "dependency_failure":
        if any(data.get(field) not in (None, [], {}) for field in ("claims", "citations", "answer")):
            raise ValueError("dependency_failure_disclosed_evidence:" + case["id"])
    state = ("partial" if "partial_manifest" in reason_codes else "refused"
             if data.get("status") == "dependency_failure" else
             "complete" if query.get("complete") is True else "incomplete")
    expected_state = ("refused" if case["id"] == "partial-manifest"
                      and expected.get("reason") == "graph_generation_changed"
                      else expected.get("query_state"))
    if state != expected_state:
        raise ValueError("scenario_query_state_mismatch:" + case["id"])
    if "reason" in expected:
        if expected["reason"] is None and reason_codes:
            raise ValueError("scenario_unexpected_reason:" + case["id"])
        if expected["reason"] is not None and expected["reason"] not in reason_codes:
            raise ValueError("scenario_reason_mismatch:" + case["id"])


def _summary(code: int, data: dict) -> dict:
    query = data.get("query") or {}
    reasons = data.get("reasons", []) if data.get("status") == "dependency_failure" else query.get("reasons", [])
    return {"exit": code, "status": data.get("status"), "query": query.get("status"),
            "complete": query.get("complete"),
            "reason_codes": [item.get("code") for item in reasons if isinstance(item, dict)]}


def _publish_report(report_path: Path, report: dict) -> None:
    report_dir = report_path.parent
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode("utf-8")) > 32768:
        raise ValueError("report_limit")
    fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        directory_fd = os.open(report_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            report_path.unlink()
        except OSError:
            pass
        raise


def _playwright_cli(value: str | None) -> Path:
    if not value:
        raise ValueError("playwright_cli_required")
    cli = Path(value).expanduser().resolve(strict=True)
    package = json.loads((cli.parent / "package.json").read_text())
    if cli.name != "cli.js" or package.get("name") != "@playwright/test" or package.get("version") != "1.63.0":
        raise ValueError("playwright_cli_invalid")
    return cli


def _run_browser(root: Path, browser: Path, phase: str, cli: Path) -> None:
    _, wiki = owned_containers(root)
    relay_files = sorted((root / "wiki").glob("relay-*.json"), key=lambda item: item.stat().st_mtime_ns)
    if not relay_files:
        raise ValueError("owned_relay_record_missing")
    relay = private_json(relay_files[-1])
    parsed_url = urlsplit(relay.get("url", ""))
    if (type(relay.get("pid")) is not int or relay["pid"] < 1
            or relay.get("container_id") != wiki or parsed_url.scheme != "http:"
            or parsed_url.hostname != "127.0.0.1" or parsed_url.username or parsed_url.password
            or parsed_url.path != "/w/" or parsed_url.query or parsed_url.fragment):
        raise ValueError("owned_relay_record_invalid")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
    env.update({"NODE_PATH": str(cli.parents[2]), "WIKIKG_SYNTHETIC_BROWSER_ROOT": str(root),
                "WIKIKG_SYNTHETIC_URL": relay["url"],
                "WIKIKG_SYNTHETIC_BROWSER_PHASE": phase,
                "WIKIKG_SYNTHETIC_BROWSER_OUTPUT": str(root / "connected" / ("browser-" + phase)),
                "WIKIKG_SYNTHETIC_PASSWORD_FILE": str(root / "wiki" / "reviewer-password"),
                "WIKIKG_SYNTHETIC_IDENTITIES_FILE": str(root / "wiki" / "connected" / "fixture-identities.json"),
                "WIKIKG_SYNTHETIC_CATALOG_FILE": str(root / "wiki" / "connected" / "questions.json")})
    artifacts = Path(env["WIKIKG_SYNTHETIC_BROWSER_OUTPUT"])
    artifacts.mkdir(mode=0o700)
    os.chmod(artifacts, 0o700)
    log = artifacts / (phase + ".log")
    try:
        result = subprocess.run(["node", str(cli), "test", "--config", str(browser / "config.cjs")], cwd=browser, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
    except subprocess.TimeoutExpired as error:
        captured = error.stdout or b""
        if isinstance(captured, str):
            captured = captured.encode("utf-8", "replace")
        with log.open("xb") as output:
            output.write(captured[:1_000_000])
        os.chmod(log, 0o600)
        raise ValueError("browser_timeout:" + phase) from error
    with log.open("xb") as output:
        output.write(result.stdout[:1_000_000])
    os.chmod(log, 0o600)
    if result.returncode:
        raise ValueError("browser_verification_failed:" + phase)


def _export(root: Path, name: str) -> Path:
    sys.path.insert(0, str(HERE))
    from run import in_wiki
    host_path = root / "wiki" / "connected" / name
    if host_path.exists():
        raise ValueError("export_target_exists")
    in_wiki(root, "php", "extensions/WikiKGExtractor/maintenance/exportReviewedProjection.php", "--actor=Synthetic reviewer",
        "--synthetic-only", "--document-root=/var/www/html", "--output=/private/wikikg/connected/" + name,
        timeout=90)
    return host_path


def _approve_rejection_fixture(root: Path, snapshot_id: str) -> None:
    sys.path.insert(0, str(HERE))
    from run import in_wiki
    output = in_wiki(root, "php", "extensions/WikiKGExtractor/maintenance/reviewOfflineClaim.php",
        "--actor=Synthetic reviewer", "--snapshot=" + snapshot_id, "--status=approved",
        "--expected-version=2", "--audience=actor-only", "--reason=Synthetic verifier fixture reset only",
        "--offline-only", "--synthetic-only", timeout=90)
    summary = json.loads(output.decode("utf-8").strip())
    if summary != {"status": "recorded", "snapshot_id": snapshot_id,
                   "review_version": 3, "decision_status": "approved"}:
        raise ValueError("fixture_review_reset_not_recorded")


def verify(root: Path, playwright_cli: str | None = None) -> dict:
    report_dir = root / "connected"
    if not report_dir.exists() and not report_dir.is_symlink():
        report_dir.mkdir(mode=0o700)
    directory_info = report_dir.lstat()
    if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != os.geteuid() or stat.S_IMODE(directory_info.st_mode) != 0o700:
        raise ValueError("report_directory_unsafe")
    report_path = report_dir / "final-report.json"
    if report_path.exists():
        raise ValueError("report_exists")
    report = {"status": "running", "cases": {}, "started": int(time.time())}
    try:
        cli = _playwright_cli(playwright_cli)
        graph, wiki = owned_containers(root)
        scenario_path = HERE / "scenarios.json"
        if scenario_path.is_symlink() or not scenario_path.is_file():
            raise ValueError("scenario_oracle_missing")
        scenarios = json.loads(scenario_path.read_text())
        expected_ids = set(STATIC_IDS + STATE_IDS)
        if {case.get("id") for case in scenarios.get("scenarios", [])} != expected_ids:
            raise ValueError("scenario_inventory_invalid")
        by_id = {case["id"]: case for case in scenarios["scenarios"]}
        manifest = root / "wiki" / "connected" / "reviewed.json"
        private_json(manifest)
        # Test an incomplete binding against the still-active complete graph.
        partial_doc = json.loads(manifest.read_text())
        partial_doc["claims"] = partial_doc["claims"][:-1]
        partial_path = root / "wiki" / "connected" / "reviewed-partial.json"
        if partial_path.exists():
            raise ValueError("partial_export_exists")
        write_private_new(partial_path, json.dumps(partial_doc, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8"))
        partial_code, partial_data = cli_case(root, wiki, by_id["partial-manifest"],
            "/private/wikikg/connected/reviewed-partial.json", "partial-manifest")
        _assert_expected(by_id["partial-manifest"], partial_code, partial_data)
        report["cases"]["partial-manifest"] = _summary(partial_code, partial_data)
        _query_cases(root, wiki, manifest, scenarios, report["cases"])
        permission_loss_case(root, wiki, by_id["permission-loss"], report["cases"], manifest)
        # Browser scenarios exercise the real special page; no request interception
        # or fake DOM responses are used. The report is kept outside the checkout.
        browser = HERE.parents[2] / "tests" / "browser" / "synthetic_questions"
        if not browser.is_dir():
            raise ValueError("browser_suite_missing")
        # Browser rejection is a real review action against the dedicated fixture.
        # Keep source editing for the later phase after the refreshed positive CLI.
        _run_browser(root, browser, "pre-source", cli)
        report["cases"]["browser"] = "passed"
        rejected_id = private_json(root / "wiki" / "connected" / "fixture-identities.json")["rejected_snapshot_id"]
        rejected_code, rejected_result = cli_case(root, wiki, by_id["rejected-claim"],
            "/private/wikikg/connected/reviewed.json", "rejected-claim")
        _assert_expected(by_id["rejected-claim"], rejected_code, rejected_result)
        report["cases"]["rejected-claim"] = _summary(rejected_code, rejected_result)
        # Reapprove only the invented rejection target (version 2). The appended
        # approved/rejected/approved history remains authoritative.
        _approve_rejection_fixture(root, rejected_id)
        refreshed = _export(root, "reviewed-after-rejection-reset.json")
        refreshed_doc = private_json(refreshed)
        if refreshed_doc.get("snapshot_count") != len(refreshed_doc.get("claims", [])):
            raise ValueError("refreshed_export_not_complete")
        from run import load_graph
        load_graph(root, "/private/wikikg/connected/reviewed-after-rejection-reset.json")
        positive_code, positive = cli_case(root, wiki, by_id["supported-compound"],
            "/private/wikikg/connected/reviewed-after-rejection-reset.json", "refreshed-positive")
        if positive_code != by_id["supported-compound"]["expected"]["exit"]:
            raise ValueError("refreshed_positive_cli_failed")
        if positive.get("query", {}).get("status") != by_id["supported-compound"]["expected"]["status"] or \
                positive.get("query", {}).get("complete") is not True:
            raise ValueError("refreshed_positive_result_mismatch")
        identities = private_json(root / "wiki" / "connected" / "fixture-identities.json")
        positive_text = json.dumps(positive, sort_keys=True)
        expected_labels = by_id["supported-compound"]["expected"]["required_claim_labels"]
        if any(identities["claim_labels"][label] not in positive_text for label in expected_labels):
            raise ValueError("refreshed_positive_witness_missing")
        if any(identities["claim_labels"][label] in positive_text
               for label in by_id["supported-compound"]["expected"]["forbidden_claim_labels"]):
            raise ValueError("refreshed_positive_forbidden_witness")
        report["cases"]["refreshed-positive"] = _summary(positive_code, positive)
        identities = private_json(root / "wiki" / "connected" / "fixture-identities.json")
        source_positive = dict(by_id["source-changed"])
        source_positive["expected"] = {"exit": 0, "status": "ok", "query_state": "complete",
            "reason": None, "required_claim_labels": [identities["source_change_claim_label"]],
            "forbidden_claim_labels": []}
        source_positive_code, source_positive_result = cli_case(root, wiki, source_positive,
            "/private/wikikg/connected/reviewed-after-rejection-reset.json", "source-change-positive")
        _assert_expected(source_positive, source_positive_code, source_positive_result)
        positive_source_snapshot = identities["source_change_snapshot_id"]
        if positive_source_snapshot not in json.dumps(source_positive_result, sort_keys=True):
            raise ValueError("source_change_positive_witness_missing")
        report["cases"]["source-change-positive"] = _summary(source_positive_code, source_positive_result)
        _run_browser(root, browser, "source-change", cli)
        source_code, source_result = cli_case(root, wiki, by_id["source-changed"],
            "/private/wikikg/connected/reviewed-after-rejection-reset.json", "source-changed")
        _assert_expected(by_id["source-changed"], source_code, source_result)
        report["cases"]["source-changed"] = _summary(source_code, source_result)
        graph, wiki = owned_containers(root)
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "DOCKER_CONFIG"}}
        subprocess.run(["docker", "stop", "--time", "10", graph], env=env,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30, check=True)
        outage_code, outage = cli_case(root, wiki, by_id["graph-unavailable"],
            "/private/wikikg/connected/reviewed-after-rejection-reset.json", "graph-unavailable")
        reasons = [item.get("code") for item in outage.get("reasons", []) if isinstance(item, dict)]
        _assert_expected(by_id["graph-unavailable"], outage_code, outage)
        if outage.get("query") is not None or outage.get("citations") or outage.get("claims") or outage.get("answer") is not None:
            raise ValueError("graph_outage_fallback_oracle_mismatch")
        report["cases"]["graph-unavailable"] = {"exit": outage_code, "reason_codes": reasons,
                                                   "citations": len(outage.get("citations", []))}
        if not expected_ids.issubset(report["cases"]):
            raise ValueError("verification_case_missing")
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = error.args[0] if error.args and isinstance(error.args[0], str) else "verification_failed"
    finally:
        try:
            stop_owned(root)
            report["cleanup"] = "passed"
        except Exception:
            report["cleanup"] = "failed"
            report["status"] = "failed"
            report["error"] = "cleanup_failed"
    report["finished"] = int(time.time())
    error = report.get("error")
    if error is not None and not re.fullmatch(r"[a-z0-9_:-]{1,80}", str(error)):
        report["error"] = "verification_failed"
    try:
        _publish_report(report_path, report)
    except ValueError as error:
        if str(error) != "report_limit":
            raise
        minimal = {"status": "failed", "cleanup": report.get("cleanup", "failed"),
                   "error": "report_limit", "finished": report["finished"]}
        _publish_report(report_path, minimal)
        report = minimal
    return report


if __name__ == "__main__":
    print(json.dumps({"status": "usage", "message": "Invoke via questions/run.py verify"}))
