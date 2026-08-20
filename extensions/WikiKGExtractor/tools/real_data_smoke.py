"""Smoke harness cho dữ liệu thật (5 trang WikiCrop, bản private).

Đọc dump MySQL mediawiki (mediawiki_new1), trích 5 trang đã chốt revision,
dựng raw_data.json, chạy kg_worker.py --no-ai hai lần, kiểm tra:
- thoát mã 0 và đủ file đầu ra,
- graph_nodes_edges.json hợp lệ với schema/graph-document.v1.schema.json,
- mọi cạnh trỏ đúng node tồn tại (không cạnh mồ côi),
- hai lần chạy cho ra byte-giống hệt nhau (deterministic).

Dữ liệu trích xuất chỉ ghi vào thư mục do người dùng chỉ định (mặc định là
thư mục tạm ngoài repo). KHÔNG commit dump hoặc dữ liệu trích xuất.

Cách chạy:
    python3 extensions/WikiKGExtractor/tools/real_data_smoke.py \
        --dump ~/Thesis/mediawiki_20260617.sql \
        --output-dir /tmp/wikicrop-smoke
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Trang mục tiêu: (title chuẩn MediaWiki, kind, crop)
TARGET_PAGES = [
    ("Lúa", "species", "Lúa"),
    ("Lúa_OM5451", "variety", "Lúa"),
    ("Lúa_ST24", "variety", "Lúa"),
    ("Lúa_ST25", "variety", "Lúa"),
    # Trang kỹ thuật: không khai báo kind để chạy đúng nhánh tương thích
    # ngược của load_pages (quy về "variety") - hành vi hiện tại cần báo cáo.
    ("Kỹ_thuật_trồng_và_chăm_sóc_lúa", None, "Lúa"),
]


class SqlValueParser:
    """Tokenizer cho dòng VALUES của mysqldump: số, 'string', 0xhex, NULL.

    Chỉ cần đủ cho các bảng page/revision/slots/content/text; không xử lý
    các kiểu khác (FROM_BASE64, UNHEX(...)) vì dump này không dùng.
    """

    @staticmethod
    def parse_values(line: str) -> list[str | int | None | bytes]:
        values: list[str | int | None | bytes] = []
        index = 0
        length = len(line)
        while index < length:
            ch = line[index]
            if ch in " \t,":
                index += 1
                continue
            if ch == "(":
                index += 1
                continue
            if ch == ")":
                index += 1
                continue
            if line.startswith("NULL", index):
                values.append(None)
                index += 4
                continue
            if line.startswith("0x", index):
                end = index + 2
                while end < length and line[end] in "0123456789abcdefABCDEF":
                    end += 1
                values.append(bytes.fromhex(line[index + 2 : end]))
                index = end
                continue
            if ch == "'":
                end = index + 1
                buffer: list[str] = []
                while end < length:
                    if line[end] == "\\" and end + 1 < length:
                        nxt = line[end + 1]
                        mapping = {
                            "0": "\0", "'": "'", '"': '"', "b": "\b",
                            "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a",
                            "\\": "\\", "%": "%", "_": "_",
                        }
                        buffer.append(mapping.get(nxt, nxt))
                        end += 2
                        continue
                    if line[end] == "'":
                        if end + 1 < length and line[end + 1] == "'":
                            buffer.append("'")
                            end += 2
                            continue
                        end += 1
                        break
                    buffer.append(line[end])
                    end += 1
                values.append("".join(buffer))
                index = end
                continue
            if ch.isdigit() or ch == "-":
                end = index
                while end < length and (
                    line[end].isdigit() or line[end] in ".-+eE"
                ):
                    end += 1
                raw = line[index:end]
                try:
                    values.append(int(raw))
                except ValueError:
                    values.append(float(raw))
                index = end
                continue
            raise ValueError(f"Không phân tích được giá trị SQL tại vị trí {index}: {line[index:index+40]!r}")
        return values


def decode(value: str | int | None | bytes) -> str:
    """Giải mã giá trị SQL thành chuỗi utf-8."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def extract_pages(dump_path: Path) -> list[dict]:
    """Trích 5 trang mục tiêu từ dump (chỉ database mediawiki_new1)."""
    page_rows: dict[int, dict] = {}
    rev_page: dict[int, int] = {}
    slot_content: dict[int, int] = {}
    content_address: dict[int, str] = {}
    text_rows: dict[int, str] = {}
    target_set = {title for title, _, _ in TARGET_PAGES}

    current_db = ""
    pending_statement = ""
    with dump_path.open("r", encoding="utf-8", errors="replace") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            use = re.match(r"^USE `([^`]+)`;?$", line)
            if use:
                current_db = use.group(1)
                continue
            if current_db != "mediawiki_new1":
                continue
            create = re.match(r"^CREATE TABLE `([^`]+)`", line)
            if create:
                current_table = create.group(1)
                continue
            if line.startswith("INSERT INTO"):
                if pending_statement:
                    raise RuntimeError("Gặp INSERT mới khi statement trước chưa kết thúc")
                pending_statement = line
            elif pending_statement:
                pending_statement += " " + line
            else:
                continue
            if not pending_statement.endswith(";"):
                continue
            statement = pending_statement
            pending_statement = ""
            table_match = re.match(
                r"^INSERT INTO `([^`]+)` \(([^)]*)\) VALUES", statement
            )
            if not table_match:
                continue
            table = table_match.group(1)
            if table not in ("page", "revision", "slots", "content", "text"):
                continue
            values_text = statement[table_match.end() :]
            if values_text.endswith(";"):
                values_text = values_text[:-1]
            values = SqlValueParser.parse_values(values_text)

            if table == "page":
                # page_id, namespace, title, redirect, is_new, random, touched,
                # links_updated, latest, len, content_model, lang
                for offset in range(0, len(values), 12):
                    row = values[offset : offset + 12]
                    if len(row) < 12:
                        break
                    page_id = int(row[0])
                    namespace = int(row[1])
                    title = decode(row[2])
                    if namespace == 0 and title in target_set:
                        page_rows[page_id] = {
                            "title": title,
                            "latest": int(row[8]),
                        }
            elif table == "revision":
                # rev_id, rev_page, comment_id, actor, timestamp, minor,
                # deleted, len, parent_id, sha1
                for offset in range(0, len(values), 10):
                    row = values[offset : offset + 10]
                    if len(row) < 10:
                        break
                    rev_page[int(row[0])] = int(row[1])
            elif table == "slots":
                # slot_revision_id, slot_role_id, slot_content_id, slot_origin
                for offset in range(0, len(values), 4):
                    row = values[offset : offset + 4]
                    if len(row) < 4:
                        break
                    if int(row[1]) == 1:  # vai trò "main"
                        slot_content[int(row[0])] = int(row[2])
            elif table == "content":
                # content_id, size, sha1, model (int -> content_models), address
                for offset in range(0, len(values), 5):
                    row = values[offset : offset + 5]
                    if len(row) < 5:
                        break
                    # content_models: model_id 1 = "wikitext".
                    if int(row[3]) != 1:
                        continue
                    content_address[int(row[0])] = decode(row[4])
            elif table == "text":
                # old_id, old_text, old_flags
                for offset in range(0, len(values), 3):
                    row = values[offset : offset + 3]
                    if len(row) < 3:
                        break
                    text_rows[int(row[0])] = decode(row[1])

    result: list[dict] = []
    for title, kind, crop in TARGET_PAGES:
        matching = [pid for pid, row in page_rows.items() if row["title"] == title]
        if not matching:
            raise RuntimeError(f"Không tìm thấy trang {title!r} trong dump")
        page_id = matching[0]
        latest = page_rows[page_id]["latest"]
        content_id = slot_content.get(latest)
        if content_id is None:
            raise RuntimeError(f"Trang {title!r} (page {page_id}) thiếu slot nội dung")
        address = content_address.get(content_id)
        if not address or not address.startswith("tt:"):
            raise RuntimeError(
                f"Trang {title!r} có content_address không hỗ trợ: {address!r}"
            )
        old_id = int(address[3:])
        text = text_rows.get(old_id)
        if text is None:
            raise RuntimeError(f"Trang {title!r} thiếu nội dung (text id {old_id})")
        text = text.strip()
        if not text:
            raise RuntimeError(f"Trang {title!r} có nội dung rỗng")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        page = {
            "title": title.replace("_", " "),
            "url": f"https://wikicrop.local/index.php?title={title.replace(' ', '_')}",
            "kind": kind,
            "crop": crop,
            "page_id": page_id,
            "revision_id": latest,
            "content_hash": content_hash,
            "text": text,
        }
        if kind is None:
            del page["kind"]
        result.append(page)
    return result


