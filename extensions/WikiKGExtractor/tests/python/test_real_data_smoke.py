"""Unit tests cho tools/real_data_smoke.py (trình trích 5 trang từ dump MySQL).

Dùng dump tổng hợp nhỏ, đúng định dạng mysqldump (INSERT nhiều dòng, blob
hex), KHÔNG chứa dữ liệu private. Chạy như bộ test khác:

    python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python \
        -p 'test_*.py'
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXTENSION_ROOT / "tools"))

import real_data_smoke  # noqa: E402

# 5 trang mục tiêu: title (underscore), kind, crop, text dạng wikitext ngắn.
PAGES = [
    ("Lúa", "species", "Lúa", "Lúa là cây lương thực chính. Cây lúa bị rầy nâu gây hại."),
    ("Lúa_OM5451", "variety", "Lúa", "OM5451 kháng bệnh đạo ôn, năng suất 6-8 tấn/ha."),
    ("Lúa_ST24", "variety", "Lúa", "ST24 kháng bệnh bạc lá, cao 100-110 cm."),
    ("Lúa_ST25", "variety", "Lúa", "ST25 thời gian sinh trưởng 115 ngày.\n"),
    (
        "Kỹ_thuật_trồng_và_chăm_sóc_lúa",
        "variety",
        "Lúa",
        "Kỹ thuật trồng lúa: mật độ gieo sạ, bón phân.",
    ),
]


def hexblob(text: str) -> str:
    return "0x" + text.encode("utf-8").hex()


def page_row(page_id: int, title: str, latest: int) -> str:
    # page_id, namespace, title, redirect, is_new, random, touched,
    # links_updated, latest, len, content_model, lang
    return (
        f"({page_id}, 0, {hexblob(title)}, 0, 0, "
        f"{hexblob('abc')}, {hexblob('20260101000000')}, NULL, "
        f"{latest}, 100, 0, NULL)"
    )


def revision_row(rev_id: int, page_id: int) -> str:
    # rev_id, rev_page, comment_id, actor, timestamp, minor, deleted,
    # len, parent_id, sha1
    return (
        f"({rev_id}, {page_id}, NULL, {hexblob('tester')}, "
        f"{hexblob('20260101000000')}, 0, 0, 100, 0, {hexblob('s')})"
    )


def slots_row(rev_id: int, content_id: int) -> str:
    return f"({rev_id}, 1, {content_id}, {content_id})"


def content_row(content_id: int, text_id: int) -> str:
    return (
        f"({content_id}, 100, {hexblob('sha')}, 1, {hexblob('tt:' + str(text_id))})"
    )


def text_row(text_id: int, body: str) -> str:
    return f"({text_id}, {hexblob(body)}, {hexblob('utf-8')})"


def build_dump() -> str:
    lines = [
        "CREATE DATABASE IF NOT EXISTS `mediawiki_new1` DEFAULT CHARACTER SET utf8mb4;",
        "USE `mediawiki_new1`;",
        "CREATE TABLE `page` ("
        "`page_id` int UNSIGNED NOT NULL, `page_namespace` int NOT NULL, "
        "`page_title` varbinary(255) NOT NULL, `page_redirect` tinyint NOT NULL, "
        "`page_is_new` tinyint NOT NULL, `page_random` varbinary(32) NOT NULL, "
        "`page_touched` varbinary(14) NOT NULL, `page_links_updated` varbinary(14), "
        "`page_latest` int UNSIGNED NOT NULL, `page_len` int UNSIGNED NOT NULL, "
        "`page_content_model` tinyint, `page_lang` varbinary(35));",
        "INSERT INTO `page` (`page_id`, `page_namespace`, `page_title`, `page_redirect`, "
        "`page_is_new`, `page_random`, `page_touched`, `page_links_updated`, `page_latest`, "
        "`page_len`, `page_content_model`, `page_lang`) VALUES",
    ]
    for index, (title, _, _, _) in enumerate(PAGES):
        page_id = 100 + index
        latest = 200 + index
        lines.append(page_row(page_id, title, latest) + ("," if index < 4 else ";"))

    lines += [
        "CREATE TABLE `revision` ("
        "`rev_id` bigint UNSIGNED NOT NULL, `rev_page` int UNSIGNED NOT NULL, "
        "`rev_comment_id` bigint, `rev_actor` bigint NOT NULL, "
        "`rev_timestamp` varbinary(14) NOT NULL, `rev_minor_edit` tinyint NOT NULL, "
        "`rev_deleted` tinyint NOT NULL, `rev_len` int UNSIGNED NOT NULL, "
        "`rev_parent_id` bigint UNSIGNED, `rev_sha1` varbinary(32));",
        "INSERT INTO `revision` (`rev_id`, `rev_page`) VALUES",
    ]
    for index in range(len(PAGES)):
        rev_id = 200 + index
        page_id = 100 + index
        lines.append(revision_row(rev_id, page_id) + (";" if index == 4 else ","))
    lines += [
        "CREATE TABLE `slots` ("
        "`slot_revision_id` bigint UNSIGNED NOT NULL, `slot_role_id` smallint UNSIGNED NOT NULL, "
        "`slot_content_id` bigint UNSIGNED NOT NULL, `slot_origin` bigint UNSIGNED NOT NULL);",
        "INSERT INTO `slots` (`slot_revision_id`, `slot_role_id`, `slot_content_id`, `slot_origin`) VALUES",
    ]
    for index in range(len(PAGES)):
        rev_id = 200 + index
        content_id = 300 + index
        lines.append(slots_row(rev_id, content_id) + (";" if index == 4 else ","))
    lines += [
        "CREATE TABLE `content` ("
        "`content_id` bigint UNSIGNED NOT NULL, `content_size` int UNSIGNED NOT NULL, "
        "`content_sha1` varbinary(32) NOT NULL, `content_model` smallint UNSIGNED NOT NULL, "
        "`content_address` varbinary(255) NOT NULL);",
        "INSERT INTO `content` (`content_id`, `content_size`, `content_sha1`, `content_model`, `content_address`) VALUES",
    ]
    for index in range(len(PAGES)):
        content_id = 300 + index
        text_id = 400 + index
        lines.append(content_row(content_id, text_id) + (";" if index == 4 else ","))
    lines += [
        "CREATE TABLE `content_models` (`model_id` int NOT NULL, `model_name` varbinary(64) NOT NULL);",
        "INSERT INTO `content_models` (`model_id`, `model_name`) VALUES",
        "(1, " + hexblob("wikitext") + ");",
        "CREATE TABLE `text` (`old_id` int UNSIGNED NOT NULL, `old_text` mediumblob NOT NULL, "
        "`old_flags` varbinary(16) NOT NULL);",
        "INSERT INTO `text` (`old_id`, `old_text`, `old_flags`) VALUES",
    ]
    for index, (_, _, _, body) in enumerate(PAGES):
        text_id = 400 + index
        lines.append(text_row(text_id, body) + (";" if index == 4 else ","))

    # Database khác: phải bị bỏ qua hoàn toàn.
    lines += [
        "CREATE DATABASE IF NOT EXISTS `otherdb` DEFAULT CHARACTER SET utf8mb4;",
        "USE `otherdb`;",
        "INSERT INTO `page` (`page_id`, `page_namespace`, `page_title`, `page_redirect`, "
        "`page_is_new`, `page_random`, `page_touched`, `page_links_updated`, `page_latest`, "
        "`page_len`, `page_content_model`, `page_lang`) VALUES",
        "(1, 0, " + hexblob("Không_liên_quan") + ", 0, 0, "
        + hexblob("z") + ", " + hexblob("20260101000000") + ", NULL, 2, 10, 0, NULL);",
    ]
    return "\n".join(lines) + "\n"


class TestExtractPages(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dump = Path(self.tmp.name) / "dump.sql"
        self.dump.write_text(build_dump(), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extracts_all_target_pages_with_content(self) -> None:
        pages = real_data_smoke.extract_pages(self.dump)
        self.assertEqual(len(pages), 5)
        by_title = {p["title"]: p for p in pages}
        lua = by_title["Lúa"]
        self.assertEqual(lua["page_id"], 100)
        self.assertEqual(lua["revision_id"], 200)
        self.assertEqual(lua["kind"], "species")
        self.assertEqual(lua["crop"], "Lúa")
        self.assertIn("rầy nâu", lua["text"])
        self.assertEqual(lua["wikitext"], lua["text"])
        om5451 = by_title["Lúa OM5451"]
        self.assertEqual(om5451["kind"], "variety")
        self.assertEqual(om5451["revision_id"], 201)
        self.assertEqual(
            om5451["content_hash"],
            hashlib.sha256(om5451["wikitext"].encode("utf-8")).hexdigest(),
        )
        st25 = by_title["Lúa ST25"]
        self.assertTrue(st25["wikitext"].endswith("\n"))
        self.assertEqual(st25["text"], st25["wikitext"])
        kt = by_title["Kỹ thuật trồng và chăm sóc lúa"]
        # kind=None trong TARGET_PAGES: extractor bỏ qua, worker sẽ fallback
        # về "variety" ở load_pages (hành vi hiện tại cần báo cáo).
        self.assertNotIn("kind", kt)

    def test_other_databases_ignored(self) -> None:
        pages = real_data_smoke.extract_pages(self.dump)
        self.assertEqual(len(pages), 5)
        self.assertNotIn("Không liên quan", {p["title"] for p in pages})

    def test_custom_targets_can_keep_missing_entries_out(self) -> None:
        targets = [
            ("Lúa_ST24", "variety", "Lúa"),
            ("Lúa_không_tồn_tại", "variety", "Lúa"),
        ]
        pages = real_data_smoke.extract_pages(
            self.dump,
            targets,
            allow_missing=True,
        )
        self.assertEqual([page["title"] for page in pages], ["Lúa ST24"])

    def test_custom_targets_still_reject_missing_by_default(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "không_tồn_tại"):
            real_data_smoke.extract_pages(
                self.dump,
                [("Lúa_không_tồn_tại", "variety", "Lúa")],
            )

    def test_decode_bytes_hex_utf8(self) -> None:
        raw = bytes.fromhex(hexblob("Lúa OM5451")[2:])
        self.assertEqual(real_data_smoke.decode(raw), "Lúa OM5451")
        self.assertEqual(real_data_smoke.decode(None), "")
        self.assertEqual(real_data_smoke.decode(7), "7")


if __name__ == "__main__":
    unittest.main()
