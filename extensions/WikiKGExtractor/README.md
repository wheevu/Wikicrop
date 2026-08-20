# WikiKGExtractor 2.1.1

**WikiKGExtractor** là tiện ích mở rộng (extension) cho MediaWiki, được xây dựng cho hệ thống **Wikicrop**. Extension cho phép nhập tên một loài cây, thu thập nội dung trang loài và các trang giống thuộc mục **Danh sách/Giống**, sau đó xuất dữ liệu và tùy chọn xây dựng **Knowledge Graph** để lưu trữ trên **Neo4j**.

---

## 1. Cấu trúc thư mục Extension

Thư mục được đặt tại `extensions/WikiKGExtractor/`:

```text
WikiKGExtractor/
├── extension.json
├── bin/
│   ├── kg_worker.py
│   └── requirements.txt
├── i18n/
├── resources/
│   └── wikikg.css
├── src/
│   ├── ExportWriter.php
│   ├── Hooks.php
│   ├── KgRunner.php
│   ├── LocalWikiExtractor.php
│   ├── SpecialWikiKGExtractor.php
│   └── GraphDocument.php
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

The first graph document uses schema version `1.0` and is validated against `schema/graph-document.v1.schema.json`.

The `Special:WikiKGExtractor` result page renders a read-only semantic graph view from `graph_nodes_edges.json`.

Collection and page-size limits are enabled by default through `WikiKGExtractorMaxCollectedPages` and `WikiKGExtractorMaxPageChars`.

The in-Wiki renderer also caps the number of displayed nodes and relationships while preserving the complete JSON export for offline analysis.

This view is intentionally implemented before parser tags, persistent graph tables, Gemini calls, or direct Neo4j writes.
