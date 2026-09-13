"""
Graph schema
--------------------------------------
Nodes
    (:Crop {id, scientific_name, kingdom, taxon_order, family, genus,
            description, text_embed, variety_count, pest_count})
        Node CHA - một loài cây, ví dụ "Lúa". Phân loại khoa học nằm ngay
        trong thuộc tính của node này, KHÔNG tách thành node riêng.

    (:Variety {id, page_title, url, growth_duration, plant_height,
               yield_amount, pest_resistance, characteristics,
               crossbred_from, variety_type?, text_embed?})
        Node CON - một giống cây, ví dụ "OM5451". Không mang tên khoa học.
        Sáu thuộc tính chính luôn tồn tại; thiếu dữ liệu thì ghi "Chưa rõ".
        Nguồn gốc lai tạo là THUỘC TÍNH "crossbred_from".
        Kèm nhãn phụ theo loài, ví dụ :LuaVariety

    (:Pest {id})                                        ví dụ "Bệnh đạo ôn"

Relationships
    (:Crop)-[:HAS_VARIETY]->(:Variety)
    (:Variety)-[:RESISTANT_TO   {level}]->(:Pest)
    (:Variety)-[:SUSCEPTIBLE_TO {level}]->(:Pest)
    (:Crop)-[:AFFECTED_BY]->(:Pest)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

RAW_INPUT_SCHEMA_VERSION = "1.0"
EXTRACTOR_VERSION = "2.2.0"

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """
Bạn là công cụ trích xuất dữ liệu nông nghiệp để xây dựng Knowledge Graph.
Đồ thị chỉ có ba loại node: LOÀI cây (node cha), GIỐNG cây (node con) và
SÂU BỆNH. Không quan tâm địa điểm, vùng trồng hay nơi xuất xứ.

Đọc các trang wiki dưới đây và chỉ trả về MỘT MẢNG JSON. Mỗi phần tử tương
ứng MỘT trang, cấu trúc:
{{
  "page_title": "Tiêu đề trang y hệt trong dữ liệu",
  "kind": "species" hoặc "variety",
  "entity": "Tên ngắn gọn (giống: 'OM5451', 'Đài Thơm 8'; loài: 'Lúa')",
  "crop": "Tên loài cây mà trang này thuộc về, ví dụ 'Lúa'",
  "taxonomy": {{
    "scientific_name": "Oryza sativa",
    "kingdom": "Plantae",
    "order": "Poales",
    "family": "Poaceae",
    "genus": "Oryza"
  }},
  "properties": {{
    "variety_type": "Lai tạo / Thuần chủng / Bản địa",
    "crossbred_from": "Tên giống bố/mẹ nếu bài nói giống này lai từ đâu, ví dụ 'IR64 / OM1490'",
    "growth_duration": "95 - 105 ngày",
    "plant_height": "100 - 110 cm",
    "yield_amount": "6 - 8 tấn/ha",
    "pest_resistance": "Tóm tắt 1-2 câu khả năng kháng và nhiễm sâu bệnh",
    "characteristics": "Tóm tắt 1-3 câu đặc tính nổi bật của giống",
    "description": "CHỈ cho trang loài: 3-4 câu giới thiệu chung về loài cây",
    "text_embed": "Đoạn tóm tắt 2-4 câu, trôi chảy, đủ ý để tìm kiếm ngữ nghĩa"
  }},
  "resistant_to": [{{"name": "Bệnh đạo ôn", "level": "Kháng tốt / Cấp 2 / ..."}}],
  "susceptible_to": [{{"name": "Rầy nâu", "level": "Nhiễm nhẹ / Cấp 5 / ..."}}],
  "pests": ["Sâu bệnh được nhắc tới mà không rõ là kháng hay nhiễm"]
}}

QUY TẮC BẮT BUỘC:
1. BỎ QUA trường nào không có thông tin trong bài. TUYỆT ĐỐI không bịa dữ
   liệu số. Không ghi "Chưa rõ", không ghi chuỗi rỗng - cứ bỏ hẳn khóa đó
   đi. Chỉ "page_title" và "entity" là luôn phải có.
2. "taxonomy" lấy từ khối "Phân loại khoa học" của trang. Cứ điền khi trang
   có khối này, kể cả trang giống - dữ liệu đó thuộc về node LOÀI.
3. "growth_duration", "plant_height", "yield_amount" phải VIẾT GỌN thành
   con số kèm đơn vị, không chép nguyên câu văn.
4. "characteristics" và "pest_resistance" là phần TÓM TẮT do bạn viết lại
   dựa trên nội dung bài, ngắn gọn, không sao chép cả đoạn dài.
5. "resistant_to" dùng cho từ khóa kháng/chống chịu tốt; "susceptible_to"
   dùng cho nhiễm/không kháng được. Nếu bài chỉ liệt kê sâu bệnh chung
   thì cho vào "pests".
6. "crossbred_from" chỉ ghi TÊN GIỐNG bố/mẹ, cách nhau bằng " / ". Không ghi
   tên viện nghiên cứu, tác giả hay năm công nhận. Bài không nói rõ lai từ
   giống nào thì bỏ hẳn khóa này.
7. Giữ nguyên tên sâu bệnh theo tiếng Việt trong bài (ví dụ "Bệnh bạc lá",
   "Rầy nâu", "Sâu cuốn lá"). Không đưa tên vùng trồng, tỉnh thành, viện
   nghiên cứu vào bất kỳ trường nào.
8. Không thêm lời giải thích, không bọc trong dấu ```.

DỮ LIỆU:
{content}
""".strip()

CROP_SUMMARY_PROMPT = """
Bạn là chuyên gia nông nghiệp. Dưới đây là tên loài cây và danh sách các
giống của nó đã trích xuất được từ wiki.

Viết một đoạn giới thiệu chung 3-4 câu về LOÀI cây này: đặc điểm chung, giá
trị sử dụng, điều kiện canh tác cơ bản và nhóm sâu bệnh thường gặp. Không
nhắc tới tên giống cụ thể, không bịa số liệu.

Chỉ trả về MỘT đối tượng JSON, không kèm giải thích, không bọc dấu ```:
{{"description": "..."}}

