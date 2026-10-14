"""Offline paired evaluation. Reports never contain question or answer text."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "comparison-protocol.v1.schema.json"
REPORT = "comparison-report.v1.json"
MAX_INPUT = 20 * 1024 * 1024
HEX = re.compile(r"^[0-9a-f]{64}$")
CONTROL_KEYS = ("source_snapshot", "source_revision_digest", "model", "prompt_hash", "decoding", "context_budget", "audience", "common_controls")
ARM_HASH_KEYS = ("code_sha256", "schema_sha256", "graph_sha256", "index_sha256", "source_sha256")
SENTINELS = {"", "unknown", "n/a", "na", "tbd", "placeholder", "pending", "dữ liệu chờ cập nhật", "chưa cập nhật", "không rõ"}


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def load(path: Path) -> Any:
    if path.stat().st_size > MAX_INPUT:
        raise ValueError("input_too_large")
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError("input_too_large")
    if path.suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("invalid_csv_header")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("invalid_csv")
        for row in rows:
            if "split_hand_checked" in row:
                if row["split_hand_checked"] not in ("true", "false"):
                    raise ValueError("invalid_csv_split_check")
                row["split_hand_checked"] = row["split_hand_checked"] == "true"
        return rows
    return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json_number")))


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def receipt_digest(value: Any) -> str | None:
    try:
        return sha(value)
    except (TypeError, ValueError, OverflowError):
        return None


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def clean_reference(q: dict[str, Any]) -> bool:
    ref = q.get("reference")
    if not isinstance(ref, str):
        return False
    value = normalize(ref).strip(" .:;-!?")
    return bool(value) and value not in SENTINELS and q.get("reference_status") == "confirmed_independent" and q.get("split") in ("development", "heldout") and q.get("split_hand_checked") is True


def question_hash(questions: list[dict[str, Any]]) -> str:
    return sha(sorted(questions, key=lambda q: str(q.get("question_id", ""))))


def reference_split_hash(questions: list[dict[str, Any]]) -> str:
    identities = []
    for q in questions:
        ref = q.get("reference")
        identities.append({"question_id":q.get("question_id"), "reference_status":q.get("reference_status"), "reference_sha256":hashlib.sha256(ref.encode()).hexdigest() if isinstance(ref,str) else None, "split":q.get("split"), "split_hand_checked":q.get("split_hand_checked")})
    return sha(sorted(identities, key=lambda x: str(x["question_id"])))


def validate_protocol(protocol: Any, questions: list[dict[str, Any]]) -> tuple[bool, str | None]:
    if not isinstance(protocol,dict): return False,None
    try:
        import jsonschema
        jsonschema.validate(protocol, json.loads(SCHEMA.read_text(encoding="utf-8")))
    except ImportError as e:
        raise ValueError("schema_validator_unavailable") from e
    except Exception:
        return False,sha(protocol)
    payload = {k:v for k,v in protocol.items() if k != "protocol_hash"}
    valid = (sha(payload) == protocol["protocol_hash"] and
             protocol["human_approved_frozen"] is True and
             protocol["question_set_sha256"] == question_hash(questions) and
             protocol["hand_checked_split_hash"] == reference_split_hash(questions))
    return valid, sha(payload)


def strict_rows(rows: Any, label: str, issues: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        issues.append("invalid_" + label + "_results"); return {}
    ids = [r.get("question_id") for r in rows if isinstance(r,dict)]
    if len(ids) != len(rows) or any(not isinstance(i,str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
        issues.append("invalid_or_duplicate_" + label + "_result_ids")
    return {r["question_id"]:r for r in rows if isinstance(r,dict) and isinstance(r.get("question_id"),str)}


def metric(rows: dict[str,dict[str,Any]], key: str, valid_ids: set[str], issues: list[str], annotated_ids: set[str] | None = None) -> dict[str,int]:
    ids = valid_ids if annotated_ids is None else valid_ids & annotated_ids
    values = [rows[i].get(key) for i in ids if i in rows]
    invalid = [v for v in values if v is not None and type(v) is not bool]
    if invalid: issues.append("invalid_metric_"+key)
    good = [v for v in values if type(v) is bool]
    return {"yes":sum(good), "no":len(good)-sum(good), "adjudicated":len(good), "excluded":len(ids)-len(good)}


def runtime(rows: dict[str,dict[str,Any]], issues: list[str]) -> dict[str,Any]:
    values=[r.get("runtime_seconds") for r in rows.values()]
    if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<0) for v in values):
        issues.append("invalid_runtime_seconds"); return {"count":0,"mean_seconds":None,"missing":len(values)}
    nums=[v for v in values if v is not None]
    return {"count":len(nums),"mean_seconds":sum(nums)/len(nums) if nums else None,"missing":len(values)-len(nums)}


def evaluate(questions: list[dict[str,Any]], before: dict[str,Any], after: dict[str,Any], protocol: dict[str,Any]) -> dict[str,Any]:
    issues: list[str]=[]
    valid_protocol, protocol_digest = validate_protocol(protocol, questions)
    if not valid_protocol: issues.append("protocol_unbound_or_unapproved")
    qids=[q.get("question_id") for q in questions if isinstance(q,dict)]
    if len(qids)!=len(questions) or any(not isinstance(i,str) or not i.strip() for i in qids) or len(qids)!=len(set(qids)):
        issues.append("invalid_or_duplicate_question_ids")
    if any(not isinstance(q, dict) or q.get("prior_exposure") not in ("seen", "unseen", "none", "unknown")
           for q in questions):
        issues.append("missing_or_invalid_prior_exposure")
    b=strict_rows(before.get("results"),"before",issues); a=strict_rows(after.get("results"),"after",issues)
    if set(qids)!=set(b) or set(qids)!=set(a): issues.append("missing_or_extra_paired_questions")
    mismatch=[]
    if valid_protocol:
        for key in CONTROL_KEYS:
            if (key not in before or key not in after
                    or canonical(before[key]) != canonical(after[key])
                    or canonical(protocol.get("shared_controls", {}).get(key)) != canonical(before.get(key))):
                mismatch.append(key)
    if mismatch: issues.extend("control_mismatch_"+k for k in mismatch)
    for receipt in (before,after):
        hashes=receipt.get("artifact_hashes")
        if not isinstance(hashes,dict) or any(not isinstance(hashes.get(k),str) or not HEX.fullmatch(hashes[k]) for k in ARM_HASH_KEYS): issues.append("invalid_arm_artifact_hashes")
    for rows in (b, a):
        for row in rows.values():
            for key in ("human_correct", "extraction_valid", "retrieval_relevant", "citation_supported", "abstained", "unanswered"):
                if row.get(key) is not None and type(row[key]) is not bool:
                    issues.append("invalid_metric_" + key)
    refs={q["question_id"] for q in questions if isinstance(q,dict) and isinstance(q.get("question_id"),str) and clean_reference(q)}
    scientific_ok=valid_protocol and not issues and bool(refs)
    def has_verdict(row):
        return (type(row.get("human_correct")) is bool
                or (row.get("unanswered") is True and protocol.get("unanswered_counts_wrong") is True))
    paired_verdicts = {qid for qid in refs if has_verdict(b.get(qid, {})) and has_verdict(a.get(qid, {}))}
    annotations: dict[str,set[str]]={}
    for kind, field in (("retrieval","retrieval_relevant"),("citation","citation_supported")):
        annotations[kind]={q["question_id"] for q in questions if isinstance(q,dict) and q.get(kind+"_annotation_status")=="independent_hand_checked" and q.get("split_hand_checked") is True and clean_reference(q)}
        annotations[kind] &= {qid for qid in refs if type(b.get(qid, {}).get(field)) is bool
                              and type(a.get(qid, {}).get(field)) is bool}
    arms={}
    for name,rows in (("before",b),("after",a)):
        denom=refs if scientific_ok else set()
        correctness={"correct":0,"wrong":0,"denominator":0,"excluded":len(qids)}
        if scientific_ok:
            correct=wrong=excluded=0
            for qid in paired_verdicts:
                r=rows.get(qid,{})
                verdict=r.get("human_correct")
                if type(verdict) is bool:
                    correct += verdict; wrong += not verdict
                elif r.get("unanswered") is True and protocol.get("unanswered_counts_wrong") is True:
                    wrong += 1
                else:
                    if verdict is not None: issues.append("invalid_human_correct")
                    excluded += 1
            correctness={"correct":correct,"wrong":wrong,"denominator":correct+wrong,"excluded":excluded+len(qids)-len(paired_verdicts)}
        arms[name]={"extraction_validity":metric(rows,"extraction_valid",set(qids),issues),
                    "retrieval_relevance":metric(rows,"retrieval_relevant",denom,issues,annotations["retrieval"]),
                    "answer_correctness":correctness,
                    "citation_support":metric(rows,"citation_supported",denom,issues,annotations["citation"]),
                    "abstention":metric(rows,"abstained",set(qids),issues),"runtime_seconds":runtime(rows,issues)}
    status="invalid" if issues and any(not x.startswith("protocol_unbound") for x in issues) else ("engineering_only" if not scientific_ok else "ready")
    if not refs:
        status = "invalid" if any(not issue.startswith("protocol_unbound") for issue in issues) else "engineering_only"
    if status != "ready":
        for arm in arms.values():
            arm["answer_correctness"]={"correct":0,"wrong":0,"denominator":0,"excluded":len(qids)}
            arm["retrieval_relevance"]={"yes":0,"no":0,"adjudicated":0,"excluded":len(qids)}
            arm["citation_support"]={"yes":0,"no":0,"adjudicated":0,"excluded":len(qids)}
    return {"schema_version":"1.0","status":status,"mode":"evaluation" if status=="ready" else "validate_only","protocol_sha256":protocol_digest,
            "question_set_sha256":question_hash(questions),"reference_split_sha256":reference_split_hash(questions),
             "counts":{"questions":len(questions),"eligible_independent_references":len(refs),
                       "reference_splits": {split: sum(q.get("question_id") in refs and q.get("split") == split
                                                  for q in questions if isinstance(q, dict))
                                            for split in ("development", "heldout")},
                       "prior_exposure_recorded": sum(q.get("prior_exposure") in ("seen", "unseen", "none", "unknown")
                                                      for q in questions if isinstance(q,dict)),
                       "prior_exposure": {label: sum(q.get("prior_exposure") == label
                                                    for q in questions if isinstance(q, dict))
                                          for label in ("seen", "unseen", "none", "unknown")}},
            "issues":sorted(set(issues)),"arms":arms,
            "receipts": {"before_sha256": receipt_digest(before), "after_sha256": receipt_digest(after),
                         "before_artifacts": {key: value if isinstance(value, str) and HEX.fullmatch(value) else None
                             for key in ARM_HASH_KEYS for value in [before.get("artifact_hashes", {}).get(key)]}
                             if isinstance(before.get("artifact_hashes"), dict) else None,
                         "after_artifacts": {key: value if isinstance(value, str) and HEX.fullmatch(value) else None
                             for key in ARM_HASH_KEYS for value in [after.get("artifact_hashes", {}).get(key)]}
                             if isinstance(after.get("artifact_hashes"), dict) else None,
                         "shared_controls_sha256": sha(protocol["shared_controls"]) if valid_protocol else None}}


def safe_output(output: Path, document_root: Path) -> None:
    output = Path(os.path.abspath(output))
    document_root = document_root.resolve(strict=True)
    if output != output.absolute() or output.exists() or output.is_symlink(): raise ValueError("unsafe_output_path")
    if ROOT in (output, *output.parents) or document_root in (output, *output.parents) or output in document_root.parents: raise ValueError("output_boundary_violation")
    parent=output.parent
    if not parent.is_dir() or any(p.is_symlink() for p in (parent,*parent.parents)): raise ValueError("unsafe_output_parent")
    mode=stat.S_IMODE(parent.stat().st_mode)
    if parent.stat().st_uid != os.getuid() or mode & 0o077: raise ValueError("output_parent_not_private")


def private_input(path: Path, document_root: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("unsafe_input_path")
    resolved = absolute.resolve(strict=True)
    if ROOT in (resolved, *resolved.parents) or document_root in (resolved, *resolved.parents):
        raise ValueError("input_boundary_violation")
    info, parent = resolved.stat(), resolved.parent.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_INPUT
            or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("input_not_private")
    return resolved


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__,epilog="Example: evaluate_comparison.py --questions <private.json> --before <before.json> --after <after.json> --protocol <protocol.json> --output <new-private-dir> --document-root <web-root>")
    for arg in ("questions","before","after","protocol","output"): p.add_argument("--"+arg,required=True)
    p.add_argument("--document-root",default=os.environ.get("WIKICROP_DOCUMENT_ROOT"))
    args=p.parse_args()
    try:
        if not args.document_root: raise ValueError("document_root_required")
        web = Path(args.document_root).expanduser().resolve(strict=True)
        if not web.is_dir(): raise ValueError("document_root_required")
        paths = {key: private_input(Path(getattr(args, key)).expanduser(), web)
                 for key in ("questions", "before", "after", "protocol")}
        out = Path(os.path.abspath(Path(args.output).expanduser()))
        safe_output(out,web)
        questions=load(paths["questions"]); before=load(paths["before"]); after=load(paths["after"]); protocol=load(paths["protocol"])
        if not isinstance(questions,list) or not isinstance(before,dict) or not isinstance(after,dict): raise ValueError("invalid_input_shape")
        report=evaluate(questions,before,after,protocol)
        out.mkdir(mode=0o700)
        target=out/REPORT
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(json.dumps(report,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps({"status":report["status"],"counts":report["counts"]},sort_keys=True))
        return 1 if report["status"]=="invalid" else (2 if report["status"]!="ready" else 0)
    except Exception as e:
        code=str(e) if isinstance(e,ValueError) and re.fullmatch(r"[a-z_]+",str(e)) else "evaluation_failed"
        print(json.dumps({"error":code},sort_keys=True),file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
