# Synthetic hardening checks

These checks cover exception handling, private-file writes, SQLite recovery, MariaDB storage, and the chatbot's MediaWiki integration.
They use invented fixtures in new local installations.
They do not approve agricultural claims, open held-out or alias text, load real development claims into Neo4j, or establish scientific accuracy.
The original thesis brief remains the acceptance baseline.

## Failure handling and private writes

An unexpected exception in `answer_query()` must return a blocked response without query payload, generated answer, or citations.
Intentional unsupported-query responses can still carry freshly authorized diagnostic evidence.
Those two paths have different contracts.

The offline question writer installs a complete private result without overwriting an existing path.
After the installed link is durable, it removes only the staging name created by that invocation.
It retains staging files when writing or installation fails, so an interrupted invocation does not erase its recovery evidence.
It never scans for or deletes another invocation's staging files.

## Run the three separate labs

Use Docker Desktop with Compose v2, Python 3.10 or newer, and the pinned Playwright runner for the browser lab.
The browser lab currently uses installed Google Chrome and `@playwright/test` 1.63.0.
Install the runner with `npm ci` in `extensions/WikiKGExtractor/tests/browser`, or pass its existing absolute `cli.js` path with `--playwright-cli`.
The MediaWiki image and dependency limits are described in the [synthetic verification guide](knowledge-graph-synthetic-verification.md).
The MariaDB lab pins an official 10.11 image for compatibility testing, not as a production recommendation.
Image preparation can require network access.
Runtime Docker networks remain internal, and the browser uses host-loopback endpoints only.

Each command requires a new root outside the checkout and every document root.
Do not reuse a failed root or supply an existing database.
The restore and chatbot labs require the already-pinned metadata manifest, not source page text or a rebuilt corpus.
Keep that manifest in an owner-only input directory with mode `0700` and file mode `0600`.

```sh
umask 077
LABS=extensions/WikiKGExtractor/demo/synthetic
CORPUS_MANIFEST='<existing absolute path to the pinned metadata file>'
PRIVATE_PARENT='<existing absolute path to an owner-only directory>'

python3 -B "$LABS/restore/run.py" \
  --private-root "$PRIVATE_PARENT/restore-new" \
  --corpus-manifest "$CORPUS_MANIFEST" --synthetic-only

python3 -B "$LABS/mariadb/run.py" \
  --private-root "$PRIVATE_PARENT/mariadb-new" --synthetic-only

python3 -B "$LABS/chatbot/run.py" \
  --private-root "$PRIVATE_PARENT/chatbot-new" \
  --corpus-manifest "$CORPUS_MANIFEST" --synthetic-only
```

Run the labs sequentially on a memory-constrained Docker host.
Each lab must stop its owned services and retain its private evidence and volumes.
There is no deletion or production-cutover step.
Use each command's `--help` for its current options and result location.

### SQLite backup and restore

The rehearsal starts with two newly installed synthetic wikis.
It creates review history in the source, stops its writers, and uses SQLite's backup API to create a consistent backup.
It restores the database into the separately owned target while that target is stopped.
Target settings, Compose identity, and receipt volume remain separate.

Comparison must include all stored snapshots, their full review histories, and actual MediaWiki revision content identities.
A reviewed export alone cannot show that a rejected claim's history survived.
Database integrity and source preservation checks are required as well.
This is a quiesced SQLite rehearsal, not a production recovery plan or a backup of uploads, external receipt volumes, or Neo4j.
It restores the main `syntheticwiki.sqlite` file only, not the auxiliary job-queue or cache databases.
The stopped source has no WAL/SHM sidecars before an immutable read-only connection opens it.
Do not use that read mode as a substitute for stopping writers on a live database.

### MariaDB compatibility

The separate lab uses MediaWiki's `mysql` adapter, a fresh database, private password files, and no published database port.
It runs the actual extension updater twice and exercises the real claim store with invented page revisions and review decisions.
Import idempotence, immutable-identity collisions, version-bound decisions, stale-version refusal, and ordered history reads are the relevant checks.
The existing SQLite-only fixture and its guards stay unchanged.

Passing these cases establishes compatibility for the exercised updater and storage operations.
It does not establish full offline-authorization compatibility, migration safety for an existing site, or production readiness.

### Chatbot inside MediaWiki

The browser loads a page rendered by real MediaWiki PHP.
The check uses the hook's actual HTML, PHP-provided configuration, and automatically loaded ResourceLoader module.
It clicks the controls and sends real HTTP requests to a separate local fixture endpoint.
No replacement DOM, fake `mw` object, injected chatbot script, or fulfilled chatbot request substitutes for that path.

