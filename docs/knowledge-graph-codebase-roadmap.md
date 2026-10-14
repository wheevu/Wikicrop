# WikiCrop thesis codebase roadmap

Created: 2026-10-03.

This document records the eight-phase codebase plan agreed in the conversation.
All phases stay within the WikiCrop knowledge-graph thesis requirements described in the [requirements check](knowledge-graph-requirements-check.md).
Site administration and production deployment are outside this deliverable.
Recording the plan does not authorize execution of the remaining phases.
On 2026-10-04, the user separately authorized pursuing all remaining code phases, subject to the existing data, audience, evaluation, and external-service gates.

Current status: Phase 1 is complete for its bounded offline purpose.
The boundary-repair slice and projection candidate each passed verification and separate independent review.
This does not authorize reader-facing retrieval, production deployment, or opening held-out data.
See the [boundary-fix verification note](knowledge-graph-phase1-boundaries.md) and the [projection experiment](knowledge-graph-reviewed-projection.md).

## Revision after plan review, 2026-10-03

A same-day review of this plan found that it had no milestone deadlines, discovered the assistant backend dependency only in Phase 6, and ordered the full-site run behind the reviewed pipeline although the 2026-09-28 weekly report promised the supervisor a full-site run with the frozen worker.
This revision adds Phase 0, tags checkpoint items, moves the preregistration freeze and the before-integration baseline ahead of retrieval work, allows an early exploratory full-site run, and points to the writing track.
The revision is a proposal from that review, not a supervisor-approved scope or schedule.
A second same-day check separates optional automation from mandatory limits, evidence eligibility, source identity, and evaluation controls.

Working order:

1. Record the external dependencies in Phase 0; unanswered items block only their dependent work.
2. Finish Phase 1.
3. Prove Phase 2 on a revision-pinned rice slice before expanding the reviewed pipeline.
4. Run exploratory Phase 3 coverage separately when source identity, held-out exclusions, and resource limits are verified; it may precede Phase 2 but never replaces that reviewed path.
5. Complete the Phase 5 evaluation prerequisite before performance-based tuning or comparative runs, then the minimum items of Phases 4, 5, and 6, Phase 7, and Phase 8 packaging.
6. Add hardening only where demonstrated needs and the confirmed deadline justify it.

### Reading the tags

`[minimum]` marks work needed to meet an item of the original brief or to keep the trust guarantees honest.
`[hardening]` marks work that improves robustness but can be deferred if the thesis deadline requires it.
Untagged items keep their original meaning until the deadline is known.
Deferring automation never defers limit enforcement, privacy, or current evidence eligibility.

## Phase 0. Record external dependencies

This is a non-code dependency checklist, not another codebase phase or a site-administration deliverable.
Technical facts should be established locally where possible; unresolved access, audience, and acceptance questions need the relevant service owner or supervisor.

- Current-semester defense and submission dates, used to schedule optional work rather than to weaken safety checks.
- [minimum] Access to the existing assistant backend behind `/ask`, as source or a development instance with enough control and metadata to preserve a comparable baseline.
- [minimum] Explicit audience and authorization policy for exported evidence, indexed text, and assistant answers; privileged export access does not establish a reader's access.
- [minimum] Whether the supervisor's 200-question workbook is the acceptance benchmark, whether either answer column was expert-checked, and how disputed answers and the 100 placeholder questions will be handled.
- [minimum] What the supervisor wants done with the 2026-09-26 full-site text export.
- Status of the MCT4SD paper and the listed 2026-09-22 deadline, tracked separately from code readiness.

Checkpoint: each item has a recorded answer, or a recorded "still unknown" with the date it was asked.
Recording an unknown does not satisfy the affected technical gate.
Projection verification and other unblocked engineering work do not wait for all these answers.

## Phase 1. Repair boundaries and verify the projection candidate

- [minimum] Address chatbot rendering, raw-export privacy, and the collector edge cases.
- [minimum] Correct the schema-validation oracle.
- [minimum] Reverify the existing projection candidate without expanding its purpose.

Checkpoint: targeted regression coverage passes, frozen artifacts remain unchanged, and the completed projection candidate receives separate review.

Keep the boundary-repair and projection deltas distinct in verification and review evidence.
The schema-oracle changes depend on the projection test fixtures; any later staging must preserve that dependency.
Commits, pushes, and off-machine backups require explicit authorization and are not actions scheduled by this plan.

## Phase 2. Prove one real-data reviewed path

