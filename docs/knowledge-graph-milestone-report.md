# WikiCrop Knowledge Graph - Integration Milestone Report

Original milestone date: 2026-08-20
Status checked: 2026-09-13
Branch: `feat/knowledge-graph-prototype`
Baseline commit: `9cabfc29104b8821072cd69a696b2cf2215d0684`, pushed to `origin/feat/knowledge-graph-prototype`
Supervisor materials baseline: `Study/Thesis/supervisor-materials/wikikgextractor/`

Current status: the August milestone is committed and pushed.
The version 2.2.0 paper-milestone work is included in the current milestone snapshot on the same branch.
The source paths now match on the five pinned revisions, and the read-only browser graph has been exercised in a local MediaWiki runtime.

## 2026-09-12 research correction and resolution

- The 10-node and 19-edge result covers the private dump harness path.
- It did not prove parity with the live `Special:WikiKGExtractor` path at the 2026-09-12 baseline.
- The production path then used rendered plain text while the smoke path used raw wikitext.
- Template taxonomy extraction needs raw wikitext.
- Version 2.2.0 now preserves both forms and hashes the exact raw wikitext.
- An isolated MariaDB import confirmed exact source-record parity on all five pinned revisions.
- The dump and Special-page worker runs produce the same 10-node, 19-edge, and 32-claim result after filtering to the same four eligible records.
- Canonical next steps live in `~/Study/Thesis/wikicrop-kg-paper-milestone-roadmap-2026-09.md`.

## 2026-09-12 technical pins

- Sprint target stays on MediaWiki 1.43.1.
- Version 1.43.9 is the current 1.43 LTS patch and adds no sprint scope.
- MediaWiki REL1_43 rendering uses `ContentRenderer::getParserOutput()` followed by `runOutputPipeline(...)->getContentHolderText()` instead of deprecated `ParserOutput::getText()`.
- Final reported results must freeze on an isolated MariaDB setup.
- Browser graph pin is Cytoscape.js v3.34.3 from `cytoscape/cytoscape.js`.
- Cytoscape Desktop 3.10.4 is the wrong artifact for browser rendering.
- Teacher link confirms the Cape Town MCT4SD event.

## 2026-09-12 to 2026-09-13 implementation update

- WikiKGExtractor is now version 2.2.0.
- Production exports preserve exact raw `wikitext` and rendered `text`.
- The worker prefers raw wikitext and retains legacy unversioned input support.
- Raw input, corpus manifest, graph document, and candidate claims have version 1.0 schemas.
- The worker emits deterministic `candidate_claims.json` with `pending` review state.
- Candidate evidence carries page ID, revision ID, content hash, extraction method, extractor version, and a source span or page-title coordinate.
- The corpus tool found 85 links from `Lúa` revision 875.
- It resolved 84 pages and recorded missing `Lúa OM49` without aborting.
- The corrected sample contains 10 development and 10 held-out pages using the recorded seed.
- Development pages are selected first, then excluded from held-out sampling with 13 pages inspected before the freeze.
- The corrected development run produced 17 nodes, 26 edges, and 54 provisional candidate claims.
- Of those claims, 52 have complete coordinates for every evidence record.
- The other 2 claims each combine revalidatable source spans with one page-level evidence record and are marked incomplete.
- The replacement held-out input was generated but not run or inspected.
- An isolated MariaDB 10.4 import contains the five private source revisions and 380 total pages.
- Dump and Special-page exports match exactly on title, page ID, revision ID, wikitext, and content hash for all five records.
- Their worker outputs match on all 19 edges and all 32 provisional claims.
- Node content matches except for the expected local-page URL base, and run metadata differs by source path as intended.
- The read-only graph enhancement uses vendored Cytoscape.js v3.34.3 through ResourceLoader while retaining the server-rendered tables.
- A local browser flow rendered a preserved 15-node and 23-edge presentation fixture without console errors.

## 2026-09-13 review correction

- The first split assigned previously inspected `Lúa OM5451` to the held-out set.
- That held-out file was never run, but the assignment was contaminated and is superseded.
- The corrected manifest records 13 previously inspected titles and excludes them from held-out sampling.
- Development selection happens before held-out selection, and every development page is also excluded from the held-out pool.
- The corrected manifest SHA-256 is `e517e9d51f74439470a800a585b3accb9f15e8d5e2e5584bc03a8f8dbf586975`.
- The corpus CLI rejects output paths inside the repository, and `.gitignore` covers the three private corpus artifacts.
- Relationship evidence now requires the target and relationship-specific language to occur in the same bounded source context.
- If that context is absent, the evidence falls back to page level and the claim is marked incomplete.
- The candidate-claim schema now requires offsets, supporting text, and a span hash for `source_span`, and a location for `page_title`.
- The schema also rejects a claim marked traceability-complete when any evidence record is page-level.

Current verification evidence:

- Python suite: 97 tests pass under both `PYTHONHASHSEED=0` and `PYTHONHASHSEED=1`.
- Private five-page smoke: 10 nodes, 19 edges, no dangling edges, and byte-identical graph and claim outputs.
- Five-page source parity: 5 of 5 exact matches across dump and Special-page exports.
- Dump/Special worker parity: 19 of 19 edges and 32 of 32 provisional claims match.
- Corrected development corpus: 17 nodes, 26 edges, and 54 deterministic provisional claims.
- All four JSON documents validate against their version 1.0 schemas.
- All 63 development source spans revalidate against the pinned raw wikitext and span hash.
- The 65 evidence records contain 63 source spans and 2 page-level records; 52 of 54 claims have complete coordinates for every evidence record.
- PHP syntax checks pass for all 13 extension PHP files.
- MediaWiki unit suite: 33 tests and 61 assertions pass on PHP 8.3.14 with both SQLite and MariaDB configurations.
- MediaWiki integration suite: 11 tests and 22 assertions pass with both SQLite and MariaDB configurations.
- QUnit graph module: 4 tests and 11 assertions pass.
- Browser check: a preserved 15-node and 23-edge graph fixture renders with Cytoscape.js v3.34.3, fits inside the graph canvas after a narrow resize, and logs no console errors.

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
PYTHONHASHSEED=0 python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_*.py'   # 97 OK
PYTHONHASHSEED=1 python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_*.py'   # 97 OK
python3 extensions/WikiKGExtractor/tools/real_data_smoke.py --dump <private-dump.sql> --output-dir <private-output-dir>  # 10 nodes, 19 edges, deterministic, schemas OK
```

The dump and generated evidence paths are private and live outside the repository.

## PHP and MediaWiki runtime

`src/GraphDocument.php` is the read-only presentation boundary (schema-version check, provenance validation, size cap, HTML escaping, render caps).

- `tests/phpunit/GraphDocumentTest.php` moved to `tests/phpunit/unit/GraphDocumentTest.php`.
- The move is required: `MediaWikiUnitTestCase` hard-fails any test file whose path lacks a `unit/` segment.
- New coverage: missing/unknown/numeric `schema_version` rejection, missing metadata and provenance rejection, invalid hash/id/kind/method rejection, oversize rejection (5 MB cap), empty provenance acceptance, node/edge/property HTML escaping, render caps at 200 nodes / 500 edges, and the exact-cap boundary (no truncation notice).
- The unit suite passes with 33 tests and 61 assertions in MediaWiki 1.43.1 on PHP 8.3.14 against both SQLite and isolated MariaDB 10.4 configurations.
- The integration suite passes with 11 tests and 22 assertions against both database configurations.
- Runtime checks confirm that the "Nguồn và revision" table renders and that the Special page exports the exact pinned revisions.

## Browser presentation

- `extension.json` registers the graph module and passes bounded graph data through `mw.config`.
- Cytoscape.js v3.34.3 is vendored as readable source with its recorded SRI hash and loaded through `ForeignResourcesDir`.
- Page-backed nodes link to local revision URLs, relationship labels remain visible, and provenance remains available in semantic server-rendered tables.
- The enhancement reveals its container before Cytoscape measures it, uses a non-animated layout, fits on initial render, and refits when Cytoscape emits `resize`.
- If Cytoscape is missing or throws during setup, the visual container is hidden and the server-rendered fallback remains available.
- QUnit passes 4 tests and 11 assertions for graph element handling, pre-measure reveal, resize refitting, and fallback restoration.
- A fresh browser session rendered the preserved 15-node and 23-edge presentation fixture at Cytoscape.js v3.34.3 with three canvases and no console errors.
- After resizing to a 390 by 844 browser viewport, MediaWiki enforced a 500-pixel page width, the graph canvas measured 407 by 362 pixels, and the fitted graph remained inside the canvas.
- The browser fixture uses preserved precomputed worker output so the check exercises the Special-page form, server render, ResourceLoader, and Cytoscape display without changing frozen graph artifacts.

## Remaining boundaries

- The production dump and its content are private; only the harness lives in the repository (synthetic fixtures only).
- Gemini extraction and Neo4j insertion remain disabled and unverified.
- A domain-reviewed gold set and extraction metrics do not yet exist.
- The browser fixture uses precomputed worker output; the real worker path is covered separately by deterministic command-line and MediaWiki integration checks.
- The corrected 17-node and 26-edge development artifact has not been replayed through the browser fixture because the review fixes do not change presentation code.
- MediaWiki's outer page layout enforces a 500-pixel minimum width at a requested 390-pixel viewport.

## Proposed next steps

1. Obtain supervisor approval for excluding pages whose type cannot be inferred safely.
2. Resolve whether the `Lúa` node is a crop concept, biological species, or WikiCrop document before assigning a scientific name.
3. Freeze the ontology, matching rules, and annotation rubric before opening held-out results.
4. If no second annotator is available, report only determinism, validity, runtime, failures, and traceability coverage for the engineering pilot.
5. Only after the graph content is reviewed: enable `$wgWikiKGExtractorEnableKG` for real, then test an isolated Neo4j projection if it remains in scope.

## Decision log

- 2026-08-20: Do not check in yet.
- 2026-08-20: Do not change the non-variety fallback behavior without supervisor direction.
- 2026-08-20: Keep the PHP and Docker-dependent validation pending rather than claiming it passed locally.
- 2026-09-07: The August milestone is committed and pushed.
- 2026-09-07: Use conservative type inference and skip unsafe page types pending supervisor approval.
- 2026-09-07: Keep the scientific name unresolved because the source page names two species.
- 2026-09-07: PHP tests and the runtime renderer are now verified in an isolated container.
- 2026-09-13: Accept exact source records plus equivalent claims and edges as parity; local URL bases and source-path metadata are expected to differ.
- 2026-09-13: Keep browser verification read-only by replaying preserved worker output through the real Special-page render and ResourceLoader path.
- 2026-09-13: Supersede the first held-out assignment because it contained a previously inspected page.
- 2026-09-13: Select development first, then exclude both development pages and the preregistered observed-page list from held-out sampling.
- 2026-09-13: Mark relationship evidence incomplete unless a bounded span contains both the target and relationship language.
