# Bounded offline discovery and extraction

Status on 2026-10-08: implemented and exercised against a disposable synthetic wiki plus synthetic unit fixtures.
The user authorized focused regression tests and isolated synthetic runs; held-out data, the live site, and real agricultural review stayed out of scope.
Independent review remains due; the dated results are in `knowledge-graph-remaining-phases.md`.

## Runtime and privacy requirements

Use a prepared Linux MediaWiki runtime with PHP, the WikiKGExtractor extension, and the Python runtime dependencies (`jsonschema`).
The Python-only `tools` image in the reviewed-offline Compose stack is not a wiki runtime and must not be used to run collection.

The runner and collector require owner-only private input and output paths outside the checkout and `DOCUMENT_ROOT`.
Linux receipt directories must use ext2/3/4, XFS, Btrfs, tmpfs, ramfs, or overlay storage.
The runner refuses other mount types with `receipt_filesystem_unsupported` before taking the receipt lock or dispatching work.
Docker Desktop host file sharing does not retain the inherited lock after parent death; use the synthetic package's native Linux receipt volume instead.
Set `DOCUMENT_ROOT` for the runner and provide the canonical wiki document root explicitly.
The named actor must be a registered reviewer with `wikikgextractor-review` permission.

Collection and extraction require Linux, a finite cgroup-v2 `memory.max`, and inherited per-process `RLIMIT_AS`.
Python must expose `waitid` with `WNOWAIT` and POSIX signal masking so the supervisor can retain its child's identity until cleanup.
The configured memory value is a virtual-address-space limit for each supervised child, not RSS and not an aggregate limit.
The cgroup path and finite aggregate limit are pinned into the run receipt and must remain unchanged for later batches.
The process group is created by the runner; timeout, output-limit, and cancellation cleanup targets only that group.
The supervisor observes exit without reaping the leader, cleans up the owned group, then reaps the child.
This ordering avoids signalling a PID that the operating system has already released for reuse.
stdout/stderr capture is bounded and never included in content-bearing logs or summaries.

Limits remain capped at 1,000 metadata rows, 100 content pages per collector invocation, 10 pages per planned batch, 5 MiB per source page, 50 MiB source bytes, 900 seconds per invocation, and 100 MiB retained output by default.
A batch uses one overall timeout across collection and extraction.
The configured timeout stays in the immutable receipt; the remaining deadline is an execution parameter, not a new run identity.
Every retained attempt, source page, worker artifact, and receipt counts against the run output budget.
Collection reserves room for receipt replacement and divides its remaining file budget over the selected source files and inventory.
Batch setup serializes the prospective receipt and reserves setup bytes, the atomic-receipt peak, and downstream headroom before it creates an attempt directory or input file; a refusal leaves the previous receipt and attempt tree untouched.
A setup step that fails after the reservation is still recorded as a retained failed attempt.
Extraction caps every expected output file, including the raw input if the worker rewrites it.
The Linux resource controls are exercised in a finite cgroup-v2 container; measured full-site coverage remains outside this verification.

## CLI

Run from the repository root.
`PRIVATE_RUN_DIR` must already exist with owner-only mode `0700`; `MANIFEST`, `CLASSIFICATIONS`, and generated inputs must be owner-only files in private `0700` directories.

Prepare a metadata-only immutable plan.
This invokes the fixed maintenance script under process supervision and does not read page content:

```text
python3 -B extensions/WikiKGExtractor/tools/bounded_discovery.py \
  --operation prepare --output-dir PRIVATE_RUN_DIR --actor REVIEWER \
  --offline-only --audience actor-only --corpus-manifest MANIFEST \
  --classifications CLASSIFICATIONS --runtime-root WIKI_INSTALL_ROOT \
  --document-root WIKI_DOCUMENT_ROOT [--timeout 900] \
  [--memory-bytes 1073741824] [--max-output-bytes 104857600] [--retry]
```

