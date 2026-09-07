# WikiCrop Knowledge Graph - Integration Milestone Report

Original milestone date: 2026-08-20
Status checked: 2026-09-07
Branch: `feat/knowledge-graph-prototype`
Baseline commit: `9cabfc29104b8821072cd69a696b2cf2215d0684`, pushed to `origin/feat/knowledge-graph-prototype`
Supervisor materials baseline: `Study/Thesis/supervisor-materials/wikikgextractor/`

Current status: the August milestone is committed and pushed.
On 2026-09-07 the extraction defects listed below were addressed locally and the PHP/runtime boundary was exercised in an isolated MediaWiki 1.43, PHP 8.3, and SQLite container.

## 2026-09-07 verification update

- Unknown legacy page types now require a safe title-based inference; unrelated pages are skipped with a warning instead of becoming fake varieties.
- The cultivation page is excluded from graph generation while remaining part of the five-page smoke corpus.
- WikiCrop `InfoPlant1` taxonomy fields are parsed for species pages.
- Parent-variety text, `N/A`, `Cao cây`, and template-contaminated embedding text are normalized.
- The real-data smoke result is now 10 nodes and 19 edges from one crop page and three eligible variety pages.
- Two runs remain byte-identical, all edges resolve, and the output validates against the graph document schema.
- 71 Python tests pass under both `PYTHONHASHSEED=0` and `PYTHONHASHSEED=1`.
- 20 PHP tests pass with 31 assertions.
- A MediaWiki runtime render check passes, including the provenance table.
- No Gemini calls, Neo4j writes, page writes, or public deployment were performed.

## Scope of this milestone

Rules-only extraction that produces a stable, schema-valid graph document and renders it read-only inside WikiCrop.

The pipeline works 100% offline: no Gemini calls, no Neo4j writes, no page writes.
All generation paths are opt-in through `LocalSettings.php` and stay disabled by default.

## What is now verified end-to-end on real data

1. The Python worker (`bin/kg_worker.py`) runs with `--no-ai` on the five-page smoke corpus from the private production dump.
2. The dump extractor (`tools/real_data_smoke.py`) reads the MySQL 1.35+ schema (multi-line INSERTs, hex-escaped blobs, `content` model ids, `tt:` content addresses) and reproduces the exact pinned revisions: `Lúa` rev 875, `Lúa OM5451` rev 534, `Lúa ST24` rev 536, `Lúa ST25` rev 513, `Kỹ thuật trồng và chăm sóc lúa` rev 221.
3. The cultivation page is read for the smoke corpus but is rejected as an unsafe graph source instead of becoming a fake variety.
4. Two consecutive smoke runs are byte-identical.
5. Result: 10 nodes (1 Crop, 3 Variety, 6 Pest) and 19 edges (3 HAS_VARIETY, 10 RESISTANT_TO, 6 AFFECTED_BY), zero dangling edges, valid against `schema/graph-document.v1.schema.json`.
6. Unit tests: 71/71 green under `PYTHONHASHSEED=0` and `=1`.

## Worker fixes made in this milestone (with supervisor-baseline diff)

- `short_variety_name()` now normalizes underscores in both the title and the crop name (`_to_spaces()`), which fixes the `Lúa Giống ST25` -> `ST25` case and keeps the second `giống` pass.
- `build_graph()` iterates `sorted(known_crops)` and builds a `canonical_crop` map so every edge references the exact Crop node id, even when a page spells the crop name differently (e.g. `lua` vs `Lúa`).
- Baseline diff artifact: `~/.cache/opencode/scratch/kg_worker_baseline_vs_supervisor.diff` (155 lines) against the supervisor original.

## Findings from real data

The rule-based extractor was written against synthetic fixtures.
Running it on the real dump exposed these concrete issues and decisions:

1. The old compatibility fallback classified `Kỹ thuật trồng và chăm sóc lúa` as a variety and polluted the graph with cultivation measurements and unrelated pest claims.
   The worker now infers missing types only from safe title patterns and skips other pages with a warning.
   This conservative policy still needs supervisor approval before wider extraction.

2. The real `Lúa` page stores taxonomy inside `InfoPlant1` template parameters.
   The rules now extract kingdom `Plantae`, order `Poales`, family `Poaceae`, and genus `Oryza`.
   `scientific_name` remains unknown because the page explicitly describes both `Oryza sativa` and `Oryza glaberrima`; choosing one would hide a real modeling question.

3. Revision 534 of `Lúa OM5451` does not contain a plant-height measurement.
   Keeping `plant_height` as `Chưa rõ` is therefore correct for this revision.
   The rules now also recognize the `Cao cây` label when it appears on other pages.

4. `N/A` is now treated as missing data.
   The real OM5451 source is normalized to `Jasmine 85 / OM2490` instead of retaining the surrounding prose.

5. The 10-node output has not yet been compared with a manually reviewed gold set.
   Node and edge counts prove pipeline behavior, not scientific accuracy.

## Verification commands

```bash
PYTHONHASHSEED=0 python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_*.py'   # 71 OK
PYTHONHASHSEED=1 python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_*.py'   # 71 OK
python3 extensions/WikiKGExtractor/tools/real_data_smoke.py --dump ~/Thesis/mediawiki_20260617.sql        # 10 nodes, 19 edges, deterministic, schema OK
```

The dump path is private and lives outside the repository; the harness writes only to the OS temporary directory.

## PHP and MediaWiki runtime

`src/GraphDocument.php` is the read-only presentation boundary (schema-version check, provenance validation, size cap, HTML escaping, render caps).

- `tests/phpunit/GraphDocumentTest.php` moved to `tests/phpunit/unit/GraphDocumentTest.php` and now contains 20 tests.
- The move is required: `MediaWikiUnitTestCase` hard-fails any test file whose path lacks a `unit/` segment.
- New coverage: missing/unknown/numeric `schema_version` rejection, missing metadata and provenance rejection, invalid hash/id/kind/method rejection, oversize rejection (5 MB cap), empty provenance acceptance, node/edge/property HTML escaping, render caps at 200 nodes / 500 edges, and the exact-cap boundary (no truncation notice).
- The suite passes with 20 tests and 31 assertions in an isolated MediaWiki 1.43, PHP 8.3, and SQLite container.
- A separate runtime probe confirms that the "Nguồn và revision" table renders while MediaWiki services are active.

## Remaining boundaries

- The production dump and its content are private; only the harness lives in the repository (synthetic fixtures only).
- The complete `Special:WikiKGExtractor` browser flow has not been exercised in a running local web service.
- Gemini extraction and Neo4j insertion remain disabled and unverified.
- A domain-reviewed gold set and extraction metrics do not yet exist.

## Proposed next steps

1. Obtain supervisor approval for excluding pages whose type cannot be inferred safely.
2. Resolve whether the `Lúa` node is a crop concept, biological species, or WikiCrop document before assigning a scientific name.
3. Create and review the first gold set from the four eligible pages.
4. Measure entity and relation precision, recall, and F1 against that set.
5. Exercise the complete Special page flow in a local web runtime.
6. Only after the graph content is reviewed: enable `$wgWikiKGExtractorEnableKG` for real, then test an isolated Neo4j projection if it remains in scope.

## Decision log

- 2026-08-20: Do not check in yet.
- 2026-08-20: Do not change the non-variety fallback behavior without supervisor direction.
- 2026-08-20: Keep the PHP and Docker-dependent validation pending rather than claiming it passed locally.
- 2026-09-07: The August milestone is committed and pushed.
- 2026-09-07: Use conservative type inference and skip unsafe page types pending supervisor approval.
- 2026-09-07: Keep the scientific name unresolved because the source page names two species.
- 2026-09-07: PHP tests and the runtime renderer are now verified in an isolated container.
