"""Unit tests cho kg_worker.py (bộ trích xuất knowledge graph bằng luật).

Chạy từ thư mục gốc repo:
    python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python \
        -p 'test_*.py'
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXTENSION_ROOT / "bin"))

import kg_worker  # noqa: E402

FIXTURES = EXTENSION_ROOT / "tests" / "fixtures"
RAW_DATA_RULES = FIXTURES / "raw-data.rules.json"
KG_WORKER = EXTENSION_ROOT / "bin" / "kg_worker.py"

HASH_LUA = "ab" * 32
HASH_OM5451 = "cd" * 32


def make_entity(
    name: str,
    crop: str,
    *,
    kind: str = "variety",
    page_title: str | None = None,
    props: dict[str, str] | None = None,
    resistant: list[dict[str, str]] | None = None,
    susceptible: list[dict[str, str]] | None = None,
    pests: list[str] | None = None,
) -> dict:
    return {
        "page_title": page_title or f"Lúa_{name}",
        "entity": name,
        "kind": kind,
        "crop": crop,
        "properties": props or {},
        "taxonomy": {},
        "resistant_to": resistant or [],
        "susceptible_to": susceptible or [],
        "pests": pests or [],
    }


def build_variety_graph(
    entities: list[dict], default_crop: str = "Lúa"
) -> tuple[kg_worker.GraphBuilder, list[str]]:
    return kg_worker.build_graph(entities, [], default_crop)


class TestShortVarietyName(unittest.TestCase):
    def test_underscore_title_normalized(self) -> None:
        # Lỗi cũ: 'Lúa_OM5451' không bị cắt tiền tố vì gạch dưới không phải
        # khoảng trắng. Kết quả cũ là 'Lúa_OM5451', đúng phải là 'OM5451'.
        self.assertEqual(kg_worker.short_variety_name("Lúa_OM5451", "Lúa"), "OM5451")

    def test_space_title_normalized(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Lúa OM5451", "Lúa"), "OM5451")

    def test_giống_prefix_stripped(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Giống Lúa ST25", "Lúa"), "ST25")

    def test_cây_prefix_stripped(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Cây lúa ST24", "Lúa"), "ST24")

    def test_species_title_kept(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Lúa", "Lúa"), "Lúa")

    def test_no_crop_prefix_kept(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("OM5451", "Lúa"), "OM5451")

    def test_multi_underscores_collapsed(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Lúa___OM5451", "Lúa"), "OM5451")

    def test_irrelevant_title_kept(self) -> None:
        title = "Kỹ thuật trồng và chăm sóc lúa"
        self.assertEqual(kg_worker.short_variety_name(title, "Lúa"), title)

    def test_empty_title(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("", "Lúa"), "")

    def test_no_crop_argument(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Lúa_OM5451"), "Lúa OM5451")

    def test_underscore_crop_name_normalized(self) -> None:
        # Cả tiêu đề lẫn tên loài có thể mang gạch dưới kiểu MediaWiki.
        self.assertEqual(kg_worker.short_variety_name("Cà_phê_Arabica", "Cà_phê"), "Arabica")

    def test_underscore_crop_with_giống_prefix(self) -> None:
        self.assertEqual(
            kg_worker.short_variety_name("Giống_cà_phê_Arabica", "Cà_phê"), "Arabica"
        )

    def test_crop_then_giống_prefix(self) -> None:
        # Tiền tố "giống" đứng sau tên loài vẫn phải được cắt ("Lúa Giống ST25").
        self.assertEqual(kg_worker.short_variety_name("Lúa Giống ST25", "Lúa"), "ST25")

    def test_underscore_crop_then_giống(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Lúa_Giống_ST25", "Lúa"), "ST25")

    def test_none_or_nonstring_inputs(self) -> None:
        self.assertEqual(kg_worker.short_variety_name(None, "Lúa"), "")
        self.assertEqual(kg_worker.short_variety_name([], "Lúa"), "")
        self.assertEqual(kg_worker.short_variety_name({"x": 1}, "Lúa"), "")

    def test_crop_normalizing_to_empty(self) -> None:
        # Loài sau khi chuẩn hóa trở thành rỗng thì bỏ qua quy tắc tiền tố.
        self.assertEqual(kg_worker.short_variety_name("Lúa ST25", "___"), "Lúa ST25")

    def test_all_underscore_title(self) -> None:
        # Toàn gạch dưới bị clean_value coi là rỗng -> trả về chuỗi rỗng.
        self.assertEqual(kg_worker.short_variety_name("___", "Lúa"), "")

    def test_prefix_only_title_falls_back_to_original(self) -> None:
        self.assertEqual(kg_worker.short_variety_name("Giống", "Lúa"), "Giống")
        self.assertEqual(kg_worker.short_variety_name("Cây", "Lúa"), "Cây")


class TestStringHelpers(unittest.TestCase):
    def test_strip_accents(self) -> None:
        self.assertEqual(kg_worker.strip_accents("Lúa đạo ôn"), "Lua dao on")

    def test_norm_key(self) -> None:
        self.assertEqual(kg_worker.norm_key("Lúa_OM5451"), "lua om5451")
        self.assertEqual(kg_worker.norm_key("  Bệnh đạo ôn  "), "benh dao on")

    def test_clean_value_unknown(self) -> None:
        for value in ("Chưa rõ", "không rõ", "N/A", "-", None):
            self.assertEqual(kg_worker.clean_value(value), "", msg=repr(value))

    def test_clean_value_keeps_text(self) -> None:
        self.assertEqual(kg_worker.clean_value(" 95-105 ngày "), "95-105 ngày")
        self.assertEqual(kg_worker.clean_value("OM5451"), "OM5451")

    def test_as_list_splits_va(self) -> None:
        self.assertEqual(kg_worker.as_list("OM1490 và IR64"), ["OM1490", "IR64"])

    def test_as_list_splits_separators(self) -> None:
        self.assertEqual(kg_worker.as_list("a; b, c\nd"), ["a", "b", "c", "d"])

    def test_as_pest_list_level(self) -> None:
        items = kg_worker.as_pest_list([{"name": "Bệnh đạo ôn", "level": "Cấp 2"}])
        self.assertEqual(items, [{"name": "Bệnh đạo ôn", "level": "Cấp 2"}])

    def test_canonical_pest(self) -> None:
        self.assertEqual(kg_worker.canonical_pest("bệnh đạo ôn"), "Bệnh đạo ôn")
        self.assertEqual(kg_worker.canonical_pest("rầy nâu"), "Rầy nâu")


class TestLoadPages(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = kg_worker.load_pages(RAW_DATA_RULES)

    def test_all_pages_loaded(self) -> None:
        self.assertEqual(len(self.pages), 4)

    def test_provenance_preserved(self) -> None:
        lua = next(p for p in self.pages if p["title"] == "Lúa")
        self.assertEqual(lua["page_id"], 100)
        self.assertEqual(lua["revision_id"], 875)
        self.assertEqual(lua["content_hash"], HASH_LUA)

    def test_page_without_safe_kind_is_excluded(self) -> None:
        self.assertNotIn(
            "Kỹ_thuật_trồng_và_chăm_sóc_lúa",
            {page["title"] for page in self.pages},
        )

    def test_excluded_page_title_is_reported(self) -> None:
        skipped: list[str] = []
        kg_worker.load_pages(RAW_DATA_RULES, skipped)
        self.assertEqual(skipped, ["Kỹ_thuật_trồng_và_chăm_sóc_lúa"])

    def test_legacy_variety_kind_inferred_from_title(self) -> None:
        data = {
            "pages": [
                {
                    "title": "Lúa_OM5451",
                    "crop": "Lúa",
                    "text": "Nội dung trang.",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            pages = kg_worker.load_pages(path)
        self.assertEqual(pages[0]["kind"], "variety")

    def test_legacy_top_level_page_list_is_accepted(self) -> None:
        data = [
            {
                "title": "Lúa OM5451",
                "crop": "Lúa",
                "text": "Nội dung trang.",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            pages = kg_worker.load_pages(path)

        self.assertEqual([page["title"] for page in pages], ["Lúa OM5451"])

    def test_kind_inferred_for_species_like_title(self) -> None:
        data = {
            "pages": [
                {
                    "title": "Lúa",
                    "crop": "Lúa",
                    "text": "Nội dung trang.",
                    "page_id": 1,
                    "revision_id": 2,
                    "content_hash": "f" * 64,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            pages = kg_worker.load_pages(path)
        self.assertEqual(pages[0]["kind"], "species")

    def test_empty_text_pages_skipped(self) -> None:
        data = {
            "pages": [
                {"title": "A", "text": "  ", "crop": "Lúa", "kind": "variety"},
                {
                    "title": "B",
                    "text": "Nội dung.",
                    "crop": "Lúa",
                    "kind": "variety",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            pages = kg_worker.load_pages(path)
        self.assertEqual([p["title"] for p in pages], ["B"])

    def test_wikitext_is_authoritative_extraction_input(self) -> None:
        wikitext = """{{InfoPlant1
|Gioi=Plantae
|Bo=Poales
|Ho=Poaceae
|Chi=Oryza
}}
Lúa là cây lương thực.
"""
        data = {
            "schema_version": "1.0",
            "pages": [
                {
                    "title": "Lúa",
                    "crop": "Lúa",
                    "kind": "species",
                    "page_id": 62,
                    "revision_id": 875,
                    "content_hash": hashlib.sha256(wikitext.encode()).hexdigest(),
                    "wikitext": wikitext,
                    "text": "Lúa là cây lương thực.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            page = kg_worker.load_pages(path)[0]

        self.assertEqual(page["text"], wikitext)
        self.assertEqual(page["rendered_text"], "Lúa là cây lương thực.")
        self.assertEqual(
            kg_worker.heuristic_entity(page)["taxonomy"],
            {
                "kingdom": "Plantae",
                "taxon_order": "Poales",
                "family": "Poaceae",
                "genus": "Oryza",
            },
        )

    def test_empty_wikitext_falls_back_to_legacy_text(self) -> None:
        data = {
            "pages": [
                {
                    "title": "Lúa",
                    "crop": "Lúa",
                    "kind": "species",
                    "wikitext": "  ",
                    "text": "Nội dung cũ.",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            page = kg_worker.load_pages(path)[0]

        self.assertEqual(page["text"], "Nội dung cũ.")

    def test_unsupported_raw_schema_version_is_rejected(self) -> None:
        data = {"schema_version": "2.0", "pages": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "2.0"):
                kg_worker.load_pages(path)

    def test_versioned_input_rejects_wikitext_hash_mismatch(self) -> None:
        data = {
            "schema_version": "1.0",
            "pages": [
                {
                    "page_id": 1,
                    "revision_id": 2,
                    "title": "Lúa",
                    "wikitext": "{{InfoCrop}}",
                    "text": "Lúa",
                    "content_hash": "0" * 64,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content_hash không khớp"):
                kg_worker.load_pages(path)

    def test_versioned_input_requires_revision_identity(self) -> None:
        wikitext = "{{InfoCrop}}"
        data = {
            "schema_version": "1.0",
            "pages": [
                {
                    "page_id": 1,
                    "revision_id": None,
                    "title": "Lúa",
                    "wikitext": wikitext,
                    "text": "Lúa",
                    "content_hash": hashlib.sha256(wikitext.encode()).hexdigest(),
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "revision_id hợp lệ"):
                kg_worker.load_pages(path)


class TestHeuristicEntity(unittest.TestCase):
    def _entity(self, title: str) -> dict:
        page = next(p for p in kg_worker.load_pages(RAW_DATA_RULES) if p["title"] == title)
        return kg_worker.heuristic_entity(page)

    def test_variety_entity_short_name_underscore_bug(self) -> None:
        # Lỗi cũ: entity của 'Lúa_OM5451' là 'Lúa_OM5451' chứ không phải 'OM5451'.
        entity = self._entity("Lúa_OM5451")
        self.assertEqual(entity["entity"], "OM5451")

    def test_variety_properties(self) -> None:
        entity = self._entity("Lúa_OM5451")
        self.assertEqual(entity["properties"]["growth_duration"], "95-105 ngày")
        self.assertEqual(entity["properties"]["plant_height"], "100-110 cm")
        self.assertEqual(entity["properties"]["yield_amount"], "6-8 tấn/ha")

    def test_variety_crossbred_from(self) -> None:
        entity = self._entity("Lúa_OM5451")
        self.assertEqual(entity["properties"]["crossbred_from"], "OM1490 / IR64")

    def test_variety_resistance_and_susceptibility(self) -> None:
        entity = self._entity("Lúa_OM5451")
        resistant = {item["name"] for item in entity["resistant_to"]}
        susceptible = {item["name"] for item in entity["susceptible_to"]}
        self.assertEqual(resistant, {"Bệnh đạo ôn", "Rầy nâu"})
        self.assertEqual(susceptible, {"Bệnh bạc lá"})
        summary = entity["properties"]["pest_resistance"]
        self.assertIn("Kháng Bệnh đạo ôn", summary)
        self.assertIn("nhiễm Bệnh bạc lá", summary)

    def test_species_entity_and_taxonomy(self) -> None:
        entity = self._entity("Lúa")
        self.assertEqual(entity["entity"], "Lúa")
        self.assertEqual(entity["taxonomy"]["scientific_name"], "Oryza sativa")
        self.assertEqual(entity["taxonomy"]["genus"], "Oryza")
        self.assertEqual(entity["taxonomy"]["family"], "Poaceae")

    def test_species_taxonomy_from_wikicrop_template(self) -> None:
        page = {
            "title": "Lúa",
            "kind": "species",
            "crop": "Lúa",
            "text": (
                "{{InfoPlant1\n | TenCayTrong = Lúa\n"
                "|Gioi=Plantae|Bo=Poales|Ho=Poaceae|Chi=Oryza}}\n"
                "Lúa là cây lương thực quan trọng của Việt Nam."
            ),
        }
        taxonomy = kg_worker.heuristic_entity(page)["taxonomy"]
        self.assertEqual(
            taxonomy,
            {
                "kingdom": "Plantae",
                "taxon_order": "Poales",
                "family": "Poaceae",
                "genus": "Oryza",
            },
        )

    def test_variety_template_does_not_override_crop_taxonomy(self) -> None:
        page = {
            "title": "Lúa_OM5451",
            "kind": "variety",
            "crop": "Lúa",
            "text": (
                "{{InfoCropPlant|TenLoai=Oryza sativa|Bo=Poaceae|Ho=Poaceae}}\n"
                "Giống lúa OM5451 có thời gian sinh trưởng 95-105 ngày."
            ),
        }
        self.assertEqual(kg_worker.heuristic_entity(page)["taxonomy"], {})

    def test_cao_cay_height_label(self) -> None:
        page = {
            "title": "Lúa_X",
            "kind": "variety",
            "crop": "Lúa",
            "text": "Cao cây: 100-110 cm. Giống lúa X sinh trưởng ổn định.",
        }
        entity = kg_worker.heuristic_entity(page)
        self.assertEqual(entity["properties"]["plant_height"], "100-110 cm")

    def test_template_markup_excluded_from_embedding_text(self) -> None:
        page = {
            "title": "Lúa_X",
            "kind": "variety",
            "crop": "Lúa",
            "text": (
                "{{InfoCropPlant|TenCayTrong=Giống Lúa X|TenLoai=Oryza sativa}}\n"
                "Giống lúa X có hạt dài và chất lượng gạo tốt, phù hợp cho "
                "nhiều điều kiện canh tác khác nhau tại Việt Nam."
            ),
        }
        entity = kg_worker.heuristic_entity(page)
        self.assertNotIn("InfoCropPlant", entity["properties"]["text_embed"])

    def test_species_pests_unclassified(self) -> None:
        entity = self._entity("Lúa")
        # Thứ tự theo vòng lặp PEST_ALIASES, không phải thứ tự xuất hiện.
        self.assertEqual(set(entity["pests"]), {"Rầy nâu", "Bệnh đạo ôn"})
        self.assertEqual(entity["resistant_to"], [])
        self.assertEqual(entity["susceptible_to"], [])


class TestCandidateClaims(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = kg_worker.load_pages(RAW_DATA_RULES)
        self.entities = [kg_worker.heuristic_entity(page) for page in self.pages]
        self.document = kg_worker.create_candidate_claims(
            self.entities,
            self.pages,
            extraction_method="rules",
            extractor_version="test",
        )

    def test_claims_are_pending_and_revision_pinned(self) -> None:
        self.assertGreater(self.document["summary"]["claim_count"], 0)
        self.assertEqual(
            self.document["summary"]["claim_count"],
            len(self.document["claims"]),
        )
        for claim in self.document["claims"]:
            self.assertEqual(claim["review"], {"status": "pending"})
            self.assertTrue(claim["evidence"])
            for evidence in claim["evidence"]:
                self.assertIsInstance(evidence["page_id"], int)
                self.assertIsInstance(evidence["revision_id"], int)
                self.assertRegex(evidence["content_hash"], r"^[a-f0-9]{64}$")

    def test_relationship_and_scalar_claims_are_emitted(self) -> None:
        predicates = {claim["predicate"] for claim in self.document["claims"]}
        self.assertIn("HAS_VARIETY", predicates)
        self.assertIn("RESISTANT_TO", predicates)
        self.assertIn("growth_duration", predicates)
        self.assertIn("crossbred_from", predicates)

    def test_variety_only_properties_are_not_claimed_for_crop_pages(self) -> None:
        crop_claims = [
            claim
            for claim in self.document["claims"]
            if claim["subject"] == {"type": "Crop", "id": "Lúa"}
        ]
        self.assertNotIn(
            "crossbred_from",
            {claim["predicate"] for claim in crop_claims},
        )

    def test_source_span_offsets_and_hash_revalidate(self) -> None:
        pages = {page["page_id"]: page for page in self.pages}
        spans = 0
        for claim in self.document["claims"]:
            for evidence in claim["evidence"]:
                if evidence["location_type"] != "source_span":
                    continue
                spans += 1
                page = pages[evidence["page_id"]]
                span = page["text"][
                    evidence["source_offset_start"] : evidence["source_offset_end"]
                ]
                self.assertEqual(span, evidence["supporting_span"])
                self.assertEqual(
                    hashlib.sha256(span.encode("utf-8")).hexdigest(),
                    evidence["span_hash"],
                )
        self.assertGreater(spans, 0)

    def test_relationship_evidence_uses_relation_specific_occurrence(self) -> None:
        text = (
            "Rầy nâu là dịch hại phổ biến.\n"
            "Giống OM1 kháng rầy nâu tốt."
        )
        page = {
            "title": "Lúa OM1",
            "kind": "variety",
            "crop": "Lúa",
            "page_id": 1,
            "revision_id": 2,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": text,
            "wikitext": text,
        }
        entity = make_entity(
            "OM1",
            "Lúa",
            page_title="Lúa OM1",
            resistant=[{"name": "Rầy nâu", "level": ""}],
        )

        document = kg_worker.create_candidate_claims(
            [entity],
            [page],
            extraction_method="rules",
            extractor_version="test",
        )
        claim = next(
            item
            for item in document["claims"]
            if item["predicate"] == "RESISTANT_TO"
        )

        self.assertTrue(claim["traceability_complete"])
        self.assertEqual(
            claim["evidence"][0]["supporting_span"],
            "Giống OM1 kháng rầy nâu tốt.",
        )

    def test_relationship_without_supporting_language_is_incomplete(self) -> None:
        text = "Rầy nâu là dịch hại phổ biến."
        page = {
            "title": "Lúa OM1",
            "kind": "variety",
            "crop": "Lúa",
            "page_id": 1,
            "revision_id": 2,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": text,
            "wikitext": text,
        }
        entity = make_entity(
            "OM1",
            "Lúa",
            page_title="Lúa OM1",
            resistant=[{"name": "Rầy nâu", "level": ""}],
        )

        document = kg_worker.create_candidate_claims(
            [entity],
            [page],
            extraction_method="rules",
            extractor_version="test",
        )
        claim = next(
            item
            for item in document["claims"]
            if item["predicate"] == "RESISTANT_TO"
        )

        self.assertFalse(claim["traceability_complete"])
        self.assertEqual(claim["evidence"][0]["location_type"], "page")

    def test_semantic_claim_ids_are_deterministic(self) -> None:
        repeated = kg_worker.create_candidate_claims(
            self.entities,
            self.pages,
            extraction_method="rules",
            extractor_version="test",
        )
        self.assertEqual(self.document, repeated)

    def test_document_validates_against_candidate_schema(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema không được cài đặt")
        schema = json.loads(
            (EXTENSION_ROOT / "schema" / "candidate-claims.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.validate(self.document, schema)

    def test_candidate_schema_rejects_incomplete_location_coordinates(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema không được cài đặt")
        schema = json.loads(
            (EXTENSION_ROOT / "schema" / "candidate-claims.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        source_claim = next(
            claim
            for claim in self.document["claims"]
            if claim["evidence"][0]["location_type"] == "source_span"
        )
        for field in (
            "source_offset_start",
            "source_offset_end",
            "supporting_span",
            "span_hash",
        ):
            invalid = json.loads(json.dumps(self.document))
            evidence = next(
                claim["evidence"][0]
                for claim in invalid["claims"]
                if claim["claim_id"] == source_claim["claim_id"]
            )
            evidence.pop(field)
            with self.subTest(field=field):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(invalid, schema)

        invalid = json.loads(json.dumps(self.document))
        evidence = invalid["claims"][0]["evidence"][0]
        evidence["location_type"] = "page_title"
        evidence.pop("location", None)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)

        invalid = json.loads(json.dumps(self.document))
        claim = invalid["claims"][0]
        claim["traceability_complete"] = True
        claim["evidence"][0]["location_type"] = "page"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)

    def test_conflicting_candidates_remain_explicit(self) -> None:
        entity = make_entity(
            "OM1",
            "Lúa",
            resistant=[{"name": "Rầy nâu", "level": ""}],
            susceptible=[{"name": "Rầy nâu", "level": ""}],
        )
        page = {
            "title": "Lúa OM1",
            "kind": "variety",
            "crop": "Lúa",
            "page_id": 1,
            "revision_id": 2,
            "content_hash": "a" * 64,
            "text": "OM1 kháng rầy nâu nhưng cũng có ghi nhận nhiễm rầy nâu.",
            "wikitext": "OM1 kháng rầy nâu nhưng cũng có ghi nhận nhiễm rầy nâu.",
        }
        document = kg_worker.create_candidate_claims(
            [entity],
            [page],
            extraction_method="rules",
            extractor_version="test",
        )
        predicates = {
            claim["predicate"]
            for claim in document["claims"]
            if claim["subject"]["type"] == "Variety"
        }
        self.assertIn("RESISTANT_TO", predicates)
        self.assertIn("SUSCEPTIBLE_TO", predicates)

    def test_pages_without_revision_provenance_do_not_create_claims(self) -> None:
        page = {
            "title": "Lúa OM1",
            "kind": "variety",
            "crop": "Lúa",
            "text": "OM1 kháng rầy nâu.",
        }
        entity = kg_worker.heuristic_entity(page)
        document = kg_worker.create_candidate_claims(
            [entity],
            [page],
            extraction_method="rules",
            extractor_version="test",
        )
        self.assertEqual(document["claims"], [])
        self.assertEqual(
            document["summary"]["skipped_source_pages"],
            ["Lúa OM1"],
        )


class TestBuildGraph(unittest.TestCase):
    def test_full_fixture_graph_shape(self) -> None:
        pages = kg_worker.load_pages(RAW_DATA_RULES)
        entities = [kg_worker.heuristic_entity(p) for p in pages]
        graph, warnings = kg_worker.build_graph(entities, pages, "Lúa")

        variety_ids = {
            node["id"]
            for node in graph.node_list()
            if node["label"] == "Variety"
        }
        self.assertEqual(variety_ids, {"OM5451", "ST24", "ST25"})
        self.assertEqual(warnings, [])

        crop_node = next(
            node
            for node in graph.node_list()
            if node["label"] == "Crop" and node["id"] == "Lúa"
        )
        self.assertEqual(crop_node["properties"]["scientific_name"], "Oryza sativa")
        self.assertEqual(crop_node["properties"]["variety_count"], "3")
        self.assertEqual(crop_node["properties"]["pest_count"], "4")

        om5451 = next(
            node
            for node in graph.node_list()
            if node["label"] == "Variety" and node["id"] == "OM5451"
        )
        self.assertIn("LuaVariety", om5451["extra_labels"])
        self.assertEqual(om5451["properties"]["growth_duration"], "95-105 ngày")

        has_variety = [
            edge for edge in graph.edge_list() if edge["type"] == "HAS_VARIETY"
        ]
        affected_by = [
            edge for edge in graph.edge_list() if edge["type"] == "AFFECTED_BY"
        ]
        self.assertEqual(len(has_variety), 3)
        self.assertEqual(len(affected_by), 4)
        self.assertEqual(
            {edge["target"] for edge in affected_by},
            {"Bệnh đạo ôn", "Rầy nâu", "Bệnh bạc lá", "Bệnh khô vằn"},
        )

    def test_missing_variety_properties_filled_with_unknown(self) -> None:
        entity = make_entity("X", "Lúa", props={"growth_duration": "90 ngày"})
        graph, _ = build_variety_graph([entity])
        node = next(n for n in graph.node_list() if n["id"] == "X")
        for field in ("plant_height", "yield_amount", "pest_resistance",
                      "characteristics", "crossbred_from"):
            self.assertEqual(node["properties"][field], "Chưa rõ", msg=field)

    def test_conflict_keeps_single_edge_and_warns(self) -> None:
        entity = make_entity(
            "X",
            "Lúa",
            resistant=[{"name": "Bệnh đạo ôn"}],
            susceptible=[{"name": "Bệnh đạo ôn"}],
        )
        graph, warnings = build_variety_graph([entity])
        edges = [
            edge
            for edge in graph.edge_list()
            if edge["type"] in ("RESISTANT_TO", "SUSCEPTIBLE_TO")
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["type"], "RESISTANT_TO")
        self.assertTrue(any("Mâu thuẫn" in w for w in warnings))

    def test_conflict_prefers_leveled_edge(self) -> None:
        # Cả hai đều có level -> giữ kháng.
        entity = make_entity(
            "X",
            "Lúa",
            resistant=[{"name": "Bệnh đạo ôn", "level": "Cấp 1"}],
            susceptible=[{"name": "Bệnh đạo ôn", "level": "Cấp 5"}],
        )
        graph, _ = build_variety_graph([entity])
        edges = [
            edge
            for edge in graph.edge_list()
            if edge["type"] in ("RESISTANT_TO", "SUSCEPTIBLE_TO")
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["type"], "RESISTANT_TO")
        self.assertEqual(edges[0]["properties"]["level"], "Cấp 1")

    def test_conflict_keeps_only_leveled_susceptible(self) -> None:
        entity = make_entity(
            "X",
            "Lúa",
            resistant=[{"name": "Bệnh đạo ôn"}],
            susceptible=[{"name": "Bệnh đạo ôn", "level": "Cấp 5"}],
        )
        graph, _ = build_variety_graph([entity])
        edges = [
            edge
            for edge in graph.edge_list()
            if edge["type"] in ("RESISTANT_TO", "SUSCEPTIBLE_TO")
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["type"], "SUSCEPTIBLE_TO")

    def test_duplicate_variety_pages_dedup(self) -> None:
        entities = [
            make_entity("OM5451", "Lúa", page_title="Lúa_OM5451",
                        props={"growth_duration": "95-105 ngày"}),
            make_entity("OM5451", "Lúa", page_title="Lúa OM5451",
                        props={"growth_duration": "98-102 ngày"}),
        ]
        graph, _ = build_variety_graph(entities)
        varieties = [n for n in graph.node_list() if n["label"] == "Variety"]
        self.assertEqual(len(varieties), 1)
        self.assertEqual(varieties[0]["id"], "OM5451")
        # Thuộc tính đầu tiên (dài hơn) được giữ lại.
        self.assertEqual(
            varieties[0]["properties"]["growth_duration"], "95-105 ngày"
        )

    def test_variety_without_crop_attached_to_default(self) -> None:
        entity = make_entity("X", "")
        graph, _ = build_variety_graph([entity], default_crop="Lúa")
        edges = [
            edge
            for edge in graph.edge_list()
            if edge["type"] == "HAS_VARIETY" and kg_worker.norm_key(edge["target"]) == "x"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["source"], "Lúa")

    def test_determinism(self) -> None:
        pages = kg_worker.load_pages(RAW_DATA_RULES)
        entities = [kg_worker.heuristic_entity(p) for p in pages]
        first, _ = kg_worker.build_graph(entities, pages, "Lúa")
        second, _ = kg_worker.build_graph(entities, pages, "Lúa")
        self.assertEqual(
            json.dumps(first.node_list(), ensure_ascii=False, sort_keys=True),
            json.dumps(second.node_list(), ensure_ascii=False, sort_keys=True),
        )
        self.assertEqual(
            json.dumps(first.edge_list(), ensure_ascii=False, sort_keys=True),
            json.dumps(second.edge_list(), ensure_ascii=False, sort_keys=True),
        )

    def test_determinism_with_multiple_crops(self) -> None:
        # known_crops là một set; thứ tự lặp phụ thuộc hash ngẫu nhiên hóa.
        # Hai loài trở lên phải vẫn cho thứ tự node ổn định trong tiến trình.
        pages = [
            {
                "title": "Lúa", "crop": "Lúa", "kind": "species",
                "text": "Lúa là cây lương thực chính.",
            },
            {
                "title": "Cà phê", "crop": "Cà phê", "kind": "species",
                "text": "Cà phê là cây công nghiệp.",
            },
        ]
        entities = [kg_worker.heuristic_entity(p) for p in pages]
        first, _ = kg_worker.build_graph(entities, pages, "Lúa")
        second, _ = kg_worker.build_graph(entities, pages, "Lúa")
        self.assertEqual(
            [node["id"] for node in first.node_list()],
            [node["id"] for node in second.node_list()],
        )

    def test_crop_spelling_canonicalized(self) -> None:
        # "Lúa" và "lua" gom về cùng norm_key; mọi cạnh phải trỏ đúng node
        # loài theo đúng cách viết đã tạo, không tạo cạnh mồ côi.
        pages = [
            {
                "title": "Lúa", "crop": "Lúa", "kind": "species",
                "text": "Lúa là cây lương thực chính.",
            },
            {
                "title": "Lúa_X", "crop": "lua", "kind": "variety",
                "text": "X là giống lúa, năng suất 6 tấn/ha.",
            },
        ]
        entities = [kg_worker.heuristic_entity(p) for p in pages]
        graph, _ = kg_worker.build_graph(entities, pages, "Lúa")
        crop_ids = {node["id"] for node in graph.node_list() if node["label"] == "Crop"}
        self.assertEqual(crop_ids, {"Lúa"})
        for edge in graph.edge_list():
            if edge["source_label"] == "Crop":
                self.assertIn(edge["source"], crop_ids, edge["type"])
            if edge["target_label"] == "Crop":
                self.assertIn(edge["target"], crop_ids, edge["type"])

    def test_all_edges_reference_existing_nodes(self) -> None:
        pages = kg_worker.load_pages(RAW_DATA_RULES)
        entities = [kg_worker.heuristic_entity(p) for p in pages]
        graph, _ = kg_worker.build_graph(entities, pages, "Lúa")
        ids = {node["id"] for node in graph.node_list()}
        for edge in graph.edge_list():
            self.assertIn(edge["source"], ids, edge["type"])
            self.assertIn(edge["target"], ids, edge["type"])


class TestGraphBuilderPrimitives(unittest.TestCase):
    def test_node_dedup_by_normalized_id(self) -> None:
        graph = kg_worker.GraphBuilder()
        graph.add_node("Variety", "ST25")
        graph.add_node("Variety", "st25", {}, ["LuaVariety"])
        nodes = graph.node_list()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["extra_labels"], ["LuaVariety"])

    def test_self_loop_rejected(self) -> None:
        graph = kg_worker.GraphBuilder()
        graph.add_node("Variety", "ST25")
        graph.add_edge("Variety", "ST25", "X", "Variety", "ST25")
        self.assertEqual(graph.edge_list(), [])

    def test_empty_id_rejected(self) -> None:
        graph = kg_worker.GraphBuilder()
        self.assertEqual(graph.add_node("Variety", "  "), "")
        graph.add_edge("Variety", "", "X", "Pest", "Bệnh đạo ôn")
        self.assertEqual(graph.edge_list(), [])


class TestCypher(unittest.TestCase):
    def test_cypher_string_escaping(self) -> None:
        self.assertEqual(kg_worker.cypher_string('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(kg_worker.cypher_string("a\\b"), '"a\\\\b"')
        self.assertEqual(kg_worker.cypher_string("line1\nline2"), '"line1\\nline2"')
        self.assertEqual(kg_worker.cypher_string(""), '""')

    def test_cypher_map_keeps_unknown_and_skips_empty(self) -> None:
        self.assertEqual(
            kg_worker.cypher_map({"pest_resistance": "Chưa rõ", "empty": ""}),
            '{pest_resistance: "Chưa rõ"}',
        )

    def test_safe_identifier(self) -> None:
        self.assertEqual(kg_worker.safe_identifier("LúaVariety"), "L_aVariety")
        self.assertEqual(kg_worker.safe_identifier("HAS_VARIETY"), "HAS_VARIETY")
        self.assertEqual(kg_worker.safe_identifier(""), "ENTITY")

    def test_build_statements_shape(self) -> None:
        graph = kg_worker.GraphBuilder()
        graph.add_node("Crop", "Lúa", {"scientific_name": "Oryza sativa"})
        variety_id = graph.add_node(
            "Variety", "ST25", {"growth_duration": "100-110 ngày"}, ["LuaVariety"]
        )
        graph.add_edge("Crop", "Lúa", "HAS_VARIETY", "Variety", variety_id, {})
        graph.add_edge(
            "Variety",
            variety_id,
            "RESISTANT_TO",
            "Pest",
            "Bệnh đạo ôn",
            {"level": "Cấp 1"},
        )
        statements = kg_worker.build_statements(graph)
        self.assertEqual(len(statements), 4)

        crop_cypher, crop_params = statements[0]
        self.assertEqual(crop_cypher, "MERGE (n:Crop {id: $id}) SET n += $props")
        self.assertEqual(crop_params["props"]["scientific_name"], "Oryza sativa")

        variety_cypher, _ = statements[1]
        self.assertEqual(
            variety_cypher,
            "MERGE (n:Variety {id: $id}) SET n:LuaVariety, n += $props",
        )

        edge_cypher, edge_params = statements[3]
        self.assertEqual(
            edge_cypher,
            "MATCH (a:Variety {id: $source}), "
            "(b:Pest {id: $target}) MERGE (a)-[r:RESISTANT_TO]->(b) SET r += $props",
        )
        self.assertEqual(edge_params["props"]["level"], "Cấp 1")

    def test_render_cypher_file_contains_constraints(self) -> None:
        graph = kg_worker.GraphBuilder()
        graph.add_node("Crop", "Lúa")
        content = kg_worker.render_cypher_file(graph, ["Lúa"])
        self.assertIn("CREATE CONSTRAINT crop_id", content)
        self.assertIn("MERGE (n:Crop {id: \"Lúa\"})", content)


class TestGraphDocument(unittest.TestCase):
    def _document(self) -> dict:
        pages = kg_worker.load_pages(RAW_DATA_RULES)
        entities = [kg_worker.heuristic_entity(p) for p in pages]
        graph, warnings = kg_worker.build_graph(entities, pages, "Lúa")
        metadata = {
            "extractor_version": kg_worker.EXTRACTOR_VERSION,
            "extraction_method": "rules",
            "source_pages": [
                {
                    key: page.get(key, "")
                    for key in (
                        "title", "url", "kind", "crop", "page_id",
                        "revision_id", "content_hash",
                    )
                    if page.get(key, "") not in ("", None)
                }
                for page in pages
            ],
            "warnings": warnings,
        }
        return kg_worker.create_nodes_edges(graph, metadata)

    def test_document_structure(self) -> None:
        doc = self._document()
        self.assertEqual(doc["schema_version"], "1.0")
        self.assertEqual(doc["metadata"]["extraction_method"], "rules")
        self.assertEqual(len(doc["metadata"]["source_pages"]), 4)
        self.assertEqual(doc["metadata"]["source_pages"][1]["page_id"], 101)
        self.assertEqual(
            doc["metadata"]["source_pages"][1]["content_hash"], HASH_OM5451
        )

    def test_document_validation_against_schema(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema không được cài đặt")
        doc = self._document()
        schema = json.loads(
            (EXTENSION_ROOT / "schema" / "graph-document.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.validate(doc, schema)


class TestWorkerIntegration(unittest.TestCase):
    def _run_worker(self, output_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(KG_WORKER),
                "--no-ai",
                "--input",
                str(RAW_DATA_RULES),
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
            cwd=EXTENSION_ROOT,
            check=False,
        )

    def test_runs_end_to_end_and_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_worker(Path(tmp))
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            output = Path(tmp)
            for name in (
                "graph_nodes_edges.json",
                "candidate_claims.json",
                "kg_summary.json",
                "graph_data_raw.json",
                "neo4j_import.cypher",
                "mediawiki_bang_thuoc_tinh_cay_trong.txt",
            ):
                self.assertTrue((output / name).is_file(), msg=name)

            doc = json.loads((output / "graph_nodes_edges.json").read_text(encoding="utf-8"))
            summary = json.loads((output / "kg_summary.json").read_text(encoding="utf-8"))
            candidates = json.loads(
                (output / "candidate_claims.json").read_text(encoding="utf-8")
            )

            # Khớp giữa tóm tắt và nội dung đồ thị.
            self.assertEqual(summary["node_count"], len(doc["nodes"]))
            self.assertEqual(summary["edge_count"], len(doc["edges"]))
            self.assertEqual(
                summary["candidate_claim_count"],
                len(candidates["claims"]),
            )
            self.assertEqual(candidates["metadata"]["review_state"], "pending")
            self.assertEqual(summary["page_count"], 4)
            self.assertFalse(summary["neo4j_pushed"])
            self.assertEqual(len(summary["warnings"]), 1)
            self.assertIn("Kỹ_thuật", summary["warnings"][0])

            variety_ids = {
                node["id"] for node in doc["nodes"] if node["type"] == "Variety"
            }
            self.assertEqual(
                variety_ids,
                {"OM5451", "ST24", "ST25"},
            )

    def test_output_validates_against_schema(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema không được cài đặt")
        schema = json.loads(
            (EXTENSION_ROOT / "schema" / "graph-document.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_worker(Path(tmp))
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            doc = json.loads(
                (Path(tmp) / "graph_nodes_edges.json").read_text(encoding="utf-8")
            )
            jsonschema.validate(doc, schema)

    def test_deterministic_across_runs(self) -> None:
        outputs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                result = self._run_worker(Path(tmp))
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                doc = (Path(tmp) / "graph_nodes_edges.json").read_bytes()
                claims = (Path(tmp) / "candidate_claims.json").read_bytes()
                summary = (Path(tmp) / "kg_summary.json").read_bytes()
                outputs.append((doc, claims, summary))
        self.assertEqual(outputs[0], outputs[1])

    def _run_worker_multi_crop(
        self,
        output_dir: Path,
        input_path: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(KG_WORKER),
                "--no-ai",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
            cwd=EXTENSION_ROOT,
            env=env,
            check=False,
        )

    def test_deterministic_multi_crop_across_hash_seeds(self) -> None:
        # Thứ tự node với nhiều loài phải ổn định bất kể PYTHONHASHSEED,
        # vì known_crops (một set) được lặp khi dựng node.
        data = {
            "pages": [
                {
                    "title": "Lúa", "crop": "Lúa", "kind": "species",
                    "page_id": 1, "revision_id": 1, "content_hash": "a" * 64,
                    "text": "Lúa là cây lương thực chính. "
                            "Lúa thường bị rầy nâu và bệnh đạo ôn gây hại.",
                },
                {
                    "title": "Cà_phê", "crop": "Cà phê", "kind": "species",
                    "page_id": 2, "revision_id": 1, "content_hash": "b" * 64,
                    "text": "Cà phê là cây công nghiệp. "
                            "Cà phê thường bị bệnh gỉ sắt gây hại.",
                },
            ]
        }
        outputs = []
        base_env = dict(os.environ)
        for seed in ("1", "2"):
            env = dict(base_env)
            env["PYTHONHASHSEED"] = seed
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                input_path = tmp / "raw.json"
                input_path.write_text(json.dumps(data), encoding="utf-8")
                result = self._run_worker_multi_crop(tmp, input_path, env=env)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                outputs.append(
                    (tmp / "graph_nodes_edges.json").read_bytes()
                )
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