The fixture checks literal response rendering and HTTP-failure recovery.
It does not contact the external `/ask` backend or demonstrate GraphRAG integration with that backend.
The existing browser-only fixtures remain a separate, faster check.

## Verification record

The dated execution result must distinguish host guard tests, actual service runs, full-suite checks, and checks that did not run.
Do not add overlapping test counts into a single total.
Keep credentials, databases, request logs, and screenshots outside Git and the web root.
Source hashes establish preservation and identity only, not agricultural truth or human approval.

## Verification, 2026-10-17

The isolated macOS/Docker Desktop run used MediaWiki 1.43.1, PHP 8.3.14, Python 3.13.16, MariaDB 10.11.19, and Playwright 1.63.0 with Google Chrome.
These are synthetic software results, not a production or scientific evaluation.

| Check | Observed result |
| --- | --- |
| Exception and staging regressions | Three decisive cases failed against unchanged HEAD modules loaded in memory and passed against the repaired modules |
| SQLite restore | Nine complete snapshot records, ten review events, and three actual revision-content identities matched after restoration; integrity passed and the source database stayed unchanged |
| MariaDB | Fresh installation, both updater passes, all five tables and their indexes, import/reimport, collision refusal, Unicode evidence, versioned decisions, and ordered history passed |
| Real MediaWiki chatbot | PHP markup, automatic ResourceLoader readiness, configured fixture URL, literal rendering, HTTP 503, and subsequent recovery passed |
| Existing chatbot fixtures | Eight browser tests passed separately |
| PHP suite | 196 cases, 973 assertions, one different-owner-fixture skip, no failures |
| Linux Python suite | 382 cases, six skips, no failures |
| Host synthetic checks | 28 checks passed, including the three labs' guards and local stub tests |
| PHP syntax | 51 files passed |

The six Python skips are three optional live Neo4j cases and three host Docker packaging cases.
The three host packaging checks passed separately without skips.
No new live Neo4j run is claimed by this pass.
The intentional process-interruption test prints a child `KeyboardInterrupt` traceback; the enclosing Linux suite finished successfully.
The host cannot run the PHP-adapter CLI cases directly because PHP is unavailable there; the packaged Linux suite exercises them.
Overlapping suites and repeated service runs must not be added into a larger test total.

The first restore attempts exposed a deprecated content accessor and host-side WAL-mode bookkeeping on a stopped database.
The corrected capture uses `TextContent::getText()`, and the backup opens the checked quiescent file without creating WAL bookkeeping.
A first full-suite run also exposed a source-string test that assumed the unrelated chatbot extension was mounted in the KG-only container.
Behavioral stub checks and the separate real-wiki browser test replace that source-string check.
Failed attempts remain distinct from the passing runs.

Owned services are stopped after each completed run, while private databases, backups, command logs, browser evidence, and volumes are retained.
No claim-decision sheet, real-data review event, original thesis material, held-out text, or external chatbot backend is part of this verification.

## Failure-path continuation

Fresh-root validation does not reserve a directory.
A lab must acquire its new root before treating any file beneath it as owned.
If another actor creates that root between validation and creation, refusal must leave that directory and its result files untouched.
Error diagnostics must follow the same ownership rule as successful results.

Result publication must finish before a lab prints passed status or exits successfully.
A full runtime check followed by a failed report write is a failed invocation.
The one-shot command must not overwrite an existing final result path or clear older evidence to make publication succeed.
Require both a successful exit code and a passed installed result before accepting a run.
Retained staging files are recovery evidence, not successful reports.

An owned child can exit between a status check and a termination request.
CPython already suppresses the ordinary `ProcessLookupError` for that race.
The chatbot runner also handles injected cleanup failures independently across its relay, other child process, and validated containers.
Genuine cleanup failures must mark the invocation failed while allowing independent cleanup steps to run.
These rules do not authorize signalling an unrelated PID or stopping a discovered foreign container.

### Continuation verification, 2026-10-18

The earlier table records the completed five-task pass.
The continuation used separate roots and evidence for the following checks.

| Check | Observed result |
| --- | --- |
| Competing root creation | Ten controlled real-thread interleavings preserved the other creator's result and produced no subprocess call or foreign diagnostic write |
| Result publication | Both helpers passed private-file, exclusive installation, payload-bound, link-failure and three fsync-boundary checks |
| Cleanup isolation | Injected failure of the first process and first container did not prevent attempts on the other process and container; ten rounds of concurrent owned children left an unrelated sentinel alive |
| Real MariaDB publication fault | Entry code returned zero and printed passed despite an obstructed result path; repaired code returned one without passed stdout and preserved the obstruction |
| Real MediaWiki chatbot publication fault | Entry code printed passed before failing its write; repaired code returned two without passed stdout and preserved the obstruction |
| Fresh happy paths | Both actual labs completed successfully with the repaired lifecycle |
| Targeted host suites | Nine MariaDB, nine chatbot and four shared publisher tests passed |
| Integrated suites | 196 PHP cases with 973 assertions and one skip; 396 Linux Python cases with six skips; no failures |

