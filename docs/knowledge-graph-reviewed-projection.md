# Reviewed projection experiment

The claim and review tables in MediaWiki remain authoritative.
This experiment exports approved claim snapshots for an isolated Neo4j database and tests retrieval with synthetic pages.
It does not publish a reader-facing graph, change the frozen extraction worker, or authorize private or held-out data use.

## Interchange contract

`extensions/WikiKGExtractor/schema/reviewed-projection.v1.schema.json` describes the export document.
The exporter walks the entire snapshot store in stable ID order, not the 50-item reviewed display page.
It emits only approved claims with evidence that the chosen MediaWiki authority may read at the exact current revision.
The `snapshot_count` includes pending, rejected, and stale snapshots examined during enumeration.
Approved snapshots with incomplete traceability are omitted too.
`source_watermark` is the SHA-256 of the sorted snapshot ID, current decision status, and decision version tuples.
It is an observation of one export run, not proof that MediaWiki remains unchanged afterward.

Each exported claim retains its snapshot and claim IDs, snapshot hash, latest approved decision version, structured qualifiers, and the complete stored evidence record.
Approved conflicting claims stay separate.
The legacy labels `Crop`, `Variety`, and `Pest` remain legacy labels; this experiment does not assert that they identify taxa or cultivars.

The loader validates the document before writing, computes a generation from the normalized content and watermark, and builds it separately from the active generation.
It checks counts and identities before atomically switching the active pointer.
The switch locks the active pointer before it compares the activation epoch.
An interrupted or invalid build leaves the previous generation active.
A build that was superseded by a newer activation cannot become active.
Reloading the export that is already active re-verifies it without advancing the activation epoch.
A later full export can remove rejected or stale claims from the active generation without deleting their MediaWiki review history.
Loading an earlier, retired generation does not reactivate it.

## Local use

Run only on a disposable wiki with synthetic pages and installed review tables.
From the MediaWiki root, use an existing reviewer account and a pre-existing owner-only directory outside both the repository and web document root:

```text
php extensions/WikiKGExtractor/maintenance/exportReviewedProjection.php \
  --actor='<reviewer-account>' --synthetic-only \
  --document-root='<absolute-web-root>' \
  --output='<private-dir>/reviewed.json'
```

The export script refuses an existing output file and writes the completed JSON with owner-only permissions.
The destination directory must belong to the effective operating-system user running PHP.
The exporter checks the directory again after collecting claims and refuses platforms without reliable POSIX ownership checks.
Install the separate dependencies in an isolated Python environment from `extensions/WikiKGExtractor/tools/requirements-reviewed-projection.txt`.
Validate the file before starting Neo4j:

```text
python3 extensions/WikiKGExtractor/tools/reviewed_projection.py validate \
  --manifest '<private-dir>/reviewed.json'
```

Connect only to an isolated loopback Neo4j instance.
Set `WIKIKG_NEO4J_PASSWORD` privately in the process environment, not in a command argument or repository file.

```text
python3 extensions/WikiKGExtractor/tools/reviewed_projection.py load \
  --manifest '<private-dir>/reviewed.json' --uri bolt://127.0.0.1:<port>
python3 extensions/WikiKGExtractor/tools/reviewed_projection.py lookup \
  --uri bolt://127.0.0.1:<port> --claim-type property \
  --subject-type Crop --subject-id '<legacy-subject-id>' --predicate '<predicate>'
```

The lookup returns bounded claims and evidence coordinates and reports whether results were truncated.
These commands do not verify current wiki permissions or revisions at query time.

## Access and limits

Export only from a disposable local wiki with synthetic pages in this experiment.
An exported file and the Neo4j database may contain source spans; keep both outside Git and inaccessible to the web server.
The local retrieval command is for an operator testing fixed, parameterized question types, not an HTTP API for users.
Export-time page checks do not prove later readability, so no user-facing retrieval may serve these records until it checks the requesting user's current MediaWiki permissions, source revisions, and review state again.
Do not connect the current `WikiChatbot` frontend or the legacy worker's provisional Neo4j push to this database.

## Completion verification, 2026-10-04