LOÀI: {crop}
CÁC GIỐNG: {varieties}
SÂU BỆNH ĐƯỢC NHẮC TỚI: {pests}
""".strip()

# ---------------------------------------------------------------------------
# Từ điển chuẩn hóa sâu bệnh (mở rộng dần cho các loài khác)
# ---------------------------------------------------------------------------

PEST_ALIASES: dict[str, str] = {
    "bac la": "Bệnh bạc lá",
    "chay bia la": "Bệnh bạc lá",
    "dao on": "Bệnh đạo ôn",
    "dao on co bong": "Bệnh đạo ôn cổ bông",
    "dom nau": "Bệnh đốm nâu",
    "dom vong": "Bệnh đốm vòng",
    "kho van": "Bệnh khô vằn",
    "vang lun": "Bệnh vàng lùn",
    "lun xoan la": "Bệnh lùn xoắn lá",
    "xoan la": "Bệnh lùn xoắn lá",
    "lun soc den": "Bệnh lùn sọc đen",
    "than thu": "Bệnh thán thư",
    "suong mai": "Bệnh sương mai",
    "heo xanh": "Bệnh héo xanh",
    "heo ru": "Bệnh héo rũ",
    "thoi than": "Bệnh thối thân",
    "thoi re": "Bệnh thối rễ",
    "vang la gan xanh": "Bệnh vàng lá gân xanh",
    "gi sat": "Bệnh gỉ sắt",
    "ray nau": "Rầy nâu",
    "ray lung trang": "Rầy lưng trắng",
    "ray xanh": "Rầy xanh",
    "bo tri": "Bọ trĩ",
    "bo xit": "Bọ xít",
    "bo xit hoi": "Bọ xít hôi",
    "sau cuon la": "Sâu cuốn lá",
    "sau duc than": "Sâu đục thân",
    "sau duc trai": "Sâu đục trái",
    "sau ve bua": "Sâu vẽ bùa",
    "sau keo mua thu": "Sâu keo mùa thu",
    "nhen gie": "Nhện gié",
    "nhen do": "Nhện đỏ",
    "tuyen trung": "Tuyến trùng",
    "oc buou vang": "Ốc bươu vàng",
    "chuot": "Chuột",
    "ruoi duc qua": "Ruồi đục quả",
    "ray phan trang": "Rầy phấn trắng",
    "sau benh": "Sâu bệnh (nói chung)",
}

RESISTANCE_HINTS = (
    "kháng",
    "chống chịu",
    "chịu được",
    "ít nhiễm",
    "chống được",
)

SUSCEPTIBLE_HINTS = (
    "nhiễm",
    "không kháng",
    "mẫn cảm",
    "dễ bị",
    "bị hại",
)

AFFECTED_HINTS = (
    "gây hại",
    "dịch hại",
    "sâu bệnh",
    "bị hại",
    "phòng trừ",
)

# Giá trị hiển thị khi không tìm được dữ liệu (yêu cầu: để trống hoặc "Chưa rõ").
UNKNOWN_VALUE = "Chưa rõ"

# Thuộc tính của node con (:Variety).
VARIETY_PROPERTY_KEYS = (
    "variety_type",
    "crossbred_from",
    "growth_duration",
    "plant_height",
    "yield_amount",
    "pest_resistance",
    "characteristics",
    "text_embed",
)

# Các thuộc tính luôn phải xuất hiện trên mọi node giống.
VARIETY_REQUIRED_KEYS = (
    "growth_duration",
    "plant_height",
    "yield_amount",
    "pest_resistance",
    "characteristics",
    "crossbred_from",
)

# Thuộc tính chung của node cha (:Crop).
CROP_PROPERTY_KEYS = (
    "description",
    "text_embed",
)

# Phân loại khoa học - gộp thẳng vào node cha, không tách node riêng.
# Dùng "taxon_order" vì ORDER là từ khóa của Cypher.
TAXONOMY_KEYS = {
    "scientific_name": "scientific_name",
    "kingdom": "kingdom",
    "order": "taxon_order",
    "ordo": "taxon_order",
    "taxon_order": "taxon_order",
    "family": "family",
    "familia": "family",
    "genus": "genus",
}

CROP_REQUIRED_KEYS = (
    "scientific_name",
    "family",
    "genus",
    "description",
)

CANDIDATE_PROPERTY_KEYS = (
    "scientific_name",
    "kingdom",
    "taxon_order",
    "family",
    "genus",
    "variety_type",
    "crossbred_from",
    "growth_duration",
    "plant_height",
    "yield_amount",
)

EMPTY_VALUES = {
    "",
    "chua ro",
    "khong ro",
    "khong co",
    "n/a",
    "n a",
    "na",
    "null",
    "none",
    "-",
    "unknown",
}


# ---------------------------------------------------------------------------
# Tiện ích chuỗi
# ---------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt, dùng cho việc so khớp và đặt tên nhãn Neo4j."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def norm_key(text: Any) -> str:
    """Khóa so khớp: bỏ dấu, thường hóa, gom khoảng trắng."""
    value = strip_accents(str(text or "")).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_value(value: Any) -> str:
    """Trả về chuỗi sạch, hoặc '' nếu giá trị là rỗng/'Chưa rõ'."""
    if value is None or isinstance(value, (list, dict)):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip(" \t\n\r-–—:;,")
    if norm_key(text) in EMPTY_VALUES:
        return ""
    return text


def infer_page_kind(title: str, crop: str, raw_kind: Any) -> str:
    """Infer old raw-data page kinds without treating arbitrary pages as varieties."""
    kind = str(raw_kind or "").strip().lower()
    if kind in ("species", "variety"):
        return kind

    title_key = norm_key(title)
    crop_key = norm_key(crop)
    if crop_key and title_key == crop_key:
        return "species"
    if crop_key and any(
        title_key.startswith(prefix)
        for prefix in (
            f"{crop_key} ",
            f"giong {crop_key} ",
            f"cay {crop_key} ",
        )
    ):
        return "variety"
    return ""


def label_suffix(crop_name: str) -> str:
    """'Lúa' -> 'Lua', 'Cà phê' -> 'Ca_Phe' (nhãn phụ hợp lệ trong Neo4j)."""
    ascii_name = strip_accents(str(crop_name or "")).strip()
    parts = [p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", ascii_name) if p]
    return "_".join(parts) or "Cay"


def short_variety_name(title: str, crop: str = "") -> str:
    """'Lúa OM5451' -> 'OM5451', 'Lúa_OM5451' -> 'OM5451', 'Lúa Giống ST25' -> 'ST25', 'Giống Lúa ST25' -> 'ST25', 'Cà_phê_Arabica' -> 'Arabica'."""
    name = clean_value(title)
    if not name:
        return ""

    def _to_spaces(text: str) -> str:
        # MediaWiki URL dùng gạch dưới thay khoảng trắng ("Lúa_OM5451");
        # chuẩn hóa về khoảng trắng trước khi áp dụng quy tắc cắt tiền tố.
        return re.sub(r"\s+", " ", re.sub(r"_+", " ", text)).strip()

    name = _to_spaces(name)
    patterns = [r"^gi[ốô]ng\s+", r"^c[âa]y\s+"]
    if crop:
        crop_key = _to_spaces(clean_value(crop))
        if crop_key:
            patterns.append(r"^" + re.escape(crop_key) + r"\s+")
    # Lặp lại tiền tố "giống" SAU tiền tố loài để xử lý dạng "Lúa Giống ST25"
    # (giống đứng sau tên loài): "Lúa Giống ST25" -> "Giống ST25" -> "ST25".
    patterns.append(r"^gi[ốô]ng\s+")
    for pattern in patterns:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)
    return name.strip() or clean_value(title)


def canonical_pest(name: Any) -> str:
    """Chuẩn hóa tên sâu bệnh về một dạng duy nhất."""
    text = clean_value(name)
    if not text:
        return ""
    key = norm_key(text)
    key = re.sub(r"^(benh|sau benh|dich hai|sau hai)\s+", "", key)
    if key in PEST_ALIASES:
        return PEST_ALIASES[key]
    for alias, canonical in PEST_ALIASES.items():
        if alias in key:
            return canonical
    return text[0].upper() + text[1:]


def as_list(value: Any) -> list[str]:
    """Chấp nhận chuỗi, danh sách, hoặc None."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[;,\n]| và ", value)
        return [p for p in (clean_value(part) for part in parts) if p]
    if isinstance(value, dict):
        return [p for p in (clean_value(v) for v in value.values()) if p]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                text = clean_value(
                    item.get("name") or item.get("Target") or item.get("value")
                )
            else:
                text = clean_value(item)
            if text:
                result.append(text)
        return result
    return []


def as_pest_list(value: Any) -> list[dict[str, str]]:
    """[{'name': ..., 'level': ...}] từ nhiều dạng đầu vào khác nhau."""
    result: list[dict[str, str]] = []
    if value is None:
        return result
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            name = canonical_pest(
                item.get("name") or item.get("Target") or item.get("pest")
            )
            level = clean_value(
                item.get("level")
                or item.get("muc_do")
                or item.get("mức độ")
                or item.get("degree")
            )
        else:
            name = canonical_pest(item)
            level = ""
        if name:
            result.append({"name": name, "level": level})
    return result


# ---------------------------------------------------------------------------
# Đọc dữ liệu đầu vào
# ---------------------------------------------------------------------------


def load_pages(
    input_path: Path,
    skipped_titles: list[str] | None = None,
) -> list[dict[str, Any]]:
    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    schema_version = None
    if isinstance(data, dict):
        schema_version = data.get("schema_version")
        if schema_version is not None and schema_version != RAW_INPUT_SCHEMA_VERSION:
            raise ValueError(
                "Phiên bản schema đầu vào không được hỗ trợ: "
                f"{schema_version!r}; hỗ trợ {RAW_INPUT_SCHEMA_VERSION!r}."
            )
        pages = data.get("pages", [])
    else:
        pages = data

    result: list[dict[str, Any]] = []
    for index, page in enumerate(pages or []):
        if not isinstance(page, dict):
            if schema_version == RAW_INPUT_SCHEMA_VERSION:
                raise ValueError(f"Trang đầu vào #{index + 1} phải là object JSON.")
            continue
        if schema_version == RAW_INPUT_SCHEMA_VERSION:
            title_value = page.get("title")
            wikitext_value = page.get("wikitext")
            page_id = page.get("page_id")
            revision_id = page.get("revision_id")
            if not isinstance(title_value, str) or not title_value.strip():
                raise ValueError(f"Trang đầu vào #{index + 1} thiếu title hợp lệ.")
            if not isinstance(wikitext_value, str):
                raise ValueError(f"Trang '{title_value}' thiếu wikitext hợp lệ.")
            if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id < 1:
                raise ValueError(f"Trang '{title_value}' thiếu page_id hợp lệ.")
            if (
                not isinstance(revision_id, int)
                or isinstance(revision_id, bool)
                or revision_id < 1
            ):
                raise ValueError(f"Trang '{title_value}' thiếu revision_id hợp lệ.")
            expected_hash = hashlib.sha256(wikitext_value.encode("utf-8")).hexdigest()
            if page.get("content_hash") != expected_hash:
                raise ValueError(
                    f"Trang '{title_value}' có content_hash không khớp raw wikitext."
                )
        title = clean_value(page.get("title"))
        rendered_text = str(page.get("text", "")).strip()
        raw_wikitext = str(page.get("wikitext", ""))
        wikitext = raw_wikitext if raw_wikitext.strip() else ""
        text = wikitext or rendered_text
        if not text:
            continue
        crop = clean_value(page.get("crop"))
        if not crop:
            sources = page.get("source_pages") or []
            crop = clean_value(sources[0]) if sources else ""
        kind = infer_page_kind(title, crop, page.get("kind"))
        if not kind:
            # Dữ liệu cũ không khai báo loại trang chỉ được giữ khi tiêu đề đủ
            # để nhận diện an toàn. Không biến trang kỹ thuật thành giống cây.
            if skipped_titles is not None and title:
                skipped_titles.append(title)
            continue
        if not crop:
            crop = title if kind == "species" else ""
        result.append(
            {
                "title": title,
                "text": text,
                "wikitext": wikitext,
                "rendered_text": rendered_text,
                "url": clean_value(page.get("url")),
                "kind": kind,
                "crop": crop,
                "page_id": page.get("page_id"),
                "revision_id": page.get("revision_id"),
                "content_hash": clean_value(page.get("content_hash")),
            }
        )
    return result


