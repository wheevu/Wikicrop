# WikiCrop rules-only engineering pilot protocol

Status: freeze candidate; the development split passes, and the held-out event has not been run.

## Purpose

This protocol evaluates whether the deterministic WikiKGExtractor pipeline produces reproducible, schema-valid, traceable candidate claims from revision-pinned WikiCrop pages.
It does not evaluate scientific correctness without an independently annotated reference set.

## Scope

The pilot uses only rules-based extraction.
Gemini, direct Neo4j writes, PlantDB identity mapping, GraphRAG, and page writes are excluded.
Every extracted claim remains provisional with review status `pending`.

## Entity semantics

- `Crop` represents a WikiCrop crop-topic concept anchored to a WikiCrop page.
- `Variety` represents a cultivar or named variety described by a WikiCrop page.
- `Pest` represents a named pest or disease mentioned in a supported relationship.
- The `Lúa` crop-topic page discusses both `Oryza sativa` and `Oryza glaberrima`, so the pilot does not assign it one scientific name.
- A page that cannot be identified safely as a crop or variety is excluded instead of being guessed.

## Relationship predicates

- `HAS_VARIETY` links a crop topic to a variety when the variety page identity and crop association are explicit.
- `RESISTANT_TO` requires the pest name and resistance language in the same bounded source context.
- `SUSCEPTIBLE_TO` requires the pest name and susceptibility language in the same bounded source context.
- `AFFECTED_BY` requires direct crop-page evidence that the pest or disease affects the crop.
- A variety-level resistance or susceptibility statement does not by itself create a crop-level `AFFECTED_BY` claim.

## Scalar predicates

The pilot supports `growth_duration`, `plant_height`, `yield_amount`, `variety_type`, and `crossbred_from` for varieties.
It supports `kingdom`, `taxon_order`, `family`, `genus`, and an unambiguous `scientific_name` for crop-topic taxonomy.
Numeric ranges must preserve both endpoints and the unit.
Seasonal or conditional alternatives become separate candidate claims with qualifiers.
Conflicting alternatives remain separate candidate claims.
`Chưa rõ` is a graph-display fallback and must never become a candidate claim.

## Evidence rules

Every candidate claim must identify the source page, page ID, revision ID, content hash, extraction method, and extractor version.
A `source_span` must match the pinned raw wikitext at its recorded offsets and SHA-256 hash.
A `page_title` coordinate is allowed only for page-identity claims.
A page-level evidence record makes its claim traceability-incomplete.
Traceability completeness measures coordinate coverage, not correctness.

## Future annotation rubric

An independent annotator should assign one of these labels without seeing extractor output first:

- `supported`: the source explicitly supports the complete claim.
- `unsupported`: the source does not support the claim.
- `ambiguous`: the source is relevant but does not determine one interpretation.
- `out_of_scope`: the statement is outside the frozen ontology.
- `abstain`: the annotator cannot decide from the available source.

Disagreement categories are entity identity, predicate, target, scalar value, qualifier, and evidence location.
Exact matching requires the normalized subject type and ID, predicate, object or scalar value, and material qualifiers to match.
No precision, recall, F1, agreement, or gold-set claim is allowed until independent annotation and adjudication exist.

## Engineering metrics

The pilot reports schema validity, deterministic output equality, runtime observations, input errors, worker failures, warnings, skipped provenance, graph invariants, evidence revalidation, and traceability coverage.
Runtime is descriptive for the recorded environment and is not a comparative benchmark.

## Held-out procedure

The machine-readable protocol pins the corrected corpus and held-out input hashes.
One held-out evaluation event consists of two predefined rules-only executions under `PYTHONHASHSEED=0` and `PYTHONHASHSEED=1`.
Detailed outputs remain private and outside the repository.
The aggregate report must not include source text or evidence spans.
Rules must not change after held-out results are viewed.
Any later extractor version requires a new held-out corpus that excludes all previously observed pages.

An identical retry is allowed only when an infrastructure failure prevents the event from producing a usable report.
The failed attempt and reason must be recorded.

## Freeze gate

Before the held-out event, the implementation, protocol, schemas, and tests must be stable.
The development evaluation must pass under both hash seeds.
The exact worker, schema, manifest, development-input, and held-out-input hashes must be recorded.
The held-out event requires explicit human approval after the freeze packet is reviewed.

## Frozen artifacts

The machine-readable protocol is `extensions/WikiKGExtractor/protocol/rice-engineering-pilot.v1.json`.
It pins extractor version `2.3.0` and the following SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| Worker | `d2f5ed85921fbb8810fb38fd3acecd2766111a5c477bf517b8a610bd8a88890b` |
| Evaluator | `d930fa641c34229315006e8b046066dd80131453ed2d341ee00628830b9d78fe` |
| Corpus manifest | `e517e9d51f74439470a800a585b3accb9f15e8d5e2e5584bc03a8f8dbf586975` |
| Development input | `bec0088253f933430749dec724898bf6112b3ba4fe66e909a3f398c63c6ba66e` |
| Held-out input | `674922f84730de3c9ae29598a36112519687cf5095408264da2e0278522d82bc` |

The protocol also pins every validation schema by SHA-256.
Changing the worker or a pinned schema invalidates the protocol before execution.

## Development result

The frozen development evaluation passed on 2026-09-18 under hash seeds `0` and `1`.
Both runs produced byte-identical deterministic outputs with 15 nodes, 20 edges, 55 pending candidate claims, and 55 traceability-complete claims across 11 pages.
The result is an engineering verification only and is not a scientific-accuracy measurement.

Run a development evaluation with:

```bash
python3 extensions/WikiKGExtractor/tools/evaluate_pilot.py \
    --protocol extensions/WikiKGExtractor/protocol/rice-engineering-pilot.v1.json \
    --manifest <private-corpus-manifest.json> \
    --input <private-development-input.json> \
    --output-dir <private-output-directory> \
    --split development
```

Do not replace `development` with `heldout` until the freeze packet has been reviewed and explicit human approval has been given.
After approval, the held-out command also requires `--confirm-heldout-approved` to prevent an accidental run.

## Reviewed 2.3.1 successor candidate

`extensions/WikiKGExtractor/protocol/rice-engineering-pilot.v2.json` pins the 2.3.1 worker separately from this historical 2.3.0 freeze.
It reuses the v1 evaluator, schemas, private corpus manifest, development input, and unopened held-out input by hash.
The successor preserves both pending resistance and susceptibility graph relationships when source evidence conflicts, matching the claim set instead of discarding one graph edge.
It also limits live collection to recognized crop roots and eligible linked varieties, and restricts the synthetic browser demo to loopback with a checkout-specific Compose project.
The development evaluation passed under both hash seeds with 15 nodes, 20 relationships, 55 pending claims, and 55 traceability-complete claims.
The synthetic MediaWiki browser run rendered 8 nodes and 11 relationships; the extension's SQLite PHPUnit suite passed 50 tests and 98 assertions.
Independent review found two live page-eligibility defects, which were corrected and approved in a focused second review.
This candidate is not yet an accepted freeze or integrated into the supervisor-facing branch.
The held-out split remains unopened and requires separate explicit approval.