This verification covers the offline synthetic projection candidate separately from the approved Phase 1 boundary-repair slice.
The first independent review requested a store-contract correction.
Final independent review approved the completed offline synthetic candidate with no remaining findings.

The isolated runtime used PHP 8.3.14, SQLite, Python 3.11.8, `jsonschema==4.26.0`, Neo4j Community 2026.09.0, and `neo4j==6.3.1`.
The PHP container had no network or published ports.
Neo4j published only Bolt on IPv4 loopback and used a new disposable database containing synthetic records only.
No working-wiki configuration, production database, external assistant backend, or held-out source was used.

| Check | Result |
| --- | --- |
| Full WikiKGExtractor PHP suite | 145 tests and 845 assertions passed |
| Projection PHP integration file | 18 tests and 246 assertions passed |
| Full WikiKGExtractor Python suite | 158 tests passed, including all three live Neo4j tests |
| Projection Python file | 31 tests passed within the full suite with the live Neo4j tests enabled |
| PHP syntax lint | 30 files passed |
| Frozen worker, evaluator, six pinned schemas, and protocol files | Unchanged |
| Roadmap phases, relative links, and diff whitespace | Passed |

The synthetic end-to-end check seeded five snapshots through `ClaimReviewStore::importVerified`, repeated the import, and exported through the actual PHP maintenance CLI.
Three approved claims survived Python CLI validation, Neo4j loading, and property or relationship lookup.
UTF-8 evidence coordinates, source identity, review versions, conflicting values, and nested qualifiers survived the transfer.
A repeated load preserved the active generation and its activation epoch.
Bounded lookup reported truncation.
Later rejection removed one claim from the next active generation while retaining both review events in MediaWiki.
A newer source revision produced an empty eligible export and an empty active graph.
Malformed exports and an already-retired generation could not replace that graph.
The live tests also exercised corrupted staging, an evidence link redirected to another snapshot, and competing activation transactions.

The CLI checks refused missing synthetic confirmation, unknown or unprivileged accounts, missing or conflicting document-root declarations, and unavailable effective-owner checks.
Existing output files remained unchanged.
An isolated privileged-process probe reproduced a privacy defect in the original candidate: it wrote a payload into a `0700` directory owned by another user.
The repaired exporter rejects that directory before reading the store or writing a payload.
Two added regressions failed against the original candidate and passed after the repair, covering different-owner directories and directories that become public during collection.
The different-owner regression requires a privileged process in a disposable fixture and skips when that fixture cannot be created.
The review also found that export validation rejected blank reasons permitted by the authoritative store.
The exporter now preserves that store contract for both approved and rejected decisions; the regression failed before the fix and passed afterward.
The projection schema now requires exactly 64 characters in every SHA-256 field, closing terminal-newline acceptance.
A regression covers all seven shared hash fields at 63, 64, and 65 characters and rejects malformed identities before database writes.
The loader, review semantics, frozen pilot schemas, and synthetic-only purpose remain unchanged.

From a disposable wiki with the extension loaded, run:

```bash
php tests/phpunit/phpunit.php extensions/WikiKGExtractor/tests/phpunit
python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python
php vendor/bin/parallel-lint extensions/WikiKGExtractor/src \
    extensions/WikiKGExtractor/maintenance extensions/WikiKGExtractor/tests/phpunit \
    extensions/WikiChatbot/includes
```

Enable the live tests only for a separately created disposable Neo4j instance:

```bash
WIKIKG_TEST_NEO4J_ISOLATED=1 \
WIKIKG_TEST_NEO4J_URI='bolt://127.0.0.1:<disposable-port>' \
    python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python
```

Those tests delete projection-labelled nodes as setup and must never target a database that needs to retain its contents.
Set `WIKIKG_TEST_NEO4J_PASSWORD` privately if the disposable instance requires authentication.

The first PHP attempts used an incorrect bootstrap or an incomplete reused fixture and failed before the full suite could run.
The final suite passed after the disposable fixture was restored from current code and public synthetic test fixtures, with hash checks.
These were fixture failures, not evidence that an incomplete run had passed.

This establishes offline engineering behavior only.
Real-data export, a controlled offline import interface, current-user online authorization, `/ask`, `/ingest`, custom ACLs, nonstandard filesystem mounts, and agricultural accuracy remain unverified.