def batches(items: list[Any], size: int) -> Iterable[list[Any]]:
    size = max(1, size)
    for index in range(0, len(items), size):
        yield items[index : index + size]


def clean_model_json(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Cứu vãn trường hợp model kèm thêm chữ trước/sau mảng JSON.
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


# ---------------------------------------------------------------------------
# Trích xuất bằng luật (dùng khi không có API key hoặc khi batch lỗi)
# ---------------------------------------------------------------------------

HEURISTIC_FIELDS = {
    "growth_duration": r"th[ờo]i gian sinh tr[ưu][ởo]ng",
    "plant_height": r"(?:chi[ềe]u cao (?:c[âa]y|trung b[ìi]nh)?|cao c[âa]y)",
    "yield_amount": r"n[ăa]ng su[ấa]t",
    "variety_type": r"lo[ạa]i gi[ốo]ng",
}

# Rút gọn câu văn dài thành con số kèm đơn vị.
CONDENSE_PATTERNS = {
    "growth_duration": r"\d{2,3}\s*(?:[-–—]\s*\d{2,3}\s*)?ng[àa]y",
    "plant_height": r"\d{2,3}(?:[.,]\d+)?\s*(?:[-–—]\s*\d{2,3}(?:[.,]\d+)?\s*)?cm",
    "yield_amount": r"\d{1,3}(?:[.,]\d+)?\s*(?:[-–—]\s*\d{1,3}(?:[.,]\d+)?\s*)?"
    r"(?:t[ấa]n|t[ạa]|kg)\s*/\s*ha",
}

# Nguồn gốc lai tạo: chỉ nhận cụm nói rõ lai từ giống nào, tránh bắt nhầm
# tên viện nghiên cứu ở nhãn "Xuất xứ"/"Nguồn gốc".
CROSSBRED_PATTERNS = (
    r"t[ổo] h[ợo]p lai\s*[:\-–—]?\s*([^\n.;()]{3,120})",
    r"lai (?:t[ạa]o\s+)?(?:t[ừu]|gi[ữu]a)\s+(?:\d+\s+gi[ốo]ng(?:\s+l[úu]a)?\s+)?([^\n.;()]{3,120})",
    r"ch[ọo]n (?:l[ọo]c|t[ạa]o)\s+t[ừu]\s+([^\n.;()]{3,120})",
)

# Nhãn của infobox: nếu regex bắt nhầm phải nhãn thì bỏ, coi như không có.
INFOBOX_LABELS = {
    "ten giong",
    "loai giong",
    "xuat xu",
    "nguon goc",
    "loai species",
    "thong tin giong",
    "phan loai khoa hoc",
    "dac diem dac tinh",
    "gioi regnum",
    "bo ordo",
    "ho familia",
    "chi genus",
}

# Khối "Phân loại khoa học" trong infobox: nhãn nằm một dòng, giá trị dòng sau.
HEURISTIC_TAXONOMY = {
    "scientific_name": r"Lo[àa]i\s*\((?:Species|species)\)",
    "kingdom": r"Gi[ớo]i\s*\(regnum\)",
    "taxon_order": r"B[ộo]\s*\(ordo\)",
    "family": r"H[ọo]\s*\(familia\)",
    "genus": r"Chi\s*\(genus\)",
}

WIKITEXT_TAXONOMY = {
    "gioi": "kingdom",
    "bo": "taxon_order",
    "ho": "family",
    "chi": "genus",
    "tenloai": "scientific_name",
}


def extract_wikitext_taxonomy(text: str) -> dict[str, str]:
    """Read taxonomy fields from WikiCrop's InfoPlant templates."""
    match = re.search(
        r"\{\{\s*(?:InfoPlant1|InfoCropPlant)\b(.*?)\}\}",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}

    taxonomy: dict[str, str] = {}
    for parameter in re.finditer(
        r"\|\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)"
        r"(?=\|\s*[A-Za-z][A-Za-z0-9_]*\s*=|\Z)",
        match.group(1),
        flags=re.DOTALL,
    ):
        key = WIKITEXT_TAXONOMY.get(norm_key(parameter.group(1)).replace(" ", ""))
        if not key:
            continue
        value = re.sub(r"<[^>]+>", " ", parameter.group(2))
        value = clean_value(value.replace("''", ""))
        if value:
            taxonomy[key] = value
    return taxonomy


def heuristic_entity(page: dict[str, str]) -> dict[str, Any]:
    """Trích xuất tối thiểu nhưng không bao giờ thất bại."""
    text = page["text"]
    flat = re.sub(r"[ \t]+", " ", text)
    properties: dict[str, str] = {}

    for key, pattern in HEURISTIC_FIELDS.items():
        condense = CONDENSE_PATTERNS.get(key)
        value = ""
        # Nhãn có thể xuất hiện nhiều lần (infobox, mục lục, đoạn văn); duyệt
        # hết cho tới khi tìm được chỗ thật sự có dữ liệu.
        for label_match in re.finditer(pattern, flat, flags=re.IGNORECASE):
            window = flat[label_match.end() : label_match.end() + 320]
            window = window.lstrip(" \t\n:.-–—")
            if condense:
                # Chỉ nhận con số nằm ngay sau đúng nhãn của nó. Quét cả bài
                # rất dễ nhặt nhầm ("cày sâu 20-25cm" thành chiều cao cây).
                found = re.search(condense, window, flags=re.IGNORECASE)
                candidate = clean_value(found.group(0)) if found else ""
            else:
                candidate = clean_value(window.split("\n")[0])
                if norm_key(candidate) in INFOBOX_LABELS:
                    candidate = ""
            if candidate:
                value = candidate
                break
        if value:
            properties[key] = value[:200]

    for pattern in CROSSBRED_PATTERNS:
        match = re.search(pattern, flat, flags=re.IGNORECASE)
        if not match:
            continue
        # Cắt phần đuôi nói về nơi/năm lai tạo, chỉ giữ tên giống bố/mẹ.
        value = clean_value(
            re.split(
                r"\s+(?:t[ạa]i|do|c[ủu]a|n[ăa]m|b[ởo]i|c[óo]\s+"
                r"(?:[đd][ặa]c\s+t[íi]nh|[đd][ặa]c\s+[đd]i[ểe]m))\s+",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
        )
        if value and norm_key(value) not in INFOBOX_LABELS:
            value = re.sub(r"\s+v[àa]\s+", " / ", value, flags=re.IGNORECASE)
            properties["crossbred_from"] = value[:200]
            break

    # Phân loại khoa học -> gắn vào node loài, không tạo node riêng.
    taxonomy: dict[str, str] = {}
    for key, pattern in HEURISTIC_TAXONOMY.items():
        match = re.search(
            pattern + r"\s*\n\s*([^\n]{2,80})", flat, flags=re.IGNORECASE
        )
        if match:
            value = clean_value(match.group(1))
            if value and not value.lower().startswith("(không phân hạng"):
                taxonomy[key] = value
    if page["kind"] == "species":
        for key, value in extract_wikitext_taxonomy(text).items():
            taxonomy.setdefault(key, value)

    paragraphs = [
        p.strip()
        for p in text.split("\n")
        if len(p.strip()) > 80
        and "{{" not in p
        and "}}" not in p
        and not p.lstrip().startswith(("|", "<span", "[[File:"))
    ]
    if paragraphs:
        properties["text_embed"] = " ".join(paragraphs[:2])[:900]

    # Tóm tắt đặc tính theo luật: lấy các câu mô tả chất lượng/đặc điểm.
    trait_sentences = [
        clean_value(match.group(0))
        for match in re.finditer(
            r"[^\n.]{20,220}(?:h[ạa]t|c[ơo]m|ch[ấa]t l[ưu][ợo]ng|d[ẻe]o|th[ơo]m|"
            r"ch[ịi]u (?:h[ạa]n|m[ặa]n|ph[èe]n|ng[ậa]p)|c[ứu]ng c[âa]y|"
            r"[đd][ẻe] nh[áa]nh)[^\n.]{0,220}\.",
            flat,
            flags=re.IGNORECASE,
        )
    ]
    if trait_sentences:
        properties["characteristics"] = " ".join(trait_sentences[:2])[:400]

    resistant: list[dict[str, str]] = []
    susceptible: list[dict[str, str]] = []
    pests: list[str] = []
    seen: set[str] = set()
    flat_key = norm_key(flat)

    for alias, canonical in PEST_ALIASES.items():
        if alias == "sau benh" or alias not in flat_key:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        # Xét ngữ cảnh ~130 ký tự trước MỌI lần xuất hiện của tên sâu bệnh.
        resistant_votes = 0
        susceptible_votes = 0
        for match in re.finditer(re.escape(alias), flat_key):
            context = flat_key[max(0, match.start() - 130) : match.start()]
            if any(norm_key(hint) in context for hint in SUSCEPTIBLE_HINTS):
                susceptible_votes += 1
            elif any(norm_key(hint) in context for hint in RESISTANCE_HINTS):
                resistant_votes += 1
        if resistant_votes >= susceptible_votes and resistant_votes > 0:
            resistant.append({"name": canonical, "level": ""})
        elif susceptible_votes > 0:
            susceptible.append({"name": canonical, "level": ""})
        else:
            pests.append(canonical)

    summary = summarize_pest_profile(resistant, susceptible)
    if summary:
        properties["pest_resistance"] = summary

    entity = {
        "page_title": page["title"],
        "kind": page["kind"],
        "entity": page["title"]
        if page["kind"] == "species"
        else short_variety_name(page["title"], page["crop"]),
        "crop": page["crop"],
        "taxonomy": taxonomy,
        "properties": properties,
        "resistant_to": resistant,
        "susceptible_to": susceptible,
        "pests": pests,
        "_source": "heuristic",
    }
    return entity


def summarize_pest_profile(
    resistant: list[dict[str, str]], susceptible: list[dict[str, str]]
) -> str:
    """Gộp danh sách kháng/nhiễm thành một câu ngắn cho thuộc tính node."""

    def render(items: list[dict[str, str]]) -> str:
        parts = []
        for item in items:
            name = item.get("name", "")
            if not name:
                continue
            level = item.get("level", "")
            parts.append(f"{name} ({level})" if level else name)
        return ", ".join(dict.fromkeys(parts))

    pieces = []
    resistant_text = render(resistant)
    susceptible_text = render(susceptible)
    if resistant_text:
        pieces.append(f"Kháng {resistant_text}")
    if susceptible_text:
        pieces.append(f"nhiễm {susceptible_text}")
    return "; ".join(pieces)


def normalize_taxonomy(value: Any) -> dict[str, str]:
    """Chuẩn hóa khối phân loại khoa học về đúng tên thuộc tính Neo4j."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = TAXONOMY_KEYS.get(norm_key(raw_key).replace(" ", "_"))
        if not key:
            continue
        text = clean_value(raw_value)
        if text and not text.lower().startswith("(không phân hạng"):
            result[key] = text
    return result


def find_supporting_span(
    text: str,
    terms: Iterable[str],
    max_chars: int = 400,
) -> tuple[str, int, int] | None:
    """Return one exact source span containing the first matching term."""
    candidates = sorted(
        {clean_value(term) for term in terms if clean_value(term)},
        key=len,
        reverse=True,
    )
    for term in candidates:
        match = re.search(re.escape(term), text, flags=re.IGNORECASE)
        if not match:
            continue
        start = text.rfind("\n", 0, match.start()) + 1
        end = text.find("\n", match.end())
        if end == -1:
            end = len(text)
        if end - start > max_chars:
            padding = max(0, (max_chars - len(term)) // 2)
            start = max(start, match.start() - padding)
            end = min(end, start + max_chars)
        span = text[start:end].strip()
        if not span:
            continue
        leading = len(text[start:end]) - len(text[start:end].lstrip())
        span_start = start + leading
        return span, span_start, span_start + len(span)
    return None


def find_relationship_supporting_span(
    text: str,
    terms: Iterable[str],
    relationship_hints: Iterable[str],
    *,
    opposing_hints: Iterable[str] = (),
    max_chars: int = 400,
) -> tuple[str, int, int] | None:
    """Return a span where the target and relationship language co-occur."""
    targets = sorted(
        {clean_value(term) for term in terms if clean_value(term)},
        key=len,
        reverse=True,
    )
    supporting = [clean_value(hint) for hint in relationship_hints if clean_value(hint)]
    opposing = [clean_value(hint) for hint in opposing_hints if clean_value(hint)]

    for term in targets:
        for target_match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            context_start = max(0, target_match.start() - 130)
            context = text[context_start : target_match.start()]
            hint_matches: list[tuple[int, bool, int]] = []
            for hint in supporting:
                for match in re.finditer(re.escape(hint), context, flags=re.IGNORECASE):
                    hint_matches.append((match.end(), True, match.start()))
            for hint in opposing:
                for match in re.finditer(re.escape(hint), context, flags=re.IGNORECASE):
                    hint_matches.append((match.end(), False, match.start()))
            if not hint_matches:
                continue

            _, supports_relationship, relative_hint_start = max(
                hint_matches,
                key=lambda item: (item[0], not item[1]),
            )
            if not supports_relationship:
                continue

            hint_start = context_start + relative_hint_start
            line_start = text.rfind("\n", 0, hint_start) + 1
            line_end = text.find("\n", target_match.end())
            if line_end == -1:
                line_end = len(text)
            start = line_start
            end = line_end
            if end - start > max_chars:
                core_start = hint_start
                core_end = target_match.end()
                padding = max(0, (max_chars - (core_end - core_start)) // 2)
                start = max(line_start, core_start - padding)
                end = min(line_end, core_end + padding)
            raw_span = text[start:end]
            span = raw_span.strip()
            if not span:
                continue
            leading = len(raw_span) - len(raw_span.lstrip())
            span_start = start + leading
            return span, span_start, span_start + len(span)
    return None


def pest_evidence_terms(name: str) -> list[str]:
    terms = [name]
    for alias, canonical in PEST_ALIASES.items():
        if canonical == name:
            terms.append(alias)
    if name.startswith("Bệnh "):
        terms.append(name[5:])
    return terms


def claim_evidence(
    page: dict[str, Any],
    terms: Iterable[str],
    *,
    extraction_method: str,
    extractor_version: str,
    page_identity: bool = False,
    relationship_hints: Iterable[str] = (),
    opposing_hints: Iterable[str] = (),
) -> dict[str, Any]:
    text = str(page.get("wikitext") or page.get("text") or "")
    evidence: dict[str, Any] = {
        "page_id": page.get("page_id"),
        "revision_id": page.get("revision_id"),
        "content_hash": clean_value(page.get("content_hash")),
        "page_title": clean_value(page.get("title")),
        "extraction_method": extraction_method,
        "extractor_version": extractor_version,
    }
    relationship_hints = tuple(relationship_hints)
    span = (
        find_relationship_supporting_span(
            text,
            terms,
            relationship_hints,
            opposing_hints=opposing_hints,
        )
        if relationship_hints
        else find_supporting_span(text, terms)
    )
    if span:
        supporting_span, start, end = span
        evidence.update(
            {
                "location_type": "source_span",
                "source_offset_start": start,
                "source_offset_end": end,
                "supporting_span": supporting_span,
                "span_hash": hashlib.sha256(
                    supporting_span.encode("utf-8")
                ).hexdigest(),
            }
        )
    elif page_identity:
        evidence.update(
            {
                "location_type": "page_title",
                "location": clean_value(page.get("title")),
            }
        )
    else:
        evidence["location_type"] = "page"

    identity = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    evidence["evidence_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return evidence


def create_candidate_claims(
    entities: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    *,
    extraction_method: str,
    extractor_version: str,
) -> dict[str, Any]:
    """Build provisional semantic claims with revision-pinned evidence."""
    page_index: dict[str, dict[str, Any]] = {}
    provenance_pages: list[dict[str, Any]] = []
    skipped_source_pages: list[str] = []
    for page in pages:
        page_id = page.get("page_id")
        revision_id = page.get("revision_id")
        content_hash = clean_value(page.get("content_hash"))
        if (
            not isinstance(page_id, int)
            or page_id < 1
            or not isinstance(revision_id, int)
            or revision_id < 1
            or re.fullmatch(r"[a-f0-9]{64}", content_hash) is None
        ):
            skipped_source_pages.append(clean_value(page.get("title")))
            continue
        provenance_pages.append(page)
        page_index[norm_key(page.get("title"))] = page
        short = short_variety_name(page.get("title"), page.get("crop"))
        page_index.setdefault(norm_key(short), page)

    claims: dict[str, dict[str, Any]] = {}

    def add_claim(
        subject_type: str,
        subject_id: str,
        predicate: str,
        page: dict[str, Any],
        *,
        object_type: str = "",
        object_id: str = "",
        value: str = "",
        qualifiers: dict[str, str] | None = None,
        evidence_terms: Iterable[str] = (),
        page_identity: bool = False,
        relationship_hints: Iterable[str] = (),
        opposing_hints: Iterable[str] = (),
    ) -> None:
        subject_id = clean_value(subject_id)
        object_id = clean_value(object_id)
        value = clean_value(value)
        qualifiers = {
            key: cleaned
            for key, raw in (qualifiers or {}).items()
            if (cleaned := clean_value(raw))
        }
        semantic: dict[str, Any] = {
            "claim_type": "relationship" if object_id else "property",
            "subject": {"type": subject_type, "id": subject_id},
            "predicate": predicate,
            "qualifiers": qualifiers,
        }
        if object_id:
            semantic["object"] = {"type": object_type, "id": object_id}
        else:
            semantic["value"] = value
        if not subject_id or (not object_id and not value):
            return

        serialized = json.dumps(semantic, ensure_ascii=False, sort_keys=True)
        claim_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        claim = claims.setdefault(
            claim_id,
            {
                "claim_id": claim_id,
                **semantic,
                "evidence": [],
                "traceability_complete": True,
                "review": {"status": "pending"},
            },
        )
        evidence = claim_evidence(
            page,
            evidence_terms,
            extraction_method=extraction_method,
            extractor_version=extractor_version,
            page_identity=page_identity,
            relationship_hints=relationship_hints,
            opposing_hints=opposing_hints,
        )
        evidence_ids = {item["evidence_id"] for item in claim["evidence"]}
        if evidence["evidence_id"] not in evidence_ids:
            claim["evidence"].append(evidence)
        if evidence["location_type"] == "page":
            claim["traceability_complete"] = False

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        page = page_index.get(norm_key(entity.get("page_title")))
        if not page:
            page = page_index.get(norm_key(entity.get("entity")))
        if not page:
            continue

        kind = str(entity.get("kind") or page.get("kind") or "").lower()
        crop = clean_value(entity.get("crop") or page.get("crop"))
        raw_name = clean_value(entity.get("entity") or page.get("title"))
        subject_type = "Crop" if kind == "species" else "Variety"
        subject_id = crop or raw_name if kind == "species" else short_variety_name(
            raw_name,
            crop,
        )

        taxonomy = normalize_taxonomy(entity.get("taxonomy"))
        properties = entity.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for key, value in taxonomy.items():
            if key not in CANDIDATE_PROPERTY_KEYS:
                continue
            add_claim(
                "Crop",
                crop or raw_name,
                key,
                page,
                value=value,
                evidence_terms=[value],
            )

        if kind != "species":
            for key in CANDIDATE_PROPERTY_KEYS:
                if key in taxonomy or key not in properties:
                    continue
                value = clean_value(properties.get(key))
                terms = [value]
                if key == "crossbred_from":
                    terms.extend(re.split(r"\s*/\s*", value))
                add_claim(
                    subject_type,
                    subject_id,
                    key,
                    page,
                    value=value,
                    evidence_terms=terms,
                )

        resistant = as_pest_list(entity.get("resistant_to"))
        susceptible = as_pest_list(entity.get("susceptible_to"))
        mentioned = [canonical_pest(name) for name in as_list(entity.get("pests"))]

        if kind != "species" and crop:
            add_claim(
                "Crop",
                crop,
                "HAS_VARIETY",
                page,
                object_type="Variety",
                object_id=subject_id,
                evidence_terms=[subject_id, raw_name],
                page_identity=True,
            )
        for relation, items in (
            ("RESISTANT_TO", resistant),
            ("SUSCEPTIBLE_TO", susceptible),
        ):
            for item in items:
                add_claim(
                    subject_type,
                    subject_id,
                    relation,
                    page,
                    object_type="Pest",
                    object_id=item["name"],
                    qualifiers={"level": item["level"]},
                    evidence_terms=pest_evidence_terms(item["name"]),
                    relationship_hints=(
                        RESISTANCE_HINTS
                        if relation == "RESISTANT_TO"
                        else SUSCEPTIBLE_HINTS
                    ),
                    opposing_hints=(
                        SUSCEPTIBLE_HINTS if relation == "RESISTANT_TO" else ()
                    ),
                )
        if crop:
            crop_pests = mentioned + [item["name"] for item in resistant + susceptible]
            for pest_name in dict.fromkeys(name for name in crop_pests if name):
                add_claim(
                    "Crop",
                    crop,
                    "AFFECTED_BY",
                    page,
                    object_type="Pest",
                    object_id=pest_name,
                    evidence_terms=pest_evidence_terms(pest_name),
                    relationship_hints=(
                        AFFECTED_HINTS + RESISTANCE_HINTS + SUSCEPTIBLE_HINTS
                    ),
                )

    ordered = sorted(claims.values(), key=lambda claim: claim["claim_id"])
    by_predicate: dict[str, int] = {}
    for claim in ordered:
        predicate = str(claim["predicate"])
        by_predicate[predicate] = by_predicate.get(predicate, 0) + 1
    return {
        "schema_version": "1.0",
        "metadata": {
            "extractor_version": extractor_version,
            "extraction_method": extraction_method,
            "review_state": "pending",
            "source_pages": [
                {
                    key: page.get(key)
                    for key in ("title", "page_id", "revision_id", "content_hash")
                }
                for page in provenance_pages
            ],
        },
        "summary": {
            "claim_count": len(ordered),
            "traceability_complete_count": sum(
                bool(claim["traceability_complete"]) for claim in ordered
            ),
            "claims_by_predicate": by_predicate,
            "skipped_source_pages": skipped_source_pages,
        },
        "claims": ordered,
    }


# ---------------------------------------------------------------------------
# Gọi Gemini
# ---------------------------------------------------------------------------


def build_prompt(batch: list[dict[str, str]], max_chars: int) -> str:
    parts = []
    for page in batch:
        text = page["text"]
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "\n[...]"
        parts.append(
            f"=== TÊN TRANG: {page['title']} ===\n"
            f"LOẠI TRANG: {'trang loài' if page['kind'] == 'species' else 'trang giống'}\n"
            f"LOÀI CÂY: {page['crop'] or 'Chưa xác định'}\n"
            f"URL: {page['url']}\n"
            f"NỘI DUNG:\n{text}"
        )
    return PROMPT_TEMPLATE.format(content="\n\n".join(parts))


def extract_with_ai(
    pages: list[dict[str, str]],
    api_key: str,
    model: str,
    batch_size: int,
    retries: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Trả về (entities, warnings). Batch lỗi sẽ rơi về heuristic."""
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu thư viện google-genai. Chạy: pip install -r bin/requirements.txt"
        ) from exc

    client = genai.Client(api_key=api_key)
    entities: list[dict[str, Any]] = []
    warnings: list[str] = []

    for number, batch in enumerate(batches(pages, batch_size), start=1):
        prompt = build_prompt(batch, max_chars)
        last_error: Exception | None = None

        for attempt in range(1, max(1, retries) + 1):
            try:
                response = client.models.generate_content(
                    model=model, contents=prompt
                )
                parsed = clean_model_json(getattr(response, "text", "") or "")
                if isinstance(parsed, dict):
                    parsed = [parsed]
                if not isinstance(parsed, list):
                    raise ValueError("Phản hồi AI không phải mảng JSON")
                for item in parsed:
                    if isinstance(item, dict):
                        item["_source"] = "ai"
                        entities.append(item)
                print(
                    f"[kg] Batch {number}: xử lý {len(batch)} trang, "
                    f"nhận {len(parsed)} thực thể.",
                    flush=True,
                )
                last_error = None
                break
            except Exception as exc:  # API/mạng/JSON
                last_error = exc
                if attempt < max(1, retries):
                    time.sleep(min(10, 2 * attempt))

        if last_error is not None:
            message = (
                f"Batch {number} lỗi ({last_error}); dùng bộ trích xuất dự phòng."
            )
            warnings.append(message)
            print(f"[kg] {message}", file=sys.stderr, flush=True)
            entities.extend(heuristic_entity(page) for page in batch)

    return entities, warnings


def describe_crop_with_ai(
    crop: str,
    varieties: list[str],
    pests: list[str],
    api_key: str,
    model: str,
    retries: int,
) -> str:
    """Sinh đoạn giới thiệu chung cho node loài khi wiki không có trang loài."""
    try:
        from google import genai
    except ImportError:
        return ""

    prompt = CROP_SUMMARY_PROMPT.format(
        crop=crop,
        varieties=", ".join(varieties[:40]) or "Chưa có",
        pests=", ".join(pests[:30]) or "Chưa có",
    )
    client = genai.Client(api_key=api_key)
    for attempt in range(1, max(1, retries) + 1):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            text = (getattr(response, "text", "") or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
                text = re.sub(r"```\s*$", "", text).strip()
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return clean_value(data.get("description"))
            except json.JSONDecodeError:
                return clean_value(text)
        except Exception:
            if attempt < max(1, retries):
                time.sleep(min(10, 2 * attempt))
    return ""


def fill_crop_descriptions(
    graph: GraphBuilder, api_key: str, model: str, retries: int
) -> list[str]:
    """Bổ sung mô tả chung cho những node loài đang để 'Chưa rõ'."""
    warnings: list[str] = []
    for (label, key), node in graph.nodes.items():
        if label != "Crop":
            continue
        if node["properties"].get("description", UNKNOWN_VALUE) != UNKNOWN_VALUE:
            continue

        varieties = [
            edge["target"]
            for edge in graph.edge_list()
            if edge["type"] == "HAS_VARIETY" and norm_key(edge["source"]) == key
        ]
        pests = [
            edge["target"]
            for edge in graph.edge_list()
            if edge["type"] == "AFFECTED_BY" and norm_key(edge["source"]) == key
        ]
        description = describe_crop_with_ai(
            node["id"], varieties, pests, api_key, model, retries
        )
        if description:
            node["properties"]["description"] = description
            if node["properties"].get("text_embed", UNKNOWN_VALUE) in (
                "",
                UNKNOWN_VALUE,
            ):
                node["properties"]["text_embed"] = description
            print(f"[kg] Đã sinh mô tả chung cho loài “{node['id']}”.", flush=True)
        else:
            warnings.append(
                f"Không sinh được mô tả chung cho loài “{node['id']}”; "
                "để giá trị Chưa rõ."
            )
    return warnings


# ---------------------------------------------------------------------------
# Dựng đồ thị
# ---------------------------------------------------------------------------


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(
        self,
        label: str,
        node_id: str,
        properties: dict[str, Any] | None = None,
        extra_labels: Iterable[str] = (),
    ) -> str:
        node_id = clean_value(node_id)
        if not node_id:
            return ""
        key = (label, norm_key(node_id))
        node = self.nodes.setdefault(
            key,
            {
                "id": node_id,
                "label": label,
                "extra_labels": [],
                "properties": {},
            },
        )
        for extra in extra_labels:
            if extra and extra not in node["extra_labels"]:
                node["extra_labels"].append(extra)
        for prop_key, prop_value in (properties or {}).items():
            value = clean_value(prop_value)
            # Không ghi đè giá trị đã có bằng giá trị rỗng.
            if value and len(value) > len(str(node["properties"].get(prop_key, ""))):
                node["properties"][prop_key] = value
        return node_id

    def add_edge(
        self,
        source_label: str,
        source_id: str,
        rel_type: str,
        target_label: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        source_id = clean_value(source_id)
        target_id = clean_value(target_id)
        if not source_id or not target_id:
            return
        if norm_key(source_id) == norm_key(target_id) and source_label == target_label:
            return
        key = (
            f"{source_label}:{norm_key(source_id)}",
            rel_type,
            f"{target_label}:{norm_key(target_id)}",
        )
        edge = self.edges.setdefault(
            key,
            {
                "source": source_id,
                "source_label": source_label,
                "type": rel_type,
                "target": target_id,
                "target_label": target_label,
                "properties": {},
            },
        )
        for prop_key, prop_value in (properties or {}).items():
            value = clean_value(prop_value)
            if value:
                edge["properties"][prop_key] = value

    def node_list(self) -> list[dict[str, Any]]:
        return list(self.nodes.values())

    def edge_list(self) -> list[dict[str, Any]]:
        return list(self.edges.values())


def index_pages(pages: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for page in pages:
        index[norm_key(page["title"])] = page
        short = short_variety_name(page["title"], page["crop"])
        index.setdefault(norm_key(short), page)
    return index


def resolve_page(
    entity: dict[str, Any], page_index: dict[str, dict[str, str]]
) -> dict[str, str]:
    for candidate in (entity.get("page_title"), entity.get("entity")):
        page = page_index.get(norm_key(candidate))
        if page:
            return page
    return {}


def build_graph(
    entities: list[dict[str, Any]],
    pages: list[dict[str, str]],
    default_crop: str,
) -> tuple[GraphBuilder, list[str]]:
    graph = GraphBuilder()
    warnings: list[str] = []
    page_index = index_pages(pages)
    crop_of_variety: dict[str, str] = {}

    # Node loài mặc định luôn tồn tại, kể cả khi trang loài rỗng thông tin.
    known_crops = {
        clean_value(page["crop"]) for page in pages if clean_value(page["crop"])
    }
    if default_crop:
        known_crops.add(default_crop)
    # Sắp xếp để thứ tự node ổn định giữa các lần chạy (set có thứ tự
    # phụ thuộc hash ngẫu nhiên hóa của tiến trình).
    for crop in sorted(known_crops):
        graph.add_node("Crop", crop)

    # Chọn một cách viết chuẩn cho mỗi loài (norm_key có thể gom nhiều
    # cách viết về cùng một khóa, ví dụ "Lúa" và "lua") để mọi cạnh trỏ
    # đúng node loài đã tạo. Sắp xếp giữ cách viết ổn định giữa các lần chạy.
    canonical_crop: dict[str, str] = {}
    for crop in sorted(known_crops):
        canonical_crop.setdefault(norm_key(crop), crop)

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        page = resolve_page(entity, page_index)
        kind = str(entity.get("kind") or page.get("kind") or "variety").lower()
        crop = (
            clean_value(entity.get("crop"))
            or clean_value(page.get("crop"))
            or default_crop
        )
        # Chuẩn hóa cách viết loài để cạnh trỏ đúng node đã tạo ở trên.
        if crop:
            crop = canonical_crop.get(norm_key(crop), crop)
        raw_name = clean_value(entity.get("entity")) or clean_value(
            page.get("title")
        )
        if not raw_name:
            warnings.append("Bỏ qua một thực thể không có tên.")
            continue

        properties_in = entity.get("properties")
        properties_in = properties_in if isinstance(properties_in, dict) else {}

        # Phân loại khoa học luôn thuộc về node cha, kể cả khi nó nằm trong
        # infobox của một trang giống.
        taxonomy = normalize_taxonomy(entity.get("taxonomy"))
        for raw_key, mapped_key in TAXONOMY_KEYS.items():
            value = clean_value(properties_in.get(raw_key))
            if value:
                taxonomy.setdefault(mapped_key, value)
        if taxonomy and (crop or kind == "species"):
            graph.add_node("Crop", crop or raw_name, taxonomy)

        resistant_items = as_pest_list(entity.get("resistant_to"))
        susceptible_items = as_pest_list(entity.get("susceptible_to"))

        if kind == "species":
            # ---- Node cha: thông tin chung của loài ----
            crop = crop or raw_name
            properties = {
                key: clean_value(properties_in.get(key))
                for key in CROP_PROPERTY_KEYS
            }
            properties = {k: v for k, v in properties.items() if v}
            if not properties.get("description"):
                fallback = clean_value(
                    properties_in.get("text_embed")
                    or properties_in.get("characteristics")
                )
                if fallback:
                    properties["description"] = fallback
            if page.get("url"):
                properties["url"] = page["url"]
            if page.get("title"):
                properties["page_title"] = page["title"]

            node_id = graph.add_node("Crop", crop or raw_name, properties)
            for pest in as_list(entity.get("pests")):
                pest_name = canonical_pest(pest)
                graph.add_node("Pest", pest_name)
                graph.add_edge(
                    "Crop", node_id, "AFFECTED_BY", "Pest", pest_name
                )
            # Sâu bệnh nêu ở trang loài vẫn nối vào loài để mở rộng về sau.
            for item in resistant_items + susceptible_items:
                graph.add_node("Pest", item["name"])
                graph.add_edge(
                    "Crop", node_id, "AFFECTED_BY", "Pest", item["name"]
                )
            continue

        # ---- Node con: một giống cây ----
        properties = {
            key: clean_value(properties_in.get(key))
            for key in VARIETY_PROPERTY_KEYS
        }
        properties = {k: v for k, v in properties.items() if v}
        if not properties.get("pest_resistance"):
            summary = summarize_pest_profile(resistant_items, susceptible_items)
            if summary:
                properties["pest_resistance"] = summary
        if page.get("url"):
            properties["url"] = page["url"]
        if page.get("title"):
            properties["page_title"] = page["title"]

        variety_id = short_variety_name(raw_name, crop)

        # Nguồn gốc lai tạo là THUỘC TÍNH của chính node giống. Trước đây mỗi
        # tên bố/mẹ sinh ra một node :Variety rỗng nên đồ thị đầy node thừa.
        if not properties.get("crossbred_from"):
            parents = [
                short_variety_name(parent, crop)
                for parent in as_list(entity.get("parents"))
            ]
            parents = [
                parent
                for parent in dict.fromkeys(parents)
                if parent and norm_key(parent) != norm_key(variety_id)
            ]
            if parents:
                properties["crossbred_from"] = " / ".join(parents)

        extra = [f"{label_suffix(crop)}Variety"] if crop else []
        variety_id = graph.add_node("Variety", variety_id, properties, extra)
        if not variety_id:
            continue
        crop_of_variety[norm_key(variety_id)] = crop

        if crop:
            graph.add_node("Crop", crop)
            graph.add_edge("Crop", crop, "HAS_VARIETY", "Variety", variety_id)

        for item in resistant_items:
            graph.add_node("Pest", item["name"])
            graph.add_edge(
                "Variety",
                variety_id,
                "RESISTANT_TO",
                "Pest",
                item["name"],
                {"level": item["level"]} if item["level"] else {},
            )

        for item in susceptible_items:
            graph.add_node("Pest", item["name"])
            graph.add_edge(
                "Variety",
                variety_id,
                "SUSCEPTIBLE_TO",
                "Pest",
                item["name"],
                {"level": item["level"]} if item["level"] else {},
            )

        # Mọi sâu bệnh xuất hiện ở trang giống cũng là sâu bệnh của loài.
        crop_pests = [canonical_pest(pest) for pest in as_list(entity.get("pests"))]
        crop_pests += [item["name"] for item in resistant_items + susceptible_items]
        for pest_name in crop_pests:
            if not pest_name:
                continue
            graph.add_node("Pest", pest_name)
            if crop:
                graph.add_edge(
                    "Crop", crop, "AFFECTED_BY", "Pest", pest_name
                )

    # Một cặp (giống, sâu bệnh) không thể vừa kháng vừa nhiễm: giữ lại cạnh
    # có ghi mức độ, nếu cả hai đều không ghi thì ưu tiên "kháng".
    for key in list(graph.edges):
        source, rel_type, target = key
        if rel_type != "SUSCEPTIBLE_TO":
            continue
        twin = (source, "RESISTANT_TO", target)
        if twin not in graph.edges:
            continue
        susceptible_edge = graph.edges[key]
        resistant_edge = graph.edges[twin]
        drop = (
            key
            if not susceptible_edge["properties"].get("level")
            else twin
            if not resistant_edge["properties"].get("level")
            else key
        )
        graph.edges.pop(drop, None)
        warnings.append(
            f"Mâu thuẫn kháng/nhiễm giữa “{susceptible_edge['source']}” và "
            f"“{susceptible_edge['target']}”; đã giữ lại một quan hệ."
        )

    # Giống chưa gắn được vào loài nào thì gắn vào loài mặc định để đồ thị
    # không có node mồ côi.
    for (label, _), node in graph.nodes.items():
        if label != "Variety":
            continue
        if default_crop and not any(
            edge["type"] == "HAS_VARIETY"
            and norm_key(edge["target"]) == norm_key(node["id"])
            for edge in graph.edges.values()
        ):
            graph.add_edge(
                "Crop", default_crop, "HAS_VARIETY", "Variety", node["id"]
            )

    finalize_nodes(graph)
    return graph, warnings


def finalize_nodes(graph: GraphBuilder) -> None:
    """Đồng bộ thuộc tính cuối cùng cho node cha và node con.

    - Node giống: 5 thuộc tính chính luôn có mặt, thiếu thì ghi "Chưa rõ".
    - Node loài: bổ sung số giống, số sâu bệnh và các trường phân loại còn thiếu.
    """
    # pest_resistance suy ra từ chính các cạnh trong đồ thị nếu vẫn còn trống.
    profiles: dict[str, dict[str, list[dict[str, str]]]] = {}
    for edge in graph.edge_list():
        if edge["type"] not in ("RESISTANT_TO", "SUSCEPTIBLE_TO"):
            continue
        bucket = profiles.setdefault(
            norm_key(edge["source"]), {"resistant": [], "susceptible": []}
        )
        slot = "resistant" if edge["type"] == "RESISTANT_TO" else "susceptible"
        bucket[slot].append(
            {
                "name": edge["target"],
                "level": edge["properties"].get("level", ""),
            }
        )

    variety_count: dict[str, int] = {}
    pest_count: dict[str, int] = {}
    for edge in graph.edge_list():
        if edge["type"] == "HAS_VARIETY":
            key = norm_key(edge["source"])
            variety_count[key] = variety_count.get(key, 0) + 1
        elif edge["type"] == "AFFECTED_BY":
            key = norm_key(edge["source"])
            pest_count[key] = pest_count.get(key, 0) + 1

    for (label, key), node in graph.nodes.items():
        properties = node["properties"]

        if label == "Variety":
            if not properties.get("pest_resistance"):
                profile = profiles.get(key)
                if profile:
                    summary = summarize_pest_profile(
                        profile["resistant"], profile["susceptible"]
                    )
                    if summary:
                        properties["pest_resistance"] = summary
            for field in VARIETY_REQUIRED_KEYS:
                if not properties.get(field):
                    properties[field] = UNKNOWN_VALUE

        elif label == "Crop":
            properties["variety_count"] = str(variety_count.get(key, 0))
            properties["pest_count"] = str(pest_count.get(key, 0))
            if not properties.get("text_embed") and properties.get("description"):
                properties["text_embed"] = properties["description"]
            for field in CROP_REQUIRED_KEYS:
                if not properties.get(field):
                    properties[field] = UNKNOWN_VALUE


# ---------------------------------------------------------------------------
# Kết xuất
# ---------------------------------------------------------------------------

CYPHER_CONSTRAINTS = [
    "CREATE CONSTRAINT crop_id IF NOT EXISTS "
    "FOR (n:Crop) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT variety_id IF NOT EXISTS "
    "FOR (n:Variety) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT pest_id IF NOT EXISTS "
    "FOR (n:Pest) REQUIRE n.id IS UNIQUE",
]


def cypher_string(value: Any) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "")
    return f'"{text}"'


def cypher_map(properties: dict[str, Any]) -> str:
    # Không dùng clean_value ở đây: "Chưa rõ" là giá trị hợp lệ cần giữ lại,
    # nếu lọc bỏ thì file .cypher sẽ khác với dữ liệu đẩy qua driver.
    pairs = [
        f"{key}: {cypher_string(value)}"
        for key, value in properties.items()
        if str(value).strip()
    ]
    return "{" + ", ".join(pairs) + "}"


def safe_identifier(text: str) -> str:
    """Nhãn/loại quan hệ chỉ nhận [A-Za-z0-9_]."""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(text)) or "ENTITY"


def build_statements(graph: GraphBuilder) -> list[tuple[str, dict[str, Any]]]:
    """Sinh danh sách (cypher, params) dùng chung cho file .cypher và driver."""
    statements: list[tuple[str, dict[str, Any]]] = []

    for node in graph.node_list():
        label = safe_identifier(node["label"])
        extra = [safe_identifier(item) for item in node["extra_labels"]]
        set_clauses = ["n += $props"]
        if extra:
            set_clauses.insert(0, "n:" + ":".join(extra))
        statements.append(
            (
                f"MERGE (n:{label} {{id: $id}}) SET " + ", ".join(set_clauses),
                {"id": node["id"], "props": dict(node["properties"])},
            )
        )

    for edge in graph.edge_list():
        source_label = safe_identifier(edge["source_label"])
        target_label = safe_identifier(edge["target_label"])
        rel_type = safe_identifier(edge["type"]).upper()
        cypher = (
            f"MATCH (a:{source_label} {{id: $source}}), "
            f"(b:{target_label} {{id: $target}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props"
        )
        statements.append(
            (
                cypher,
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "props": dict(edge["properties"]),
                },
            )
        )

    return statements


def render_cypher_file(graph: GraphBuilder, crop_names: list[str]) -> str:
    lines: list[str] = [
        "// Knowledge Graph generated by WikiKGExtractor",
        "// Crop(s): " + (", ".join(crop_names) if crop_names else "unknown"),
        "// Run with cypher-shell or paste into Neo4j Browser.",
        "",
        "// ----- 0. CONSTRAINTS -----",
    ]
    lines.extend(f"{constraint};" for constraint in CYPHER_CONSTRAINTS)

    sections = [
        ("1. CROP NODES (node cha, kèm phân loại khoa học)", "Crop"),
        ("2. VARIETY NODES (node con)", "Variety"),
        ("3. PEST NODES", "Pest"),
    ]
    for heading, label in sections:
        nodes = [node for node in graph.node_list() if node["label"] == label]
        if not nodes:
            continue
        lines.extend(["", f"// ----- {heading} -----"])
        for node in sorted(nodes, key=lambda item: item["id"]):
            merge = (
                f"MERGE (n:{safe_identifier(node['label'])} "
                f"{{id: {cypher_string(node['id'])}}})"
            )
            if node["extra_labels"]:
                extra = ":".join(
                    safe_identifier(item) for item in node["extra_labels"]
                )
                merge += f" SET n:{extra}"
                if node["properties"]:
                    merge += f", n += {cypher_map(node['properties'])}"
            elif node["properties"]:
                merge += f" SET n += {cypher_map(node['properties'])}"
            lines.append(merge + ";")

    edge_sections = [
        ("4. CROP -> VARIETY", ["HAS_VARIETY"]),
        ("5. PEST INTERACTIONS", ["RESISTANT_TO", "SUSCEPTIBLE_TO", "AFFECTED_BY"]),
    ]
    for heading, types in edge_sections:
        edges = [edge for edge in graph.edge_list() if edge["type"] in types]
        if not edges:
            continue
        lines.extend(["", f"// ----- {heading} -----"])
        for edge in edges:
            source_label = safe_identifier(edge["source_label"])
            target_label = safe_identifier(edge["target_label"])
            rel_type = safe_identifier(edge["type"]).upper()
            statement = (
                f"MATCH (a:{source_label} {{id: {cypher_string(edge['source'])}}}), "
                f"(b:{target_label} {{id: {cypher_string(edge['target'])}}}) "
                f"MERGE (a)-[r:{rel_type}]->(b)"
            )
            if edge["properties"]:
                statement += f" SET r += {cypher_map(edge['properties'])}"
            lines.append(statement + ";")

    lines.append("")
    return "\n".join(lines)


def create_nodes_edges(
    graph: GraphBuilder,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Định dạng graph document ổn định cho web và các consumer khác."""
    return {
        "schema_version": "1.0",
        "metadata": metadata or {},
        "nodes": [
            {
                "id": node["id"],
                "label": node["id"],
                "type": node["label"],
                "extra_labels": node["extra_labels"],
                "properties": node["properties"],
            }
            for node in graph.node_list()
        ],
        "edges": [
            {
                "source": edge["source"],
                "source_type": edge["source_label"],
                "type": edge["type"],
                "target": edge["target"],
                "target_type": edge["target_label"],
                "properties": edge["properties"],
            }
            for edge in graph.edge_list()
        ],
    }


def create_mediawiki_table(graph: GraphBuilder) -> str:
    unknown = UNKNOWN_VALUE
    lines = [
        '{| class="wikitable sortable" style="text-align: left;"',
        "! STT !! Tên giống !! Thời gian sinh trưởng !! Chiều cao cây !! "
        "Năng suất !! Kháng sâu bệnh !! Đặc tính !! Kháng !! Nhiễm",
    ]

    def targets(node_id: str, rel_type: str) -> str:
        values = []
        for edge in graph.edge_list():
            if edge["type"] != rel_type:
                continue
            if norm_key(edge["source"]) != norm_key(node_id):
                continue
            level = edge["properties"].get("level", "")
            values.append(f"{edge['target']} ({level})" if level else edge["target"])
        return ", ".join(dict.fromkeys(values)) or unknown

    varieties = [node for node in graph.node_list() if node["label"] == "Variety"]
    for index, node in enumerate(
        sorted(varieties, key=lambda item: item["id"]), start=1
    ):
        properties = node["properties"]
        page_title = properties.get("page_title", node["id"])
        lines.extend(
            [
                "|-",
                f"| {index}",
                f"| [[{page_title}|{node['id']}]]",
                f"| {properties.get('growth_duration', unknown)}",
                f"| {properties.get('plant_height', unknown)}",
                f"| {properties.get('yield_amount', unknown)}",
                f"| {properties.get('pest_resistance', unknown)}",
                f"| {properties.get('characteristics', unknown)}",
                f"| {targets(node['id'], 'RESISTANT_TO')}",
                f"| {targets(node['id'], 'SUSCEPTIBLE_TO')}",
            ]
        )

    lines.append("|}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Đẩy thẳng vào Neo4j
# ---------------------------------------------------------------------------


def push_to_neo4j(
    statements: list[tuple[str, dict[str, Any]]],
    uri: str,
    user: str,
    password: str,
    database: str,
) -> int:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu thư viện neo4j. Chạy: pip install -r bin/requirements.txt"
        ) from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    applied = 0
    try:
        driver.verify_connectivity()
        with driver.session(database=database or None) as session:
            for constraint in CYPHER_CONSTRAINTS:
                try:
                    session.run(constraint)
                except Exception as exc:  # phiên bản Neo4j cũ có cú pháp khác
                    print(f"[kg] Bỏ qua ràng buộc: {exc}", file=sys.stderr)
            for cypher, params in statements:
                session.run(cypher, **params)
                applied += 1
    finally:
        driver.close()
    return applied


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dựng Knowledge Graph cây trồng và xuất Cypher cho Neo4j."
    )
    parser.add_argument("--input", required=True, help="Đường dẫn raw_data.json")
    parser.add_argument("--output-dir", required=True, help="Thư mục kết xuất")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--max-chars",
        type=int,
        default=6000,
        help="Cắt bớt nội dung mỗi trang trước khi gửi cho AI (0 = không cắt).",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Chỉ dùng bộ trích xuất bằng luật, không gọi Gemini.",
    )
    parser.add_argument(
        "--push-neo4j",
        action="store_true",
        help="Ghi thẳng đồ thị vào Neo4j sau khi dựng xong.",
    )
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", ""))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument(
        "--neo4j-database", default=os.getenv("NEO4J_DATABASE", "neo4j")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.is_file():
        print(f"Không tìm thấy file đầu vào: {input_path}", file=sys.stderr)
        return 2

    skipped_titles: list[str] = []
    try:
        pages = load_pages(input_path, skipped_titles)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Không đọc được file đầu vào: {error}", file=sys.stderr)
        return 2
    if not pages:
        print("Không có trang nào có nội dung trong file đầu vào.", file=sys.stderr)
        return 3

    species_pages = [page for page in pages if page["kind"] == "species"]
    variety_pages = [page for page in pages if page["kind"] != "species"]
    default_crop = ""
    if species_pages:
        default_crop = species_pages[0]["crop"] or species_pages[0]["title"]
    elif variety_pages:
        default_crop = variety_pages[0]["crop"]

    print(
        f"[kg] Đầu vào: {len(species_pages)} trang loài, "
        f"{len(variety_pages)} trang giống. Loài mặc định: {default_crop or 'N/A'}",
        flush=True,
    )

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    warnings = [
        f"Bỏ qua trang không xác định an toàn là loài hoặc giống: “{title}”."
        for title in skipped_titles
    ]
    for warning in warnings:
        print(f"[kg] {warning}", file=sys.stderr, flush=True)

    # Trang loài đứng trước để AI có ngữ cảnh loài trong batch đầu tiên.
    ordered_pages = species_pages + variety_pages

    if args.no_ai or not api_key:
        if not args.no_ai:
            warnings.append(
                "Thiếu GEMINI_API_KEY nên chỉ dựng KG bằng bộ trích xuất theo luật."
            )
            print(f"[kg] {warnings[-1]}", file=sys.stderr, flush=True)
        entities = [heuristic_entity(page) for page in ordered_pages]
    else:
        entities, ai_warnings = extract_with_ai(
            pages=ordered_pages,
            api_key=api_key,
            model=args.model,
            batch_size=args.batch_size,
            retries=args.retries,
            max_chars=args.max_chars,
        )
        warnings.extend(ai_warnings)
        # Trang nào AI bỏ sót thì bù bằng heuristic để không mất node.
        covered = {norm_key(item.get("page_title")) for item in entities}
        covered |= {norm_key(item.get("entity")) for item in entities}
        for page in ordered_pages:
            if norm_key(page["title"]) in covered:
                continue
            if norm_key(short_variety_name(page["title"], page["crop"])) in covered:
                continue
            entities.append(heuristic_entity(page))
            warnings.append(f"AI bỏ sót trang “{page['title']}”; đã bù bằng luật.")

    extraction_method = "ai" if not args.no_ai and api_key else "rules"
    extractor_version = EXTRACTOR_VERSION
    candidate_claims = create_candidate_claims(
        entities,
        ordered_pages,
        extraction_method=extraction_method,
        extractor_version=extractor_version,
    )
    warnings.extend(
        "Không tạo candidate claim cho trang thiếu provenance: " + title
        for title in candidate_claims["summary"]["skipped_source_pages"]
    )

    graph, graph_warnings = build_graph(entities, ordered_pages, default_crop)
    warnings.extend(graph_warnings)

    # Node cha phải có thông tin chung; nếu wiki không có trang loài thì nhờ
    # API viết tóm tắt, không có API thì giữ nguyên "Chưa rõ".
    if not args.no_ai and api_key:
        warnings.extend(
            fill_crop_descriptions(graph, api_key, args.model, args.retries)
        )

    crop_names = [
        node["id"] for node in graph.node_list() if node["label"] == "Crop"
    ]
    statements = build_statements(graph)

    graph_document = create_nodes_edges(
        graph,
        {
            "extractor_version": extractor_version,
            "extraction_method": extraction_method,
            "source_pages": [
                {
                    key: page.get(key, "")
                    for key in (
                        "title",
                        "url",
                        "kind",
                        "crop",
                        "page_id",
                        "revision_id",
                        "content_hash",
                    )
                    if page.get(key, "") not in ("", None)
                }
                for page in ordered_pages
            ],
            "warnings": warnings,
        },
    )

    outputs = {
        "graph_data_raw.json": json.dumps(entities, ensure_ascii=False, indent=2),
        "candidate_claims.json": json.dumps(
            candidate_claims,
            ensure_ascii=False,
            indent=2,
        ),
        "neo4j_import.cypher": render_cypher_file(graph, crop_names),
        "mediawiki_bang_thuoc_tinh_cay_trong.txt": create_mediawiki_table(graph),
    }

    node_counts: dict[str, int] = {}
    for node in graph.node_list():
        node_counts[node["label"]] = node_counts.get(node["label"], 0) + 1
    edge_counts: dict[str, int] = {}
    for edge in graph.edge_list():
        edge_counts[edge["type"]] = edge_counts.get(edge["type"], 0) + 1

    summary = {
        "crops": crop_names,
        "page_count": len(ordered_pages),
        "species_page_count": len(species_pages),
        "variety_page_count": len(variety_pages),
        "node_count": len(graph.node_list()),
        "edge_count": len(graph.edge_list()),
        "candidate_claim_count": candidate_claims["summary"]["claim_count"],
        "traceability_complete_count": candidate_claims["summary"][
            "traceability_complete_count"
        ],
        "nodes_by_label": node_counts,
        "edges_by_type": edge_counts,
        "warnings": warnings,
        "neo4j_pushed": False,
    }

    if args.push_neo4j:
        password = os.getenv("NEO4J_PASSWORD", "")
        uri = args.neo4j_uri or "bolt://localhost:7687"
        try:
            applied = push_to_neo4j(
                statements, uri, args.neo4j_user, password, args.neo4j_database
            )
            summary["neo4j_pushed"] = True
            summary["neo4j_statements"] = applied
            print(f"[kg] Đã ghi {applied} câu lệnh vào Neo4j ({uri}).", flush=True)
        except Exception as exc:
            message = f"Không ghi được vào Neo4j: {exc}"
            warnings.append(message)
            summary["warnings"] = warnings
            print(f"[kg] {message}", file=sys.stderr, flush=True)

    graph_document["metadata"]["warnings"] = warnings
    outputs["graph_nodes_edges.json"] = json.dumps(
        graph_document, ensure_ascii=False, indent=2
    )
    outputs["kg_summary.json"] = json.dumps(summary, ensure_ascii=False, indent=2)

    for name, content in outputs.items():
        (output_dir / name).write_text(content, encoding="utf-8")
        print(f"[kg] Đã ghi {output_dir / name}", flush=True)

    print(
        f"[kg] Hoàn tất: {summary['node_count']} node, "
        f"{summary['edge_count']} quan hệ.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
