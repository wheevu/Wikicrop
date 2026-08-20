# WikiCrop Knowledge Graph - Integration Milestone Report

Date: 2026-08-20
Branch: `feat/knowledge-graph-prototype` (nothing committed yet; all changes staged as working-tree files)
Supervisor materials baseline: `Study/Thesis/supervisor-materials/wikikgextractor/`

Current status: implementation paused at the external-runtime boundary, not checked in.
The remaining work requires PHP/Composer or Docker, and the non-variety page policy remains undecided.

## Scope of this milestone

Rules-only extraction that produces a stable, schema-valid graph document and renders it read-only inside WikiCrop.

The pipeline works 100% offline: no Gemini calls, no Neo4j writes, no page writes.
All generation paths are opt-in through `LocalSettings.php` and stay disabled by default.

## What is now verified end-to-end on real data

1. The Python worker (`bin/kg_worker.py`) runs with `--no-ai` on the five pinned pages of the private production dump.
2. The dump extractor (`tools/real_data_smoke.py`) reads the MySQL 1.35+ schema (multi-line INSERTs, hex-escaped blobs, `content` model ids, `tt:` content addresses) and reproduces the exact pinned revisions: `Lúa` rev 875, `Lúa OM5451` rev 534, `Lúa ST24` rev 536, `Lúa ST25` rev 513, `Kỹ thuật trồng và chăm sóc lúa` rev 221.
3. Two consecutive smoke runs are byte-identical (deterministic output).
4. Result: 19 nodes (1 Crop, 4 Variety, 14 Pest) and 31 edges (4 HAS_VARIETY, 11 RESISTANT_TO, 14 AFFECTED_BY, 2 SUSCEPTIBLE_TO), zero dangling edges, valid against `schema/graph-document.v1.schema.json`.
5. Unit tests: 65/65 green under `PYTHONHASHSEED=0` and `=1` (62 worker tests + 3 dump-extractor tests).

## Worker fixes made in this milestone (with supervisor-baseline diff)

- `short_variety_name()` now normalizes underscores in both the title and the crop name (`_to_spaces()`), which fixes the `Lúa Giống ST25` -> `ST25` case and keeps the second `giống` pass.
- `build_graph()` iterates `sorted(known_crops)` and builds a `canonical_crop` map so every edge references the exact Crop node id, even when a page spells the crop name differently (e.g. `lua` vs `Lúa`).
- Baseline diff artifact: `~/.cache/opencode/scratch/kg_worker_baseline_vs_supervisor.diff` (155 lines) against the supervisor original.

## Findings from real data (heuristic limits, needs supervisor decision)

The rule-based extractor was written against synthetic fixtures.
Running it on the real dump exposed four concrete issues:

1. Non-variety pages are misclassified.
   `Kỹ thuật trồng và chăm sóc lúa` has no declared kind, so `load_pages()` falls back to `variety`.
   The page then pollutes the graph with false positives: `plant_height: 10-20 cm`, and the text `Kháng Rầy nâu; nhiễm Rầy lưng trắng, Bọ trĩ` is stored as a single `pest_resistance` value.
   The generated MediaWiki comparison table also puts cultivation prose into the "Đặc tính" column.
   Question for the supervisor: should pages without a declared kind be excluded from graph generation instead of falling back?

2. Species taxonomy extraction fails on the real `Lúa` page.
   `scientific_name`, `family`, `genus` are all `Chưa rõ`.
   The heuristic expects label-newline-value text (e.g. `Loài (species)` then `Oryza sativa`), but the real page stores taxonomy inside `InfoPlant1` template parameters.

3. `OM5451` plant height is `Chưa rõ` (false negative).
   The real text likely writes the height as `Cao cây` while the heuristic only matches `chiều cao cây`.

4. Minor, previously logged: the literal `N/A` survives normalization (`norm_key "n a"` never matches the empty-values list), and the `crossbred_from` heuristic keeps `và` instead of converting it to ` / `.

None of these crash the pipeline; they lower extraction quality and should be fixed in a follow-up slice or accepted as documented limits.

## Verification commands

```bash
PYTHONHASHSEED=0 python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_*.py'   # 65 OK
PYTHONHASHSEED=1 python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_*.py'   # 65 OK
python3 extensions/WikiKGExtractor/tools/real_data_smoke.py --dump ~/Thesis/mediawiki_20260617.sql        # extraction + 2 runs byte-identical + schema OK
```

The dump path is private and lives outside the repository; the harness writes only to the OS temporary directory.

## PHP side (write-only in this environment)

`src/GraphDocument.php` is the read-only presentation boundary (schema-version check, provenance validation, size cap, HTML escaping, render caps).

- `tests/phpunit/GraphDocumentTest.php` moved to `tests/phpunit/unit/GraphDocumentTest.php` and expanded from 3 to 19 tests.
- The move is required: `MediaWikiUnitTestCase` hard-fails any test file whose path lacks a `unit/` segment.
- New coverage: missing/unknown/numeric `schema_version` rejection, missing metadata and provenance rejection, invalid hash/id/kind/method rejection, oversize rejection (5 MB cap), empty provenance acceptance, node/edge/property HTML escaping, render caps at 200 nodes / 500 edges, and the exact-cap boundary (no truncation notice).
- The "Nguồn và revision" table rendering path calls `Title::newFromText()`, which requires MediaWiki services and therefore cannot run in `MediaWikiUnitTestCase`; it is deferred to the MediaWiki runtime check (Slice 4).
- These tests could not be executed here (no PHP/Composer on the host) and are pending a run inside a MediaWiki phpunit environment.

## Blocked items

- No PHP/Composer and no Docker daemon on this machine, so Slice 2 PHPUnit tests and the Slice 4 MediaWiki runtime render check cannot run locally.
- The production dump and its content are private; only the harness lives in the repository (synthetic fixtures only).

## Proposed next steps

1. Obtain a supervisor decision on the non-variety page policy: exclude pages without a declared kind, or keep the current variety fallback.
2. Run the PHP unit tests inside a MediaWiki install (`php tests/phpunit/phpunit.php extensions/WikiKGExtractor/tests/phpunit/unit`).
3. Add the MediaWiki runtime render check once Docker or a local install is available.
4. After the policy decision, fix the `InfoPlant1` taxonomy and `Cao cây` extraction heuristics if they remain in scope.
5. Only after the graph content is reviewed: enable `$wgWikiKGExtractorEnableKG` for real, then optionally Neo4j push.

## Decision log

- 2026-08-20: Do not check in yet.
- 2026-08-20: Do not change the non-variety fallback behavior without supervisor direction.
- 2026-08-20: Keep the PHP and Docker-dependent validation pending rather than claiming it passed locally.