`--retry` on `prepare` is accepted only after a recorded failed or interrupted metadata attempt, with the exact same inputs, code pins, runtime roots, and limits.
It creates a new attempt directory; it never reuses the old one.
A running metadata attempt is persisted before any attempt directory exists, so a hard interruption is recorded as `interrupted` on the next invocation and requires that explicit `--retry`.

Execute one planned batch, sequentially.
Repeat with each numeric batch index; no parallelism or implicit retries are performed.
The resource values must match the immutable plan:

```text
python3 -B extensions/WikiKGExtractor/tools/bounded_discovery.py \
  --operation batch --output-dir PRIVATE_RUN_DIR --batch-index INDEX \
  --actor REVIEWER --offline-only --audience actor-only \
  --corpus-manifest MANIFEST --classifications CLASSIFICATIONS \
  --runtime-root WIKI_INSTALL_ROOT --document-root WIKI_DOCUMENT_ROOT \
  [--timeout 900] [--memory-bytes 1073741824] \
  [--max-output-bytes 104857600]
```

After a failed or interrupted batch, pass `--retry` to create a new retained collection and extraction attempt.
Reusing a completed batch verifies its pinned metadata and artifact hashes; it does not rerun the worker.

Read a content-free status summary:

```text
python3 -B extensions/WikiKGExtractor/tools/bounded_discovery.py \
  --operation summary --output-dir PRIVATE_RUN_DIR
```

The extraction-only compatibility route remains available with `--operation extract --inventory PATH --corpus-manifest PATH --output-dir PRIVATE_RUN_DIR --actor REVIEWER --offline-only --audience actor-only`.
It retains its v1 receipt contract; incomplete v1 attempts cannot be resumed in place.
Use a new private directory for a new standalone run.

The fixed PHP maintenance interface accepts `--metadata-only` for metadata preparation, or `--selected-pages=PRIVATE_JSON_LIST --expected-metadata=PRIVATE_JSON_SNAPSHOT` for content collection.
The list contains unique terminal page IDs.
The expected snapshot binds primary database identity and all page/revision/namespace/redirect rows; PHP refuses any drift before opening selected page content.

All commands are noninteractive.
Unknown options fail via argparse.
Operational failures return nonzero; terminal diagnostics contain only a stable error class, and summaries contain identities/counts/bytes/timing, not source text.
`run_in_progress` means another invocation or its inherited child still owns the receipt lock.
Wait for that invocation to finish; do not delete or replace the lock file to force a retry.

## Metadata, batches, and authority

The metadata-only inventory retains all bounded page identities, revisions, namespaces, raw redirect metadata, redirect chains, and terminal classifications.
It contains no wikitext, source-file references, or fabricated content hashes.
`OfflineSourceInventory::inventory` requests classification-only access from its callback for metadata-only preparation and for unselected terminals.
Classified terminal candidates use `eligible: false` and `reason: awaiting_collection`; unresolved or unsupported metadata remains explicitly classified by its reason.

The immutable plan uses deterministic crop-then-page-ID order and batches of at most 10 pages, never mixing crop groups.
It stores the full metadata inventory, source database identity, reviewer, manifest/classification hashes, worker/protocol/schema/PHP/supervisor code identities, runner identity, limits, and exact selected page/revision/crop identities.
Held-out aliases are closed over the full redirect metadata before any source read.
During collection the PHP process checks the primary database identity and every prepared page/revision/redirect row before content access.
It also checks current permissions, source revision, suppression flags, and page-byte limits.

Collection receives a private list of selected terminal IDs plus the prepared metadata snapshot.
The PHP collector returns all metadata rows and marks other planned pages `not_selected_in_batch`; omitted pages are never interpreted as unclassified.
Python compares the returned full metadata and selected identities against the plan before extraction.

