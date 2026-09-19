# WikiKGExtractor 2.3.0

**WikiKGExtractor** là tiện ích mở rộng (extension) cho MediaWiki, được xây dựng cho hệ thống **Wikicrop**. Extension cho phép nhập tên một loài cây, thu thập nội dung trang loài và các trang giống thuộc mục **Danh sách/Giống**, sau đó xuất dữ liệu và tùy chọn xây dựng **Knowledge Graph** để lưu trữ trên **Neo4j**.

---

## 1. Cấu trúc thư mục Extension

Thư mục được đặt tại `extensions/WikiKGExtractor/`:

```text
WikiKGExtractor/
├── extension.json
├── WikiKGExtractor.alias.php
├── bin/
│   ├── kg_worker.py
│   └── requirements.txt
├── i18n/
├── resources/
│   ├── lib/
│   │   ├── cytoscape/
│   │   └── foreign-resources.yaml
│   ├── wikikg.css
│   └── wikikg.graph.js
├── schema/
│   ├── candidate-claims.v1.schema.json
│   ├── corpus-manifest.v1.schema.json
│   ├── evaluation-protocol.v1.schema.json
│   ├── evaluation-report.v1.schema.json
│   ├── graph-document.v1.schema.json
│   └── raw-data.v1.schema.json
├── protocol/
│   └── rice-engineering-pilot.v1.json
├── src/
│   ├── ExportWriter.php
│   ├── Hooks.php
│   ├── KgRunner.php
│   ├── LocalWikiExtractor.php
│   ├── SpecialWikiKGExtractor.php
│   └── GraphDocument.php
├── tools/
│   ├── evaluate_pilot.py
│   ├── real_data_smoke.py
│   └── rice_corpus.py
├── tests/
└── README.md
```

---

## 2. Cài đặt Extension

### Bước 1: Sao chép Extension

Sao chép toàn bộ thư mục `WikiKGExtractor` vào thư mục `extensions/` của Wikicrop:

```text
C:\xampp\htdocs\Wikicrop\extensions\WikiKGExtractor\
```

### Bước 2: Kích hoạt Extension

Mở file `LocalSettings.php` trong thư mục gốc của Wikicrop và thêm:

```php
wfLoadExtension( 'WikiKGExtractor' );
```

---

## 3. Cấu hình Knowledge Graph

(Nếu chỉ muốn thu thập và xuất dữ liệu TXT/JSON có thể bỏ qua bước này)

Nếu muốn tạo **Knowledge Graph**, cài các thư viện Python cần thiết:

```powershell
cd C:\xampp\htdocs\Wikicrop\extensions\WikiKGExtractor
python -m pip install -r bin\requirements.txt
```

Sau đó thêm cấu hình vào `LocalSettings.php`:

```php
$wgWikiKGExtractorEnableKG = true;
$wgWikiKGExtractorPythonCommand = 'python';
$wgWikiKGExtractorGeminiApiKey = getenv( 'GEMINI_API_KEY' );
$wgWikiKGExtractorGeminiModel = 'gemini-2.5-flash';
```

Trong đó:

* `GEMINI_API_KEY`: API key dùng cho việc trích xuất dữ liệu bằng Gemini.
* Không nên ghi trực tiếp API key vào `LocalSettings.php`.

Trên Windows, có thể đặt API key bằng biến môi trường:

```powershell
setx GEMINI_API_KEY "api-key-cua-ban" /M
```

Sau khi đặt biến môi trường, **khởi động lại Apache** để hệ thống nhận giá trị mới.

> Nếu không có Gemini API key, extension vẫn có thể tạo Knowledge Graph bằng bộ trích xuất theo luật. Tuy nhiên, lượng thông tin được trích xuất có thể ít hơn so với chế độ sử dụng AI.

---

## 4. Cấu hình Neo4j

Việc kết nối trực tiếp với Neo4j là **tùy chọn**. Nếu không bật chức năng này, extension vẫn tạo file `neo4j_import.cypher` để có thể nhập dữ liệu vào Neo4j thủ công.

### Neo4j Desktop

Nếu sử dụng Neo4j chạy trên máy cá nhân:

```php
$wgWikiKGExtractorPushToNeo4j = true;
$wgWikiKGExtractorNeo4jUri = 'bolt://localhost:7687';
$wgWikiKGExtractorNeo4jUser = 'neo4j';
$wgWikiKGExtractorNeo4jPassword = getenv( 'NEO4J_PASSWORD' );
$wgWikiKGExtractorNeo4jDatabase = 'neo4j';
```

### Neo4j Aura

Nếu sử dụng Neo4j Aura:

```php
$wgWikiKGExtractorPushToNeo4j = true;
$wgWikiKGExtractorNeo4jUri = 'neo4j+s://your-instance.databases.neo4j.io';
$wgWikiKGExtractorNeo4jUser = 'neo4j';
$wgWikiKGExtractorNeo4jPassword = getenv( 'NEO4J_PASSWORD' );
$wgWikiKGExtractorNeo4jDatabase = 'neo4j';
```

