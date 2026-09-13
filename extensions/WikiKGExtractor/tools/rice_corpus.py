"""Build a reproducible rice-link corpus and frozen development/test split.

The tool reads the private WikiCrop SQL dump but writes outputs only to the
explicit directory supplied by the caller. The manifest contains hashes and
metadata. Raw page text stays in separate private development/test inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from real_data_smoke import extract_pages


MANIFEST_SCHEMA_VERSION = "1.0"
RAW_INPUT_SCHEMA_VERSION = "1.0"
DEFAULT_SOURCE_TITLE = "Lúa"
DEFAULT_SOURCE_REVISION = 875
DEFAULT_SEED = "wikicrop-rice-2026-09-12"
# Pages inspected in the five-page smoke or the first development run before
# the held-out protocol was corrected on 2026-09-13.
DEFAULT_PREVIOUSLY_OBSERVED_TITLES = (
    "Lúa Nàng Hương",
    "Lúa OM29",
    "Lúa OM345",
    "Lúa OM380",
    "Lúa OM5451",
    "Lúa OM6162",
    "Lúa OMCS2000",
    "Lúa RVT",
    "Lúa SR114",
    "Lúa ST24",
    "Lúa ST25",
    "Lúa VND95-20",
    "Lúa VNR-20",
)

HEADING_RE = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.MULTILINE)
LINK_RE = re.compile(r"\[\[\s*([^\]|#]+)")
REDIRECT_RE = re.compile(
    r"^\s*#redirect\s*\[\[\s*([^\]|#]+)",
    re.IGNORECASE,
)


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )


def normalized_key(text: str) -> str:
    value = strip_accents(text).casefold().replace("_", " ")
    return re.sub(r"\s+", " ", value).strip()


def database_title(text: str) -> str:
    value = text.strip().lstrip(":").replace(" ", "_")
    return re.sub(r"_+", "_", value)


def display_title(text: str) -> str:
    return re.sub(r"_+", " ", text).strip()


def is_variety_heading(text: str) -> bool:
    key = normalized_key(re.sub(r"<[^>]+>", "", text))
    return "danh sach" in key or "giong" in key


def discover_variety_links(wikitext: str) -> list[str]:
    """Return unique main-namespace links under matching H2 sections."""
    headings = list(HEADING_RE.finditer(wikitext))
    links: list[str] = []
    seen: set[str] = set()

    for index, heading in enumerate(headings):
        level = len(heading.group(1))
        if level != 2 or not is_variety_heading(heading.group(2)):
            continue

        end = len(wikitext)
        for next_heading in headings[index + 1 :]:
            if len(next_heading.group(1)) <= level:
                end = next_heading.start()
                break

        for match in LINK_RE.finditer(wikitext, heading.end(), end):
            title = match.group(1).strip().lstrip(":")
            if not title or ":" in title:
                continue
            key = normalized_key(title)
            if key in seen:
                continue
            seen.add(key)
            links.append(display_title(title))

    return links


def template_family(wikitext: str) -> str:
    for name in ("InfoCropPlant", "InfoPlant1Giong", "InfoPlant1", "InfoPlant"):
        if re.search(r"\{\{\s*" + re.escape(name) + r"\b", wikitext, re.I):
            return name
    return "none"


def length_band(source_chars: int) -> str:
    if source_chars < 5000:
        return "short"
    if source_chars < 15000:
        return "medium"
    return "long"


def selection_hash(entry: dict[str, Any], seed: str) -> str:
    material = "\0".join(
        (
            seed,
            str(entry["requested_title"]),
            str(entry["revision_id"]),
            str(entry["content_hash"]),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
        raise ValueError(
            "Thư mục kết xuất corpus phải nằm ngoài repository để tránh lộ dữ liệu."
        )


def allocate_stratified_sample(
    entries: list[dict[str, Any]],
    sample_size: int,
    seed: str,
) -> list[dict[str, Any]]:
    eligible = [entry for entry in entries if entry.get("status") == "resolved"]
    if sample_size < 1:
        raise ValueError("Cỡ mẫu phải lớn hơn 0.")
    if len(eligible) < sample_size:
        raise ValueError(
            f"Chỉ có {len(eligible)} trang hợp lệ, không đủ mẫu {sample_size}."
        )

    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in eligible:
        entry["selection_hash"] = selection_hash(entry, seed)
        strata[str(entry["stratum"])].append(entry)

    total = len(eligible)
    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for stratum, items in strata.items():
        exact = sample_size * len(items) / total
        quotas[stratum] = math.floor(exact)
        remainders.append((exact - quotas[stratum], stratum))

    remaining = sample_size - sum(quotas.values())
    for _, stratum in sorted(remainders, key=lambda item: (-item[0], item[1])):
        if remaining == 0:
            break
        if quotas[stratum] < len(strata[stratum]):
            quotas[stratum] += 1
            remaining -= 1

    selected: list[dict[str, Any]] = []
    for stratum in sorted(strata):
        ordered = sorted(strata[stratum], key=lambda entry: entry["selection_hash"])
        selected.extend(ordered[: quotas[stratum]])
    return sorted(selected, key=lambda entry: entry["selection_hash"])


def assign_split(selected: list[dict[str, Any]], split: str) -> None:
    for entry in selected:
        entry["selected"] = True
        entry["split"] = split


def page_record(
    requested_title: str,
    page: dict[str, Any],
    discovery_index: int,
) -> dict[str, Any]:
    source_chars = len(page["wikitext"])
    family = template_family(page["wikitext"])
    band = length_band(source_chars)
    return {
        "discovery_index": discovery_index,
        "requested_title": requested_title,
        "resolved_title": page["title"],
        "status": "resolved",
        "page_id": page["page_id"],
        "revision_id": page["revision_id"],
        "content_hash": page["content_hash"],
        "content_model": "wikitext",
        "template_family": family,
        "source_chars": source_chars,
        "length_band": band,
        "stratum": f"{family}:{band}",
        "selected": False,
        "split": None,
    }


def raw_page(page: dict[str, Any], *, kind: str, crop: str) -> dict[str, Any]:
    return {
        "title": page["title"],
        "url": page["url"],
        "kind": kind,
        "crop": crop,
        "page_id": page["page_id"],
        "revision_id": page["revision_id"],
        "content_hash": page["content_hash"],
        "wikitext": page["wikitext"],
        "text": page["text"],
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_corpus(
    dump_path: Path,
    output_dir: Path,
    *,
    sample_size: int,
    dev_size: int,
    seed: str,
    previously_observed_titles: tuple[str, ...] = DEFAULT_PREVIOUSLY_OBSERVED_TITLES,
) -> dict[str, Any]:
    ensure_output_outside_repository(output_dir)
    test_size = sample_size - dev_size
    if dev_size < 1 or test_size < 1:
        raise ValueError("Cỡ tập phát triển và held-out đều phải lớn hơn 0.")

    source = extract_pages(
        dump_path,
        [(database_title(DEFAULT_SOURCE_TITLE), "species", DEFAULT_SOURCE_TITLE)],
    )[0]
    if source["revision_id"] != DEFAULT_SOURCE_REVISION:
        raise ValueError(
            f"Revision nguồn là {source['revision_id']}, cần {DEFAULT_SOURCE_REVISION}."
        )

    requested_titles = discover_variety_links(source["wikitext"])
    targets = [
        (database_title(title), "variety", DEFAULT_SOURCE_TITLE)
        for title in requested_titles
    ]
    resolved_pages = extract_pages(
        dump_path,
        targets,
        allow_missing=True,
    )
    by_title = {
        normalized_key(page["title"]): page
        for page in resolved_pages
    }

    entries: list[dict[str, Any]] = []
    for index, requested_title in enumerate(requested_titles):
        page = by_title.get(normalized_key(requested_title))
        if page is None:
            entries.append(
                {
                    "discovery_index": index,
                    "requested_title": requested_title,
                    "resolved_title": None,
                    "status": "missing",
                    "reason": "Trang không tồn tại trong bản dump được cấp.",
                    "selected": False,
                    "split": None,
                }
            )
            continue

        redirect = REDIRECT_RE.match(page["wikitext"])
        if redirect:
            entries.append(
                {
                    "discovery_index": index,
                    "requested_title": requested_title,
                    "resolved_title": display_title(redirect.group(1)),
                    "status": "redirect_unresolved",
                    "reason": "Cần MediaWiki Title resolution trước khi lấy mẫu.",
                    "page_id": page["page_id"],
                    "revision_id": page["revision_id"],
                    "content_hash": page["content_hash"],
                    "selected": False,
                    "split": None,
                }
            )
            continue

        entries.append(page_record(requested_title, page, index))

    seen_page_ids: set[int] = set()
    for entry in entries:
        if entry["status"] != "resolved":
            continue
        page_id = int(entry["page_id"])
        if page_id in seen_page_ids:
            entry["status"] = "duplicate_target"
            entry["reason"] = "Nhiều liên kết cùng trỏ đến một page_id."
            continue
        seen_page_ids.add(page_id)

    observed_keys = {
        normalized_key(title) for title in previously_observed_titles
    }
    for entry in entries:
        resolved = entry["status"] == "resolved"
        previously_observed = normalized_key(entry["requested_title"]) in observed_keys
        entry["eligible_for_development"] = resolved
        entry["eligible_for_heldout"] = resolved and not previously_observed
        if not resolved:
            entry["heldout_exclusion_reason"] = entry.get(
                "reason",
                "Trang không đủ điều kiện chọn mẫu.",
            )
        elif previously_observed:
            entry["heldout_exclusion_reason"] = (
                "Trang đã được xem hoặc dùng để phát triển luật trước khi đóng băng held-out."
            )
        else:
            entry["heldout_exclusion_reason"] = None

    development = allocate_stratified_sample(entries, dev_size, seed)
    assign_split(development, "dev")
    for entry in development:
        if entry["eligible_for_heldout"]:
            entry["eligible_for_heldout"] = False
            entry["heldout_exclusion_reason"] = (
                "Trang đã được chọn cho tập phát triển trước khi lấy mẫu held-out."
            )

    heldout_pool = [entry for entry in entries if entry["eligible_for_heldout"]]
    heldout = allocate_stratified_sample(heldout_pool, test_size, seed)
    assign_split(heldout, "test")
    selected = development + heldout

    status_counts: dict[str, int] = {}
    for entry in entries:
        status = str(entry["status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus": "wikicrop-rice-links-lua-revision-875",
        "source": {
            "title": source["title"],
            "page_id": source["page_id"],
            "revision_id": source["revision_id"],
            "content_hash": source["content_hash"],
            "discovery_heading_rule": "H2 contains Danh sách or Giống",
        },
        "selection": {
            "method": "development-first-proportional-stratified-sha256-v2",
            "strata": "template_family:length_band",
            "length_bands": {
                "short": "<5000 characters",
                "medium": "5000-14999 characters",
                "long": ">=15000 characters",
            },
            "seed": seed,
            "sample_size": sample_size,
            "dev_size": dev_size,
            "test_size": test_size,
            "heldout_exclusion_policy": (
                "Exclude pages inspected before the freeze and pages selected for development."
            ),
            "previously_observed_titles": sorted(previously_observed_titles),
        },
        "counts": {
            "discovered": len(entries),
            "statuses": status_counts,
            "selected": len(selected),
        },
        "pages": entries,
    }

    pages_by_id = {int(page["page_id"]): page for page in resolved_pages}
    split_inputs: dict[str, list[dict[str, Any]]] = {"dev": [], "test": []}
    for split in split_inputs:
        split_inputs[split].append(
            raw_page(source, kind="species", crop=DEFAULT_SOURCE_TITLE)
        )
    for entry in development + heldout:
        page = pages_by_id[int(entry["page_id"])]
        split_inputs[str(entry["split"])].append(
            raw_page(page, kind="variety", crop=DEFAULT_SOURCE_TITLE)
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "rice-corpus-manifest.v1.json", manifest)
    for split, pages in split_inputs.items():
        write_json(
            output_dir / f"raw-data.{split}.v1.json",
            {
                "schema_version": RAW_INPUT_SCHEMA_VERSION,
                "source_url": "private WikiCrop dump",
                "page_count": len(pages),
                "error_count": 0,
                "pages": pages,
                "errors": [],
            },
        )

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tạo manifest corpus lúa và tập dev/test từ dump WikiCrop.",
        epilog=(
            "Ví dụ: python3 rice_corpus.py --dump <dump.sql> "
            "--output-dir <private-output-dir>"
        ),
    )
    parser.add_argument("--dump", required=True, help="Đường dẫn dump MySQL riêng tư")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Thư mục riêng tư ngoài repository",
    )
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--dev-size", type=int, default=10)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dump_path = Path(args.dump).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not dump_path.is_file():
        print(f"Không tìm thấy dump: {dump_path}", file=sys.stderr)
        return 2

    try:
        manifest = build_corpus(
            dump_path,
            output_dir,
            sample_size=args.sample_size,
            dev_size=args.dev_size,
            seed=args.seed,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Không tạo được corpus: {error}", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "manifest": str(output_dir / "rice-corpus-manifest.v1.json"),
                "counts": manifest["counts"],
                "selection": manifest["selection"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