Each real fault run completed its application checks before the deliberately obstructed final report write.
The fault probes are expected-failure verification, not passing lab invocations.
No owned container remained running after any of the six real before/after/happy-path runs, and all recorded chatbot loopback endpoints were closed.
The full-suite skips have the same explicit limits as the earlier pass.
The root-race and cleanup-adapter checks are software fault-injection evidence, not agricultural or scientific measurements.
The final MariaDB suite also replaces an already-acquired root and verifies that its replacement receives neither diagnostics nor results.

## Further synthetic failure cases

This pass applies the same report-publication contract to the SQLite restore rehearsal.
An existing result must survive unchanged, and a failed installation or durability check must fail the command without passed output.
Backups, logs and retained staging bytes remain recovery evidence.
Neither a staging file nor a passed-looking file left by a failed durability check establishes a successful invocation.

MariaDB cleanup must validate each enumerated candidate independently.
Failure to inspect or stop one container must not suppress attempts on later candidates from the same owned project.
A mismatched label or private mount never grants permission to stop a container.
Failure to enumerate candidates must fail closed without searching for broader cleanup targets.
Any cleanup failure makes the invocation fail, even when its application checks passed.

The expanded browser checks keep the actual PHP hook, ResourceLoader module and loopback HTTP requests.
Malformed response handling, timeout recovery and concurrent reply placement are software checks against invented fixtures.
They do not establish the external chatbot backend's behavior or agricultural answer quality.
Every scenario blocks page requests outside the two local fixture origins.
The stub has two fixed synchronization actions, separate from chatbot questions: wait for the late response attempt, and release the first concurrent response after the second is visible.
These actions cannot choose response content, paths, URLs or application operations.
The actual 30-second application timeout is unchanged.
The timed-out fetch is aborted, so the test observes the server's later write attempt rather than claiming that a late response reached the browser.

### Further verification, 2026-10-18

The saved entry restore runner overwrote a deliberately existing result after a complete real rehearsal and returned zero with passed output.
The repaired runner completed the same application checks, refused publication with exit one, preserved the prior result, retained its staging file and printed no passed output.
Both runs stopped their owned services.

With one controlled inspection error around actual Docker containers, the entry MariaDB runner stopped its cleanup loop and left its owned database running.
The probe then verified that database's ownership and stopped it separately.
The repaired runner continued inspecting later candidates, stopped its database itself and still returned failure for the injected inspection error.
This is controlled fault injection, not a claim that Docker failed spontaneously.

An initial restore attempt stopped before its intended fault because Docker's automatic subnet pools were exhausted.
The later checks use temporary, test-only explicit IPAM subnets after checking existing Docker allocations and host routes.
They leave repository Compose files, daemon settings and earlier retained resources unchanged.
No network, volume, container or image was deleted to free capacity.

The first expanded browser run exposed a test-count error: the timeout check omitted the real PHP greeting from its expected message list.
Its trace, network records and screenshot showed the intended timeout and recovery behavior.
The corrected assertion preserves the initial messages and requires exactly the timeout and recovery replies after them.
The failed attempt remains separate from subsequent verification.

| Check | Observed result |
| --- | --- |
| Focused host suites | Eleven restore, ten MariaDB, ten chatbot and four shared publication cases passed |
| Fresh SQLite restore | Nine snapshots, ten review events and three revision identities matched; integrity and source-preservation checks passed |
| Fresh MariaDB | Both updater passes and the real storage fixture passed; immutable wiki image identity was recorded |
| Real MediaWiki browser checks | Four scenarios passed in each of two fresh installations, with no retries, skips or flaky results; each recorded fifteen application questions and two fixed fixture-control actions |
| Existing chatbot browser fixtures | Eight cases passed separately |
| Integrated PHP | 196 cases, 973 assertions, one skip, no failures |
| Integrated Linux Python | 403 cases, six skips, no failures |
| Host packaging | Three cases passed separately, without skips |

The PHP skip remains the different-owner fixture unavailable under the runtime user.
The six Python skips remain three optional live Neo4j cases and three host Docker packaging cases.
No new live Neo4j result is claimed.
The Python suite's intentional interruption case prints a child traceback while the enclosing suite succeeds.
The PHP runner also reports its existing deprecated entry point and slow-test notices.
Overlapping checks and repeated installations must not be added into a larger test total.