Implement and demonstrate:

```text
revision-pinned source → worker → ClaimDocument → importVerified → review → export → Neo4j → lookup
```

- [minimum] Add the missing controlled offline import interface.
- [minimum] Add a separately guarded real-data export mode.

Do not merely remove `--synthetic-only`.
Preserve private output handling and explicit audience scope.

Checkpoint: actual source evidence survives PHP-to-Python transfer.
Reimport is idempotent.
A later rejection or source revision change removes eligibility without erasing history.

## Phase 3. Add full-site discovery and bounded execution

- [minimum] Build a revision-aware inventory with eligibility and exclusion reasons.
- [minimum] Enforce held-out exclusions, including redirect aliases.
- [minimum] Enforce page and byte limits before execution, and claim and evidence limits before importing each bundle; split inputs when necessary or refuse the run clearly.
- [minimum] Record completed, skipped, and failed work in a small manifest with source identities and enough information to restart without silent duplication.
- [hardening] Automate batching, retries, and resume beyond the bounded execution and accounting needed for the chosen workload.
- [minimum] Run the unchanged frozen rules as an exploratory baseline.

Keep cross-crop outputs separate until identity handling is corrected.

The inventory, held-out exclusion, and exploratory run may happen early, before Phase 2, once their own safety gates pass.
They use the frozen worker unchanged and match the full-site run promised in the 2026-09-28 weekly report.
Label the result as coverage of the current rules, not as reviewed or complete graph data.
The supervisor's 2026-09-26 export has about 420 page sections, not an established revision-aware inventory.
Its per-page IDs, revision IDs, and content hashes must be mapped before it is used as revision-pinned evidence or treated as an eligible full-site input.
Do not attach older database revisions to newer exported text.
Resolve held-out aliases before extracting their contents, and report unresolved identities as excluded or blocked rather than silently eligible.
Use a revision-pinned database source when the text export cannot meet those conditions.

A one-shot exploratory worker run is acceptable only after its resource bounds are established.
It is separate from reviewed import, which currently limits each verified document to 100 source pages, 500 claims, 20 evidence items per claim, and a 5 MiB payload.
Measure the relevant input and output sizes instead of inferring feasibility from section count.

Checkpoint: every eligible input is accounted for.
Interrupted work resumes without silent duplication.
Unsupported pages remain visible in coverage reports.

## Phase 4. Extend the model around required questions

- [minimum] Resolve entity identity and aliases.
- [minimum] Add evidence-backed, traversable parentage.
- [minimum] Define numeric filtering over units, ranges, seasons, and conditions.
- [minimum] Preserve conflicting assertions rather than selecting an arbitrary value.
- Extend agricultural concepts only where source coverage and required questions justify them.
- [minimum] Version changed semantics while preserving old snapshots and pilot artifacts.

The concept-extension item depends on the Phase 0 benchmark answer.
If the 200-question workbook is the acceptance benchmark, consider mapping to the supervisor's graph schema before inventing parallel relation types.

Checkpoint: synthetic cases distinguish different crops with identical short names, uncertain parents, conflicting values, and ambiguous numeric ranges.

## Phase 5. Build bounded, permission-aware retrieval

Prerequisite, before performance-based tuning or comparative evaluation runs:

- [minimum] Confirm and freeze the evaluation protocol, eligible question set, exclusions, scoring rubric, reference status, and hand-checked reference split with hashes.
- [minimum] Treat the first 100 supplied answers as candidate references, not confirmed gold; the existing preregistration is an unconfirmed draft, and placeholder answers remain unscored.
- [minimum] Preserve the before-integration assistant baseline, including its model, prompt, decoding settings, retrieval index, source revisions, audience, and context budget.
- [minimum] Record final code/schema versions, graph generation, and other arm-specific artifacts before comparative runs; if the backend cannot provide comparable controls, report that limitation rather than claiming a controlled improvement.

Required questions may guide the interface and model, but performance-driven tuning must be distinguished from evaluation.
Freezing a protocol now does not erase earlier exposure to the workbooks; disclose that exposure and do not call them an unseen test set.
The workbook comparison remains separate from the sealed rice held-out pilot and its opening approval.

- [minimum] Extend the existing subject/object links with fixed query intents.
- [minimum] Support the required conjunction of property filters and relationship paths.
- [minimum] Check current review state, permissions, revision visibility, and source currentness before supplying evidence to an answer generator.
- [minimum] Return generation, claim, review-version, and citation identity.
- [minimum] Distinguish empty results, unsupported questions, truncation, and dependency failure.
- [hardening] Measure query plans before adding indexes.