Mật khẩu Neo4j nên được lưu dưới dạng biến môi trường:

```powershell
setx NEO4J_PASSWORD "mat-khau-neo4j" /M
```

Sau đó **khởi động lại Apache**.

---

## 5. Sử dụng Extension

Sau khi cài đặt và cấu hình, truy cập:

```text
http://localhost/Wikicrop/index.php/Special:WikiKGExtractor
```

Thực hiện các bước:

1. Nhập tên loài cây, ví dụ `Lúa`.
2. Chọn **Tạo Knowledge Graph** nếu muốn xây dựng Knowledge Graph.
3. Nhấn nút thực hiện.

Extension sẽ:

1. Tìm trang loài tương ứng.
2. Thu thập nội dung trang loài.
3. Tìm và thu thập các trang giống trong mục **Danh sách/Giống**.
4. Xuất dữ liệu thu thập được.
5. Nếu bật Knowledge Graph, trích xuất các thực thể và quan hệ.
6. Tạo dữ liệu Knowledge Graph và file Cypher.
7. Nếu bật kết nối Neo4j, tự động nạp dữ liệu vào Neo4j.

---

## 6. Các file kết quả

Sau mỗi lần thực hiện, extension tạo một thư mục phiên xuất chứa các file chính:

| File                                      | Nội dung                                               |
| ----------------------------------------- | ------------------------------------------------------ |
| `raw_data.txt`                            | Dữ liệu văn bản thu thập từ các trang Wiki             |
| `raw_data.json`                           | Dữ liệu thu thập ở dạng JSON                           |
| `graph_data_raw.json`                     | Dữ liệu thực thể và quan hệ được trích xuất            |
| `candidate_claims.json`                   | Candidate claim đang chờ duyệt và evidence theo revision |
| `graph_nodes_edges.json`                  | Danh sách node và edge của Knowledge Graph             |
| `neo4j_import.cypher`                     | Script Cypher dùng để nhập dữ liệu vào Neo4j           |
| `mediawiki_bang_thuoc_tinh_cay_trong.txt` | Bảng thông tin các giống ở dạng wikitext               |
| `kg_summary.json`                         | Thống kê kết quả và các cảnh báo trong quá trình xử lý |

---

## 7. Mô hình Knowledge Graph

Knowledge Graph của extension sử dụng ba loại node chính:

```text
Crop       → Loài cây trồng
Variety    → Giống cây
Pest       → Sâu bệnh
```

Các quan hệ chính:

```text
(Crop)-[:HAS_VARIETY]->(Variety)

(Variety)-[:RESISTANT_TO]->(Pest)

(Variety)-[:SUSCEPTIBLE_TO]->(Pest)

(Crop)-[:AFFECTED_BY]->(Pest)
```

Ví dụ với loài **Lúa**:

```text
Lúa
 ├── HAS_VARIETY → OM5451
 ├── HAS_VARIETY → Jasmine 85
 └── AFFECTED_BY → Bệnh đạo ôn

OM5451
 ├── RESISTANT_TO → Bệnh đạo ôn
 └── SUSCEPTIBLE_TO → Rầy nâu
```

Thông tin phân loại khoa học của loài được lưu trực tiếp trong node `Crop`. Thông tin đặc điểm của giống được lưu trong node `Variety`.

Các trường hợp thiếu thông tin được ghi là: "Chưa rõ"

---

## 8. Kiểm tra kết quả trên Neo4j

Sau khi dữ liệu được nạp vào Neo4j, có thể kiểm tra nhanh bằng các truy vấn sau.

**Toàn bộ giống của một loài**

```cypher
MATCH (c:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
RETURN c, v;
```

**Một vài giống**
```cypher
MATCH p = (:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
WITH p, v LIMIT 3
MATCH p2 = (v)-[:RESISTANT_TO|SUSCEPTIBLE_TO]->(:Pest)
RETURN p, p2;
```

**Bảng thuộc tính các giống**:

```cypher
MATCH (:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
RETURN v.id, v.crossbred_from, v.growth_duration, v.plant_height,
       v.yield_amount, v.pest_resistance
ORDER BY v.id;
```

**Thông tin loài và phân loại khoa học**

```cypher
MATCH (c:Crop {id: "Lúa"})
RETURN c.scientific_name, c.family, c.genus, c.variety_count, c.pest_count;
```

**Quan hệ sâu bệnh của vài giống**:

```cypher
MATCH (:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
WITH v LIMIT 3
MATCH path = (v)-[:RESISTANT_TO|SUSCEPTIBLE_TO]->(:Pest)
RETURN path;
```

## 9. WikiCrop thesis development mode

The thesis integration keeps graph generation opt-in and does not modify WikiCrop pages.

For a local SQLite environment, load the extension only in the untracked `LocalSettings.php` file.

Keep these settings disabled until the extraction output has been reviewed:

```php
$wgWikiKGExtractorEnableKG = false;
$wgWikiKGExtractorPushToNeo4j = false;
```

