# Reproducible offline verification, 2026-10-09

This milestone packages the synthetic reviewer workflow and checks its authorization, recovery, browser, and graph boundaries.
It is engineering evidence only.
It does not authorize full-site collection, held-out evaluation, real agricultural decisions, public assistant integration, or deployment.

Status: integrated synthetic verification passed; independent completion review approved on 2026-10-10 with no critical or major findings.

## Acceptance criteria

| Criterion | Required evidence |
| --- | --- |
| Fresh installation | Public version/checksum-pinned dependencies, a new synthetic SQLite database, and a maintained setup command |
| Repeatable extraction | Two completed runs of five page jobs across two crop-separated batches, unchanged snapshot identities on reimport, and retained receipts |
| Question authority | Two fresh PHP authorization calls per successful evidence-only question; no generation or model service |
| Safe refusal | Rejection, permission loss, and a source edit after export suppress query payload, answer, and citations through the real PHP bridge |
| Composition isolation | Caller/callback mutation cannot change the authorized question or evidence; final authorization denial suppresses generated output |
| Recovery | SIGINT, inherited child locks, concurrent invocation refusal, interruption near completion, and explicit retry retain truthful outcomes |
| Browser navigation | A real pointer click opens the fixture's claim; keyboard and no-JavaScript routes reach the exact seed revision; signed-out access discloses no claims |
| Graph package | Authenticated load and identity lookup, idempotent reload, later-export rebuild, and retired-export refusal in the existing opt-in package |
| Preservation | Frozen pilot artifacts, unrelated entry modifications, private inputs, and sent materials remain untouched |

The query-to-composition tests use generator spies, not a model endpoint.
The shipped question command remains evidence-only.
Neo4j lookup is export-time retrieval and cannot replace the current MediaWiki authorization checks.

## Corrections to the prior record