Checkpoint: stale, rejected, or unreadable claims cannot contribute to an answer.
Partial retrieval cannot masquerade as a complete negative result.

## Phase 6. Integrate the existing assistant and text index

- [minimum] Locate the backend source or establish a tested adapter contract; the request for access belongs in Phase 0.
- [minimum] Add versioned ingestion identity and record delivery success or failure; a failed delivery must not be reported as a successful update.
- [minimum] Prevent rejected, stale, suppressed, or unreadable indexed text and graph claims from reaching the answer generator, through invalidation or equivalent current-eligibility checks at retrieval time.
- [hardening] Automate retries and background index cleanup after the minimum delivery and eligibility guarantees are demonstrated.
- [minimum] Combine authorized text and graph evidence.
- [minimum] Handle conflicts, missing evidence, citations, timeouts, and outages explicitly.
- [minimum] Verify the visible flow inside WikiCrop.

Checkpoint: a real question produces a supported answer with usable source links.
Rejection, permission revocation, and dependency failure produce the expected behavior.
An approval followed by indexing and rejection must make that text ineligible for the next answer, even when the graph has already been refreshed.

## Phase 7. Add evaluation tooling

- [minimum] Accept private input paths rather than machine-specific workbook locations.
- [minimum] Reject placeholder answers as scoring references.
- [minimum] Separate extraction accuracy, retrieval quality, answer correctness, citation support, abstention, and runtime.
- [minimum] Compare before and after using the same questions, source snapshot, and controlled model settings, with the baseline recorded under the Phase 5 prerequisite.
- [minimum] Keep the frozen engineering evaluator unchanged.

Checkpoint: unknown answers never enter the accuracy denominator.
Without independent references, output remains engineering evidence rather than a scientific accuracy claim.

## Phase 8. Package a reproducible installation

- [minimum] Add an opt-in graph/service configuration rather than rewriting the minimal base Compose setup.
- [minimum] Document service topology, configuration, migrations, private storage, and dependency versions.
- Test rebuild, backup/restore, interrupted installation, and rollback; rebuild is `[minimum]`, the rest are `[hardening]`.
- [hardening] Back up authoritative MediaWiki claim/review state, not only Neo4j.
- [minimum] Reconcile README instructions with provisional, reviewed, offline, and online modes.

The brief asks for technical documentation and packaged source that deploys easily on WikiCrop's server; recovery drills go beyond that.
Deferring operational recovery drills does not permit implementation failures to erase authoritative claim or review history.

Checkpoint: an isolated installation can reproduce the pipeline and recover its state.
Reproduction is `[minimum]`; state recovery is `[hardening]`.
Production deployment is outside this codebase-only deliverable.

## Writing and paper track

This is not codebase work; it is recorded here so the code plan does not crowd it out.
Methods writing can describe verified existing protocols now; comparative evaluation methods must match the confirmed protocol, and results come only from preserved artifacts.
The paper gates, including the v2.3.1 freeze decision and held-out approval, stay in `~/Study/Thesis/wikicrop-kg-paper-milestone-roadmap-2026-09.md` Gate 6.

## What should remain optional

Bulk review, review-list filters, larger graph views, additional indexes, job tables, a second database, and LLM extraction should follow demonstrated needs.
They should not block the first real-data pipeline.

The simpler alternative is a full-site worker export loaded directly into Neo4j.
That is useful for exploratory coverage inspection.
It bypasses reviewed claims and cannot establish the final assistant's trust guarantees.

## Verification and remaining dependencies

### Verification recorded with the original plan

These are the original pre-implementation observations, not a statement of the current runtime or test status.

- Frozen worker, evaluator, and pinned schema hashes match protocol v2.
- The synthetic cross-crop collision probe reproduced the identity problem.
- `git diff --check` passes.
- The pre-existing working changes remain unchanged.

At that handoff, full test suites and browser flows had not been rerun.
Local PHP was unavailable, the Docker daemon was stopped, and the collector findings still needed integration reproduction.
Separate implementation review remained due.

### Update recorded on 2026-10-03