Generated exports are written to the OS temporary directory after checking that it is outside the MediaWiki document root.

They are not exposed through a public URL unless an administrator explicitly configures `$wgWikiKGExtractorOutputBaseUrl`.

New raw inputs use schema version `1.0` and preserve both exact `wikitext` and rendered `text`.

The worker prefers `wikitext` for extraction and accepts older unversioned inputs through the legacy `text` field.

The first graph document uses schema version `1.0` and is validated against `schema/graph-document.v1.schema.json`.

Candidate claims use `schema/candidate-claims.v1.schema.json`.

Every generated claim starts with review status `pending` and carries page, revision, content hash, extraction method, and the best available source coordinate.

The `Special:WikiKGExtractor` result page renders a read-only semantic graph view from `graph_nodes_edges.json`.

Older `raw_data.json` files without a `kind` field are accepted only when the title identifies a species or variety safely.

Unrelated pages are skipped with a warning instead of being treated as varieties.

Collection and page-size limits are enabled by default through `WikiKGExtractorMaxCollectedPages` and `WikiKGExtractorMaxPageChars`.

The in-Wiki renderer also caps the number of displayed nodes and relationships while preserving the complete JSON export for offline analysis.

This view is intentionally implemented before parser tags, persistent graph tables, Gemini calls, or direct Neo4j writes.

## 10. Reproducible rice corpus

`tools/rice_corpus.py` discovers variety links under the `Danh sách giống lúa` section of `Lúa` revision 875.

It writes a deterministic metadata manifest plus private development and held-out inputs.

The output directory is required, and the tool rejects paths inside the repository to protect private page text.

```bash
python3 extensions/WikiKGExtractor/tools/rice_corpus.py \
    --dump <private-mediawiki-dump.sql> \
    --output-dir <private-output-dir>
```

The default split selects 20 pages through proportional strata based on template family and source length.

The tool selects the 10 development pages first.

It then selects 10 held-out pages after excluding the development set and every page recorded as inspected before the freeze.

The exclusion register is stored in the manifest so a previously observed page cannot silently enter the held-out split.

Do not inspect or tune rules against `raw-data.test.v1.json` before the rule version and evaluation protocol are frozen.

## 11. Frozen rules-only engineering pilot

The frozen rice pilot is defined by `protocol/rice-engineering-pilot.v1.json` and documented in `docs/knowledge-graph-pilot-protocol.md`.
The evaluator verifies pinned artifact hashes, JSON schemas, source records, exact evidence offsets, graph invariants, and byte-identical output under `PYTHONHASHSEED=0` and `1`.
It rejects output directories inside the repository.

```bash
python3 extensions/WikiKGExtractor/tools/evaluate_pilot.py \
    --protocol extensions/WikiKGExtractor/protocol/rice-engineering-pilot.v1.json \
    --manifest <private-corpus-manifest.json> \
    --input <private-development-input.json> \
    --output-dir <private-output-directory> \
    --split development
```

Detailed run outputs remain private and outside Git.
The aggregate report does not include page text or evidence spans.
Do not run the held-out split without explicit approval after reviewing the freeze packet.
After approval, the held-out command also requires `--confirm-heldout-approved`.

## 12. Supervisor check and browser visualization

The committed synthetic fixture provides a quick rules-only check without Gemini, Neo4j, or private WikiCrop data.

```bash
OUT="${TMPDIR:-/tmp}/wikicrop-kg-demo"
python3 extensions/WikiKGExtractor/bin/kg_worker.py \
    --input extensions/WikiKGExtractor/tests/fixtures/raw-data.rules.json \
    --output-dir "$OUT" \
    --no-ai
```

The expected result is 8 nodes and 11 relationships from one crop, three varieties, and four pests.
The worker also produces 26 pending candidate claims, of which 24 have complete evidence coordinates in this synthetic fixture.
It safely skips the unrelated cultivation page.

To inspect the real MediaWiki and Cytoscape visualization from a clean clone, install and start Docker Desktop, then run:

```bash
python3 extensions/WikiKGExtractor/tools/wikikg_demo.py start
```

The first run builds the MediaWiki development image with Python 3, installs a local SQLite wiki, enables WikiKGExtractor in rules-only mode, and loads the synthetic rice pages.
It starts only the MediaWiki PHP and web services on `http://localhost:8088`.
It does not start Keycloak, call Gemini, write to Neo4j, or use the private development and held-out corpora.

Open the URL printed by the command and sign in with the printed local demo credentials.
Enter `Lúa`, select **Tạo Knowledge Graph**, and run the extraction.
The result page renders the production Cytoscape graph, node cards, relationship table, and revision-pinned source table.

Check the service state or stop the demo with:

```bash
python3 extensions/WikiKGExtractor/tools/wikikg_demo.py status
python3 extensions/WikiKGExtractor/tools/wikikg_demo.py stop
```

The demo refuses to alter an existing non-demo `LocalSettings.php`.
Use a clean clone when another WikiCrop installation already occupies the repository directory.