There is one receipt authority: `wikikg.bounded-discovery-receipt.v2` at `PRIVATE_RUN_DIR/receipt.json`.
All CLI operations, including `summary`, acquire the same nonblocking exclusive lock on the private, empty `.receipt.lock` file.
The lock is synchronization, not another outcome ledger.
Supervised children inherit its descriptor, so a surviving child can keep an abruptly terminated parent's run locked.
Its top-level fields are `config`, `inventory`, `batches`, `prepare`, and `summary`.
Each batch records its index, crop, exact page/revision/title/kind/crop members, status, and append-only `attempts`.
Each attempt records collection and extraction stage status, termination reason, elapsed time, selected identities, collection inventory hash/path, and the in-place extraction receipt with artifact hashes.
Collection and extraction attempts have distinct private directories.
Attempts are append-only and never cleaned up automatically.
A crash-left `running` state is recorded as `interrupted` on resume.
Only an invocation holding the lock may perform that recovery.
Owner-only `.receipt-<pid>-<nonce>.tmp` files from interrupted writes remain retained and count against the budget; they are never adopted as receipts.
Handled SIGINT or SIGTERM cancellation runs process cleanup and records interruption where the receipt can still be written.
Limit-triggered cleanup allows a two-second TERM grace period before KILL; receipt and filesystem work are outside that child-process deadline.
SIGKILL, machine failure, escaped process groups, disk exhaustion, and kernel-level termination cannot guarantee a final receipt write.
Those cases need authorized recovery evidence before retrying or claiming completion.
A v1 receipt is not migrated implicitly; prepare refuses to adopt it.

The standalone extraction route continues to use `wikikg.bounded-discovery-receipt.v1`.
Its completed artifacts are reused only after source, code, limit, and artifact checks.
Failed, interrupted, or pending v1 attempts refuse in-place resume to prevent stale output inheritance.

## Summary semantics and status

The v2 summary reports metadata rows, planned candidates, current and historical collected terminals, batch-specific pending scheduling counts, exclusions, stage outcomes, input bytes, retained output bytes, elapsed time, attempt counts, and `complete`.
A page contributes once to selected-page status counts even when several attempts exist.
`page_outcomes` distinguishes pending collection, failed/interrupted collection, collected-but-not-extracted pages, and actual extraction states.
`extraction_states` counts jobs only; a collection failure is not an extraction failure.
Collection failure counts denote pages whose latest batch collection was not accepted, not a measured number of individual failed page fetches.
Partial source files from a rejected batch remain diagnostic artifacts and do not become accepted extraction inputs.
`content_eligible_terminals` counts current successful collection states; `ever_collected_terminals` retains the historical count.
Byte and elapsed-time totals include repeated attempts, unlike the deduplicated page-state counts.
Extraction input validation records its byte total once per attempt instead of copying the same failed-read total onto every page.
Collection input-byte totals include only known measurements; `collection_attempts_with_unknown_source_bytes` makes incomplete measurements visible.
`scheduled_other_batch` counts pages still awaiting collection outside the most recently attempted batch, not every page in other batches regardless of completion.
`worker_produced_no_claims` counts current completed jobs whose candidate artifact contains zero claims.
A worker producing zero claims is an extraction outcome, not evidence that the source lacks relevant facts.
`complete` requires completed preparation, accepted completion of every batch and its latest collection/extraction attempt, the exact completed job set, and retained output within the configured limit.
Completed jobs inside an interrupted batch do not establish completed orchestration.
The terminal summary recomputes retained bytes after receipt persistence.
The `summary` stored inside the receipt is a pre-write observation, not a reusable authorization or a current disk measurement.

Implementation status: the 2026-10-08 synthetic verification covers C1-C5's bounded workflow and the recorded regressions.
It does not establish full-site behavior, surviving-child concurrency, or production readiness.
The 2026-10-09 continuation adds a reproducible synthetic package and the missing recovery checks; its results must be recorded separately.
Targeted Python command: `python3 -B -m unittest discover -s extensions/WikiKGExtractor/tests/python -p 'test_bounded*.py'`.
Run Linux enforcement checks inside an explicitly selected finite-cgroup disposable environment.