The boundary-repair slice subsequently passed 142 PHP tests with 813 assertions, 154 Python tests, and eight browser tests.
Three live Neo4j tests were skipped; external assistant and ingestion backends were not tested.
The collector cap and redirect defects were reproduced with synthetic MediaWiki fixtures.
The private-export checks include different-owner rejection before payload writes.
Independent completion review approved this bounded slice with no remaining findings.
That approval does not cover the pre-existing projection candidate.

Frozen pilot artifacts and the pre-existing non-test projection work remain unchanged.
Held-out pages and their aliases remain sealed.
The isolated test container was stopped after verification and review.
No commit, push, or deployment occurred.

### Update recorded on 2026-10-04

The roadmap now treats execution limits, basic work accounting, delivery outcomes, and current evidence eligibility as minimum requirements.
Early exploratory coverage requires source identity, held-out alias exclusions, and measured resource bounds.
Audience policy and controlled evaluation baselines are explicit dependencies.
The eight codebase phases remain unchanged in purpose, and the reordered work remains a proposal rather than supervisor approval.

Separate projection verification passed 145 PHP tests with 845 assertions and 158 Python tests, including all three live Neo4j checks.
A synthetic PHP-to-Python-to-Neo4j CLI check preserved evidence and conflicts and removed rejected or stale claims on a later full export.
A different-owner output-directory defect was reproduced and repaired.
Two added export-privacy regressions failed against the original candidate and passed after the repair.
The first independent projection review found a mismatch between permitted review reasons and export validation, plus a minor hash-length gap.
Both were corrected with failing-then-passing regressions; the loader, authoritative review semantics, frozen pilot artifacts, and offline purpose remain unchanged.
Final independent review approved the completed offline synthetic candidate with no remaining findings.
The reviewer reran the full PHP suite and independently checked exact hash validation and frozen-artifact preservation.
The user then authorized pursuit of the remaining code phases; their factual prerequisites and checkpoints remain binding.

The remaining phases now have bounded offline implementations and integration evidence in the [remaining-phases note](knowledge-graph-remaining-phases.md).
The development-only fixture preserved pinned source identities through discovery, frozen extraction, portable paired import, scoped export, primary authorization, and Neo4j lookup.
Synthetic checks demonstrated rejection during generation even before graph refresh.
This does not establish live backend integration, full-site currentness, agricultural approval, or scientific improvement.
Independent completion review of this new deliverable was still due at the 2026-10-04 handoff.

### Completion follow-up, 2026-10-05

The interrupted first review resumed and returned three major findings: relationship scope filtering, omission of opposing path assertions, and Python held-out alias-to-terminal exclusion.
Repairs are limited to the two affected Python tools and their regression files.
Integrated query/discovery checks passed 27/20 tests; the full Python runner reported 244 tests with 241 passing and three live Neo4j skips because Docker was unavailable.
Frozen contracts and unrelated entry work remain preserved.
Final re-review verified the three original repairs but requested changes for one major defect: partially scoped opposing assertions can still yield a falsely complete path and reach generation.
The remaining-phase deliverable is not approved; implementation stopped at the agreed two-round boundary without a third review cycle.
This also does not complete the online, full-site, agricultural, or scientific checkpoints.
The original owned verification-container shutdown remains unverified while Docker is unreachable, with all state and evidence retained.

### Authorized focused repair, 2026-10-05

The user authorized reopening the remaining conditional-conflict defect before finalizing the report.
The query retains opposing assertions unless an explicit shared season or condition proves their scopes disjoint.
The assistant applies the same conservative conflict check even when callers omit query completeness metadata.
The seven added tests cover missing and more-specific qualifiers, both predicates, explicit disjointness, numeric diagnostics, parent provenance, and claim order.
Query/assistant checks passed 31/20 tests; the full Python runner reported 251 tests with 248 passing and three unselected live Neo4j checks skipped.
A separate finite-world oracle checked 162 query cases and 648 compositions; syntax, schema, correctness-focused lint, and frozen-artifact checks passed.
These are self-verification results, not a new independent review verdict.
The caller must arrange review of the stable follow-up diff before treating the remaining-phase deliverable as approved.
The supervisor draft keeps the period through 4 October; the 5 October repair is deferred to the next report.
No live administration, held-out opening, deployment, or additional review invocation occurred.

### Dependencies and implementation handoff

The material dependencies are source/revision mapping, backend contract availability, audience policy, and trustworthy evaluation references.
None requires turning this plan into administrative work.

Implementation handoff: `orchestrate` when the work has genuinely independent packages.
Source/batching, projection, and assistant work can have separate file ownership once their shared contracts are settled.