def run_worker(worker: Path, input_path: Path, output_dir: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(worker), "--no-ai",
            "--input", str(input_path), "--output-dir", str(output_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True, help="Đường dẫn dump MySQL")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Thư mục kết xuất (mặc định: thư mục tạm ngoài repo)",
    )
    parser.add_argument(
        "--worker",
        default=str(Path(__file__).resolve().parents[1] / "bin" / "kg_worker.py"),
        help="Đường dẫn kg_worker.py",
    )
    args = parser.parse_args()

    dump_path = Path(args.dump).expanduser().resolve()
    if not dump_path.is_file():
        print(f"Không tìm thấy dump: {dump_path}", file=sys.stderr)
        return 2

    pages = extract_pages(dump_path)
    raw_data = {"pages": pages}

    if args.output_dir:
        out_root = Path(args.output_dir).expanduser().resolve()
    else:
        out_root = Path(tempfile.mkdtemp(prefix="wikicrop-smoke-"))
    out_root.mkdir(parents=True, exist_ok=True)

    input_path = out_root / "raw_data.real.json"
    input_path.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[smoke] Đã trích {len(pages)} trang -> {input_path}")
    for page in pages:
        print(
            f"[smoke]   {page['title']!r} page_id={page['page_id']} "
            f"rev={page['revision_id']} kind={page.get('kind', '(fallback)')} "
            f"len={len(page['text'])}"
        )

    outputs = []
    for index, seed in enumerate(("1", "2")):
        run_dir = out_root / f"run-{index + 1}"
        env = dict(__import__("os").environ)
        env["PYTHONHASHSEED"] = seed
        result = run_worker(Path(args.worker), input_path, run_dir, env)
        if result.returncode != 0:
            print(f"[smoke] Lần chạy {index + 1} thất bại (rc={result.returncode}):\n{result.stderr}", file=sys.stderr)
            return 3
        graph_path = run_dir / "graph_nodes_edges.json"
        if not graph_path.is_file():
            print(f"[smoke] Thiếu {graph_path}", file=sys.stderr)
            return 3
        outputs.append(graph_path.read_bytes())

    print("[smoke] Hai lần chạy byte-giống hệt nhau:", outputs[0] == outputs[1])
    if outputs[0] != outputs[1]:
        print("[smoke] KHÔNG khớp: đầu ra không deterministic", file=sys.stderr)
        return 4

    import json as _json
    doc = _json.loads(outputs[0].decode("utf-8"))
    ids = {node["id"] for node in doc["nodes"]}
    dangling = [
        edge for edge in doc["edges"]
        if edge["source"] not in ids or edge["target"] not in ids
    ]
    print(f"[smoke] nodes={len(doc['nodes'])} edges={len(doc['edges'])} cạnh mồ côi={len(dangling)}")
    if dangling:
        print("[smoke] Phát hiện cạnh mồ côi:", dangling, file=sys.stderr)
        return 5

    try:
        import jsonschema  # noqa: F401
    except ImportError:
        print("[smoke] jsonschema chưa cài, bỏ qua kiểm tra schema", file=sys.stderr)
    else:
        schema = _json.loads(
            (Path(__file__).resolve().parents[1] / "schema" / "graph-document.v1.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.validate(doc, schema)
        print("[smoke] Schema validation: OK")

    print(f"[smoke] Kết quả tại {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())