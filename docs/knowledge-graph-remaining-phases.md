# Remaining thesis phases: implementation and acceptance

The user authorized pursuit of the remaining code phases on 2026-10-04.
The original thesis brief remains the acceptance baseline.
This work does not authorize site administration, production migrations, deployment, or opening the rice held-out split.

## Current implementation pass, 2026-10-08

The current work is a reviewer-only offline continuation, not a full-site accuracy result or a public assistant.
Implementation was first written under a no-testing restriction; the user then authorized focused regression tests and isolated synthetic runs while keeping held-out data sealed.
Neither authorization covers full-site collection, live-site changes, real agricultural review, or ontology decisions.
Historical results below remain tied to their dated inputs and code.

The implementation packages now have code and documentation written:

- Experimental query v2 and controlled query-to-composition orchestration, with conservative scope and interval interpretation.
- Whole-run planning, collection/extraction supervision, and recoverable attempts in the existing bounded runner.
- A private [structured question command](knowledge-graph-offline-question.md) using the PHP current-evidence bridge, with no generator transport.
- Reviewed graph edge-to-claim navigation and a README that separates reviewed offline use from [legacy provisional setup](knowledge-graph-legacy-extraction.md).

The source and resource prerequisites still block real full-site execution.
The unknown live backend, ordinary-reader policy, independent benchmark references, and ontology decisions remain separate acceptance gates.
Final integration used manual code and diff inspection before the authorized verification recorded below.
The query result separates eligible, diagnostic, and display-context claim identities so composition does not inherit unsupported claims from a reviewer listing.
The runner retains one receipt, serializes CLI operations with a shared lock, preserves failed/interrupted attempts, and records page outcomes separately from job outcomes.
The [verification record](#authorized-verification-2026-10-08) below states what was executed and what remains unproven.
The [2026-10-09 reproducibility milestone](knowledge-graph-release-verification.md) adds a fresh synthetic installation, real-pointer browser checks, real PHP refusal cases, and native-Linux recovery checks.
Its independent completion review approved the synthetic milestone on 2026-10-10 with no critical or major findings; the dated results below remain historical.

## Acceptance gates

The code can enforce a gate without establishing the missing fact behind it.
These questions still control the dependent acceptance checkpoints:

| Gate | Current fact | Dependent checkpoint |
| --- | --- | --- |
| Existing assistant backend | No verified source or development contract with comparable model, prompt, retrieval index, decoding, and context controls | Real before-integration baseline and visible GraphRAG answer flow |
| Reader audience | Privileged reviewer export access does not define ordinary readers' access | Online evidence and answer authorization |
| Benchmark references | First 100 answers are supplied candidates; the appended 100 answers are placeholders; expert-review columns are empty | Scientific accuracy and before/after improvement claims |
| Evaluation protocol | Prior preregistration is unconfirmed; no confirmed hand-checked independent reference split | Comparative evaluation and performance-driven tuning |
| Newer full-site export | About 420 sections; per-page source IDs, revision IDs, and exact content hashes remain unmapped | Current full-site revision-pinned evidence |
| Ontology scope | Earlier ontology research is a proposal, not a supervisor-approved agricultural schema | Final concept/model freeze and broad agricultural coverage |

The historical revision-pinned development corpus is separate from the newer text export.
Do not attach its old revision IDs to newer text.
Prior workbook exposure must remain disclosed; freezing a protocol now cannot make those questions unseen.

## Preservation

The frozen `kg_worker.py`, `evaluate_pilot.py`, six pinned schemas, and protocol files remain the pilot's contract.
New query semantics belong to a separate experimental offline interpretation.
They do not rewrite stored snapshots or approve an agricultural ontology.
MediaWiki retains authoritative snapshots and append-only review history.
Neo4j and query results remain rebuildable projections.
Real-data outputs and verification details containing source text must stay in owner-only storage outside Git and the document root.

## Installation route

The [isolated installation guide](knowledge-graph-offline-installation.md) documents the separate graph/tools package.
The [connected synthetic demonstration](knowledge-graph-connected-demo.md) adds a separate reviewer-only MediaWiki question page and a bounded Neo4j source; its own execution record states whether fresh verification has completed.
It leaves the base Compose configuration and live assistant routes unchanged.
The [eight-phase roadmap](knowledge-graph-codebase-roadmap.md) distinguishes code progress from the acceptance gates above.

## Controlled offline workflow

Use an isolated revision-pinned source database and an existing named reviewer account.
The real-data mode accepts only the source article and selected development pages from the protocol-pinned corpus manifest.
It does not grant permission to ingest the newer supervisor text export or any held-out page.
Synthetic mode must be selected explicitly and used only with a disposable synthetic wiki.

The guarded importer reads paired `candidate_claims.json` and `raw_data.json` files in the same private job directory.
It checks that the artifact identities correspond to current readable source revisions and exact wikitext, not merely that the two artifacts agree.
Its default is a dry run.
`--confirm-import` persists pending snapshots; it does not approve them.

```bash
php extensions/WikiKGExtractor/maintenance/importReviewedClaims.php \
    --actor='<existing reviewer>' --offline-only --audience=actor-only \
    --corpus-manifest='<private pinned manifest path>' \
    --document-root='<actual server document root>' \
    --claims='<private job>/candidate_claims.json' \
    --raw-data='<private job>/raw_data.json'
```

After inspecting the dry run and claims, repeat with `--confirm-import` only if persistence is intended.
Record individual review decisions explicitly:

```bash
php extensions/WikiKGExtractor/maintenance/reviewOfflineClaim.php \
    --actor='<existing reviewer>' --offline-only --audience=actor-only \
    --corpus-manifest='<private pinned manifest path>' \
    --snapshot='<snapshot SHA-256>' --status=approved \
    --expected-version='<current review version>' --reason='<review reason>'

php extensions/WikiKGExtractor/maintenance/exportReviewedOffline.php \
    --actor='<existing reviewer>' --offline-only --audience=actor-only \
    --corpus-manifest='<private pinned manifest path>' \
    --document-root='<actual server document root>' \
    --output='<private directory>/reviewed.json'
```

The separate export checks development scope inside collection and revalidation, before installing a private output file.
It aborts if any otherwise eligible claim cites an out-of-scope source rather than quietly writing a misleading partial export.
Review decisions and snapshots remain in MediaWiki when later eligibility changes.

## Current evidence bridge

`authorizeOfflineEvidence.php` is a local process bridge, not an HTTP API.
Its private request contains `graph` snapshot/hash/review-version identities and `text` page/revision/content-hash identities.
Graph requests also carry the export's `source_watermark`; a new approval, rejection, or imported snapshot invalidates the old manifest before generation.
The gate re-reads authoritative claims and approved source text.
It does not accept indexed text as authority.
Text additionally requires exactly one matching latest approved `approve_queue` row; missing or ambiguous approval state denies the response.

```bash
php extensions/WikiKGExtractor/maintenance/authorizeOfflineEvidence.php \
    --actor='<existing reviewer>' --offline-only --audience=actor-only \
    --corpus-manifest='<private pinned manifest path>' \
    --document-root='<actual server document root>' \
    --request='<private directory>/identities.json' \
    --output='<private directory>/new-current-evidence.json'
```

The bridge returns no evidence when a dependency, identity, source hash, visibility check, or current decision fails.
It bounds the response and checks source-span offsets against the actual current source.
Callers still need fresh checks immediately before generation and before returning an answer.
This reviewer-only contract cannot be used as proof of an ordinary reader's access.

## Metadata discovery and bounded extraction

`discoverOfflineSources.php` reads bounded primary-store metadata before source content.
It excludes held-out identities and redirect aliases from the frozen manifest, then reads only explicitly classified, current, visible main-namespace pages.
The classification file maps exact page titles to `kind`, either `species` or `variety`, and a nonempty `crop` string.
It records a classification choice; it does not establish agricultural truth.
Unclassified, unresolved, suppressed, and otherwise ineligible pages remain visible in the inventory.

Use the maintained [preparation, batch and summary commands](knowledge-graph-bounded-runner.md#cli).
Preparation is metadata-only; content collection requires explicit selected page IDs and the expected metadata snapshot.
Do not invoke the collector without either `--metadata-only` or the paired `--selected-pages` and `--expected-metadata` inputs.
The Python executor verifies manifest, worker, protocol, inventory, and source hashes before running the unchanged rules-only worker.
Its metadata-only exclusion policy resolves raw main-namespace redirects and excludes a terminal source and its entire alias group whenever any group identity is held out.
It keeps crop-separated per-page jobs, validates paired output artifacts, and resumes only from receipts whose inputs, limits, states, and artifacts still match.
Changing an inventory or a configured limit requires a new execution directory.
The receipt's completed, skipped, and failed states describe extraction work, not reviewed graph coverage.

## Query, assistant, and comparison contracts

`tools/reviewed_query.py` provides fixed `lookup`, `filter`, and `path` intents under `schema/reviewed-query.v1.schema.json`.
That sentence describes the historical v1 request boundary.
The continuation accepts legacy inputs through the separate experimental `schema/reviewed-query.v2.schema.json` and adds bounded relationship `conjunction` requests.
Its synthetic verification is recorded below; scientific and real-source acceptance remain separate.
Each branch uses the same resolved crop/source-scoped root and retains its supporting claims; scalar pedigree text never supplies a missing parent edge.
The [query contract](knowledge-graph-query-contract.md) records the current interface and deferred compatibility checks.
Variety traversal uses the current node's reviewed, same-crop membership pages rather than joining arbitrary equal local IDs.
Parentage requires explicit resolved evidence.
Numeric filters use the frozen `growth_duration`, `plant_height`, and `yield_amount` predicates, with explicit experimental aliases and matching unit dimensions.
The intended contract forbids missing conditions, conflicting alternatives, ambiguous ranges, and partial manifests from becoming complete negative answers.
Path retrieval, numeric filtering, and composition use `tools/qualifier_scope.py`, policy `wikikg.qualifier-scope.v1`.
Every traversed relationship must match or cover the requested season and condition.
Known season lists use the frozen worker's four labels; a claim scoped to `Mùa; Đông Xuân` can cover a request for `Đông Xuân`.
Free-text conditions support exact non-empty literal matching only.
Unknown season labels support exact single-label literal matching, not inferred list containment or exclusion.
Missing, empty, partially applicable, or unresolved scope makes interpretation unsupported rather than a complete negative.
Resistance and susceptibility with potentially overlapping season/condition scope remain visible within the current crop/source identity, including when numeric filtering returns diagnostics.
Different strings do not establish disjoint scope, even when two recognized season lists have no shared label.
The current policy has no approved exclusion rules, so it does not return `DISJOINT`.
Opposing claims remain canonical diagnostics and make the path unsupported/incomplete.
Numeric filters retain uncertain candidates as diagnostics, and composition blocks differing scalar values whose scopes are not proven disjoint.
Successful interpretation receives a final exact canonical authorization check.

`answer_query(...)` invokes interpretation itself rather than accepting loose claim lists with caller-supplied completeness flags.
It preserves status, generation, scope-policy version, diagnostics, and reasons.
Evidence-purpose lookup retains original scopes; answer-purpose lookup requires established applicability.
The default command returns evidence and citations without generated prose.
Unsupported, incomplete, uncertain, truncated, or authorization-failed interpretation cannot be converted into a generated answer by dropping metadata.
Decimal commas and straddling-range decisions are part of experimental v2, not changes to the frozen worker or pilot schemas.

`tools/assistant_contract.py` combines bounded current graph and text evidence through local callbacks.
It reauthorizes before generation and after the generator returns, and requires citation IDs from the supplied catalog.
Citation identity validation does not establish that the generated sentence is supported by the cited source.
The versioned ingestion request and acknowledgment contract does not deliver content to the unknown `/ingest` backend.

`tools/evaluate_comparison.py` accepts private JSON or CSV question inputs and identity-bound paired-arm receipts.
Scientific denominators require a bound, explicitly approved protocol, independent references, a hand-checked split, paired judgments, comparable controls, and recorded prior exposure.
Unapproved or unbound inputs remain validation-only with zero scientific denominators.
The report separates extraction validity, retrieval relevance, correctness, citation support, abstention, and runtime without copying question or answer text.
It validates supplied artifacts and judgments; it does not generate the missing baseline or certify a claimed human approval.

## Implementation and verification status

The bounded offline implementations passed integration verification on 2026-10-04.
The resumed first completion review on 2026-10-05 reproduced three boundary defects and requested changes.
Round 2 verified the three original repairs but returned `request_changes` for one adjacent major defect involving partially scoped opposing assertions.
Code work initially stopped at the agreed two-round review boundary.
The user subsequently authorized a focused repair on 2026-10-05; the query and assistant conflict checks now pass the failing cases and broader self-verification.
The deliverable is still not independently approved: separate review of the additional repair is due.
This evidence does not complete the dependent online, full-site, agricultural, or scientific checkpoints.

### Unverified qualifier repair, 2026-10-07

The user authorized the focused qualifier repair after the documentation corrections and requested no additional validation or testing in this pass.
The patch changes the two tools and adds their shared scope policy; request/response shapes, canonical claim/evidence payloads, authorizers, and frozen pilot artifacts are not edited.
Implementation was inspected by reading the changed code and diff only.
No tests were added or modified, and no test, build, lint, syntax, or runtime validation was run for this patch.
The patch is not verified or approved; independent review remains due once the deliverable is self-verified.
Historical passing counts below do not cover this patch.

Existing tests that treat string differences as proof of exclusion now express the superseded policy, including `test_explicitly_disjoint_opposition_does_not_block_answer_generation`, `test_explicit_scope_difference_allows_opposing_claims_with_missing_qualifiers`, `test_matching_path_scope_excludes_known_other_scope_without_conflict`, and `test_exact_season_and_condition_filters`.
They remain unchanged as requested; this is a known expectation mismatch, not a recorded test result.
Other missing-scope cases retain the existing unsupported result, while diagnostic numeric results may now include additional uncertain claims.
This repair does not begin full-site execution, online integration, ontology expansion, or held-out evaluation.
The 2026-10-08 authorized verification reconciled and executed the stale expectations listed above; the results are in the verification record at the end of this section.

| Phase | Verified offline implementation | Acceptance still outside the verified fixtures |
| --- | --- | --- |
| 2. Reviewed real-data path | Guarded historical-development fixture import, explicit fixture review, scoped export, primary authorization, Neo4j load and lookup | Live current-source mapping and real agricultural review decisions |
| 3. Discovery and execution | Metadata inventory, alias exclusions, eleven completed development jobs, one visible unclassified page, validated resume and portable pairs | Eligible full-site database identity and measured full-site coverage |
| 4. Model semantics | Versioned crop/source-scoped interpretation, resolved parent relationships, conservative ranges, alternatives | Supervisor-approved agricultural model and representative real-source coverage |
| 5. Retrieval | Fixed bounded intents, per-subject conjunctions and paths, mandatory fresh authorization | Online audience, controlled baseline, and confirmed evaluation protocol |
| 6. Assistant | Local current-evidence bridge, graph/text composition, citation and acknowledgment contracts | Existing live backend transport, compensation behavior, and visible WikiCrop flow |
| 7. Evaluation | Private-path comparison validator, bound protocol/reference receipts, separate metrics | Confirmed independent references and human adjudication |
| 8. Packaging | Separate opt-in authenticated graph/tools stack and private-storage instructions | Production security/compatibility, deployment, and optional recovery drills |

### Recorded verification

The ignored evidence packet is `cache/opencode-thesis-phases-20261004/`.
Source text, credentials, raw job artifacts, and private diagnostic details remain in the owned temporary fixtures outside the checkout.

- The full extension PHP suite passed 191 tests with 953 assertions.
- The full Python suite passed 235 tests, including all three isolated live Neo4j checks.
- PHP syntax lint, nine JSON-schema definition checks, 23 Python syntax checks, and `git diff --check` passed.
- `workflow-e2e-result.json` records five synthetic imported snapshots, four traceable exported claims, idempotent import, graph/text composition, and answer denial after rejection while the stale graph still contained the claim.
- `development-workflow-result.json` records only the hash-pinned development input, eleven eligible discovered pages, eleven completed worker jobs, one unclassified page, validated resume, portable paired import, and scoped primary-store authorization.
- `development-graph-result.json` records authenticated loading and evidence-identity lookup for one fixture-approved development claim.
- `package-result.json` records the pinned tools build, authenticated container-local loading, idempotence, a rebuild from four claims to three, and refusal to reactivate the retired export.
- `evaluation-cli-result.json` records private-mode reports and zero scientific denominators for an unapproved protocol, using fabricated input only.
- `preservation.json` records unchanged frozen worker, evaluator, schemas, protocols, and prior boundary work outside the explicitly extended exporter and README.

An isolated fixture approval is a simulated review transition for engineering verification.
It does not approve real agricultural claims or replace independent benchmark references.

### Post-period completion checks, 2026-10-05

The first review found relationship traversal ignoring season/condition, path results omitting opposing resistance claims, and Python exclusion closure missing a held-out alias's terminal target.
The repair changes only the two Python tools and their regression files; PHP, the assistant contract, and frozen pilot artifacts are unchanged.
The actual query-to-assistant regression confirms that diagnostic opposing claims with identical scope suppress generation even if a caller drops query completeness metadata.
The discovery regression refuses falsely eligible held-out terminals before source reads and derives exclusions from raw redirects rather than misleading precomputed resolution fields.

- Integrated query checks passed 27 tests, and discovery checks passed 20 tests.
- The full Python runner reported 244 tests: 241 passed and three live Neo4j tests skipped because Docker was unavailable.
- Four changed Python files passed syntax checks, all nine schema definitions passed validation, preservation checks passed, and `git diff --check` passed.
- The unchanged PHP and live Neo4j runtime evidence remains the 2026-10-04 run, not a new runtime check after repair.
- Round 1 findings are recorded in the ignored `review-round1.md`; the final `review-round2.md` records `request_changes` and the remaining major finding.

The final reviewer requested summer/irrigated resistance while susceptibility for the same variety, pest, and source was qualified only as summer.
The query omitted the susceptibility claim and returned `status=ok`, `complete=true`; forwarding its actual results through exact authorization still ran generation.
Empty and condition-only opposition reproduced the same defect, while explicitly incompatible winter scope was correctly excluded.
Missing qualifiers cannot establish incompatibility, so that verdict blocked completion of Phases 4-6.
At that handoff, no third review cycle or further source repair was attempted.

### Authorized conditional-conflict follow-up, 2026-10-05

The user authorized reopening only the remaining partially scoped opposition defect before finalizing the report.
Synthetic reproduction confirmed that season-only, condition-only, and unqualified opposition were omitted, producing complete query results that ran answer generation.
The query now keeps potentially overlapping opponents within the existing crop/source identity and returns unsupported/incomplete diagnostics.
The assistant independently detects the same potential conflicts even if a caller drops the query completeness metadata.
Explicitly disjoint seasons or conditions still permit generation; missing qualifiers are not scientific proof of a contradiction.
Both checks preserve exact canonical assertions, fresh authorization, source identities, and existing response formats.

- Two new focused regressions failed before repair, with 22 failing subcases, and passed after repair.
- Query checks passed 31 tests; assistant checks passed 20 tests.
- The full Python runner reported 251 tests: 248 passed and three live Neo4j tests skipped because no disposable local instance was selected.
- A separate finite-world oracle checked 162 scope pairs across both resistance directions, 324 direct compositions, and 324 actual query-to-assistant compositions, including dropped completeness metadata and reversed claim order.
- Four changed Python files passed syntax/compile checks and correctness-focused Ruff checks; all nine schema definitions validated.
- Frozen worker, evaluator, six schemas, both protocols, and unrelated entry changes remain unchanged.
- PHP, live Neo4j, and the earlier one-shot workflows were not rerun; their prior runtime evidence remains dated 2026-10-04.

The entry-relative patch, exact commands/output, final hashes, and acceptance map are in the ignored `conditional-conflict-repair-20261005/` subdirectory of the evidence packet.
The supervisor draft stays scoped to 28 September through 4 October; the 5 October repair is deferred to the next report, with its verified counts and pending independent check kept in internal records.
No separate reviewer was invoked in this build session; review is due through the caller before declaring the remaining-phase deliverable approved.
The earlier round-2 verdict is retained as historical evidence, not overwritten as approval of the new code.

The earlier package/development graph shutdown receipts record stopped projects with volumes and private files retained.
On 2026-10-05 the Docker daemon was unreachable, so shutdown of the two original owned verification containers could not be verified.
No Docker service was started for cleanup and no container, volume, or private evidence was deleted.

### Authorized verification, 2026-10-08

The user authorized focused regression tests and isolated synthetic runs for the offline packages.
The held-out split, the live site, sent materials, frozen pilot artifacts, and real agricultural review stayed out of scope.
Verification used a disposable synthetic MediaWiki 1.43.1 wiki (SQLite, PHP 8.3.14) inside a loopback-only Linux container with a 1 GiB cgroup memory limit, plus synthetic unit fixtures.

Three inspection findings were repaired and demonstrated:

- Answer-purpose lookup treated unresolved, unknown, or invalid relationship identities, and relationship targets without a unique same-crop membership, as eligible.
  Generation is now blocked with `unresolved_identity`, while evidence-purpose lookup and diagnostic display keep the same claims.
- The metadata inventory attached resolved terminal identity only to classified or selected rows, so an unchanged preparation and collection disagreed whenever an unclassified terminal existed.
  Safe non-excluded rows now carry stable resolved identity before classification or batch selection.
- Batch setup created attempt directories and input files before reserving output space or recording intent.
  Setup is now serialized and reserved before mutation, a refusal leaves the previous receipt and attempt tree untouched, and a hard interruption is restart-accounted as `interrupted` and needs explicit `--retry`.

Recorded results in this environment:

- PHP extension suite: 195 tests, 970 assertions, one documented skip for a different-owner fixture, no errors.
- Python suite: 272 tests, six documented skips (three Compose packaging checks, three Neo4j integration checks that need an explicitly selected disposable instance).
  The packaging checks ran separately on the host and passed three tests.
- Browser checks against the disposable synthetic wiki: three passed, covering a programmatically selected edge's claim/review links, revision navigation, the keyboard and no-JavaScript table route, and signed-out refusal.
  The run did not exercise a real pointer click on the rendered edge or bind the revision assertion to the seed identity.
- Red/green proofs: the new query regressions receive unresolved relationships in the generator on the pre-repair source; the three new runner regressions fail on the pre-repair source; a standalone inventory probe reports prepared/collected metadata mismatches before the repair and none after.
- Two synthetic workflow runs on the same wiki: metadata-first preparation, five completed page jobs across two batches per run, nine pending claims, nine fixture approvals, scoped export, three structured questions with fresh two-call authorization and no generation, a 95-105 day interval blocked as `numeric_uncertain` against a strict `< 100 days` test, SIGTERM interruption with explicit-retry recovery, and metadata-drift refusal before extraction.
  The second run demonstrates repeatability and idempotent reimport, not a clean installation.
- PHP syntax lint over 45 files, Python compilation over 28 modules, 15 JSON documents parsed, and `git diff --check` passed.

Run logs, receipts, exports, red/green outputs, and the browser screenshot stay in owner-only scratch storage outside the checkout and Git.
The repairs do not complete the acceptance gates above.
Full-site collection, held-out evaluation, ontology freezing, and real claim decisions remain separately gated.
The recorded synthetic checks passed; the full reproducibility and recovery milestone was not complete.
Independent review remained due and was not performed in that session.
