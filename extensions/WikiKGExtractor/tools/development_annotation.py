"""Prepare development sources, retain submissions, and propose discrepancies.

No extraction, model calls, scientific scoring, or claim-review writes occur here.
Checksums detect drift against retained or caller-supplied anchors; they do not
authenticate a person, prove source-first work, or grant agricultural authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

import evaluate_pilot as pilot

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
SCHEMA = ROOT / "schema" / "development-annotation.v1.schema.json"
RUBRIC = ROOT / "tools" / "development-annotation-rubric.md"
PROTOCOL = ROOT / "protocol" / "rice-engineering-pilot.v2.json"
VERSION = "wikikg.development-annotation.v1"
MAX_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_FILES = 1010
SHA = re.compile(r"^[a-f0-9]{64}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")
FORMAT_CHECKER = jsonschema.FormatChecker()


@FORMAT_CHECKER.checks("date-time")
def valid_timestamp(value: Any) -> bool:
    # jsonschema's built-in date-time checker is absent without optional extras.
    # Enforce this workflow's explicit timestamp contract on every installation.
    if (
        not isinstance(value, str)
        or len(value) > 64
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?"
            r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
            value,
        )
        is None
    ):
        return False
    try:
        datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


class AnnotationError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.result = {"status": "error", "code": code, "message": message, **details}


def require(condition: bool, code: str, message: str, **details: Any) -> None:
    if not condition:
        raise AnnotationError(code, message, **details)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as error:
        raise AnnotationError("BAD_JSON", "Use finite values and valid UTF-8 JSON.") from error


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "BAD_JSON", "Remove duplicate JSON field names.")
        result[key] = value
    return result


def decode(data: bytes) -> Any:
    def nonfinite(_: str) -> None:
        raise AnnotationError("BAD_JSON", "Use finite JSON numbers.")

    try:
        result = json.loads(
            data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=nonfinite
        )
        pending = [(result, 0)]
        while pending:
            value, depth = pending.pop()
            require(depth <= 32, "BAD_JSON", "Reduce JSON nesting to at most 32 levels.")
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        encoded(result)  # Reject lone Unicode surrogates and overflowing numeric values.
        return result
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise AnnotationError(
            "BAD_JSON", "Use valid UTF-8 JSON without duplicate fields."
        ) from error


def read_bytes(path: Path, *, private: bool = False) -> bytes:
    require(os.name == "posix", "PLATFORM", "Use a POSIX system with private local storage.")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode), "FILE_TYPE", "Choose a regular file, not a link or device."
        )
        if private:
            require(
                info.st_uid == os.getuid()
                and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600,
                "PRIVATE_FILE",
                "Use owned, unlinked files with mode 0600.",
            )
        require(info.st_size <= MAX_BYTES, "TOO_LARGE", "Keep each input below 16 MiB.")
        data = stream.read(MAX_BYTES + 1)
        require(len(data) <= MAX_BYTES, "TOO_LARGE", "Keep each input below 16 MiB.")
        return data


def validate_schema(value: Any, schema: dict[str, Any], definition: str | None = None) -> None:
    if definition:
        schema = {**schema, "$ref": f"#/$defs/{definition}"}
    validator = jsonschema.Draft202012Validator(schema, format_checker=FORMAT_CHECKER)
    public_fields = {"condition", "season", "level"}
    pending = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            public_fields.update(node.get("properties", {}))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    errors = []
    for error in validator.iter_errors(value):
        # Never echo jsonschema's message: it can contain a private value or quote.
        path = [
            part if isinstance(part, int) or part in public_fields else "<field>"
            for part in error.absolute_path
        ]
        errors.append({"field": path, "rule": error.validator})
        if len(errors) == 20:
            break
    require(
        not errors,
        "SCHEMA",
        "Correct the listed fields using annotation-schema.json.",
        issues=errors,
        truncated=len(errors) == 20,
    )


def private_directory(path: Path) -> Path:
    require(os.name == "posix", "PLATFORM", "Use a POSIX system with private local storage.")
    absolute = path.absolute()
    require(
        absolute == absolute.resolve() and not absolute.is_symlink(),
        "SYMLINK",
        "Use a real directory path without symbolic links.",
    )
    info = absolute.stat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "PRIVATE_DIRECTORY",
        "Use an owned directory with mode 0700.",
    )
    return absolute


def outside_roots(path: Path, document_roots: tuple[Path, ...] = ()) -> None:
    target = path.resolve()
    require(
        not any(
            target == root.resolve() or root.resolve() in target.parents
            for root in (REPOSITORY, *document_roots)
        ),
        "PUBLIC_DESTINATION",
        "Choose private storage outside repositories and document roots.",
    )
    require(
        not any((parent / ".git").exists() for parent in (target, *target.parents)),
        "PUBLIC_DESTINATION",
        "Choose a destination outside every Git checkout.",
    )


def load_bundle(
    directory: Path, kind: str, document_roots: tuple[Path, ...] = ()
) -> tuple[dict, dict]:
    outside_roots(directory, document_roots)
    directory = private_directory(directory)
    receipt = decode(read_bytes(directory / "receipt.json", private=True))
    require(
        isinstance(receipt, dict)
        and set(receipt)
        == {
            "schema_version",
            "kind",
            "created_at",
            "tool_sha256",
            "files_sha256",
            "operation_sha256",
        },
        "RECEIPT",
        "Use a complete bundle with its original receipt.",
    )
    hashes = receipt["files_sha256"]
    require(
        receipt["schema_version"] == VERSION
        and receipt["kind"] == kind
        and isinstance(hashes, dict)
        and 0 < len(hashes) <= MAX_FILES,
        "RECEIPT",
        "Choose the correct complete bundle type.",
    )
    require(
        isinstance(receipt["tool_sha256"], str)
        and SHA.fullmatch(receipt["tool_sha256"])
        and isinstance(receipt["created_at"], str)
        and valid_timestamp(receipt["created_at"]),
        "RECEIPT",
        "Keep the original receipt metadata.",
    )
    require(
        set(item.name for item in directory.iterdir()) == set(hashes) | {"receipt.json"},
        "BUNDLE_CHANGED",
        "Restore the original bundle; work in a separate copy.",
    )
    files = {}
    total_bytes = 0
    for name, digest in hashes.items():
        require(
            isinstance(name, str)
            and SAFE_FILE.fullmatch(name) is not None
            and name != "receipt.json"
            and isinstance(digest, str)
            and SHA.fullmatch(digest) is not None,
            "RECEIPT",
            "Keep flat filenames and valid checksums in the receipt.",
        )
        data = read_bytes(directory / name, private=True)
        total_bytes += len(data)
        require(total_bytes <= MAX_BUNDLE_BYTES, "TOO_LARGE", "Keep each bundle below 64 MiB.")
        require(
            sha(data) == digest,
            "BUNDLE_CHANGED",
            "Restore the retained file from its original copy.",
        )
        files[name] = data
    require(
        receipt["operation_sha256"] == sha(encoded({"kind": kind, "files_sha256": hashes})),
        "RECEIPT",
        "Restore the original receipt checksum.",
    )
    return files, receipt


def write_bundle(
    directory: Path, kind: str, files: dict[str, bytes], document_roots: tuple[Path, ...] = ()
) -> dict:
    outside_roots(directory, document_roots)
    parent = private_directory(directory.parent)
    directory = parent / directory.name
    hashes = {name: sha(data) for name, data in files.items()}
    require(
        0 < len(files) <= MAX_FILES
        and sum(map(len, files.values())) <= MAX_BUNDLE_BYTES
        and all(
            SAFE_FILE.fullmatch(name) and name != "receipt.json" and len(data) <= MAX_BYTES
            for name, data in files.items()
        ),
        "BUNDLE_SIZE",
        "Use bounded files with flat names.",
    )
    if directory.exists() or directory.is_symlink():
        existing, receipt = load_bundle(directory, kind, document_roots)
        require(
            existing == files,
            "OUTPUT_EXISTS",
            "Keep the existing bundle and choose a new destination.",
        )
        status = "unchanged"
    else:
        # Only the successful mkdir caller owns publication in this destination.
        # Other writers refuse partial output or verify a complete identical retry.
        directory.mkdir(mode=0o700)
        private_directory(directory)
        status = "created"
        receipt = {
            "schema_version": VERSION,
            "kind": kind,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tool_sha256": sha(read_bytes(Path(__file__))),
            "files_sha256": hashes,
            "operation_sha256": sha(encoded({"kind": kind, "files_sha256": hashes})),
        }
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if status == "created":
                for name, data in [*files.items(), (".receipt.pending", encoded(receipt))]:
                    fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=descriptor,
                    )
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                # Persist payload entries, staged receipt and the new bundle's
                # parent entry before publishing a consumable completion marker.
                os.fsync(descriptor)
                os.fsync(parent_descriptor)
                # link never replaces an existing receipt. Until the staging link
                # is removed, private-file checks reject its two-link inode.
                os.link(
                    ".receipt.pending",
                    "receipt.json",
                    src_dir_fd=descriptor,
                    dst_dir_fd=descriptor,
                    follow_symlinks=False,
                )
                os.unlink(".receipt.pending", dir_fd=descriptor)
            else:
                # A prior writer may have stopped after complete publication but
                # before its final directory barriers. Reapply all barriers without
                # rewriting bytes, timestamps or receipts on an identical retry.
                for name in [*files, "receipt.json"]:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            os.fsync(descriptor)
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        os.close(descriptor)
    return {"status": status, "operation_sha256": receipt["operation_sha256"]}


def submission_template(pack: dict) -> dict:
    return {
        "schema_version": VERSION,
        "split": "development",
        "pack_id": pack["pack_id"],
        "submission_id": "",
        "version": 1,
        "previous_submission_sha256": None,
        "provenance": {"annotator_id": "", "submitted_at": "", "exposure": "unknown", "notes": ""},
        "page_reviews": [
            {"page_id": page["page_id"], "status": "not_started", "notes": ""}
            for page in pack["pages"]
        ],
        "annotations": [],
    }


def annotation_form(pack: dict) -> dict:
    return {
        "annotation_id": "",
        "subject": {"literal": "", "type": "unknown", "identity_status": "unresolved"},
        "predicate": "",
        "value_literal": "",
        "unit": {"state": "uncertain", "literal": None},
        "qualifiers": {
            key: {"state": "uncertain", "literal": None} for key in pack["scope"]["qualifier_keys"]
        },
        "evidence": {
            "page_id": None,
            "revision_id": None,
            "content_hash": "",
            "source_offset_start": None,
            "source_offset_end": None,
            "supporting_span": "",
            "span_hash": "",
        },
        "judgment": "uncertain",
        "outside_scope": False,
        "reason": "",
    }


def check_scope(scope: Any, *, code: str = "PACK") -> None:
    require(
        isinstance(scope, dict)
        and set(scope)
        == {"entity_types", "relationship_predicates", "scalar_predicates", "qualifier_keys"}
        and all(
            isinstance(values, list)
            and len(values) <= 32
            and all(
                isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", value)
                for value in values
            )
            and len(values) == len(set(values))
            for values in scope.values()
        ),
        code,
        "Use bounded vocabulary lists with supported annotation field names.",
    )
    require(
        bool(scope["entity_types"])
        and bool(scope["relationship_predicates"])
        and bool(scope["scalar_predicates"])
        and set(scope["entity_types"]) <= {"Crop", "Variety", "Pest"}
        and not set(scope["relationship_predicates"]) & set(scope["scalar_predicates"])
        and all(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key) for key in scope["qualifier_keys"]),
        code,
        "Use supported entity types, distinct predicates and lowercase qualifier names.",
    )


def prepare(
    protocol_path: Path,
    manifest_path: Path,
    input_path: Path,
    output: Path,
    document_roots: tuple[Path, ...] = (),
) -> dict:
    protocol_bytes = read_bytes(protocol_path)
    protocol = decode(protocol_bytes)
    validate_schema(
        protocol, decode(read_bytes(ROOT / "schema" / "evaluation-protocol.v1.schema.json"))
    )
    check_scope(protocol["ontology"], code="PROTOCOL_SCOPE")
    for name in sorted(pilot.PINNED_SCHEMA_NAMES):
        require(
            protocol["artifacts"]["schema_sha256"].get(name)
            == sha(read_bytes(ROOT / "schema" / name)),
            "FROZEN_SCHEMA",
            "Restore the pinned pilot schemas before preparing a pack.",
        )
    raw_bytes, manifest_bytes = read_bytes(input_path), read_bytes(manifest_path)
    require(
        sha(raw_bytes) == protocol["artifacts"]["development_input_sha256"]
        and sha(manifest_bytes) == protocol["artifacts"]["manifest_sha256"],
        "DEVELOPMENT_PIN",
        "Use the pinned development input and manifest, not another split.",
    )
    raw, manifest = decode(raw_bytes), decode(manifest_bytes)
    validate_schema(raw, decode(read_bytes(ROOT / "schema" / "raw-data.v1.schema.json")))
    validate_schema(
        manifest, decode(read_bytes(ROOT / "schema" / "corpus-manifest.v1.schema.json"))
    )
    development_pins = [
        manifest["source"],
        *(page for page in manifest["pages"] if page["split"] == "dev"),
    ]
    require(
        0 < len(raw["pages"]) <= 1000
        and all(
            type(page.get("page_id")) is int
            and page["page_id"] > 0
            and type(page.get("revision_id")) is int
            and page["revision_id"] > 0
            for page in [*raw["pages"], *development_pins]
        )
        and all(bool(page["title"].strip()) for page in raw["pages"])
        and pilot.source_records_match_manifest(raw, manifest, "development"),
        "SOURCE_PIN",
        "Restore the exact development page identities and wikitext.",
    )
    expected = {manifest["source"]["page_id"]: manifest["source"]["title"]}
    for page in manifest["pages"]:
        if page["split"] == "dev":
            require(
                page["page_id"] not in expected,
                "SOURCE_PIN",
                "Use unique development page identities.",
            )
            expected[page["page_id"]] = page["resolved_title"]
    require(
        all(expected.get(page["page_id"]) == page["title"] for page in raw["pages"]),
        "SOURCE_PIN",
        "Keep development titles identical to the manifest.",
    )
    schema_bytes, rubric_bytes = read_bytes(SCHEMA), read_bytes(RUBRIC)
    pages = sorted(raw["pages"], key=lambda page: page["page_id"])
    pack = {
        "schema_version": VERSION,
        "split": "development",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha(protocol_bytes),
        "input_sha256": sha(raw_bytes),
        "manifest_sha256": sha(manifest_bytes),
        "scope": protocol["ontology"],
        "annotation_schema_sha256": sha(schema_bytes),
        "rubric_sha256": sha(rubric_bytes),
        "pilot_schema_sha256": {
            name: protocol["artifacts"]["schema_sha256"][name]
            for name in sorted(pilot.PINNED_SCHEMA_NAMES)
        },
        "pages": [
            {
                **{key: page[key] for key in ("title", "page_id", "revision_id", "content_hash")},
                "filename": f"source-{page['page_id']}.txt",
                "source_codepoints": len(page["wikitext"]),
            }
            for page in pages
        ],
    }
    pack["pack_id"] = sha(encoded(pack))
    files = {f"source-{page['page_id']}.txt": page["wikitext"].encode("utf-8") for page in pages}
    files.update(
        {
            "pack.json": encoded(pack),
            "rubric.md": rubric_bytes,
            "annotation-schema.json": schema_bytes,
            "submission-template.json": encoded(submission_template(pack)),
            "annotation-form.json": encoded(annotation_form(pack)),
        }
    )
    result = write_bundle(output, "source_pack", files, document_roots)
    return {
        **result,
        "pack_id": pack["pack_id"],
        "source_pages": len(pages),
        "split": "development",
        "annotations": 0,
        "machine_suggestions": 0,
    }


def load_pack(directory: Path, document_roots: tuple[Path, ...] = ()) -> tuple[dict, dict, dict]:
    files, receipt = load_bundle(directory, "source_pack", document_roots)
    require("pack.json" in files, "PACK", "Choose a complete source pack.")
    pack = decode(files["pack.json"])
    require(
        isinstance(pack, dict)
        and set(pack)
        == {
            "schema_version",
            "split",
            "protocol_id",
            "protocol_sha256",
            "input_sha256",
            "manifest_sha256",
            "scope",
            "annotation_schema_sha256",
            "rubric_sha256",
            "pages",
            "pack_id",
            "pilot_schema_sha256",
        },
        "PACK",
        "Keep the original pack metadata.",
    )
    require(
        pack["schema_version"] == VERSION
        and pack["split"] == "development"
        and isinstance(pack["protocol_id"], str)
        and all(
            isinstance(pack[key], str) and SHA.fullmatch(pack[key])
            for key in (
                "pack_id",
                "protocol_sha256",
                "input_sha256",
                "manifest_sha256",
                "annotation_schema_sha256",
                "rubric_sha256",
            )
        ),
        "PACK",
        "Use the supported development pack format.",
    )
    require(
        pack["pack_id"]
        == sha(encoded({key: value for key, value in pack.items() if key != "pack_id"})),
        "PACK_CHANGED",
        "Restore the original source pack checksum.",
    )
    require(
        pack["pilot_schema_sha256"]
        == {
            name: sha(read_bytes(ROOT / "schema" / name))
            for name in sorted(pilot.PINNED_SCHEMA_NAMES)
        },
        "FROZEN_SCHEMA",
        "Restore the pilot schemas pinned by this source pack.",
    )
    check_scope(pack["scope"])
    require(
        isinstance(pack["pages"], list) and 0 < len(pack["pages"]) <= 1000,
        "PACK",
        "Use a nonempty bounded development pack.",
    )
    pages = {}
    for page in pack["pages"]:
        require(
            isinstance(page, dict)
            and set(page)
            == {"title", "page_id", "revision_id", "content_hash", "filename", "source_codepoints"},
            "PACK",
            "Keep the original source metadata fields.",
        )
        require(
            type(page["page_id"]) is int
            and page["page_id"] > 0
            and type(page["revision_id"]) is int
            and page["revision_id"] > 0
            and type(page["source_codepoints"]) is int
            and page["source_codepoints"] > 0
            and isinstance(page["title"], str)
            and bool(page["title"].strip())
            and page["page_id"] not in pages
            and page["filename"] == f"source-{page['page_id']}.txt",
            "PACK",
            "Keep unique source identities and generated filenames.",
        )
        require(
            page["filename"] in files and sha(files[page["filename"]]) == page["content_hash"],
            "SOURCE_PIN",
            "Restore the original pinned wikitext.",
        )
        text = files[page["filename"]].decode("utf-8")
        require(
            len(text) == page["source_codepoints"],
            "SOURCE_PIN",
            "Restore the original codepoint count.",
        )
        pages[page["page_id"]] = {**page, "wikitext": text}
    require(
        set(files)
        == {
            "pack.json",
            "rubric.md",
            "annotation-schema.json",
            "submission-template.json",
            "annotation-form.json",
        }
        | {page["filename"] for page in pages.values()},
        "PACK",
        "Keep only the generated source-pack files.",
    )
    require(
        sha(files["annotation-schema.json"])
        == pack["annotation_schema_sha256"]
        == sha(read_bytes(SCHEMA))
        and sha(files["rubric.md"]) == pack["rubric_sha256"]
        and files["submission-template.json"] == encoded(submission_template(pack))
        and files["annotation-form.json"] == encoded(annotation_form(pack)),
        "PACK_CHANGED",
        "Restore the original forms, rubric and supported annotation schema.",
    )
    return pack, pages, receipt


def check_evidence(evidence: dict, pages: dict, *, index: int) -> None:
    page = pages.get(evidence["page_id"])
    require(
        page is not None
        and evidence["revision_id"] == page["revision_id"]
        and evidence["content_hash"] == page["content_hash"],
        "EVIDENCE_PIN",
        "Use the page identity from this source pack.",
        annotation_index=index,
    )
    start, end = evidence["source_offset_start"], evidence["source_offset_end"]
    require(
        0 <= start < end <= len(page["wikitext"]),
        "EVIDENCE_RANGE",
        "Use codepoint offsets within this wikitext.",
        annotation_index=index,
    )
    quote = page["wikitext"][start:end]
    require(
        quote == evidence["supporting_span"]
        and sha(quote.encode("utf-8")) == evidence["span_hash"],
        "EVIDENCE_SPAN",
        "Copy the exact codepoint slice and its UTF-8 checksum.",
        annotation_index=index,
    )


def check_submission(submission: Any, pack: dict, pages: dict) -> dict:
    validate_schema(submission, decode(read_bytes(SCHEMA)))
    require(
        submission["pack_id"] == pack["pack_id"],
        "SUBMISSION_PACK",
        "Use this pack's submission template.",
    )
    reviews = {item["page_id"]: item for item in submission["page_reviews"]}
    require(
        len(reviews) == len(submission["page_reviews"]) and set(reviews) == set(pages),
        "PAGE_REVIEWS",
        "Record every source page once, including unstarted pages.",
    )
    require(
        (submission["version"] == 1) == (submission["previous_submission_sha256"] is None),
        "VERSION",
        "Link corrections to the previous checksum; initial versions have no predecessor.",
    )
    identifiers = set()
    scope = pack["scope"]
    for index, annotation in enumerate(submission["annotations"]):
        identifier = annotation["annotation_id"]
        require(
            identifier not in identifiers and not SHA.fullmatch(identifier),
            "ANNOTATION_ID",
            "Use unique annotation IDs distinct from machine claim hashes.",
            annotation_index=index,
        )
        identifiers.add(identifier)
        check_evidence(annotation["evidence"], pages, index=index)
        page_id = annotation["evidence"]["page_id"]
        require(
            reviews[page_id]["status"] != "not_started",
            "PAGE_REVIEWS",
            "Mark the source page partial or complete before adding its annotations.",
            annotation_index=index,
        )
        relationship = "object" in annotation
        predicates = (
            scope["relationship_predicates"] if relationship else scope["scalar_predicates"]
        )
        require(
            annotation["outside_scope"] or annotation["predicate"] in predicates,
            "PREDICATE_SCOPE",
            "Use a predicate of the correct form, or record an outside-scope gap.",
            annotation_index=index,
        )
        require(
            set(scope["qualifier_keys"]) <= set(annotation["qualifiers"]),
            "QUALIFIER_STATE",
            "Record stated, not_stated or uncertain for every pilot qualifier.",
            annotation_index=index,
        )
        entities = [annotation["subject"], *([annotation["object"]] if relationship else [])]
        for entity in entities:
            require(
                entity["identity_status"] != "resolved" or entity["type"] in scope["entity_types"],
                "IDENTITY",
                "Keep an unknown entity type unresolved.",
                annotation_index=index,
            )
        needs_reason = (
            annotation["outside_scope"]
            or annotation["judgment"] != "supported"
            or any(entity["identity_status"] == "unresolved" for entity in entities)
            or any(
                item["state"] == "uncertain"
                for item in [annotation["unit"], *annotation["qualifiers"].values()]
            )
        )
        require(
            not needs_reason or bool(annotation["reason"].strip()),
            "REASON",
            "Explain the unresolved identity, qualifier, judgment or scope.",
            annotation_index=index,
        )
    return {
        "annotations": len(identifiers),
        "page_reviews": dict(sorted(Counter(item["status"] for item in reviews.values()).items())),
        "provenance_verification": "not_established",
    }


def validate(pack_path: Path, submission_path: Path, document_roots: tuple[Path, ...] = ()) -> dict:
    pack, pages, _ = load_pack(pack_path, document_roots)
    data = read_bytes(submission_path)
    summary = check_submission(decode(data), pack, pages)
    return {"status": "valid_format_and_coordinates", "submission_sha256": sha(data), **summary}


def load_retained(
    directory: Path,
    expected_sha256: str,
    pack: dict,
    pages: dict,
    document_roots: tuple[Path, ...] = (),
) -> tuple[dict, dict]:
    require(
        SHA.fullmatch(expected_sha256) is not None,
        "CHECKSUM",
        "Supply the retained submission's SHA-256.",
    )
    files, receipt = load_bundle(directory, "submission", document_roots)
    require(
        set(files) == {"submission.json", "binding.json"},
        "RECEIPT",
        "Choose a retained submission bundle.",
    )
    require(
        sha(files["submission.json"]) == expected_sha256,
        "SUBMISSION_CHANGED",
        "Use the original submission and its separately retained checksum.",
    )
    binding = decode(files["binding.json"])
    require(
        binding
        == {
            "pack_id": pack["pack_id"],
            "pack_sha256": sha(encoded(pack)),
            "submission_sha256": expected_sha256,
            "validation_scope": "format_and_coordinates_only",
        },
        "SUBMISSION_PACK",
        "Use a submission retained against this exact source pack.",
    )
    submission = decode(files["submission.json"])
    check_submission(submission, pack, pages)
    return submission, receipt


def retain(
    pack_path: Path,
    submission_path: Path,
    output: Path,
    previous: Path | None = None,
    previous_sha256: str | None = None,
    document_roots: tuple[Path, ...] = (),
) -> dict:
    pack, pages, _ = load_pack(pack_path, document_roots)
    data = read_bytes(submission_path)
    submission = decode(data)
    summary = check_submission(submission, pack, pages)
    if submission["version"] == 1:
        require(
            previous is None and previous_sha256 is None,
            "VERSION",
            "Do not link an initial submission to a predecessor.",
        )
    else:
        require(
            previous is not None and previous_sha256 is not None,
            "PREDECESSOR",
            "Supply the retained previous version and its original checksum.",
        )
        earlier, _ = load_retained(previous, previous_sha256, pack, pages, document_roots)
        require(
            submission["previous_submission_sha256"] == previous_sha256
            and submission["version"] == earlier["version"] + 1
            and submission["submission_id"] == earlier["submission_id"]
            and submission["provenance"]["annotator_id"] == earlier["provenance"]["annotator_id"],
            "PREDECESSOR",
            "Keep the same annotator and submission ID, and increment the linked version once.",
        )
        earlier_exposure = earlier["provenance"]["exposure"]
        require(
            earlier_exposure not in {"machine_seen", "synthetic"}
            or submission["provenance"]["exposure"] == earlier_exposure,
            "EXPOSURE",
            "A correction cannot undo recorded machine exposure or synthetic provenance.",
        )
    binding = {
        "pack_id": pack["pack_id"],
        "pack_sha256": sha(encoded(pack)),
        "submission_sha256": sha(data),
        "validation_scope": "format_and_coordinates_only",
    }
    result = write_bundle(
        output,
        "submission",
        {"submission.json": data, "binding.json": encoded(binding)},
        document_roots,
    )
    return {**result, "submission_sha256": sha(data), "version": submission["version"], **summary}


def locate(
    pack_path: Path,
    page_id: int,
    quote_path: Path,
    output: Path,
    start: int | None = None,
    document_roots: tuple[Path, ...] = (),
) -> dict:
    _, pages, _ = load_pack(pack_path, document_roots)
    require(page_id in pages, "PAGE", "Choose a page ID from pack.json.")
    quote = read_bytes(quote_path).decode("utf-8")
    require(
        bool(quote.strip()) and len(quote) <= 65536,
        "QUOTE",
        "Copy a nonempty exact quote of at most 65536 codepoints.",
    )
    page = pages[page_id]
    text = page["wikitext"]
    if start is None:
        start = text.find(quote)
        require(
            start >= 0,
            "QUOTE_NOT_FOUND",
            "Copy the exact wikitext, including spaces and line endings.",
        )
        require(
            text.find(quote, start + 1) < 0,
            "AMBIGUOUS_QUOTE",
            "This quote repeats; supply --start with the chosen codepoint position.",
        )
    require(
        0 <= start < len(text) and text[start : start + len(quote)] == quote,
        "QUOTE_NOT_FOUND",
        "Use the exact quote and its codepoint start position.",
    )
    evidence = {key: page[key] for key in ("page_id", "revision_id", "content_hash")}
    evidence.update(
        {
            "source_offset_start": start,
            "source_offset_end": start + len(quote),
            "supporting_span": quote,
            "span_hash": sha(quote.encode("utf-8")),
        }
    )
    result = write_bundle(output, "evidence", {"evidence.json": encoded(evidence)}, document_roots)
    return {
        **result,
        "page_id": page_id,
        "source_offset_start": start,
        "source_offset_end": start + len(quote),
    }


def candidate_input(path: Path, expected_sha256: str, pages: dict, scope: dict) -> tuple[dict, str]:
    require(
        SHA.fullmatch(expected_sha256) is not None,
        "CHECKSUM",
        "Supply the selected candidate artifact's SHA-256.",
    )
    data = read_bytes(path)
    require(
        sha(data) == expected_sha256,
        "CANDIDATE_CHANGED",
        "Restore the pinned candidate artifact or select a new explicit checksum.",
    )
    candidates = decode(data)
    validate_schema(
        candidates, decode(read_bytes(ROOT / "schema" / "candidate-claims.v1.schema.json"))
    )
    require(
        len(candidates["claims"]) <= 10000,
        "TOO_LARGE",
        "Compare at most 10000 candidate claims per pack.",
    )
    claims = candidates["claims"]
    require(
        len({claim["claim_id"] for claim in claims}) == len(claims)
        and candidates["summary"]["claim_count"] == len(claims)
        and candidates["summary"]["claims_by_predicate"]
        == dict(Counter(claim["predicate"] for claim in claims)),
        "CANDIDATE_ACCOUNTING",
        "Use unique claim IDs and a summary that accounts for every claim.",
    )
    sources = candidates["metadata"]["source_pages"]
    actual = {
        (page["title"], page["page_id"], page["revision_id"], page["content_hash"])
        for page in sources
    }
    expected = {
        (page["title"], page["page_id"], page["revision_id"], page["content_hash"])
        for page in pages.values()
    }
    require(
        actual == expected
        and len(actual) == len(sources)
        and candidates["metadata"]["extraction_method"] == "rules"
        and pilot.evidence_revalidates({"pages": list(pages.values())}, candidates),
        "CANDIDATE_SOURCE",
        "Use pending rules-only candidates from these exact source revisions and spans.",
    )
    for claim in claims:
        predicates = (
            scope["relationship_predicates"] if "object" in claim else scope["scalar_predicates"]
        )
        require(
            claim["claim_type"] == ("relationship" if "object" in claim else "property")
            and claim["predicate"] in predicates
            and claim["subject"]["type"] in scope["entity_types"]
            and ("object" not in claim or claim["object"]["type"] in scope["entity_types"])
            and set(claim["qualifiers"]) <= set(scope["qualifier_keys"])
            and all(isinstance(value, str) for value in claim["qualifiers"].values()),
            "CANDIDATE_SCOPE",
            "Use candidates in this pack's frozen pilot vocabulary.",
        )
    return candidates, sha(data)


def pair_details(annotation: dict, candidate: dict) -> dict:
    if "object" in annotation:
        value = {
            "annotation": annotation["object"],
            "candidate": candidate.get("object"),
            "exact_literal": candidate.get("object")
            == {"type": annotation["object"]["type"], "id": annotation["object"]["literal"]},
        }
    else:
        value = {
            "annotation": annotation["value_literal"],
            "candidate": candidate.get("value"),
            "exact_literal": annotation["value_literal"] == candidate.get("value"),
        }
    candidate_value = candidate.get("value", "")
    # This only exposes a literal suffix; it is not unit conversion or a parser
    # that treats differently written measurements as equivalent.
    unit = next(
        (
            suffix
            for suffix in ("tấn/ha", "tạ/ha", "kg/ha", "ngày", "cm")
            if candidate_value.endswith(" " + suffix)
        ),
        None,
    )
    qualifiers = {}
    for key in sorted(set(annotation["qualifiers"]) | set(candidate["qualifiers"])):
        annotated = annotation["qualifiers"].get(key, {"state": "uncertain", "literal": None})
        machine = candidate["qualifiers"].get(key)
        if annotated["state"] == "uncertain":
            state = "unresolved"
        elif annotated["state"] == "not_stated":
            state = "both_not_stated" if machine is None else "candidate_only"
        elif machine is None:
            state = "candidate_missing"
        else:
            state = "exact_literal" if annotated["literal"] == machine else "different_literal"
        qualifiers[key] = {"annotation": annotated, "candidate": machine, "comparison": state}
    evidence = annotation["evidence"]
    coordinates = (
        evidence["page_id"],
        evidence["revision_id"],
        evidence["content_hash"],
        evidence["source_offset_start"],
        evidence["source_offset_end"],
        evidence["span_hash"],
    )
    exact_span = any(
        item["location_type"] == "source_span"
        and coordinates
        == (
            item["page_id"],
            item["revision_id"],
            item["content_hash"],
            item["source_offset_start"],
            item["source_offset_end"],
            item["span_hash"],
        )
        for item in candidate["evidence"]
    )
    return {
        "target_or_value": value,
        "unit": {
            "annotation": annotation["unit"],
            "candidate_literal_suffix": unit,
            "exact_literal": annotation["unit"]["state"] == "stated"
            and annotation["unit"]["literal"] == unit,
            "candidate_unit_field": "not_separately_represented",
        },
        "qualifiers": qualifiers,
        "evidence": {
            "exact_span": exact_span,
            "semantic_support": "not_judged",
            "candidate_locations": [item["location_type"] for item in candidate["evidence"]],
        },
    }


def discrepancies(submission: dict, candidates: dict) -> list[dict]:
    buckets: dict[tuple, dict] = defaultdict(lambda: {"annotations": [], "candidates": []})
    groups = []
    for annotation in sorted(submission["annotations"], key=lambda item: item["annotation_id"]):
        unresolved = (
            annotation["judgment"] != "supported"
            or annotation["subject"]["identity_status"] != "resolved"
            or ("object" in annotation and annotation["object"]["identity_status"] != "resolved")
        )
        if annotation["outside_scope"] or unresolved:
            groups.append(
                {
                    "status": (
                        "outside_scope" if annotation["outside_scope"] else "unresolved_annotation"
                    ),
                    "annotations": [annotation],
                    "candidates": [],
                }
            )
            continue
        evidence = annotation["evidence"]
        key = (
            evidence["page_id"],
            evidence["revision_id"],
            evidence["content_hash"],
            annotation["subject"]["type"],
            annotation["subject"]["literal"],
            annotation["predicate"],
        )
        buckets[key]["annotations"].append(annotation)
    for candidate in sorted(candidates["claims"], key=lambda item: item["claim_id"]):
        sources = {
            (item["page_id"], item["revision_id"], item["content_hash"])
            for item in candidate["evidence"]
        }
        if len(sources) != 1:
            groups.append(
                {
                    "status": "unresolved_candidate_sources",
                    "annotations": [],
                    "candidates": [candidate],
                }
            )
            continue
        key = (
            *next(iter(sources)),
            candidate["subject"]["type"],
            candidate["subject"]["id"],
            candidate["predicate"],
        )
        buckets[key]["candidates"].append(candidate)
    for key in sorted(buckets):
        group = buckets[key]
        annotations, claims = group["annotations"], group["candidates"]
        if not annotations:
            status = "unmatched_candidates"
        elif not claims:
            status = "unmatched_annotations"
        elif len(annotations) == len(claims) == 1:
            status = "possible_pair"
            group["comparison"] = pair_details(annotations[0], claims[0])
        else:
            status = "ambiguous"
        groups.append({"status": status, **group})
    for index, group in enumerate(groups, start=1):
        group["group_id"] = f"G{index:05d}"
        group["annotation_ids"] = [item["annotation_id"] for item in group["annotations"]]
        group["candidate_ids"] = [item["claim_id"] for item in group["candidates"]]
        group["human_decision"] = None
    return groups


def compare(
    pack_path: Path,
    retained_path: Path,
    submission_sha256: str,
    candidate_path: Path,
    candidate_sha256: str,
    output: Path,
    document_roots: tuple[Path, ...] = (),
) -> dict:
    pack, pages, _ = load_pack(pack_path, document_roots)
    # Retention and its external checksum are checked before machine output is read.
    submission, receipt = load_retained(
        retained_path, submission_sha256, pack, pages, document_roots
    )
    candidates, candidate_hash = candidate_input(
        candidate_path, candidate_sha256, pages, pack["scope"]
    )
    groups = discrepancies(submission, candidates)
    require(
        sum(len(group["annotations"]) for group in groups) == len(submission["annotations"])
        and sum(len(group["candidates"]) for group in groups) == len(candidates["claims"]),
        "ACCOUNTING",
        "Every item must belong to one discrepancy group.",
    )
    counts = {
        "annotations": len(submission["annotations"]),
        "candidates": len(candidates["claims"]),
        "groups_by_status": dict(sorted(Counter(group["status"] for group in groups).items())),
        "page_reviews": dict(
            sorted(Counter(item["status"] for item in submission["page_reviews"]).items())
        ),
    }
    report = {
        "schema_version": VERSION,
        "kind": "development_discrepancies",
        "split": "development",
        "pack_id": pack["pack_id"],
        "submission_sha256": submission_sha256,
        "retention_receipt_sha256": sha(encoded(receipt)),
        "candidate_sha256": candidate_hash,
        "provenance": submission["provenance"],
        "provenance_verification": "not_established",
        "page_reviews": submission["page_reviews"],
        "counts": counts,
        "groups": groups,
        "limitations": [
            "Development practice only; no scientific scores or denominator.",
            "Exposure and identity are reported, not authenticated or independently confirmed.",
            "Possible pairs are not true positives; unmatched items are not error judgments.",
            "Literal and coordinate agreement do not establish semantic or agricultural correctness.",
            "No alias normalization, unit conversion, parent inference or automatic adjudication.",
        ],
    }
    decisions = io.StringIO(newline="")
    writer = csv.writer(decisions)
    writer.writerow(
        [
            "group_id",
            "annotation_ids",
            "candidate_ids",
            "status",
            "first_view",
            "second_view",
            "adjudicator_id",
            "decision",
            "reason",
        ]
    )
    lines = [
        "Development discrepancies. All human decisions remain blank.",
        "Possible pairs are suggestions, not correctness labels. See report.json for all source evidence.",
        "",
    ]
    for group in groups:
        writer.writerow(
            [
                group["group_id"],
                ";".join(group["annotation_ids"]),
                ";".join(group["candidate_ids"]),
                group["status"],
                "",
                "",
                "",
                "",
                "",
            ]
        )
        lines.append(
            f"{group['group_id']} {group['status']}: {len(group['annotations'])} annotation(s), {len(group['candidates'])} candidate(s)"
        )
        if "comparison" in group:
            lines.append(json.dumps(group["comparison"], ensure_ascii=False, sort_keys=True))
    result = write_bundle(
        output,
        "discrepancies",
        {
            "report.json": encoded(report),
            "discrepancies.txt": ("\n".join(lines) + "\n").encode("utf-8"),
            "decisions.csv": decisions.getvalue().encode("utf-8"),
        },
        document_roots,
    )
    return {**result, **counts, "scientific_scores": "not_computed", "human_decisions": 0}


class Parser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, allow_abbrev=False, **kwargs)

    def error(self, message: str) -> None:
        raise AnnotationError(
            "ARGUMENTS",
            "Check the command's --help; supply its required flags and no unknown flags.",
        )


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        description="Development source annotation. No scoring, models or held-out mode.",
        epilog="Next: prepare --help, then validate --help. Retain source-first work before compare.",
    )
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    preparation = commands.add_parser(
        "prepare",
        help="Create a source-only blank development pack.",
        epilog="Example: prepare --input <dev.json> --manifest <manifest.json> --output <private-root/pack>",
    )
    preparation.add_argument("--protocol", type=Path, default=PROTOCOL)
    preparation.add_argument("--manifest", type=Path, required=True)
    preparation.add_argument("--input", type=Path, required=True)
    preparation.add_argument("--output", type=Path, required=True)
    validation = commands.add_parser(
        "validate",
        help="Check a working submission without retaining or approving it.",
        epilog="Example: validate --pack <pack> --submission <working.json>",
    )
    retention = commands.add_parser(
        "retain",
        help="Preserve submitted bytes and record a checksum receipt.",
        epilog="Example: retain --pack <pack> --submission <working.json> --output <private-root/version-1>",
    )
    locating = commands.add_parser(
        "locate",
        help="Find your own quote in a pinned source; write private coordinates.",
        epilog="Example: locate --pack <pack> --page-id <id> --quote-file <quote.txt> --output <private-root/quote-1>",
    )
    comparison = commands.add_parser(
        "compare",
        help="Propose discrepancies from a retained submission, without scoring.",
        epilog="Example: compare --pack <pack> --retained <version-1> --submission-sha256 <sha256> --candidates <candidates.json> --candidate-sha256 <sha256> --output <private-root/comparison-1>",
    )
    for command in (preparation, validation, retention, locating, comparison):
        command.add_argument(
            "--document-root",
            type=Path,
            action="append",
            default=[],
            help="Additional hosted root to refuse; repeat for each document root.",
        )
    for command in (validation, retention, locating, comparison):
        command.add_argument("--pack", type=Path, required=True)
    for command in (validation, retention):
        command.add_argument("--submission", type=Path, required=True)
    for command in (retention, locating, comparison):
        command.add_argument("--output", type=Path, required=True)
    retention.add_argument("--previous", type=Path)
    retention.add_argument("--previous-sha256")
    locating.add_argument("--page-id", type=int, required=True)
    locating.add_argument("--quote-file", type=Path, required=True)
    locating.add_argument(
        "--start", type=int, help="Explicit codepoint start for a repeated quote."
    )
    comparison.add_argument("--retained", type=Path, required=True)
    comparison.add_argument("--submission-sha256", required=True)
    comparison.add_argument("--candidates", type=Path, required=True)
    comparison.add_argument("--candidate-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        roots = tuple(args.document_root)
        if args.command == "prepare":
            result = prepare(args.protocol, args.manifest, args.input, args.output, roots)
        elif args.command == "validate":
            result = validate(args.pack, args.submission, roots)
        elif args.command == "retain":
            result = retain(
                args.pack, args.submission, args.output, args.previous, args.previous_sha256, roots
            )
        elif args.command == "locate":
            result = locate(
                args.pack, args.page_id, args.quote_file, args.output, args.start, roots
            )
        else:
            result = compare(
                args.pack,
                args.retained,
                args.submission_sha256,
                args.candidates,
                args.candidate_sha256,
                args.output,
                roots,
            )
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 0
    except AnnotationError as error:
        print(json.dumps(error.result, sort_keys=True, ensure_ascii=True), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        code = "FILE_ACCESS" if isinstance(error, OSError) else "FORMAT"
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": code,
                    "message": "Check file access and bundle format; keep originals and use a new private destination.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