The 2026-10-08 workflow completed five page jobs across two batches per run, not five batches.
Both runs used the same database, so they established repeatability rather than fresh installation.
The prior browser test selected an edge programmatically and checked only that its revision URL contained a number.
The 55-snapshot PHP pagination test already covered the existing 50-row limit.
The dated [remaining-phases note](knowledge-graph-remaining-phases.md#authorized-verification-2026-10-08) and internal tracker now preserve those distinctions.

## Reproduction and evidence

Use the [synthetic package guide](knowledge-graph-synthetic-verification.md) for fresh setup and the happy-path workflow.
Browser dependencies and configuration are maintained under `extensions/WikiKGExtractor/tests/browser/`.
The real bridge verifier is `demo/synthetic/verify_authorization.py`.
The graph verifier is `demo/synthetic/verify_graph.py`, run inside the existing reviewed-offline tools container.

All runtime configuration, database files, credentials, receipts, and browser artifacts must stay in owner-only storage outside the checkout and document root.
Each verification run uses new output paths and retains its evidence.
Stopping owned resources must not erase databases, graph volumes, or evidence.

Retained private packet: `~/.cache/opencode/scratch/wikicrop-release-20261009-5fdaba88/`.
The successful fresh wiki is `private-7/`; earlier failed roots and logs are retained separately.
`current-evidence-index.json` routes to the final reports, suite logs, and archived receipts.
`browser-evidence-index.json` records the separate report and custom-screenshot directories.
The earlier OS-temporary packet was cleared during a pause, so none of its missing artifacts is used to establish this milestone.
These private paths record this run, not prerequisites for later reproduction.

## Results

Verification ran on Docker Desktop's native Linux receipt volume with MediaWiki 1.43.1, PHP 8.3.14, Python 3.13.16, a fresh SQLite wiki, and a separate PHPUnit database.
The owned wiki and graph containers used internal networks and 1 GiB memory limits.
Host browser access used the guarded loopback relay on port 8791, not a published container port.

| Check | Recorded result | Retained evidence |
| --- | --- | --- |
| Fresh installation | Two new SQLite databases; no existing database reused or reset | `setup-7.log` and `private-7/command-*.log` |
| Bounded workflow | Two completed runs, each with five page jobs across two batches; nine unchanged snapshot IDs on reimport | `private-7/workflow-results.json`, `receipt-state-run7.tar.gz`, and the owned receipt volume |
| Structured questions | Six questions across both runs; two fresh PHP calls each; no model/generator call; straddling interval blocked as `numeric_uncertain` | `private-7/evidence-1/`, `private-7/evidence-2/`, and `private-7/workflow-commands.log` |
| Real authorization bridge | Six cases passed; permission loss, source revision drift, and a stale post-rejection watermark suppress all query/answer/citation payload | `private-7/authorization-results.json` and `authorization-cases/` |
| Reduced fresh export | Surviving evidence is freshly authorized twice, but `partial_manifest` blocks completeness and generation; nine stored snapshots remain while eligible claims fall to seven, then six | Later exports and authorization case results |
| Browser journey | Three passed with no retries or flaky results; real pointer click, exact seed revision, keyboard/no-JavaScript route, and signed-out refusal | `browser-run-IW7eEJ/report.json`; screenshot under `browser-run-0i1rqG/results/` |
| Opt-in graph package | Bad credentials refused; nine exact claim lookups; idempotent reload; six-claim rebuild; three removed claims absent from active lookup; retired export refused | `private-7/graph-initial-results.json`, `graph-rebuilt-results.json` |
| PHP suite | 196 tests, 973 assertions, one different-owner-fixture skip, no errors | `private-7/command-9g1i4vx3.log` |
| Python suite | 296 tests, six skips, no failures; includes the real-OS signal/lock/retry checks and callback-mutation/final-denial regressions | `private-7/command-qdyiz4bd.log` |
| Host packaging checks | Three passed separately; their container counterparts account for three Python skips | `packaging-final.log` |
| Syntax and validation | PHP lint over 47 files, Python compilation over 35 files without bytecode writes, correctness-focused Ruff, three JavaScript syntax checks, 11 schema definitions, six further JSON documents, and `git diff --check` passed | `php-lint-final.log`, `syntax-python.json`, `ruff-final.log`, `json-validation.json` |
| Frozen artifacts | Worker, evaluator, six pilot schemas, two protocol files, and pilot protocol note match HEAD byte-for-byte | `frozen-preservation.json`, 11 files |

The other three Python skips are the optional generic Neo4j integration tests, which require their own explicitly selected disposable instance.
The graph package's authenticated initial/rebuild verifiers ran against a fresh owned graph volume instead; this does not mean those skipped tests ran.
Recovery coverage uses real SIGINT and parent-death/inherited-lock cases, including concurrent refusal while a worker survives and explicit retry after interruption near completion.
Those runner checks ran on native Linux storage, not Docker Desktop's host-shared filesystem.
The targeted bounded suites passed 36 tests with no skips in `bounded-os-final.log`.
`receipt-storage-probe.json` records acceptance of the native volume and refusal of the actual host-shared mount.

Integration repaired test-package defects in Composer/PHP setup, volume ownership initialization, Apache account lookup, and host access to the no-egress wiki.
The authorization fixture now writes revisions through the same supported MediaWiki API as setup.
The verifiers distinguish total stored snapshots from eligible exported claims and retain the existing conservative partial-manifest behavior.
Focused graph-count and direct-composition question-isolation regressions failed before their repairs and passed afterward.
The direct composition helper's question-copy repair was followed by the final full PHP/Python suite run; it uses generator spies, while the shipped question CLI remains evidence-only.

Unrelated entry modifications remain in the working tree.
No sent material, thesis draft, held-out page content, production database, or supervisor communication was changed.
No commit, push, merge, or deployment was performed.
`cleanup-record.json` records the stopped owned wiki/graph containers and retained volumes.
The loopback relay was also stopped; no container, volume, database, or evidence file was deleted.

## Review and remaining gates

Independent completion review finished on 2026-10-10 after the complete deliverable passed its checks and the diff was stable.
The primary reviewer reached its usage limit without a verdict; the configured `reviewer-spark` fallback approved the same packet.
The review covered the current query/composition rules, source authorization, runner accounting, and synthetic package isolation.
It checked the 93-file hash-bound inventory, preservation evidence, host-runnable tests, and retained integration results.
The reviewer did not restart stopped services or repeat the PHP, browser, and live graph runs.
Its full host-side Python discovery failed on Linux-only process-supervision cases; the packaged Linux run remains the passing full-suite target.
The verdict and its limits are retained privately in `completion-review-result-20261010.md`.

Three nonblocking notes are accepted without further code changes:

- The unexpected-exception branch in `answer_query` can retain already-authorized diagnostic query/citation payload.
  A later hardening change can clear payload on that exceptional branch without removing intentional incomplete-evidence diagnostics.
- The question writer retains a staging hard link after success as well as after interruption.
  A later storage change should either document success retention or define safe cleanup of that exact owned staging name.
- Property rows lack the relationship rows' snapshot data marker.
  Current graph edges cite relationship snapshots only; property-backed edges would need matching row markers and coverage if introduced.

These notes do not approve model integration, agricultural interpretation, or production use.
The original WikiCrop review dated 2026-09-25 remains requirements context, not approval of this code.
The [requirements check](knowledge-graph-requirements-check.md) records the repository's acceptance baseline.
The original thesis requirements and separate research/production gates still apply.

## Publication re-verification, 2026-10-14

The current tree was rechecked in the maintained synthetic runtime with the separate PHPUnit database, without resetting the W1a installation.
These are additional dated software results, not replacements for the historical table above.

| Check | Result |
| --- | --- |
| PHP listing and full suite | 178 locally declared methods plus 18 inherited `testValidCovers` checks; 196 cases, 973 assertions, one different-owner-fixture skip |
| Linux Python full suite | 360 cases, six skips, no failures |
| Separate optional checks | 31 projection tests passed against a fresh disposable Neo4j instance, including the three optional live cases; three host packaging tests passed |
| Synthetic workflow | Two runs of five page jobs across two batches, nine unchanged snapshot IDs on reimport, no model calls |
| Browser and authorization | Three real-wiki browser journeys and six PHP authorization cases passed; eight separate chatbot browser fixtures passed |
| Syntax | PHP lint over 44 source/maintenance/test files and both changed JavaScript scripts passed |

The six Python skips are the three optional Neo4j cases and the three host Docker packaging cases, exercised separately rather than silently counted as part of a zero-skip full run.
The available 06/10 local packet separately lists 174 declared methods plus 17 inherited framework checks, 191 cases, which explains that packet's declaration/listing difference without claiming recovery of the original emailed archive.
The chatbot fixtures establish frontend rendering, timeout, response validation and request ordering, not availability of the external `/ask` backend.
All owned wiki, graph and loopback-relay services were stopped; private databases, credentials, receipts, logs and volumes were retained outside Git and the web root.
The private dated verification packet is `~/Study/Thesis/generated/w1b-verification-2026-10-14-gGCInr/`.
Real development data was not approved or exported, and the synthetic-only exporter guard was not changed.
