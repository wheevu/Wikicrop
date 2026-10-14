"""Ask a structured reviewer-only question through the local PHP evidence gate.

This command never calls a model or the live assistant backend. Authorization
files are private, retained per invocation, and never reused as cached approval.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import reviewed_projection as projection
from bounded_process import child_has_exited, require_owned_group_support, stop_owned_group

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
BRIDGE = ROOT / "maintenance" / "authorizeOfflineEvidence.php"
REQUEST_SCHEMA = ROOT / "schema" / "offline-question.v1.schema.json"
VERSION = "wikikg.reviewed-answer.v1"
MAX_REQUEST = 65_536
MAX_RESPONSE = 1_048_576
MAX_RESULT = 2_000_000
MAX_EXPORT = 8_000_000
MAX_CALLS = 8
MAX_WORK_BYTES = 12_000_000
REASONS = {
    "invalid_arguments": "Required offline command options are missing or invalid.",
    "unsafe_private_path": "Use owner-only files and directories outside the checkout and document root.",
    "invalid_request": "The private question document is malformed or unsupported.",
    "invalid_export": "The selected reviewed export is malformed or unsupported.",
    "runtime_missing": "The PHP executable, wiki runtime, or Python dependency is unavailable.",
    "runtime_failure": "The local wiki process did not complete authorization.",
    "authorization_refused": "Current evidence authorization was refused.",
    "offline_authorization_refused": "Current evidence authorization was refused.",
    "authorization_dependency_failure": "The authoritative source store could not be checked.",
    "invalid_authorization_request": "The evidence authority refused the identity request.",
    "authorization_response_limit": "The evidence authority response exceeds its bound.",
    "source_revision_changed": "A selected source revision changed or is no longer visible.",
    "source_content_changed": "Current source content no longer matches the export.",
    "source_evidence_changed": "Current evidence changed during authorization.",
    "source_approval_changed": "Source approval is missing, changed, or ambiguous.",
    "claim_review_changed": "A claim snapshot or its current review decision changed.",
    "export_watermark_changed": "The review-store watermark changed. Create a new reviewed export.",
    "authorization_timeout": "The offline question deadline expired.",
    "malformed_authorization_response": "The evidence authority returned malformed or incomplete data.",
    "authorization_output_limit": "The private authorization output budget was reached.",
    "result_output_limit": "The private question result exceeds its output bound.",
    "private_write_failed": "The private result could not be installed. Use a new result path.",
    "interrupted": "The offline question was interrupted.",
}


class OfflineError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Do not echo arbitrary flag values, paths, or private request content.
        print(json.dumps({"status": "refused", "error": "invalid_arguments"}), file=sys.stderr)
        raise SystemExit(2)


def _plain_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise OfflineError("unsafe_private_path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise OfflineError("unsafe_private_path")
        except FileNotFoundError:
            continue


def _private_directory(path: Path, web: Path) -> Path:
    _plain_path(path)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise OfflineError("unsafe_private_path")
    real = path.resolve(strict=True)
    for root in (REPOSITORY, web):
        if real == root or root in real.parents:
            raise OfflineError("unsafe_private_path")
    return real


def _read_private(path: Path, bound: int, web: Path) -> bytes:
    _private_directory(path.parent, web)
    _plain_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > bound):
            raise OfflineError("unsafe_private_path")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(bound + 1)
        if len(raw) > bound:
            raise OfflineError("unsafe_private_path")
        return raw
    finally:
        os.close(fd)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                      parse_float=Decimal, parse_constant=_constant)


def _write_new(path: Path, raw: bytes, bound: int, web: Path) -> None:
    if len(raw) > bound:
        raise OfflineError("result_output_limit")
    _private_directory(path.parent, web)
    _plain_path(path)
    if path.exists() or path.is_symlink():
        raise OfflineError("unsafe_private_path")
    # Retain this owned staging file on interruption; never expose a partial result.
    fd, staging_name = tempfile.mkstemp(prefix=".question-write-", dir=path.parent)
    staging = Path(staging_name)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise OfflineError("unsafe_private_path")
        with os.fdopen(fd, "wb", closefd=False) as stream:
            if stream.write(raw) != len(raw):
                raise OfflineError("result_output_limit")
            stream.flush()
            os.fsync(fd)
        _private_directory(path.parent, web)
        _plain_path(path)
        os.link(staging, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(fd)


def _loopback(base: str, script: str) -> None:
    try:
        parsed = urlsplit(base)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        if (len(base) > 512 or "\\" in base or any(ord(c) < 32 or ord(c) == 127 for c in base)
                or parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or not valid_port or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*index\.php", script)):
            raise OfflineError("invalid_arguments")
    except ValueError as error:
        if isinstance(error, OfflineError):
            raise
        raise OfflineError("invalid_arguments") from error


def _work_bytes(root: Path, web: Path) -> int:
    _private_directory(root, web)
    total = 0
    for child in root.iterdir():
        _plain_path(child)
        if child.is_dir():
            _private_directory(child, web)
            for file in child.iterdir():
                _plain_path(file)
                info = file.stat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise OfflineError("unsafe_private_path")
                total += info.st_size
        else:
            raise OfflineError("unsafe_private_path")
    return total


class EvidenceBridge:
    def __init__(self, *, actor: str, web: Path, wiki: Path, php: str, private_root: Path,
                 watermark: str, corpus_manifest: Path | None, synthetic: bool,
                 deadline: float):
        self.actor, self.web, self.wiki, self.php = actor, web, wiki, php
        self.root = Path(tempfile.mkdtemp(prefix="question-auth-", dir=private_root))
        os.chmod(self.root, 0o700)
        self.watermark, self.corpus_manifest, self.synthetic = watermark, corpus_manifest, synthetic
        self.deadline = deadline
        self.calls = 0
        self.failure: str | None = None

    def _call(self, graph: list[dict[str, Any]], text: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        if self.calls > MAX_CALLS or time.monotonic() >= self.deadline:
            raise OfflineError("authorization_timeout")
        remaining = MAX_WORK_BYTES - _work_bytes(self.root, self.web) - 2 * MAX_REQUEST
        if remaining < MAX_RESPONSE * 3:
            raise OfflineError("authorization_output_limit")
        attempt = self.root / f"authorization-{self.calls:02d}"
        attempt.mkdir(mode=0o700)
        request = attempt / "identities.json"
        response = attempt / "current.json"
        payload = {"graph": [{key: claim[key] for key in
                              ("snapshot_id", "snapshot_hash", "review_version")} for claim in graph],
                   "text": text, "source_watermark": self.watermark}
        _write_new(request, projection.canonical_json(payload).encode("utf-8"), MAX_REQUEST, self.web)
        command = [self.php, str(BRIDGE), "--actor=" + self.actor, "--offline-only",
                   "--audience=actor-only", "--document-root=" + str(self.web),
                   "--request=" + str(request), "--output=" + str(response)]
        command += (["--synthetic-only"] if self.synthetic else
                    ["--corpus-manifest=" + str(self.corpus_manifest)])
        environment = {**os.environ, "MW_INSTALL_PATH": str(self.wiki), "DOCUMENT_ROOT": str(self.web)}

        def child_limits() -> None:
            import resource

            os.umask(0o077)
            # One response and two logs, each hard-capped. No unbounded pipes.
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESPONSE, MAX_RESPONSE))

        process = None
        logs = [attempt / "stdout.log", attempt / "stderr.log"]
        handles = []
        try:
            for path in logs:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                handles.append(os.fdopen(fd, "wb"))
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handles[0],
                                       stderr=handles[1], cwd=self.wiki, env=environment,
                                       start_new_session=True, preexec_fn=child_limits)
            while not child_has_exited(process):
                if time.monotonic() >= self.deadline:
                    raise OfflineError("authorization_timeout")
                if _work_bytes(self.root, self.web) > MAX_WORK_BYTES:
                    raise OfflineError("authorization_output_limit")
                time.sleep(0.05)
            if _work_bytes(self.root, self.web) > MAX_WORK_BYTES:
                raise OfflineError("authorization_output_limit")
            stop_owned_group(process)
            if process.returncode != 0:
                if process.returncode == -signal.SIGXFSZ:
                    raise OfflineError("authorization_output_limit")
                code = "runtime_failure"
                for log in logs:
                    # The bridge emits only stable codes. Never publish raw logs.
                    raw = _read_private(log, MAX_RESPONSE, self.web)
                    matches = re.findall(rb'\{"status":"dependency_failure"[^\r\n]*?\}', raw)
                    for match in matches:
                        try:
                            failure = _json(match)
                            if (isinstance(failure, dict)
                                    and failure.get("error") == "offline_authorization_refused"):
                                reason = failure.get("reason", "authorization_refused")
                                code = reason if isinstance(reason, str) and reason in REASONS else "authorization_refused"
                        except (ValueError, UnicodeError):
                            continue
                raise OfflineError(code)
        except FileNotFoundError as error:
            raise OfflineError("runtime_missing") from error
        finally:
            try:
                if process is not None and process.returncode is None:
                    # Popen created this session; no discovered or unowned PID is used.
                    stop_owned_group(process)
            finally:
                for handle in handles:
                    handle.close()
        try:
            document = _json(_read_private(response, MAX_RESPONSE, self.web))
            if (not isinstance(document, dict) or set(document) != {"schema_version", "status", "claims", "text"}
                    or document["schema_version"] != "wikikg.current-evidence.v1"
                    or document["status"] != "ok" or not isinstance(document["claims"], list)
                    or not isinstance(document["text"], list)):
                raise ValueError("incomplete current evidence")
            if len(document["claims"]) != len(graph) or len(document["text"]) != len(text):
                raise ValueError("missing evidence identities")
            actual_graph = {}
            for claim in document["claims"]:
                if (not isinstance(claim, dict) or not isinstance(claim.get("snapshot_id"), str)
                        or claim["snapshot_id"] in actual_graph):
                    raise ValueError("duplicate or malformed graph identity")
                actual_graph[claim["snapshot_id"]] = claim
            expected_graph = {claim["snapshot_id"]: claim for claim in graph}
            if set(actual_graph) != set(expected_graph):
                raise ValueError("unexpected graph identities")
            if projection.canonical_json(actual_graph) != projection.canonical_json(expected_graph):
                raise OfflineError("source_evidence_changed")
            expected_text = {(item["page_id"], item["revision_id"], item["content_hash"]) for item in text}
            actual_text = set()
            for item in document["text"]:
                if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                    raise ValueError("missing authoritative source text")
                identity = (item.get("page_id"), item.get("revision_id"), item.get("content_hash"))
                if identity in actual_text or identity not in expected_text:
                    raise ValueError("unexpected text identities")
                actual_text.add(identity)
            if actual_text != expected_text:
                raise ValueError("missing authoritative text identities")
            return document
        except OfflineError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise OfflineError("malformed_authorization_response") from error

    def graph_authorizer(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            result = self._call(claims, [])
            return {"status": "ok", "claims": result["claims"]}
        except OfflineError as error:
            self.failure = error.code
            raise

    def text_authorizer(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            result = self._call([], candidates)
            return {"status": "ok", "text": result["text"]}
        except OfflineError as error:
            self.failure = error.code
            raise


def _failure(code: str) -> dict[str, Any]:
    return {"version": VERSION, "offline_only": True, "status": "dependency_failure",
            "query": None, "answer": None, "citations": [],
            "reasons": [{"code": code, "message": REASONS.get(code, REASONS["runtime_failure"])}]}


def _interrupted(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__, epilog=(
        "Example: offline_answer.py --manifest <private-reviewed.json> --request <private-question.json> "
        "--output <new-private-result.json> --actor <reviewer> --wiki-root <wiki> "
        "--document-root <document-root> --corpus-manifest <pinned-metadata.json> "
        "--offline-only --audience actor-only. No model or HTTP backend is called."))
    for name in ("manifest", "request", "output", "actor", "wiki-root", "document-root"):
        parser.add_argument("--" + name, required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--corpus-manifest")
    scope.add_argument("--synthetic-only", action="store_true", help="Disposable synthetic wiki only")
    parser.add_argument("--offline-only", action="store_true", required=True)
    parser.add_argument("--audience", choices=["actor-only"], required=True)
    parser.add_argument("--php", default="php", help="PHP executable, resolved without a shell")
    parser.add_argument("--timeout", type=int, default=60, help="Whole-question deadline, 1-900 seconds")
    parser.add_argument("--wiki-base", default="http://127.0.0.1")
    parser.add_argument("--wiki-script-path", default="/w/index.php")
    args = parser.parse_args(argv)
    result, output, web = None, None, None
    bridge = None
    previous_term = None
    begun = time.monotonic()
    try:
        if (os.name != "posix" or not hasattr(os, "geteuid") or not hasattr(os, "O_NOFOLLOW")
                or not 1 <= args.timeout <= 900 or not args.actor.strip() or len(args.actor) > 255
                or any(ord(c) < 32 for c in args.actor)):
            raise OfflineError("invalid_arguments")
        try:
            require_owned_group_support()
        except ValueError as error:
            raise OfflineError("runtime_missing") from error
        previous_term = signal.signal(signal.SIGTERM, _interrupted)
        _loopback(args.wiki_base, args.wiki_script_path)
        web, wiki = Path(args.document_root), Path(args.wiki_root)
        _plain_path(web)
        _plain_path(wiki)
        web, wiki = web.resolve(strict=True), wiki.resolve(strict=True)
        if not web.is_dir() or not (wiki / "maintenance" / "Maintenance.php").is_file():
            raise OfflineError("runtime_missing")
        if os.environ.get("DOCUMENT_ROOT") and Path(os.environ["DOCUMENT_ROOT"]).resolve() != web:
            raise OfflineError("invalid_arguments")
        output = Path(args.output)
        _private_directory(output.parent, web)
        _plain_path(output)
        if output.exists() or output.is_symlink():
            raise OfflineError("unsafe_private_path")
        php = shutil.which(args.php)
        if not php or not BRIDGE.is_file():
            raise OfflineError("runtime_missing")
        import jsonschema

        try:
            document = projection.validate_manifest_document(_json(_read_private(Path(args.manifest), MAX_EXPORT, web)))
        except OfflineError:
            raise
        except (ValueError, UnicodeError, RecursionError) as error:
            raise OfflineError("invalid_export") from error
        try:
            request = _json(_read_private(Path(args.request), MAX_REQUEST, web))
            jsonschema.Draft202012Validator(json.loads(REQUEST_SCHEMA.read_text(encoding="utf-8"))).validate(request)
        except OfflineError:
            raise
        except (ValueError, UnicodeError, RecursionError, jsonschema.ValidationError) as error:
            raise OfflineError("invalid_request") from error
        corpus = Path(args.corpus_manifest) if args.corpus_manifest else None
        if corpus is not None:
            # Metadata only. The PHP scope enforces the frozen hash and dev identities.
            _read_private(corpus, 5_242_880, web)
        bridge = EvidenceBridge(actor=args.actor, web=web, wiki=wiki, php=php,
                                private_root=output.parent, watermark=document["source_watermark"],
                                corpus_manifest=corpus, synthetic=args.synthetic_only,
                                deadline=begun + args.timeout)
        import reviewed_query
        result = reviewed_query.answer_query(
            document, request["query"], bridge.graph_authorizer, bridge.text_authorizer,
            generator=None, wiki_base=args.wiki_base, bindings=request.get("bindings"),
            text_candidates=request.get("text_candidates", []), wiki_script_path=args.wiki_script_path)
        if bridge.failure:
            raise OfflineError(bridge.failure)
        if time.monotonic() >= begun + args.timeout:
            raise OfflineError("authorization_timeout")
        result["execution"] = {"mode": "reviewer-evidence-only", "generated": False,
                               "authorization_calls": bridge.calls,
                               "elapsed_seconds": round(time.monotonic() - begun, 3)}
        interpreted = result.get("query") or {}
        if any(item.get("code") == "invalid_request" for item in interpreted.get("reasons", [])):
            raise OfflineError("invalid_request")
        code = (0 if result.get("status") == "evidence" and interpreted.get("complete") is True
                and interpreted.get("status") in ("ok", "empty") else 3)
    except KeyboardInterrupt:
        result, code = _failure("interrupted"), 130
    except OfflineError as error:
        result, code = _failure(error.code), 2
    except ImportError:
        result, code = _failure("runtime_missing"), 2
    except Exception:
        result, code = _failure("runtime_failure"), 2
    written = False
    if output is not None and web is not None:
        try:
            raw = (projection.canonical_json(result) + "\n").encode("utf-8")
            if len(raw) > MAX_RESULT:
                result, code = _failure("result_output_limit"), 2
                raw = (projection.canonical_json(result) + "\n").encode("utf-8")
            _write_new(output, raw, MAX_RESULT, web)
            written = True
        except OfflineError as error:
            result, code = _failure(error.code), 2
        except KeyboardInterrupt:
            result, code = _failure("interrupted"), 130
        except Exception:
            result, code = _failure("private_write_failed"), 2
    reason_codes = [item["code"] for item in result.get("reasons", [])
                    if isinstance(item, dict) and isinstance(item.get("code"), str)]
    print(json.dumps({"status": result["status"], "reason_codes": reason_codes[:20],
                      "result_written": written, "authorization_calls": bridge.calls if bridge else 0},
                      sort_keys=True))
    if previous_term is not None:
        signal.signal(signal.SIGTERM, previous_term)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
