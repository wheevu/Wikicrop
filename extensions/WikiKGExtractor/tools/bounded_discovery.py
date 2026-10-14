"""Run the pinned offline rule worker on pages authorized by a pinned corpus manifest."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from bounded_process import ProcessResult, linux_memory_identity, require_linux_memory_budget, run_bounded

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / "bin" / "kg_worker.py"
PROTOCOL = ROOT / "protocol" / "rice-engineering-pilot.v2.json"
CLAIM_SCHEMA = ROOT / "schema" / "candidate-claims.v1.schema.json"
PHP_COLLECTOR = ROOT / "maintenance" / "discoverOfflineSources.php"
PHP_INVENTORY = ROOT / "src" / "OfflineSourceInventory.php"
WORKER_SHA256 = "ba87f32ddd3deb72e78b11551b15dfa07ea0715246cec4522eabaf842dfd31f0"
PROTOCOL_SHA256 = "ad5457a097d1c702f97368ff91e403efd135d15eb7a458fe915f3d00edf8eb7e"
MANIFEST_SHA256 = "e517e9d51f74439470a800a585b3accb9f15e8d5e2e5584bc03a8f8dbf586975"
MAX_PAGES, MAX_INVENTORY_ROWS = 100, 1000
MAX_BYTES, MAX_SECONDS, MAX_OUTPUT = 50 * 1024 * 1024, 900, 100 * 1024 * 1024
MAX_PAGE_BYTES, MAX_CLAIMS, MAX_EVIDENCE = 5 * 1024 * 1024, 500, 20
MAX_INVENTORY_BYTES, MAX_MANIFEST_BYTES, MAX_RECEIPT_BYTES = 20 * 1024 * 1024, 20 * 1024 * 1024, 40 * 1024 * 1024
UINT32_MAX = 0xFFFFFFFF
EXPECTED_ARTIFACTS = {
    "raw_data.json", "candidate_claims.json", "graph_data_raw.json", "graph_nodes_edges.json",
    "kg_summary.json", "mediawiki_bang_thuoc_tinh_cay_trong.txt", "neo4j_import.cypher",
}


def reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ValueError("symlink path component refused")
        except FileNotFoundError:
            continue


def bounded_read(path: Path, limit: int) -> bytes:
    reject_symlink_components(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("file is not regular or exceeds its read bound")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode) or current.st_size > limit:
            raise ValueError("file changed or exceeds its read bound")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("file exceeds its read bound")
        return raw
    finally:
        os.close(fd)


def no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number refused")


def strict_json(raw: bytes) -> Any:
    return json.loads(raw, object_pairs_hook=no_duplicate_pairs, parse_constant=reject_constant)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path, limit: int) -> str:
    return sha256_bytes(bounded_read(path, limit))


def normalize_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFC", value.replace("_", " "))
    return " ".join(normalized.split()).casefold()


def read_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = bounded_read(path, MAX_MANIFEST_BYTES)
    digest = sha256_bytes(raw)
    if digest != MANIFEST_SHA256:
        raise ValueError("corpus manifest does not match the frozen raw hash")
    manifest = strict_json(raw)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
        raise ValueError("corpus manifest is malformed")
    return manifest, digest


def derive_exclusion_policy(manifest: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    held_ids: set[int] = set()
    held_titles: set[str] = set()
    for row in manifest["pages"]:
        if not isinstance(row, dict) or row.get("split") != "test":
            continue
        page_id = row.get("page_id")
        if type(page_id) is int and page_id > 0:
            held_ids.add(page_id)
        for key in ("title", "requested_title", "resolved_title"):
            title = normalize_title(row.get(key))
            if title:
                held_titles.add(title)

    pages = inventory.get("pages")
    validate_page_metadata(pages)
    # Resolve only raw, supported redirect edges. Precomputed resolution fields
    # are intentionally not used: a redirect's raw edge is authoritative.
    by_title: dict[str, dict[str, Any]] = {}
    for row in pages:
        if row["namespace"] == 0:
            title = normalize_title(row.get("title"))
            if title:
                by_title[title] = row
    terminals: dict[str, tuple[str, int] | None] = {}
    for title in by_title:
        current = title
        visited: set[str] = set()
        terminal: tuple[str, int] | None = None
        while current not in visited:
            visited.add(current)
            row = by_title.get(current)
            if row is None or row["redirect_external"] or row["redirect_namespace"] != 0:
                break
            target_name = normalize_title(row.get("redirect_target"))
            if not target_name:
                terminal = (current, row["page_id"])
                break
            # A redirect edge must resolve to a page in this bounded metadata.
            if target_name not in by_title:
                break
            current = target_name
        terminals[title] = terminal

    held_terminals: set[tuple[str, int]] = set()
    for title, row in by_title.items():
        chain = row.get("redirect_chain", [])
        direct_held = title in held_titles or row["page_id"] in held_ids
        chain_held = isinstance(chain, list) and bool(set(chain) & held_ids)
        if direct_held or chain_held:
            held_titles.add(title)
            terminal = terminals[title]
            if terminal is not None:
                held_terminals.add(terminal)
    for title, terminal in terminals.items():
        if terminal is not None and terminal in held_terminals:
            held_titles.add(title)
            held_ids.add(by_title[title]["page_id"])
    return {"heldout_ids": held_ids, "heldout_titles": held_titles, "manifest_sha256": MANIFEST_SHA256}


def validate_exclusion_closure(manifest: dict[str, Any], inventory: dict[str, Any]) -> None:
    """Refuse a held-out metadata identity whose redirect chain cannot be closed."""
    by_title = {normalize_title(row["title"]): row for row in inventory.get("pages", [])
                if row.get("namespace") == 0}
    by_id = {row["page_id"]: row for row in inventory.get("pages", [])}
    for item in manifest.get("pages", []):
        if not isinstance(item, dict) or item.get("split") != "test":
            continue
        starts = []
        page_id = item.get("page_id")
        if type(page_id) is int and page_id in by_id:
            starts.append(by_id[page_id])
        for key in ("title", "requested_title", "resolved_title"):
            title = normalize_title(item.get(key))
            if title in by_title and by_title[title] not in starts:
                starts.append(by_title[title])
        for start in starts:
            current = start
            seen: set[int] = set()
            while True:
                page_id = current["page_id"]
                if page_id in seen:
                    raise ValueError("held-out redirect cycle makes exclusion closure ambiguous")
                seen.add(page_id)
                if current.get("redirect_external") or current.get("redirect_namespace", 0) != 0:
                    raise ValueError("held-out redirect metadata makes exclusion closure ambiguous")
                target = normalize_title(current.get("redirect_target"))
                if not target:
                    break
                next_page = by_title.get(target)
                if next_page is None:
                    raise ValueError("held-out redirect target is outside full metadata")
                current = next_page


def validate_page_metadata(pages: Any) -> None:
    if not isinstance(pages, list) or len(pages) > MAX_INVENTORY_ROWS:
        raise ValueError("inventory page metadata exceeds its bound")
    page_ids: set[int] = set()
    main_titles: dict[str, int] = {}
    for row in pages:
        if not isinstance(row, dict):
            raise ValueError("inventory page metadata row is malformed")
        page_id, revision_id, namespace = row.get("page_id"), row.get("revision_id"), row.get("namespace")
        title = row.get("title")
        if (type(page_id) is not int or not 1 <= page_id <= UINT32_MAX
                or type(revision_id) is not int or not 1 <= revision_id <= UINT32_MAX
                or type(namespace) is not int or not 0 <= namespace <= UINT32_MAX
                or not isinstance(title, str) or not title.strip()):
            raise ValueError("inventory page/revision/title/namespace identity is invalid")
        if page_id in page_ids:
            raise ValueError("inventory contains duplicate page IDs")
        page_ids.add(page_id)
        redirect_target = row.get("redirect_target")
        redirect_namespace = row.get("redirect_namespace", 0)
        redirect_external = row.get("redirect_external", False)
        if (redirect_target is not None and (not isinstance(redirect_target, str) or not redirect_target.strip())
                or type(redirect_namespace) is not int or not 0 <= redirect_namespace <= UINT32_MAX
                or type(redirect_external) is not bool):
            raise ValueError("inventory redirect metadata is malformed")
        if row.get("kind") not in (None, "species", "variety"):
            raise ValueError("inventory claims an unsupported page kind")
        crop = row.get("crop")
        if crop is not None and (not isinstance(crop, str) or not crop.strip()):
            raise ValueError("inventory crop metadata is malformed")
        reason = row.get("reason")
        if row.get("eligible") is True:
            if reason is not None:
                raise ValueError("eligible page has a non-null exclusion reason")
        elif not isinstance(reason, str) or not reason.strip():
            raise ValueError("ineligible page is missing an explicit reason")
        if namespace == 0:
            key = normalize_title(title)
            if not key or key in main_titles:
                raise ValueError("ambiguous normalized main-namespace title")
            main_titles[key] = page_id
        if row.get("eligible") is True:
            if namespace != 0 or redirect_external or redirect_namespace != 0 or redirect_target:
                raise ValueError("inventory claims an unsupported or unresolved redirect eligible")
            if row.get("kind") not in ("species", "variety") or not isinstance(row.get("crop"), str) or not row["crop"].strip():
                raise ValueError("eligible page has an invalid kind or crop classification")
            if not isinstance(row.get("content_hash"), str) or len(row["content_hash"]) != 64:
                raise ValueError("eligible page content hash is invalid")
            if (row.get("resolved_page_id") != page_id or normalize_title(row.get("resolved_title")) != normalize_title(title)
                    or row.get("resolved_revision_id") != revision_id):
                raise ValueError("eligible page lacks its authority-checked terminal source identity")
        elif redirect_external and row.get("reason") != "external_redirect":
            raise ValueError("external redirect lacks its explicit refusal reason")
        elif redirect_namespace != 0 and row.get("reason") != "unsupported_redirect_namespace":
            raise ValueError("unsupported redirect namespace lacks its explicit refusal reason")
        if row.get("resolved_title") is not None and (
            not isinstance(row["resolved_title"], str) or not row["resolved_title"].strip()
        ):
            raise ValueError("resolved title metadata is invalid")
        if row.get("resolved_page_id") is not None and (
            type(row["resolved_page_id"]) is not int or not 1 <= row["resolved_page_id"] <= UINT32_MAX
        ):
            raise ValueError("resolved page identity is invalid")
        chain = row.get("redirect_chain", [])
        if (not isinstance(chain, list) or any(type(value) is not int or not 1 <= value <= UINT32_MAX for value in chain)
                or len(chain) != len(set(chain))):
            raise ValueError("redirect chain contains invalid page identities")
        if row.get("eligible") is True:
            source_file = row.get("source_file")
            if not isinstance(source_file, str) or not source_file or "\0" in source_file:
                raise ValueError("eligible inventory row lacks a source file reference")
            if chain and chain[-1] != page_id:
                raise ValueError("eligible redirect chain does not end at its page identity")
    for row in pages:
        if any(item not in page_ids for item in row.get("redirect_chain", [])):
            raise ValueError("redirect chain refers to a page outside the bounded inventory")


def validate_inventory_metadata(inventory: dict[str, Any]) -> None:
    if not isinstance(inventory, dict) or inventory.get("schema_version") != "wikikg.offline-source-inventory.v1":
        raise ValueError("unsupported inventory document")
    source = inventory.get("source_identity")
    if (not isinstance(source, dict) or not isinstance(source.get("domain_id"), str) or not source["domain_id"].strip()
            or not isinstance(source.get("database_name"), str) or not source["database_name"].strip()
            or source.get("read_role") != "primary"):
        raise ValueError("inventory lacks an authoritative primary source database identity")
    validate_page_metadata(inventory.get("pages"))


def validate_eligibility(inventory: dict[str, Any], policy: dict[str, Any]) -> None:
    validate_inventory_metadata(inventory)
    for row in inventory.get("pages", []):
        if not isinstance(row, dict):
            raise ValueError("inventory row is malformed")
        ids = {row.get("page_id"), row.get("resolved_page_id")}
        names = {normalize_title(row.get(k)) for k in ("title", "requested_title", "resolved_title")}
        chain = row.get("redirect_chain", [])
        if isinstance(chain, list):
            ids.update(chain)
        excluded = bool(ids & policy["heldout_ids"] or (names - {""}) & policy["heldout_titles"])
        if row.get("eligible") is True and (excluded or row.get("heldout_excluded") is True):
            raise ValueError("inventory falsely marks a held-out page or alias eligible")


def load_jobs(inventory: dict[str, Any], inventory_dir: Path, max_pages: int,
              max_bytes: int, policy: dict[str, Any], metrics: dict[str, int] | None = None,
              deadline: float | None = None) -> list[dict[str, Any]]:
    validate_eligibility(inventory, policy)
    entries = inventory["pages"]
    jobs: list[dict[str, Any]] = []
    total_bytes = 0
    seen: set[tuple[int, int]] = set()
    run_dir = inventory_dir.resolve(strict=True)
    for entry in entries:
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("overall runtime limit reached during source validation")
        if entry.get("eligible") is not True:
            continue
        page_id, revision_id = entry.get("page_id"), entry.get("revision_id")
        title, kind, crop = entry.get("title"), entry.get("kind"), entry.get("crop")
        if type(page_id) is not int or page_id < 1 or type(revision_id) is not int or revision_id < 1:
            raise ValueError("eligible source identity is incomplete")
        if not all(isinstance(v, str) and v.strip() for v in (title, crop)) or kind not in ("species", "variety"):
            raise ValueError("eligible classification is incomplete or unsupported")
        digest = entry.get("content_hash")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("eligible content hash is invalid")
        identity = (page_id, revision_id)
        if identity in seen:
            raise ValueError("duplicate eligible source identity")
        seen.add(identity)
        # The path is never opened until manifest closure and eligibility checks pass.
        source_ref = entry.get("source_file")
        if not isinstance(source_ref, str) or not source_ref or "\0" in source_ref:
            raise ValueError("eligible source file reference is missing")
        source = Path(source_ref)
        reject_symlink_components(source if source.is_absolute() else run_dir / source)
        if source.is_absolute():
            # Compatibility for earlier inventories, but only when directly in
            # the same private run directory. No transfer depends on this form.
            if source.parent.resolve(strict=True) != run_dir:
                raise ValueError("absolute source file escapes the private inventory directory")
        elif len(source.parts) != 1 or source.name in (".", ".."):
            raise ValueError("relative source file must be a single run-local name")
        else:
            source = run_dir / source
        reject_symlink_components(source)
        source_info = source.stat()
        if (not stat.S_ISREG(source_info.st_mode) or source_info.st_uid != os.geteuid()
                or stat.S_IMODE(source_info.st_mode) != 0o600 or source_info.st_size > MAX_PAGE_BYTES):
            raise ValueError("eligible source must be owner-only regular file within 5 MiB")
        resolved = source.resolve(strict=True)
        try:
            resolved.relative_to(run_dir)
        except ValueError as error:
            raise ValueError("eligible source escapes the private inventory directory") from error
        total_bytes += source_info.st_size
        if total_bytes > max_bytes:
            raise ValueError("total source bytes exceed the configured bound")
        source_raw = bounded_read(resolved, MAX_PAGE_BYTES)
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("overall runtime limit reached during source validation")
        if metrics is not None:
            metrics["source_bytes"] = metrics.get("source_bytes", 0) + len(source_raw)
        payload = strict_json(source_raw)
        page = payload.get("page") if isinstance(payload, dict) else None
        if not isinstance(page, dict):
            raise ValueError("source page document is malformed")
        if (page.get("page_id"), page.get("revision_id"), page.get("title"), page.get("kind"), page.get("crop")) != (page_id, revision_id, title, kind, crop):
            raise ValueError("source identity or classification does not match inventory")
        text = page.get("wikitext")
        if not isinstance(text, str) or page.get("content_hash") != digest or sha256_bytes(text.encode("utf-8")) != digest:
            raise ValueError("source content hash does not match inventory")
        page["text"] = text
        page["source_pages"] = []
        jobs.append({"entry": entry, "page": page, "source_bytes": source_info.st_size})
    if len(jobs) > max_pages:
        raise ValueError("eligible page count exceeds the configured bound")
    return jobs


def private_directory(path: Path) -> Path:
    if os.name != "posix" or not hasattr(os, "geteuid"):
        raise ValueError("owner-only POSIX output permissions cannot be verified")
    reject_symlink_components(path)
    if not path.is_absolute() or ".." in path.parts or not path.is_dir():
        raise ValueError("output directory must already exist as an absolute path")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("output directory must be owned by this user with mode 0700")
    if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
        raise ValueError("output directory must be outside the repository")
    document_root = os.environ.get("DOCUMENT_ROOT")
    if not document_root:
        raise ValueError("known DOCUMENT_ROOT is required")
    reject_symlink_components(Path(document_root))
    web = Path(document_root).resolve(strict=True)
    if resolved == web or web in resolved.parents:
        raise ValueError("output directory must be outside DOCUMENT_ROOT")
    return resolved


def _outside_private_roots(path: Path) -> Path:
    reject_symlink_components(path)
    resolved = path.resolve(strict=True)
    if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
        raise ValueError("private data must be outside the repository")
    document_root = os.environ.get("DOCUMENT_ROOT")
    if not document_root:
        raise ValueError("known DOCUMENT_ROOT is required")
    web = Path(document_root).resolve(strict=True)
    if resolved == web or web in resolved.parents:
        raise ValueError("private data must be outside DOCUMENT_ROOT")
    return resolved


def _safe_existing_file(path: Path, limit: int) -> bytes:
    reject_symlink_components(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit):
        raise ValueError("existing private file has unsafe owner, mode, type, or size")
    return bounded_read(path, limit)


def _safe_existing_directory(path: Path) -> None:
    reject_symlink_components(path)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("existing private directory has unsafe owner, mode, or type")


@contextmanager
def _receipt_lock(output: Path):
    """Keep one receipt owner, including while a supervised child survives."""
    import fcntl

    output = private_directory(output)
    from bounded_process import require_inherited_lock_filesystem
    require_inherited_lock_filesystem(output)
    path = output / ".receipt.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0 or info.st_nlink != 1):
            raise ValueError("private receipt lock is unsafe")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise ValueError("run_in_progress") from None
            raise
        yield fd
    finally:
        # Do not unlink or explicitly unlock an inode still inherited by a child.
        os.close(fd)


def _write_atomic_receipt(path: Path, receipt: dict[str, Any], max_total_bytes: int | None = None) -> None:
    payload = json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds its fixed size bound")
    reject_symlink_components(path.parent)
    if path.exists() or path.is_symlink():
        _safe_existing_file(path, MAX_RECEIPT_BYTES)
    if max_total_bytes is not None:
        current = _output_bytes(path.parent)
        # The old receipt remains installed until replacement; count the temporary copy too.
        if current + len(payload) > max_total_bytes:
            raise ValueError("receipt would exceed configured total output byte limit")
    temp = path.parent / f".receipt-{os.getpid()}-{os.urandom(8).hex()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise ValueError("short receipt write")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    final_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(final_fd)
    finally:
        os.close(final_fd)
    parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("receipt owner/mode verification failed")


def _output_bytes(root: Path) -> int:
    total = 0
    _safe_existing_directory(root)

    def valid_name(parts: tuple[str, ...], name: str, is_dir: bool) -> bool:
        depth = len(parts)
        if depth == 0:
            return (is_dir and (name.startswith("prepare-attempt-") or name.startswith("batch-"))
                    or is_dir and len(name) == 20 and all(c in "0123456789abcdef" for c in name)
                    or not is_dir and (name in ("receipt.json", ".receipt.lock")
                        or re.fullmatch(r"\.receipt-[0-9]+-[a-f0-9]{16}\.tmp", name)))
        if depth == 1 and parts[0].startswith(("prepare-attempt-", "batch-")):
            return ((is_dir and name in ("php", "extraction"))
                    or (not is_dir and name in ("selected-pages.json", "expected-metadata.json")))
        if depth == 1 and len(parts[0]) == 20 and all(c in "0123456789abcdef" for c in parts[0]):
            return is_dir and name == "worker"
        if depth == 1 and parts[0] == "extraction":
            return is_dir and len(name) == 20 and all(c in "0123456789abcdef" for c in name)
        if depth == 2 and parts[-1] == "extraction":
            return is_dir and len(name) == 20 and all(c in "0123456789abcdef" for c in name)
        if depth == 2 and parts[-1] == "php":
            return is_dir and name.startswith("offline-")
        if ((depth == 2 and len(parts[-1]) == 20 and parts[-1] != "php")
                or (depth == 3 and parts[-2] == "extraction" and len(parts[-1]) == 20)):
            return is_dir and name == "worker"
        if depth == 3 and parts[-2] == "php" and parts[-1].startswith("offline-"):
            return (not is_dir and (name == "inventory.json" or
                    (name.startswith("page-") and name.endswith(".json")
                     and all(c.isdigit() or c == "-" for c in name[5:-5]))))
        if parts[-1] == "worker":
            return not is_dir and name in EXPECTED_ARTIFACTS
        return False

    for current, directories, files in os.walk(root, followlinks=False):
        parent = Path(current)
        parent_parts = parent.relative_to(root).parts
        for name in directories:
            child = parent / name
            reject_symlink_components(child)
            if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in name):
                raise ValueError("invalid private attempt directory name")
            if not valid_name(parent_parts, name, True):
                raise ValueError("unexpected private attempt directory")
            _safe_existing_directory(child)
        for name in files:
            child = parent / name
            reject_symlink_components(child)
            if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in name):
                raise ValueError("invalid private output file name")
            if not valid_name(parent_parts, name, False):
                raise ValueError("unexpected private output file")
            info = child.stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("private output file has unsafe owner, mode, or type")
            if name.startswith(".receipt-") or name == "receipt.json":
                if info.st_size > MAX_RECEIPT_BYTES:
                    raise ValueError("private receipt exceeds its fixed bound")
            if name == ".receipt.lock" and (info.st_size != 0 or info.st_nlink != 1):
                raise ValueError("private receipt lock is unsafe")
            total += info.st_size
    return total


def _private_inventory(path: Path) -> tuple[bytes, dict[str, Any]]:
    reject_symlink_components(path)
    _outside_private_roots(path.parent)
    parent = path.parent.stat()
    info = path.stat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700 or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > MAX_INVENTORY_BYTES):
        raise ValueError("inventory file and directory must be private, owner-only, and bounded")
    raw = bounded_read(path, MAX_INVENTORY_BYTES)
    doc = strict_json(raw)
    if not isinstance(doc, dict) or doc.get("schema_version") != "wikikg.offline-source-inventory.v1":
        raise ValueError("unsupported inventory document")
    return raw, doc


def _current_output_artifacts(worker_dir: Path, expected: set[str]) -> list[dict[str, Any]]:
    found: set[str] = set()
    for child in worker_dir.iterdir():
        reject_symlink_components(child)
        if child.is_symlink() or not child.is_file():
            raise ValueError("worker output contains a symlink or non-file artifact")
        info = child.stat()
        if info.st_uid != os.geteuid():
            raise ValueError("worker artifact has unexpected owner")
        os.chmod(child, 0o600)
        found.add(child.name)
    if found != expected or not found:
        raise ValueError("worker output artifact set is incomplete or unexpected")
    return [{"name": name, "sha256": sha256_file(worker_dir / name, MAX_OUTPUT),
             "bytes": (worker_dir / name).stat().st_size} for name in sorted(expected)]


def _secure_worker_dir(worker_dir: Path, expected: set[str], *, normalize_new_files: bool = False) -> None:
    reject_symlink_components(worker_dir)
    _safe_existing_directory(worker_dir)
    for child in worker_dir.iterdir():
        if child.is_symlink() or child.name not in expected or not child.is_file():
            raise ValueError("worker directory has a symlink, unexpected, or non-file artifact")
        info = child.stat()
        if info.st_uid != os.geteuid():
            raise ValueError("worker artifact has unexpected owner")
        if stat.S_IMODE(info.st_mode) != 0o600:
            if not normalize_new_files:
                raise ValueError("existing worker artifact has insecure permissions")
            os.chmod(child, 0o600)


def _verify_completed_job(output: Path, prior: dict[str, Any], expected: set[str],
                          raw_bytes: bytes, page: dict[str, Any], schema_path: Path,
                          max_output_bytes: int) -> None:
    job_key = prior.get("job_dir")
    if not isinstance(job_key, str) or len(job_key) != 20 or any(c not in "0123456789abcdef" for c in job_key):
        raise ValueError("completed receipt job path is invalid")
    if (not isinstance(prior.get("artifacts"), list) or len(prior["artifacts"]) != len(expected)
            or any(not isinstance(row, dict) for row in prior["artifacts"])):
        raise ValueError("completed receipt artifact set is incomplete")
    names = [row.get("name") for row in prior["artifacts"]]
    if len(set(names)) != len(expected) or set(names) != expected:
        raise ValueError("completed receipt artifact names are missing, duplicated, or unexpected")
    job_root = output / job_key
    worker_root = job_root / "worker"
    _safe_existing_directory(job_root)
    _safe_existing_directory(worker_root)
    if {child.name for child in job_root.iterdir()} != {"worker"}:
        raise ValueError("completed job directory contains unexpected entries")
    if {child.name for child in worker_root.iterdir()} != expected:
        raise ValueError("completed worker directory has a missing or extra artifact")
    actual: dict[str, bytes] = {}
    for artifact in prior["artifacts"]:
        name = artifact["name"]
        data = _safe_existing_file(worker_root / name, max_output_bytes)
        if len(data) > MAX_PAGE_BYTES and name in {"raw_data.json", "candidate_claims.json"}:
            raise ValueError("completed import payload exceeds 5 MiB")
        if len(data) != artifact.get("bytes") or sha256_bytes(data) != artifact.get("sha256"):
            raise ValueError("completed output changed; refusing resume")
        actual[name] = data
    if actual["raw_data.json"] != raw_bytes or sha256_bytes(raw_bytes) != prior.get("raw_sha256"):
        raise ValueError("completed raw artifact differs from the current verified page")
    if prior.get("output") != f"{job_key}/worker/candidate_claims.json":
        raise ValueError("completed receipt output path is invalid")
    claims = strict_json(actual["candidate_claims.json"])
    _validate_claims(claims, page, schema_path)
    if prior.get("output_sha256") != sha256_bytes(actual["candidate_claims.json"]):
        raise ValueError("completed receipt candidate hash does not match its artifact")
    if _output_bytes(output) > max_output_bytes:
        raise ValueError("resumed output exceeds configured total byte limit")


def _validate_claims(document: Any, page: dict[str, Any], schema_path: Path) -> None:
    try:
        import jsonschema
    except ImportError as error:
        raise ValueError("jsonschema is required to validate worker claims") from error
    schema = strict_json(bounded_read(schema_path, 1024 * 1024))
    jsonschema.Draft202012Validator.check_schema(schema)
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    if errors:
        raise ValueError("candidate claims fail the frozen Draft 2020-12 schema")
    metadata = document["metadata"]
    if len(metadata["source_pages"]) != 1:
        raise ValueError("candidate source metadata is not single-page")
    source = metadata["source_pages"][0]
    identity = (page["page_id"], page["revision_id"], page["title"], page["content_hash"])
    if (source.get("page_id"), source.get("revision_id"), source.get("title"), source.get("content_hash")) != identity:
        raise ValueError("candidate source metadata does not match raw input")
    claims = document["claims"]
    if len(claims) > MAX_CLAIMS:
        raise ValueError("candidate claim count exceeds 500")
    seen_claims: set[str] = set()
    metadata_method = metadata["extraction_method"]
    metadata_version = metadata["extractor_version"]
    for claim in claims:
        claim_id = claim.get("claim_id")
        if claim_id in seen_claims:
            raise ValueError("candidate claim identity is duplicated")
        seen_claims.add(claim_id)
        evidence_list = claim.get("evidence", [])
        if len(evidence_list) > MAX_EVIDENCE:
            raise ValueError("candidate evidence count exceeds 20")
        seen_evidence: set[str] = set()
        for evidence in evidence_list:
            if (evidence.get("page_id"), evidence.get("revision_id"), evidence.get("page_title"), evidence.get("content_hash")) != identity:
                raise ValueError("claim evidence does not identify its paired raw source")
            if evidence["extraction_method"] != metadata_method or evidence["extractor_version"] != metadata_version:
                raise ValueError("claim evidence extraction identity differs from candidate metadata")
            evidence_id = evidence["evidence_id"]
            if evidence_id in seen_evidence:
                raise ValueError("candidate evidence identity is duplicated")
            seen_evidence.add(evidence_id)
            location_type = evidence["location_type"]
            if location_type == "source_span":
                start, end, span = evidence["source_offset_start"], evidence["source_offset_end"], evidence["supporting_span"]
                if (not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start
                        or end > len(page["wikitext"]) or end - start != len(span)
                        or len(span) > 10000 or page["wikitext"][start:end] != span
                        or evidence["span_hash"] != sha256_bytes(span.encode("utf-8"))):
                    raise ValueError("claim evidence span does not match paired raw wikitext")
            elif location_type == "page_title" and evidence["location"] != page["title"]:
                raise ValueError("claim title evidence does not match paired raw title")
            elif location_type == "page" and any(k in evidence for k in
                    ("location", "source_offset_start", "source_offset_end", "supporting_span", "span_hash")):
                raise ValueError("page-only evidence contains quote fields")


def run(inventory_path: Path, output: Path, actor: str, max_pages: int = MAX_PAGES,
        max_bytes: int = MAX_BYTES, timeout: int = MAX_SECONDS, max_output_bytes: int = MAX_OUTPUT,
        manifest_path: Path | None = None, *, exclusion_policy: dict[str, Any] | None = None,
        memory_bytes: int = 1024 * 1024 * 1024, accounting_root: Path | None = None,
        run_state: tuple[dict[str, Any], dict[str, Any]] | None = None,
        execution_deadline: float | None = None, lock_fd: int | None = None) -> dict[str, Any]:
    if (not 1 <= max_pages <= MAX_PAGES or not 1 <= max_bytes <= MAX_BYTES
            or not 1 <= timeout <= MAX_SECONDS or not 1 <= max_output_bytes <= MAX_OUTPUT
            or not 64 * 1024 * 1024 <= memory_bytes <= 8 * 1024 * 1024 * 1024):
        raise ValueError("requested limits exceed the frozen discovery bounds")
    memory_control = require_linux_memory_budget(memory_bytes)
    limits = {"max_pages": max_pages, "max_bytes": max_bytes, "timeout_seconds": timeout,
              "max_output_bytes": max_output_bytes, "memory_bytes": memory_bytes,
              "memory_control": memory_control,
              "max_page_bytes": MAX_PAGE_BYTES, "max_inventory_rows": MAX_INVENTORY_ROWS,
              "max_collection_pages": 100}
    start = time.monotonic()
    deadline = min(start + timeout, execution_deadline) if execution_deadline is not None else start + timeout
    output = private_directory(output)
    budget_root = private_directory(accounting_root) if accounting_root is not None else output
    if not actor.strip():
        raise ValueError("named reviewer actor is required")
    if sha256_file(WORKER, 10 * 1024 * 1024) != WORKER_SHA256 or sha256_file(PROTOCOL, 1024 * 1024) != PROTOCOL_SHA256:
        raise ValueError("frozen worker or protocol hash mismatch")
    protocol = strict_json(bounded_read(PROTOCOL, 1024 * 1024))
    if protocol.get("artifacts", {}).get("manifest_sha256") != MANIFEST_SHA256:
        raise ValueError("frozen protocol manifest pin mismatch")
    if manifest_path is not None:
        _outside_private_roots(manifest_path)
        _safe_existing_directory(manifest_path.parent)
        manifest_info = manifest_path.stat()
        if (not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_uid != os.geteuid()
                or stat.S_IMODE(manifest_info.st_mode) != 0o600):
            raise ValueError("corpus manifest must be an owner-only regular file")
        manifest, manifest_hash = read_manifest(manifest_path)
        inventory_raw, inventory = _private_inventory(inventory_path)
        validate_exclusion_closure(manifest, inventory)
        policy = derive_exclusion_policy(manifest, inventory)
    elif exclusion_policy is not None:
        # Test seam only: CLI deliberately has no switch for injected policy.
        policy = exclusion_policy
        manifest_hash = MANIFEST_SHA256
        inventory_raw, inventory = _private_inventory(inventory_path)
    else:
        raise ValueError("--corpus-manifest is required")
    if inventory.get("actor") != actor:
        raise ValueError("inventory reviewer actor differs from requested actor")
    if inventory.get("protocol_sha256") != PROTOCOL_SHA256 or inventory.get("heldout_manifest_sha256") != manifest_hash:
        raise ValueError("inventory pinned code or manifest identity mismatch")
    schema_hash = protocol["artifacts"]["schema_sha256"]["candidate-claims.v1.schema.json"]
    if sha256_file(CLAIM_SCHEMA, 1024 * 1024) != schema_hash:
        raise ValueError("candidate claim schema hash differs from protocol")
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        raise ValueError("jsonschema is required before source access") from None
    validate_eligibility(inventory, policy)
    config = {"actor": actor, "inventory_sha256": sha256_bytes(inventory_raw), "manifest_sha256": manifest_hash,
              "worker_sha256": WORKER_SHA256, "protocol_sha256": PROTOCOL_SHA256,
              "runner_sha256": sha256_file(Path(__file__).resolve(), 1024 * 1024),
              "process_supervisor_sha256": sha256_file(Path(__file__).with_name("bounded_process.py"), 1024 * 1024),
              "schema_sha256": schema_hash,
              "source_identity": inventory["source_identity"],
              "limits": limits}
    receipt_path = budget_root / "receipt.json"
    receipt: dict[str, Any] = {"schema_version": "wikikg.bounded-discovery-receipt.v1", "config": config, "jobs": []}
    if run_state is not None:
        parent, slot = run_state
        prior = slot.get("extraction_receipt")
        if prior is not None:
            if (not isinstance(prior, dict) or prior.get("schema_version") != "wikikg.bounded-discovery-receipt.v1"
                    or prior.get("config") != config or not isinstance(prior.get("jobs"), list)):
                raise ValueError("batch extraction receipt configuration changed")
            receipt = prior
    elif receipt_path.exists() or receipt_path.is_symlink():
        prior = strict_json(_safe_existing_file(receipt_path, MAX_RECEIPT_BYTES))
        if (not isinstance(prior, dict) or prior.get("schema_version") != "wikikg.bounded-discovery-receipt.v1"
                or prior.get("config") != config or not isinstance(prior.get("jobs"), list)):
            raise ValueError("resume configuration or source/pin/limit set changed")
        if any(row.get("status") in ("pending", "failed", "interrupted") for row in prior["jobs"]):
            raise ValueError("incomplete v1 attempts are immutable; use a new private run directory")
        receipt = prior

    def persist() -> None:
        if run_state is None:
            _write_atomic_receipt(receipt_path, receipt, max_output_bytes)
        else:
            parent, slot = run_state
            slot["extraction_receipt"] = receipt
            _write_atomic_receipt(receipt_path, parent, max_output_bytes)
    allowed_statuses = {"pending", "completed", "skipped", "failed", "interrupted"}
    for row in receipt["jobs"]:
        if (not isinstance(row, dict) or type(row.get("page_id")) is not int
                or type(row.get("revision_id")) is not int or row.get("status") not in allowed_statuses):
            raise ValueError("resume receipt contains malformed job state")
    row_by_id = {(row.get("page_id"), row.get("revision_id")): row for row in receipt["jobs"]}
    if len(row_by_id) != len(receipt["jobs"]):
        raise ValueError("resume receipt contains duplicate job identities")
    metadata_ids = {(row.get("page_id"), row.get("revision_id")) for row in inventory["pages"]}
    if not set(row_by_id).issubset(metadata_ids):
        raise ValueError("resume receipt contains an identity outside this inventory")
    entry_by_id = {(row["page_id"], row["revision_id"]): row for row in inventory["pages"]}
    allowed_job_dirs = set()
    for identity, entry in entry_by_id.items():
        if entry.get("eligible") is True:
            key = hashlib.sha256(f"{identity[0]}:{identity[1]}:{entry['crop']}".encode()).hexdigest()[:20]
            allowed_job_dirs.add(key)
            prior_row = row_by_id.get(identity)
            if prior_row:
                if prior_row.get("status") == "skipped":
                    raise ValueError("resume receipt skips an eligible inventory page")
                if prior_row.get("source_sha256") not in (None, entry["content_hash"]):
                    raise ValueError("resume receipt source identity/hash changed")
                if prior_row.get("job_dir") not in (None, key):
                    raise ValueError("resume receipt job directory differs from current page identity")
                if prior_row.get("status") == "completed" and (
                    prior_row.get("raw_sha256") is None or prior_row.get("output_sha256") is None
                ):
                    raise ValueError("completed resume receipt lacks paired artifact hashes")
        elif row_by_id.get(identity) and row_by_id[identity].get("status") != "skipped":
            raise ValueError("resume receipt ran an ineligible inventory page")
    if any(child.name not in ("receipt.json", ".receipt.lock") and child.name not in allowed_job_dirs
           and not re.fullmatch(r"\.receipt-[0-9]+-[a-f0-9]{16}\.tmp", child.name)
           for child in output.iterdir()):
        raise ValueError("private output contains a job directory outside the current inventory")
    for entry in inventory["pages"]:
        identity = (entry.get("page_id"), entry.get("revision_id"))
        existing = row_by_id.get(identity)
        if existing and entry.get("eligible") is True and existing.get("status") == "skipped":
            raise ValueError("resume receipt skips an eligible inventory page")
        if existing and entry.get("eligible") is not True and existing.get("status") != "skipped":
            raise ValueError("resume receipt ran an ineligible inventory page")
        if identity not in row_by_id:
            if entry.get("eligible") is True:
                initial = {"page_id": identity[0], "revision_id": identity[1], "title": entry.get("title", ""),
                           "crop": entry.get("crop", ""), "source_sha256": entry.get("content_hash", ""),
                           "status": "pending"}
            elif run_state is not None:
                continue
            else:
                initial = {"page_id": identity[0], "revision_id": identity[1], "title": entry.get("title", ""),
                           "status": "skipped", "reason": entry.get("reason", "ineligible")}
            receipt["jobs"].append(initial)
            row_by_id[identity] = initial
    persist()

    source_metrics = {"source_bytes": 0}
    source_check_started = time.monotonic()
    try:
        jobs = load_jobs(inventory, inventory_path.parent, max_pages, max_bytes, policy,
                         source_metrics, deadline)
        receipt["source_validation"] = {"status": "completed", **source_metrics,
                                        "elapsed_seconds": time.monotonic() - source_check_started}
    except KeyboardInterrupt:
        elapsed = time.monotonic() - source_check_started
        receipt["source_validation"] = {"status": "interrupted", **source_metrics, "elapsed_seconds": elapsed}
        for row in receipt["jobs"]:
            if row.get("status") == "pending":
                row.update(status="interrupted",
                            runtime_seconds=elapsed, output_bytes=_output_bytes(budget_root))
        persist()
        raise
    except Exception:
        elapsed = time.monotonic() - source_check_started
        receipt["source_validation"] = {"status": "failed", **source_metrics, "elapsed_seconds": elapsed}
        eligible_ids = {(entry.get("page_id"), entry.get("revision_id"))
                        for entry in inventory["pages"] if entry.get("eligible") is True}
        for row in receipt["jobs"]:
            if ((row.get("page_id"), row.get("revision_id")) in eligible_ids
                    and row.get("status") == "pending"):
                row.update(status="failed", reason="source validation failed",
                           runtime_seconds=elapsed, output_bytes=_output_bytes(budget_root))
        persist()
        raise ValueError("source metadata or input validation failed")
    expected = set(protocol["evaluation"]["deterministic_outputs"]) | {"raw_data.json"}
    for job in jobs:
        entry, page = job["entry"], job["page"]
        identity = (entry["page_id"], entry["revision_id"])
        raw_doc = {"schema_version": "1.0", "page_count": 1, "error_count": 0,
                   "pages": [{k: page[k] for k in ("title", "text", "wikitext", "kind", "crop", "page_id", "revision_id", "content_hash")}],
                   "errors": []}
        raw_bytes = json.dumps(raw_doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw_bytes) > MAX_PAGE_BYTES:
            raise ValueError("raw_data.json exceeds the 5 MiB import bound")
        prior = row_by_id.get(identity)
        if prior and prior.get("status") == "completed":
            if prior.get("source_sha256") != entry["content_hash"]:
                raise ValueError("completed receipt source identity/hash changed")
            _verify_completed_job(output, prior, expected, raw_bytes, page, CLAIM_SCHEMA, max_output_bytes)
            continue
        if time.monotonic() >= deadline:
            raise ValueError("overall runtime limit reached before next job")
        key = hashlib.sha256(f"{identity[0]}:{identity[1]}:{page['crop']}".encode()).hexdigest()[:20]
        job_dir = output / key
        worker_dir = job_dir / "worker"
        reject_symlink_components(job_dir)
        if job_dir.exists():
            info = job_dir.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("existing job directory is unsafe")
        else:
            job_dir.mkdir(mode=0o700)
            os.chmod(job_dir, 0o700)
        _safe_existing_directory(job_dir)
        reject_symlink_components(worker_dir)
        if worker_dir.exists():
            info = worker_dir.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("existing worker directory is unsafe")
        else:
            worker_dir.mkdir(mode=0o700)
            os.chmod(worker_dir, 0o700)
        _safe_existing_directory(worker_dir)
        if {item.name for item in job_dir.iterdir()} != {"worker"}:
            raise ValueError("job directory contains unexpected entries")
        _secure_worker_dir(worker_dir, expected)
        current_bytes = _output_bytes(budget_root)
        if current_bytes + MAX_PAGE_BYTES + MAX_RECEIPT_BYTES > max_output_bytes:
            prior.update(status="failed", reason="insufficient reserved output budget", runtime_seconds=0.0,
                         output_bytes=current_bytes)
            persist()
            raise ValueError("insufficient reserved output budget before worker start")
        raw_path = worker_dir / "raw_data.json"
        if raw_path.exists() or raw_path.is_symlink():
            if _safe_existing_file(raw_path, MAX_PAGE_BYTES) != raw_bytes:
                raise ValueError("existing pending raw input differs from verified source")
        else:
            fd = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                offset = 0
                while offset < len(raw_bytes):
                    written = os.write(fd, raw_bytes[offset:])
                    if written <= 0:
                        raise ValueError("short private raw input write")
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)
        pending = {"page_id": identity[0], "revision_id": identity[1], "title": page["title"], "crop": page["crop"],
                   "source_sha256": entry["content_hash"], "source_bytes": job["source_bytes"], "raw_sha256": sha256_bytes(raw_bytes),
                   "job_dir": key, "status": "pending"}
        receipt["jobs"] = [r for r in receipt["jobs"] if (r.get("page_id"), r.get("revision_id")) != identity] + [pending]
        row_by_id[identity] = pending
        persist()
        remaining_budget = max_output_bytes - _output_bytes(budget_root) - MAX_RECEIPT_BYTES
        per_file_cap = max(1, remaining_budget // len(expected))

        begun = time.monotonic()
        status = "failed"
        reason = "worker failed"
        hashes: list[dict[str, Any]] = []
        try:
            _secure_worker_dir(worker_dir, expected)
            # Per-file write cap remains inherited in addition to the address-space limit.
            result = run_bounded(
                [sys.executable, "-B", str(WORKER), "--no-ai", "--input", str(raw_path), "--output-dir", str(worker_dir)],
                timeout=deadline - time.monotonic(),
                max_output_bytes=max(1, min(1024 * 1024, max_output_bytes - _output_bytes(budget_root))),
                memory_bytes=memory_bytes, file_bytes=per_file_cap,
                owned_output_dir=budget_root, total_output_limit=max_output_bytes,
                lock_fd=lock_fd,
                env={**os.environ, "GEMINI_API_KEY": ""})
            _secure_worker_dir(worker_dir, expected, normalize_new_files=True)
            if result.reason:
                reason = result.reason
            elif time.monotonic() >= deadline:
                reason = "overall runtime limit exceeded"
            elif result.returncode == 0:
                hashes = _current_output_artifacts(worker_dir, expected)
                if _output_bytes(budget_root) > max_output_bytes:
                    reason = "overall output byte bound exceeded"
                else:
                    raw_check = _safe_existing_file(raw_path, MAX_PAGE_BYTES)
                    if sha256_bytes(raw_check) != pending["raw_sha256"]:
                        raise ValueError("worker changed raw_data.json")
                    claims_path = worker_dir / "candidate_claims.json"
                    claims_raw = bounded_read(claims_path, MAX_PAGE_BYTES)
                    claims_doc = strict_json(claims_raw)
                    _validate_claims(claims_doc, page, CLAIM_SCHEMA)
                    pending["claim_count"] = len(claims_doc["claims"])
                    status, reason = "completed", ""
        except (ValueError, OSError):
            status, reason = "failed", "enforcement unavailable"
        except KeyboardInterrupt:
            pending["status"] = "interrupted"
            pending["termination_reason"] = "cancelled"
            pending["runtime_seconds"] = time.monotonic() - begun
            persist()
            raise
        except Exception:
            status, reason = "failed", "worker output validation failed"
        pending["status"] = status
        pending["runtime_seconds"] = time.monotonic() - begun
        try:
            pending["output_bytes"] = _output_bytes(budget_root)
        except Exception:
            pending["output_bytes"] = None
        if status == "completed":
            pending["artifacts"] = hashes
            pending["output"] = f"{key}/worker/candidate_claims.json"
            pending["output_sha256"] = next(a["sha256"] for a in hashes if a["name"] == "candidate_claims.json")
        else:
            pending["reason"] = reason
        persist()
        if status != "completed":
            raise ValueError(reason)
    return receipt


def _private_input_path(path: Path, limit: int) -> Path:
    if not path.is_absolute():
        raise ValueError("private input paths must be absolute")
    reject_symlink_components(path)
    _outside_private_roots(path)
    parent = path.parent.stat()
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit
            or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("private input must be owner-only and bounded")
    return path.resolve(strict=True)


def _make_private_dir(path: Path) -> Path:
    reject_symlink_components(path)
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    _safe_existing_directory(path)
    return path.resolve(strict=True)


def _write_private(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise ValueError("short private file write")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _receipt_payload(receipt: dict[str, Any]) -> bytes:
    payload = json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds its fixed size bound")
    return payload


def _reserve_setup(output: Path, setup_bytes: int, receipt: dict[str, Any], max_output_bytes: int) -> None:
    """Prove setup plus receipt replacement and collector headroom fit before creating anything."""
    current = _output_bytes(output)
    payload_size = len(_receipt_payload(receipt))
    # Existing receipt remains while the atomic replacement is written. Keep one
    # maximum-sized final receipt slot for collection/extraction completion.
    if current + setup_bytes + payload_size + MAX_RECEIPT_BYTES > max_output_bytes:
        raise ValueError("setup exceeds reserved output budget")


def _run_collector(runtime_root: Path, output_parent: Path, actor: str, document_root: Path,
                   manifest_path: Path, classifications_path: Path, *, metadata_only: bool,
                   selected_path: Path | None, expected_metadata_path: Path | None = None,
                   timeout: int, memory_bytes: int,
                   output_limit: int, lock_fd: int | None = None) -> tuple[dict[str, Any], ProcessResult]:
    if (not 1 <= timeout <= MAX_SECONDS or not 64 * 1024 * 1024 <= memory_bytes <= 8 * 1024 * 1024 * 1024
            or not 1 <= output_limit <= MAX_OUTPUT):
        raise ValueError("collector limits exceed the bounded execution contract")
    require_linux_memory_budget(memory_bytes)
    reject_symlink_components(runtime_root)
    runtime_original = runtime_root
    runtime_root = runtime_root.resolve(strict=True)
    if runtime_original != runtime_root or not runtime_root.is_dir():
        raise ValueError("MediaWiki runtime root is unavailable")
    reject_symlink_components(document_root)
    if document_root.resolve(strict=True) != document_root:
        raise ValueError("document root must be a canonical non-symlink path")
    _safe_existing_directory(output_parent)
    php = shutil.which("php")
    if not php:
        raise ValueError("PHP runtime is unavailable")
    command = [php, str(ROOT / "maintenance" / "discoverOfflineSources.php"),
               "--actor=" + actor, "--offline-only", "--audience=actor-only",
               "--output-dir=" + str(output_parent), "--document-root=" + str(document_root),
               "--manifest=" + str(manifest_path), "--classifications=" + str(classifications_path)]
    if metadata_only:
        command.append("--metadata-only")
    if selected_path is not None:
        command.append("--selected-pages=" + str(selected_path))
    if expected_metadata_path is not None:
        command.append("--expected-metadata=" + str(expected_metadata_path))
    output_files = 1 if metadata_only else 101
    if selected_path is not None:
        selected_ids = strict_json(bounded_read(selected_path, 64 * 1024))
        if not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 100:
            raise ValueError("selected terminal count exceeds the collector bound")
        output_files = len(selected_ids) + 1
    if output_limit < output_files:
        raise ValueError("insufficient reserved output budget before collector start")
    result = run_bounded(command, timeout=timeout, max_output_bytes=64 * 1024,
                         memory_bytes=memory_bytes, file_bytes=min(MAX_INVENTORY_BYTES, output_limit // output_files),
                         owned_output_dir=output_parent,
                         total_output_limit=output_limit,
                         lock_fd=lock_fd,
                         env={**os.environ, "MW_INSTALL_PATH": str(runtime_root),
                              "DOCUMENT_ROOT": str(document_root), "GEMINI_API_KEY": ""})
    if result.reason or result.returncode != 0:
        raise ValueError("supervised source collection failed: " + (result.reason or "collector_failure"))
    try:
        response = strict_json(result.stdout.strip())
    except (ValueError, UnicodeDecodeError):
        raise ValueError("collector returned a malformed bounded summary") from None
    if not isinstance(response, dict) or response.get("status") != "completed" or not isinstance(response.get("inventory_path"), str):
        raise ValueError("collector did not return a completed inventory")
    return response, result


def _inventory_without_content(inventory: dict[str, Any]) -> None:
    validate_inventory_metadata(inventory)
    for row in inventory["pages"]:
        if any(key in row for key in ("wikitext", "source_file", "content_hash")):
            raise ValueError("metadata-only inventory contains content fields")


def _plan_inventory(inventory: dict[str, Any], manifest: dict[str, Any], manifest_hash: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _inventory_without_content(inventory)
    if inventory.get("heldout_manifest_sha256") != manifest_hash:
        raise ValueError("metadata inventory manifest pin differs from the supplied manifest")
    validate_exclusion_closure(manifest, inventory)
    policy = derive_exclusion_policy(manifest, inventory)
    validate_eligibility(inventory, policy)
    candidates: list[dict[str, Any]] = []
    for row in inventory["pages"]:
        if row.get("reason") != "awaiting_collection":
            continue
        if (row.get("resolved_page_id") != row["page_id"]
                or row.get("resolved_revision_id") != row["revision_id"]
                or row.get("redirect_target") or row.get("redirect_external")
                or row.get("redirect_namespace", 0) != 0
                or row.get("kind") not in ("species", "variety")
                or not isinstance(row.get("crop"), str) or not row["crop"].strip()):
            raise ValueError("metadata candidate is not an unambiguous terminal identity")
        ids = {row["page_id"], row.get("resolved_page_id")}
        titles = {normalize_title(row.get(key)) for key in ("title", "resolved_title")}
        if ids & policy["heldout_ids"] or (titles - {""}) & policy["heldout_titles"]:
            raise ValueError("metadata candidate intersects exclusion closure")
        candidates.append({key: row[key] for key in ("page_id", "revision_id", "title", "kind", "crop")})
    candidates.sort(key=lambda row: (normalize_title(row["crop"]), row["page_id"], row["revision_id"]))
    return candidates, policy


def _create_plan(output: Path, actor: str, manifest_path: Path,
                 classifications_path: Path, runtime_root: Path, document_root: Path,
                 *, timeout: int, memory_bytes: int, max_output_bytes: int,
                 retry: bool = False, lock_fd: int | None = None) -> dict[str, Any]:
    output = private_directory(output)
    if not actor.strip():
        raise ValueError("named reviewer actor is required")
    if (not 1 <= timeout <= MAX_SECONDS or not 1 <= max_output_bytes <= MAX_OUTPUT
            or not 64 * 1024 * 1024 <= memory_bytes <= 8 * 1024 * 1024 * 1024):
        raise ValueError("requested limits exceed the frozen discovery bounds")
    memory_control = require_linux_memory_budget(memory_bytes)
    protocol = strict_json(bounded_read(PROTOCOL, 1024 * 1024))
    schema_hash = protocol.get("artifacts", {}).get("schema_sha256", {}).get("candidate-claims.v1.schema.json")
    if (sha256_file(WORKER, 10 * 1024 * 1024) != WORKER_SHA256
            or sha256_file(PROTOCOL, 1024 * 1024) != PROTOCOL_SHA256
            or protocol.get("artifacts", {}).get("manifest_sha256") != MANIFEST_SHA256
            or sha256_file(CLAIM_SCHEMA, 1024 * 1024) != schema_hash):
        raise ValueError("frozen worker, protocol, or schema hash mismatch")
    php_collector_hash = sha256_file(PHP_COLLECTOR, 1024 * 1024)
    php_inventory_hash = sha256_file(PHP_INVENTORY, 1024 * 1024)
    process_supervisor_hash = sha256_file(Path(__file__).with_name("bounded_process.py"), 1024 * 1024)
    receipt_path = output / "receipt.json"
    manifest_path = _private_input_path(manifest_path, MAX_MANIFEST_BYTES)
    classifications_path = _private_input_path(classifications_path, 2 * 1024 * 1024)
    classification_doc = strict_json(bounded_read(classifications_path, 2 * 1024 * 1024))
    if not isinstance(classification_doc, dict):
        raise ValueError("classification map must be a strict JSON object")
    manifest, manifest_hash = read_manifest(manifest_path)
    reject_symlink_components(runtime_root)
    reject_symlink_components(document_root)
    original_runtime, original_web = runtime_root, document_root
    runtime_root = runtime_root.resolve(strict=True)
    document_root = document_root.resolve(strict=True)
    if runtime_root != original_runtime or document_root != original_web:
        raise ValueError("runtime and document roots must be canonical non-symlink paths")
    runtime_web = os.environ.get("DOCUMENT_ROOT")
    if not runtime_web or Path(runtime_web).resolve(strict=True) != document_root:
        raise ValueError("DOCUMENT_ROOT differs from the configured wiki document root")
    base_config = {"actor": actor, "manifest_sha256": manifest_hash,
              "classifications_sha256": sha256_file(classifications_path, 2 * 1024 * 1024),
              "worker_sha256": WORKER_SHA256, "protocol_sha256": PROTOCOL_SHA256,
              "runner_sha256": sha256_file(Path(__file__).resolve(), 1024 * 1024),
              "process_supervisor_sha256": process_supervisor_hash,
              "php_collector_sha256": php_collector_hash,
              "php_inventory_sha256": php_inventory_hash,
              "schema_sha256": schema_hash,
              "runtime_root": str(runtime_root), "document_root": str(document_root),
              "memory_control": memory_control,
              "limits": {"max_pages": MAX_PAGES, "max_bytes": MAX_BYTES, "timeout_seconds": timeout,
                         "memory_bytes": memory_bytes, "max_output_bytes": max_output_bytes,
                         "max_page_bytes": MAX_PAGE_BYTES, "max_inventory_rows": MAX_INVENTORY_ROWS,
                         "max_collection_pages": 100, "batch_size": 10}}
    prior_receipt = None
    if receipt_path.exists() or receipt_path.is_symlink():
        prior_receipt = strict_json(_safe_existing_file(receipt_path, MAX_RECEIPT_BYTES))
        if not isinstance(prior_receipt, dict) or prior_receipt.get("schema_version") != "wikikg.bounded-discovery-receipt.v2":
            raise ValueError("v1 receipt migration is not implicit; create a new private run directory")
        if prior_receipt.get("prepare", {}).get("status") == "completed":
            if prior_receipt.get("config", {}).get("prepare_config") != base_config:
                raise ValueError("completed preparation identity differs; do not replace its plan")
            if retry:
                raise ValueError("completed preparation is immutable and does not accept --retry")
            return _load_v2(output)
        if not retry:
            raise ValueError("a new metadata attempt requires explicit --retry")
        if prior_receipt.get("config", {}).get("prepare_config") != base_config:
            raise ValueError("retry preparation identity differs from the failed attempt")
        receipt = prior_receipt
    else:
        if retry:
            raise ValueError("--retry requires a recorded preparation attempt")
        if any(child.name != ".receipt.lock" for child in output.iterdir()):
            raise ValueError("a new plan requires an empty private run directory")
        receipt = {"schema_version": "wikikg.bounded-discovery-receipt.v2", "config": {},
                   "inventory": None, "batches": [], "prepare": {"status": "pending", "attempts": []},
                   "created_at": time.time()}
        receipt["config"]["prepare_config"] = base_config
    if receipt.get("prepare", {}).get("status") == "running":
        for row in receipt["prepare"].get("attempts", []):
            if row.get("status") == "running":
                row.update(status="interrupted", termination_reason="process_interrupted_before_completion")
        receipt["prepare"]["status"] = "interrupted"
        _write_atomic_receipt(receipt_path, receipt, max_output_bytes)
    attempt_no = len(receipt["prepare"].get("attempts", [])) + 1
    attempt_name = f"prepare-attempt-{attempt_no:04d}-{os.urandom(4).hex()}"
    attempt = {"attempt": attempt_no, "attempt_dir": attempt_name, "status": "running",
               "started_at": time.time()}
    receipt["prepare"].setdefault("attempts", []).append(attempt)
    receipt["prepare"].update(status="running", active_attempt=attempt_no)
    _reserve_setup(output, 0, receipt, max_output_bytes)
    _write_atomic_receipt(receipt_path, receipt, max_output_bytes)
    started = time.monotonic()
    try:
        attempt_path = _make_private_dir(output / attempt_name)
        php_output = _make_private_dir(attempt_path / "php")
        response, process = _run_collector(runtime_root, php_output, actor, document_root, manifest_path,
                                           classifications_path, metadata_only=True, selected_path=None,
                                           timeout=timeout, memory_bytes=memory_bytes,
                                           output_limit=max_output_bytes - _output_bytes(output) - MAX_RECEIPT_BYTES,
                                           lock_fd=lock_fd)
        if linux_memory_identity() != memory_control:
            raise ValueError("cgroup memory control changed during preparation")
        inventory_path = Path(response["inventory_path"])
        _private_input_path(inventory_path, MAX_INVENTORY_BYTES)
        inventory_raw, inventory = _private_inventory(inventory_path)
        if (inventory.get("actor") != actor or inventory.get("protocol_sha256") != PROTOCOL_SHA256
                or inventory.get("heldout_manifest_sha256") != manifest_hash):
            raise ValueError("metadata inventory actor or frozen pin identity mismatch")
        candidates, _ = _plan_inventory(inventory, manifest, manifest_hash)
        canonical_inventory_hash = sha256_bytes(json.dumps(inventory, ensure_ascii=False, sort_keys=True,
                                                           separators=(",", ":")).encode())
        config = {**base_config, "prepare_config": base_config,
                  "source_identity": inventory["source_identity"],
                  "inventory_sha256": canonical_inventory_hash,
                  "metadata_snapshot_sha256": sha256_bytes(inventory_raw)}
        batches = []
        crops = sorted({row["crop"] for row in candidates}, key=normalize_title)
        for crop in crops:
            crop_rows = [row for row in candidates if row["crop"] == crop]
            for offset in range(0, len(crop_rows), 10):
                rows = crop_rows[offset:offset + 10]
                batches.append({"index": len(batches), "crop": crop,
                                "pages": rows, "status": "pending", "attempts": []})
        receipt.update(config=config, inventory=inventory, batches=batches,
                       prepare={"status": "completed", "attempts": receipt["prepare"]["attempts"],
                                "inventory_path": str(inventory_path.relative_to(output)),
                                "metadata_rows": len(inventory["pages"]),
                                "candidate_count": len(candidates), "elapsed_seconds": process.elapsed_seconds})
        attempt.update(status="completed", elapsed_seconds=time.monotonic() - started)
    except KeyboardInterrupt:
        attempt.update(status="interrupted", termination_reason="cancelled", elapsed_seconds=time.monotonic() - started)
        receipt["prepare"]["status"] = "interrupted"
        raise
    except Exception as error:
        attempt.update(status="failed", termination_reason=_stable_error(error), elapsed_seconds=time.monotonic() - started)
        receipt["prepare"]["status"] = "failed"
        receipt["prepare"]["error"] = _stable_error(error)
        raise
    finally:
        receipt["summary"] = summarize_run(receipt, output)
        _write_atomic_receipt(receipt_path, receipt, max_output_bytes)
    return receipt


def _load_v2(output: Path) -> dict[str, Any]:
    output = private_directory(output)
    path = output / "receipt.json"
    receipt = strict_json(_safe_existing_file(path, MAX_RECEIPT_BYTES))
    if not isinstance(receipt, dict) or receipt.get("schema_version") != "wikikg.bounded-discovery-receipt.v2":
        raise ValueError("expected one authoritative v2 run receipt; v1 migration is refused")
    config = receipt.get("config", {})
    if receipt.get("prepare", {}).get("status") != "completed":
        prepare_config = config.get("prepare_config", {})
        if (prepare_config.get("worker_sha256") != WORKER_SHA256
                or prepare_config.get("protocol_sha256") != PROTOCOL_SHA256
                or sha256_file(WORKER, 10 * 1024 * 1024) != WORKER_SHA256
                or sha256_file(PROTOCOL, 1024 * 1024) != PROTOCOL_SHA256
                or sha256_file(CLAIM_SCHEMA, 1024 * 1024) != prepare_config.get("schema_sha256")
                or sha256_file(PHP_COLLECTOR, 1024 * 1024) != prepare_config.get("php_collector_sha256")
                or sha256_file(PHP_INVENTORY, 1024 * 1024) != prepare_config.get("php_inventory_sha256")
                or sha256_file(Path(__file__).with_name("bounded_process.py"), 1024 * 1024)
                   != prepare_config.get("process_supervisor_sha256")
                or prepare_config.get("runner_sha256") != sha256_file(Path(__file__).resolve(), 1024 * 1024)):
            raise ValueError("incomplete preparation code identity changed")
        return receipt
    if (config.get("worker_sha256") != WORKER_SHA256 or config.get("protocol_sha256") != PROTOCOL_SHA256
            or sha256_file(WORKER, 10 * 1024 * 1024) != WORKER_SHA256
            or sha256_file(PROTOCOL, 1024 * 1024) != PROTOCOL_SHA256
            or sha256_file(CLAIM_SCHEMA, 1024 * 1024) != config.get("schema_sha256")
            or sha256_file(PHP_COLLECTOR, 1024 * 1024) != config.get("php_collector_sha256")
            or sha256_file(PHP_INVENTORY, 1024 * 1024) != config.get("php_inventory_sha256")
            or sha256_file(Path(__file__).with_name("bounded_process.py"), 1024 * 1024)
               != config.get("process_supervisor_sha256")
            or config.get("runner_sha256") != sha256_file(Path(__file__).resolve(), 1024 * 1024)):
        raise ValueError("immutable run code identity changed")
    inventory_rel = receipt.get("prepare", {}).get("inventory_path")
    if not isinstance(inventory_rel, str) or Path(inventory_rel).is_absolute() or ".." in Path(inventory_rel).parts:
        raise ValueError("immutable metadata inventory path is invalid")
    snapshot = output / inventory_rel
    raw, doc = _private_inventory(snapshot)
    if (sha256_bytes(raw) != config.get("metadata_snapshot_sha256")
            or doc != receipt.get("inventory")
            or sha256_bytes(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
            != config.get("inventory_sha256")):
        raise ValueError("immutable metadata inventory identity changed")
    for batch in receipt.get("batches", []):
        if batch.get("status") != "completed":
            continue
        attempts = batch.get("attempts", [])
        if not attempts or attempts[-1].get("status") != "completed":
            raise ValueError("completed batch lacks its completed attempt record")
        attempt = attempts[-1]
        attempt_dir = attempt.get("attempt_dir")
        if (not isinstance(attempt_dir, str) or Path(attempt_dir).is_absolute()
                or len(Path(attempt_dir).parts) != 1 or attempt_dir in (".", "..")):
            raise ValueError("completed attempt directory is invalid")
        _safe_existing_directory(output / attempt_dir)
        collection_rel = attempt.get("collection_inventory_path")
        if not isinstance(collection_rel, str) or Path(collection_rel).is_absolute() or ".." in Path(collection_rel).parts:
            raise ValueError("completed collection inventory path is invalid")
        collected_raw, collected = _private_inventory(output / collection_rel)
        if (sha256_bytes(collected_raw) != attempt.get("collection_inventory_sha256")
                or collected.get("source_identity") != config.get("source_identity")):
            raise ValueError("completed collection inventory identity changed")
        validate_inventory_metadata(collected)
        if (collected.get("actor") != config.get("actor")
                or collected.get("protocol_sha256") != config.get("protocol_sha256")
                or collected.get("heldout_manifest_sha256") != config.get("manifest_sha256")):
            raise ValueError("completed collection actor or pin identity changed")
        planned_rows = {row["page_id"]: row for row in receipt["inventory"]["pages"]}
        stable_fields = ("page_id", "revision_id", "title", "namespace", "redirect_target",
                         "redirect_namespace", "redirect_external", "redirect_chain",
                         "resolved_page_id", "resolved_title", "resolved_revision_id")
        if len(collected["pages"]) != len(planned_rows):
            raise ValueError("completed collection metadata row count changed")
        for row in collected["pages"]:
            prior_row = planned_rows.get(row.get("page_id"))
            if prior_row is None or any(prior_row.get(key) != row.get(key) for key in stable_fields):
                raise ValueError("completed collection full metadata identity changed")
        extraction = attempt.get("extraction_receipt", {})
        extraction_config = extraction.get("config", {}) if isinstance(extraction, dict) else {}
        if (not isinstance(extraction, dict)
                or extraction_config.get("worker_sha256") != config.get("worker_sha256")
                or extraction_config.get("protocol_sha256") != config.get("protocol_sha256")
                or extraction_config.get("runner_sha256") != config.get("runner_sha256")
                or extraction_config.get("process_supervisor_sha256") != config.get("process_supervisor_sha256")
                or extraction_config.get("schema_sha256") != config.get("schema_sha256")
                or extraction_config.get("source_identity") != config.get("source_identity")
                or extraction_config.get("inventory_sha256") != sha256_bytes(collected_raw)
                or extraction_config.get("manifest_sha256") != config.get("manifest_sha256")
                or extraction_config.get("actor") != config.get("actor")
                or extraction_config.get("limits", {}).get("memory_bytes") != config.get("limits", {}).get("memory_bytes")
                or extraction_config.get("limits", {}).get("memory_control") != config.get("memory_control")
                or extraction_config.get("limits", {}).get("timeout_seconds") != config.get("limits", {}).get("timeout_seconds")
                or extraction_config.get("limits", {}).get("max_output_bytes") != config.get("limits", {}).get("max_output_bytes")
                or extraction_config.get("limits", {}).get("max_bytes") != config.get("limits", {}).get("max_bytes")
                or extraction_config.get("limits", {}).get("max_page_bytes") != config.get("limits", {}).get("max_page_bytes")
                or extraction_config.get("limits", {}).get("max_inventory_rows") != config.get("limits", {}).get("max_inventory_rows")
                or extraction_config.get("limits", {}).get("max_collection_pages") != config.get("limits", {}).get("max_collection_pages")):
            raise ValueError("completed extraction limits differ from the run plan")
        collected_rows = {(row.get("page_id"), row.get("revision_id")): row for row in collected.get("pages", [])}
        if len(collected_rows) != len(collected.get("pages", [])):
            raise ValueError("completed collection contains duplicate page identities")
        selected_rows = {(row["page_id"], row["revision_id"]): row for row in batch.get("pages", [])}
        collected_selected = {identity for identity, row in collected_rows.items() if row.get("eligible") is True}
        if collected_selected != set(selected_rows):
            raise ValueError("completed collection terminal identities differ from the plan")
        jobs = extraction.get("jobs")
        if (not isinstance(jobs, list) or len(jobs) != len(selected_rows)
                or any(not isinstance(job, dict) or job.get("status") != "completed" for job in jobs)
                or {(job.get("page_id"), job.get("revision_id")) for job in jobs} != set(selected_rows)
                or extraction_config.get("limits", {}).get("max_pages") != len(selected_rows)):
            raise ValueError("completed batch lacks an exact completed job set")
        for job in jobs:
            identity = (job.get("page_id"), job.get("revision_id"))
            if identity not in {(row["page_id"], row["revision_id"]) for row in batch.get("pages", [])}:
                raise ValueError("completed extraction identity is outside its planned batch")
            source_row = collected_rows.get(identity)
            selected_row = selected_rows.get(identity)
            if (source_row is None or source_row.get("eligible") is not True
                    or selected_row is None
                    or (source_row.get("title"), source_row.get("kind"), source_row.get("crop"))
                       != (selected_row.get("title"), selected_row.get("kind"), selected_row.get("crop"))
                    or source_row.get("content_hash") != job.get("source_sha256")):
                raise ValueError("completed extraction source identity changed")
            source_file = source_row.get("source_file")
            if not isinstance(source_file, str) or Path(source_file).name != source_file:
                raise ValueError("completed source path is invalid")
            source_raw = _safe_existing_file((output / collection_rel).parent / source_file, MAX_PAGE_BYTES)
            source_doc = strict_json(source_raw)
            source_page = source_doc.get("page") if isinstance(source_doc, dict) else None
            if (not isinstance(source_page, dict)
                    or (source_page.get("page_id"), source_page.get("revision_id"), source_page.get("title"),
                        source_page.get("kind"), source_page.get("crop"), source_page.get("content_hash"))
                    != (identity[0], identity[1], selected_row.get("title"), selected_row.get("kind"),
                        selected_row.get("crop"), job.get("source_sha256"))
                    or not isinstance(source_page.get("wikitext"), str)
                    or sha256_bytes(source_page["wikitext"].encode("utf-8")) != job.get("source_sha256")):
                raise ValueError("completed source artifact identity changed")
            job_dir = job.get("job_dir")
            if not isinstance(job_dir, str) or len(job_dir) != 20 or any(c not in "0123456789abcdef" for c in job_dir):
                raise ValueError("completed extraction job directory is invalid")
            worker_dir = output / attempt["attempt_dir"] / "extraction" / job_dir / "worker"
            artifacts = job.get("artifacts", [])
            if (not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_ARTIFACTS)
                    or {row.get("name") for row in artifacts} != EXPECTED_ARTIFACTS):
                raise ValueError("completed extraction artifact inventory is incomplete")
            for artifact in artifacts:
                name = artifact.get("name")
                if name not in EXPECTED_ARTIFACTS:
                    raise ValueError("completed artifact name is invalid")
                data = _safe_existing_file(worker_dir / name, MAX_OUTPUT)
                if len(data) != artifact.get("bytes") or sha256_bytes(data) != artifact.get("sha256"):
                    raise ValueError("completed extraction artifact identity changed")
    return receipt


def _resume_states(receipt: dict[str, Any], output: Path) -> None:
    changed = False
    for attempt in receipt.get("prepare", {}).get("attempts", []):
        if attempt.get("status") == "running":
            attempt.update(status="interrupted", termination_reason="process_interrupted_before_completion")
            receipt["prepare"]["status"] = "interrupted"
            changed = True
    for batch in receipt.get("batches", []):
        for attempt in batch.get("attempts", []):
            if attempt.get("status") == "running":
                attempt["status"] = "interrupted"
                attempt["termination_reason"] = "process_interrupted_before_receipt_completion"
                if attempt.get("collection_status") == "running":
                    attempt["collection_status"] = "interrupted"
                extraction = attempt.get("extraction_receipt", {})
                for job in extraction.get("jobs", []) if isinstance(extraction, dict) else []:
                    if job.get("status") == "pending":
                        job["status"] = "interrupted"
                        job["reason"] = "parent process interrupted"
                changed = True
    if changed:
        limits = receipt.get("config", {}).get("limits") or receipt.get("config", {}).get("prepare_config", {}).get("limits", {})
        _write_atomic_receipt(output / "receipt.json", receipt, limits.get("max_output_bytes", MAX_OUTPUT))


def summarize_run(receipt: dict[str, Any], output: Path) -> dict[str, Any]:
    batches = receipt.get("batches", [])
    inventory = receipt.get("inventory") or {}
    planned = { (row["page_id"], row["revision_id"]): row
                for batch in batches for row in batch.get("pages", []) }
    selected_states: dict[tuple[int, int], str] = {key: "pending_collection" for key in planned}
    latest_jobs: dict[tuple[int, int], dict[str, Any]] = {}
    exclusions: dict[str, int] = {}
    for row in inventory.get("pages", []):
        reason = row.get("reason")
        if reason not in ("awaiting_collection", "not_selected_in_batch"):
            exclusions[reason or "unclassified"] = exclusions.get(reason or "unclassified", 0) + 1
    collection_states: dict[tuple[int, int], str] = {}
    eligible_collected: set[tuple[int, int]] = set()
    attempts = 0
    prepare_attempts = receipt.get("prepare", {}).get("attempts", [])
    elapsed = sum(float(row.get("elapsed_seconds", 0.0)) for row in prepare_attempts)
    for batch in batches:
        for attempt in batch.get("attempts", []):
            attempts += 1
            elapsed += float(attempt.get("elapsed_seconds", 0.0))
            if attempt.get("collection_status") in ("failed", "interrupted"):
                for page in batch.get("pages", []):
                    key = (page["page_id"], page["revision_id"])
                    collection_states[key] = attempt["collection_status"]
                    selected_states[key] = "collection_" + attempt["collection_status"]
                    latest_jobs.pop(key, None)
            elif attempt.get("collection_status") == "completed":
                eligible_collected.update((page["page_id"], page["revision_id"])
                                          for page in batch.get("pages", []))
                for page in batch.get("pages", []):
                    collection_states[(page["page_id"], page["revision_id"])] = "completed"
                    selected_states[(page["page_id"], page["revision_id"])] = "collected_pending_extraction"
                    latest_jobs.pop((page["page_id"], page["revision_id"]), None)
            elif attempt.get("collection_status") == "running":
                for page in batch.get("pages", []):
                    key = (page["page_id"], page["revision_id"])
                    collection_states[key] = "running"
                    selected_states[key] = "collection_running"
                    latest_jobs.pop(key, None)
            for row in attempt.get("extraction_receipt", {}).get("jobs", []):
                key = (row.get("page_id"), row.get("revision_id"))
                if key in selected_states and row.get("status") in ("completed", "failed", "interrupted", "pending"):
                    selected_states[key] = "extraction_" + row["status"]
                    latest_jobs[key] = row
    counts = {state: sum(row.get("status") == state for row in latest_jobs.values())
              for state in ("completed", "failed", "interrupted", "pending")}
    page_outcomes = {state: list(selected_states.values()).count(state) for state in (
        "pending_collection", "collection_running", "collection_failed", "collection_interrupted",
        "collected_pending_extraction", "extraction_pending", "extraction_completed",
        "extraction_failed", "extraction_interrupted")}
    most_recent_batch = None
    most_recent_at = -1.0
    for batch in batches:
        for attempt in batch.get("attempts", []):
            stamp = float(attempt.get("started_at", 0.0))
            if stamp >= most_recent_at:
                most_recent_at = stamp
                most_recent_batch = batch
    other_batch_ids = set(planned)
    if most_recent_batch is not None:
        other_batch_ids -= {(row["page_id"], row["revision_id"])
                            for row in most_recent_batch.get("pages", [])}
    try:
        retained = _output_bytes(output)
    except (OSError, ValueError):
        retained = None
    source_bytes = sum(int(attempt.get("extraction_receipt", {}).get("source_validation", {}).get("source_bytes", 0))
                       for batch in batches for attempt in batch.get("attempts", []))
    collection_source_bytes = sum(int(attempt.get("source_bytes", 0)) for batch in batches
                                  for attempt in batch.get("attempts", []))
    job_attempts = sum(len(attempt.get("extraction_receipt", {}).get("jobs", []))
                       for batch in batches for attempt in batch.get("attempts", []))
    return {"metadata_rows": len(inventory.get("pages", [])),
            "planned_candidates": len(planned),
            "content_eligible_terminals": sum(state == "completed" for state in collection_states.values()),
            "ever_collected_terminals": len(eligible_collected),
            "scheduled_other_batch": sum(selected_states[key] == "pending_collection" for key in other_batch_ids),
            "scheduled_other_batch_by_batch": {
                str(batch.get("index")): sum(selected_states[key] == "pending_collection"
                    for key in set(planned) - {(row["page_id"], row["revision_id"]) for row in batch.get("pages", [])})
                for batch in batches},
            "exclusions_by_reason": exclusions,
            "collection_failures": sum(value == "failed" for value in collection_states.values()),
            "collection_interrupted": sum(value == "interrupted" for value in collection_states.values()),
            "extraction_states": counts, "page_outcomes": page_outcomes,
            "worker_produced_no_claims": sum(row.get("claim_count") == 0 and row.get("status") == "completed"
                                             for row in latest_jobs.values()),
            "collection_source_bytes": collection_source_bytes,
            "collection_attempts_with_unknown_source_bytes": sum("source_bytes" not in attempt
                for batch in batches for attempt in batch.get("attempts", [])),
            "extraction_source_bytes": source_bytes,
            "retained_output_bytes": retained, "elapsed_seconds": elapsed, "attempt_count": attempts,
            "job_attempt_count": job_attempts,
            "prepare_attempt_count": len(prepare_attempts),
            "prepare_status": receipt.get("prepare", {}).get("status"),
            "prepare_failures": sum(row.get("status") == "failed" for row in prepare_attempts),
            "complete": receipt.get("prepare", {}).get("status") == "completed"
                        and all(batch.get("status") == "completed"
                            and batch.get("attempts")
                            and batch["attempts"][-1].get("status") == "completed"
                            and batch["attempts"][-1].get("collection_status") == "completed"
                            and batch["attempts"][-1].get("extraction_status") == "completed"
                            for batch in batches)
                        and counts["completed"] == len(planned)
                        and retained is not None
                        and retained <= receipt.get("config", {}).get("limits", {}).get("max_output_bytes",
                                                                                         receipt.get("config", {}).get("prepare_config", {}).get("limits", {}).get("max_output_bytes", 0))}


def _execute_batch(output: Path, batch_index: int, *, actor: str, manifest_path: Path,
                   classifications_path: Path, runtime_root: Path, document_root: Path,
                   timeout: int, memory_bytes: int, max_output_bytes: int,
                   retry: bool, lock_fd: int | None = None) -> dict[str, Any]:
    receipt = _load_v2(output)
    _resume_states(receipt, output)
    receipt = _load_v2(output)
    batches = receipt.get("batches")
    if not isinstance(batches, list) or not 0 <= batch_index < len(batches):
        raise ValueError("batch index is outside the immutable plan")
    batch = batches[batch_index]
    if receipt["config"].get("actor") != actor:
        raise ValueError("reviewer actor differs from immutable plan")
    limits = receipt["config"].get("limits", {})
    if (timeout != limits.get("timeout_seconds") or memory_bytes != limits.get("memory_bytes")
            or max_output_bytes != limits.get("max_output_bytes")):
        raise ValueError("batch resource limits differ from immutable plan")
    if linux_memory_identity() != receipt["config"].get("memory_control"):
        raise ValueError("cgroup memory control differs from immutable plan")
    if (runtime_root.resolve(strict=True) != Path(receipt["config"]["runtime_root"])
            or document_root.resolve(strict=True) != Path(receipt["config"]["document_root"])):
        raise ValueError("runtime roots differ from immutable plan")
    runtime_web = os.environ.get("DOCUMENT_ROOT")
    if not runtime_web or Path(runtime_web).resolve(strict=True) != document_root.resolve(strict=True):
        raise ValueError("DOCUMENT_ROOT differs from the configured wiki document root")
    classifications_path = _private_input_path(classifications_path, 2 * 1024 * 1024)
    manifest_path = _private_input_path(manifest_path, MAX_MANIFEST_BYTES)
    strict_json(bounded_read(classifications_path, 2 * 1024 * 1024))
    if (sha256_file(classifications_path, 2 * 1024 * 1024) != receipt["config"]["classifications_sha256"]
            or read_manifest(manifest_path)[1] != receipt["config"]["manifest_sha256"]):
        raise ValueError("prepared classification or manifest identity changed")
    if batch.get("status") == "completed":
        return receipt
    if batch.get("attempts") and not retry:
        raise ValueError("an explicit --retry is required after a prior batch attempt")
    if retry and not batch.get("attempts"):
        raise ValueError("--retry is only valid after a recorded attempt")
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        raise ValueError("jsonschema is required before collection") from None
    attempt_no = len(batch["attempts"]) + 1
    attempt_name = f"batch-{batch_index:04d}-attempt-{attempt_no:04d}-{os.urandom(4).hex()}"
    selected_ids = [row["page_id"] for row in batch["pages"]]
    selected_raw = json.dumps(selected_ids, separators=(",", ":")).encode()
    fields = ("page_id", "revision_id", "title", "namespace", "redirect_target",
              "redirect_namespace", "redirect_external")
    expected_rows = [{key: row.get(key) for key in fields} for row in receipt["inventory"]["pages"]]
    expected_doc = {"source_identity": receipt["config"]["source_identity"], "pages": expected_rows}
    expected_raw = json.dumps(expected_doc, ensure_ascii=False, separators=(",", ":")).encode()
    attempt: dict[str, Any] = {"attempt": attempt_no, "attempt_dir": attempt_name,
                               "status": "running", "collection_status": "running",
                               "started_at": time.time(), "selected": batch["pages"]}
    batch["attempts"].append(attempt)
    _reserve_setup(output, len(selected_raw) + len(expected_raw), receipt, max_output_bytes)
    _write_atomic_receipt(output / "receipt.json", receipt, max_output_bytes)
    started = time.monotonic()
    try:
        attempt_dir = _make_private_dir(output / attempt_name)
        php_output = _make_private_dir(attempt_dir / "php")
        selected_path = attempt_dir / "selected-pages.json"
        _write_private(selected_path, selected_raw)
        expected_path = attempt_dir / "expected-metadata.json"
        _write_private(expected_path, expected_raw)
        response, result = _run_collector(runtime_root, php_output, actor, document_root,
                                          manifest_path, classifications_path, metadata_only=False,
                                          selected_path=selected_path, expected_metadata_path=expected_path, timeout=timeout,
                                          memory_bytes=memory_bytes,
                                          output_limit=max_output_bytes - _output_bytes(output) - MAX_RECEIPT_BYTES,
                                          lock_fd=lock_fd)
        if linux_memory_identity() != receipt["config"]["memory_control"]:
            raise ValueError("cgroup memory control changed during collection")
        inventory_path = Path(response["inventory_path"])
        _private_input_path(inventory_path, MAX_INVENTORY_BYTES)
        raw, collected = _private_inventory(inventory_path)
        expected_inventory = receipt["inventory"]
        if collected.get("source_identity") != receipt["config"]["source_identity"]:
            raise ValueError("database identity drifted from prepared plan")
        stable_fields = ("page_id", "revision_id", "title", "namespace", "redirect_target",
                         "redirect_namespace", "redirect_external", "redirect_chain",
                         "resolved_page_id", "resolved_title", "resolved_revision_id")
        expected_rows = {row["page_id"]: row for row in expected_inventory["pages"]}
        if len(collected.get("pages", [])) != len(expected_rows):
            raise ValueError("metadata row count drifted from prepared plan")
        for row in collected["pages"]:
            prior = expected_rows.get(row.get("page_id"))
            if prior is None or any(prior.get(k) != row.get(k) for k in stable_fields):
                raise ValueError("full page or redirect metadata drifted from prepared plan")
        chosen = {(row["page_id"], row["revision_id"]): row for row in batch["pages"]}
        seen = set()
        planned_by_id = {row["page_id"]: row for row in expected_inventory["pages"]}
        for row in collected["pages"]:
            identity = (row.get("page_id"), row.get("revision_id"))
            prior = planned_by_id.get(row.get("page_id"))
            if prior is None:
                raise ValueError("collector returned a page outside prepared metadata")
            if prior.get("reason") == "awaiting_collection":
                if identity not in chosen:
                    if (row.get("reason") != "not_selected_in_batch" or row.get("kind") != prior.get("kind")
                            or row.get("crop") != prior.get("crop")):
                        raise ValueError("unselected candidate lost its classification or schedule state")
                    continue
            elif row.get("reason") != prior.get("reason"):
                raise ValueError("policy exclusion or unsupported classification changed")
            if identity not in chosen:
                if row.get("reason") == "eligible" or row.get("eligible") is True:
                    raise ValueError("collector returned an unselected content-eligible page")
                continue
            plan_row = chosen[identity]
            if (row.get("eligible") is not True or row.get("kind") != plan_row["kind"]
                    or row.get("crop") != plan_row["crop"] or row.get("source_file") is None):
                raise ValueError("selected source identity or classification differs from prepared plan")
            seen.add(identity)
        if seen != set(chosen):
            raise ValueError("collector omitted one or more selected terminal identities")
        if receipt["config"]["inventory_sha256"] != sha256_bytes(
                json.dumps(expected_inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()):
            raise ValueError("prepared inventory identity changed")
        attempt.update(collection_status="completed", collection_inventory_sha256=sha256_bytes(raw),
                       collection_inventory_path=str(inventory_path.relative_to(output)),
                       collection_elapsed_seconds=result.elapsed_seconds,
                       source_bytes=response.get("source_bytes", 0))
        _write_atomic_receipt(output / "receipt.json", receipt, max_output_bytes)
        extraction_output = _make_private_dir(attempt_dir / "extraction")
        if time.monotonic() >= started + timeout:
            raise ValueError("deadline")
        run(inventory_path, extraction_output, actor, max_pages=len(batch["pages"]),
            max_bytes=MAX_BYTES, timeout=timeout, max_output_bytes=max_output_bytes,
            manifest_path=manifest_path, memory_bytes=memory_bytes, accounting_root=output,
            run_state=(receipt, attempt), execution_deadline=started + timeout, lock_fd=lock_fd)
        attempt.update(status="completed", extraction_status="completed")
        batch["status"] = "completed"
    except KeyboardInterrupt:
        attempt.update(status="interrupted", termination_reason="cancelled")
        if attempt.get("collection_status") == "running":
            attempt["collection_status"] = "interrupted"
        if attempt.get("collection_status") == "completed":
            attempt["extraction_status"] = "interrupted"
        raise
    except Exception as error:
        attempt.update(status="failed", termination_reason=_stable_error(error))
        if attempt.get("collection_status") == "running":
            attempt["collection_status"] = "failed"
        if attempt.get("collection_status") != "completed":
            attempt["extraction_status"] = "not_started"
        elif "extraction_receipt" not in attempt:
            attempt["extraction_status"] = "not_started"
        else:
            statuses = {row.get("status") for row in attempt["extraction_receipt"].get("jobs", [])}
            attempt["extraction_status"] = ("interrupted" if "interrupted" in statuses else
                                              "failed" if "failed" in statuses else "pending")
    finally:
        attempt["elapsed_seconds"] = time.monotonic() - started
        attempt["finished_at"] = time.time()
        receipt["summary"] = summarize_run(receipt, output)
        _write_atomic_receipt(output / "receipt.json", receipt, max_output_bytes)
    return receipt


def _stable_error(error: Exception) -> str:
    message = str(error)
    allowed = ("database identity drifted", "metadata row count drifted", "full page or redirect metadata drifted",
               "unselected content-eligible", "selected source identity", "collector omitted",
               "prepared inventory identity", "supervised source collection failed", "PHP runtime is unavailable",
               "finite cgroup-v2", "enforceable Linux memory budget", "deadline", "output_limit",
               "malformed bounded summary", "reviewer actor", "classification or manifest identity changed",
               "source metadata or input validation failed", "frozen worker or protocol hash mismatch",
               "candidate claims fail", "exclusion closure", "held-out redirect")
    allowed = allowed + ("cgroup memory control",)
    return next((text for text in allowed if text in message), "batch_failed")


def _dispatch(args, output: Path, lock_fd: int) -> int:
    if args.operation == "summary":
        if args.retry:
            raise ValueError("--retry applies only to prepare or batch")
        receipt = _load_v2(output)
        _resume_states(receipt, output)
        receipt = _load_v2(output)
        summary = summarize_run(receipt, output)
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary["complete"] else 3
    if not args.actor or not args.offline_only or args.audience != "actor-only":
        raise ValueError("named actor, --offline-only, and --audience=actor-only are required")
    if args.operation == "extract":
        if args.retry:
            raise ValueError("incomplete v1 attempts require a new private output directory")
        if not args.inventory or not args.corpus_manifest:
            raise ValueError("extract requires --inventory and --corpus-manifest")
        receipt = run(Path(args.inventory), output, args.actor, args.max_pages, args.max_bytes,
                      args.timeout, args.max_output_bytes, Path(args.corpus_manifest),
                      memory_bytes=args.memory_bytes, lock_fd=lock_fd)
        counts = {state: sum(row.get("status") == state for row in receipt["jobs"])
                  for state in ("pending", "completed", "skipped", "failed", "interrupted")}
        print(json.dumps({"job_count": len(receipt["jobs"]), "statuses": counts}, sort_keys=True))
        return 0 if all(row.get("status") in ("completed", "skipped") for row in receipt["jobs"]) else 3
    if not args.corpus_manifest:
        raise ValueError("prepare and batch require --corpus-manifest")
    if args.max_pages != MAX_PAGES or args.max_bytes != MAX_BYTES:
        raise ValueError("prepare and batch use their pinned page and input-byte limits")
    manifest_path = Path(args.corpus_manifest)
    if args.operation == "prepare":
        if not args.classifications or not args.runtime_root or not args.document_root:
            raise ValueError("prepare requires --classifications, --runtime-root, and --document-root")
        receipt = _create_plan(output, args.actor, manifest_path, Path(args.classifications), Path(args.runtime_root),
                               Path(args.document_root), timeout=args.timeout,
                               memory_bytes=args.memory_bytes, max_output_bytes=args.max_output_bytes,
                               retry=args.retry, lock_fd=lock_fd)
    else:
        if args.batch_index is None or not args.classifications or not args.runtime_root or not args.document_root:
            raise ValueError("batch requires --batch-index, --classifications, --runtime-root, and --document-root")
        receipt = _execute_batch(output, args.batch_index, actor=args.actor, manifest_path=manifest_path,
                                 classifications_path=Path(args.classifications), runtime_root=Path(args.runtime_root),
                                 document_root=Path(args.document_root), timeout=args.timeout,
                                 memory_bytes=args.memory_bytes, max_output_bytes=args.max_output_bytes,
                                 retry=args.retry, lock_fd=lock_fd)
    summary = summarize_run(receipt, output)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["complete"] else 3


def _interrupted(signum, frame) -> None:
    raise KeyboardInterrupt


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"error": "bounded_discovery_invalid_arguments"}), file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    parser = Parser(description=__doc__, epilog=(
        "Example: bounded_discovery.py --operation summary --output-dir <private-run>. "
        "Preparation and batches require explicit --offline-only --audience actor-only, a named actor, "
        "a corpus manifest, classifications, and canonical runtime/document roots."))
    parser.add_argument("--operation", choices=["extract", "prepare", "batch", "summary"], default="extract")
    parser.add_argument("--inventory")
    parser.add_argument("--corpus-manifest")
    parser.add_argument("--classifications")
    parser.add_argument("--runtime-root")
    parser.add_argument("--document-root")
    parser.add_argument("--batch-index", type=int)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--actor")
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--audience", choices=["actor-only"])
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=MAX_SECONDS)
    parser.add_argument("--max-output-bytes", type=int, default=MAX_OUTPUT)
    parser.add_argument("--memory-bytes", type=int, default=1024 * 1024 * 1024)
    args = parser.parse_args()
    previous_term = None
    try:
        previous_term = signal.signal(signal.SIGTERM, _interrupted)
        output = private_directory(Path(args.output_dir))
        with _receipt_lock(output) as lock_fd:
            return _dispatch(args, output, lock_fd)
    except KeyboardInterrupt:
        print(json.dumps({"error": "bounded_discovery_interrupted"}, sort_keys=True), file=sys.stderr)
        return 130
    except Exception as error:
        code = str(error) if str(error) in {"run_in_progress", "receipt_filesystem_unsupported"} else "bounded_discovery_refused"
        print(json.dumps({"error": code, "type": type(error).__name__}, sort_keys=True),
              file=sys.stderr)
        return 2
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
