"""Tests for the deterministic private-dump rice corpus builder."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXTENSION_ROOT / "tools"))

import rice_corpus  # noqa: E402


def sample_page(title: str, page_id: int, revision_id: int, body: str) -> dict:
    return {
        "title": title,
        "url": f"https://wikicrop.local/wiki/{title.replace(' ', '_')}",
        "kind": "variety",
        "crop": "Lúa",
        "page_id": page_id,
        "revision_id": revision_id,
        "content_hash": f"{page_id:064x}"[-64:],
        "wikitext": body,
        "text": body,
    }


class TestLinkDiscovery(unittest.TestCase):
    def test_only_unique_mainspace_links_under_matching_h2(self) -> None:
        text = """== Giới thiệu ==
[[Lúa ngoài mục]]
== Danh sách giống lúa ==
[[Lúa OM1]] [[Lúa_OM2|OM2]] [[File:Ảnh.jpg]] [[Lúa OM1]]
=== Nhóm phụ ===
[[Lúa OM3]]
== Tham khảo ==
[[Lúa ngoài mục 2]]
"""
        self.assertEqual(
            rice_corpus.discover_variety_links(text),
            ["Lúa OM1", "Lúa OM2", "Lúa OM3"],
        )

    def test_nonmatching_sections_produce_no_candidates(self) -> None:
        self.assertEqual(
            rice_corpus.discover_variety_links("== Giới thiệu ==\n[[Lúa OM1]]"),
            [],
        )


class TestSelection(unittest.TestCase):
    def test_stratified_selection_and_split_are_deterministic(self) -> None:
        entries = []
        for index in range(8):
            entries.append(
                {
                    "requested_title": f"Lúa OM{index}",
                    "revision_id": 100 + index,
                    "content_hash": f"{index + 1:064x}",
                    "status": "resolved",
                    "stratum": "InfoCropPlant:short" if index < 4 else "none:long",
                    "selected": False,
                    "split": None,
                }
            )

        first = rice_corpus.allocate_stratified_sample(entries, 4, "seed")
        second = rice_corpus.allocate_stratified_sample(entries, 4, "seed")
        self.assertEqual(
            [entry["requested_title"] for entry in first],
            [entry["requested_title"] for entry in second],
        )
        self.assertEqual(
            {stratum: sum(entry["stratum"] == stratum for entry in first)
             for stratum in {entry["stratum"] for entry in entries}},
            {"InfoCropPlant:short": 2, "none:long": 2},
        )

        rice_corpus.assign_split(first[:2], "dev")
        rice_corpus.assign_split(first[2:], "test")
        self.assertEqual([entry["split"] for entry in first], ["dev", "dev", "test", "test"])

    def test_sample_larger_than_eligible_population_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "không đủ mẫu"):
            rice_corpus.allocate_stratified_sample([], 1, "seed")


class TestCorpusBuild(unittest.TestCase):
    def test_build_writes_manifest_and_private_split_inputs(self) -> None:
        source = sample_page(
            "Lúa",
            62,
            875,
            "== Danh sách giống lúa ==\n[[Lúa OM1]] [[Lúa OM2]] [[Lúa OM49]]",
        )
        source["kind"] = "species"
        resolved = [
            sample_page("Lúa OM1", 101, 201, "{{InfoCropPlant}}\nNội dung 1"),
            sample_page("Lúa OM2", 102, 202, "{{InfoCropPlant}}\nNội dung 2"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump.sql"
            dump.write_text("synthetic", encoding="utf-8")
            output = root / "private-output"
            with mock.patch.object(
                rice_corpus,
                "extract_pages",
                side_effect=[[source], resolved],
            ):
                manifest = rice_corpus.build_corpus(
                    dump,
                    output,
                    sample_size=2,
                    dev_size=1,
                    seed="fixed",
                )

            self.assertEqual(manifest["counts"]["discovered"], 3)
            self.assertEqual(
                manifest["counts"]["statuses"],
                {"resolved": 2, "missing": 1},
            )
            self.assertEqual(manifest["counts"]["selected"], 2)
            self.assertEqual(
                manifest["selection"]["method"],
                "development-first-proportional-stratified-sha256-v2",
            )

            stored = json.loads(
                (output / "rice-corpus-manifest.v1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored, manifest)
            try:
                import jsonschema
            except ImportError:
                jsonschema = None
            if jsonschema is not None:
                schema = json.loads(
                    (EXTENSION_ROOT / "schema" / "corpus-manifest.v1.schema.json")
                    .read_text(encoding="utf-8")
                )
                jsonschema.validate(stored, schema)
            for split in ("dev", "test"):
                raw = json.loads(
                    (output / f"raw-data.{split}.v1.json").read_text(encoding="utf-8")
                )
                self.assertEqual(raw["schema_version"], "1.0")
                self.assertEqual(raw["page_count"], 2)
                self.assertEqual(raw["pages"][0]["title"], "Lúa")

    def test_previously_observed_page_cannot_enter_heldout_split(self) -> None:
        source = sample_page(
            "Lúa",
            62,
            875,
            "== Danh sách giống lúa ==\n"
            "[[Lúa OM1]] [[Lúa OM2]] [[Lúa OM3]] [[Lúa OM4]] [[Lúa OM5]]",
        )
        source["kind"] = "species"
        resolved = [
            sample_page(
                f"Lúa OM{index}",
                100 + index,
                200 + index,
                f"{{{{InfoCropPlant}}}}\nNội dung {index}",
            )
            for index in range(1, 6)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump.sql"
            dump.write_text("synthetic", encoding="utf-8")
            with mock.patch.object(
                rice_corpus,
                "extract_pages",
                side_effect=[[source], resolved],
            ):
                manifest = rice_corpus.build_corpus(
                    dump,
                    root / "private-output",
                    sample_size=4,
                    dev_size=2,
                    seed="fixed",
                    previously_observed_titles=("Lúa OM1",),
                )

        observed = next(
            page for page in manifest["pages"]
            if page["requested_title"] == "Lúa OM1"
        )
        self.assertNotEqual(observed["split"], "test")
        self.assertFalse(observed["eligible_for_heldout"])
        self.assertIn("đã được xem", observed["heldout_exclusion_reason"])
        self.assertEqual(
            sum(page["split"] == "test" for page in manifest["pages"]),
            2,
        )

    def test_repository_output_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ngoài repository"):
            rice_corpus.ensure_output_outside_repository(
                EXTENSION_ROOT / "generated"
            )


if __name__ == "__main__":
    unittest.main()
