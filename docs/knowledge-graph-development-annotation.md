# Development annotation workflow

Prepare full pinned sources, retain source-first submissions, then inspect discrepancies.
This local tool never runs extraction, changes claim review, calls a model, opens a held-out mode, or computes scientific scores.
The frozen worker, pilot evaluator and pilot schemas remain unchanged.
The [rubric](../extensions/WikiKGExtractor/tools/development-annotation-rubric.md) is a proposal for development practice, not an approved reference protocol.

## Reproduce the synthetic demonstration

The maintained demonstration uses only [fictional fixtures](../extensions/WikiKGExtractor/demo/annotation-synthetic/fixture.json) from this repository.
It needs Python 3.11 or later and the existing `jsonschema` dependency.
It does not read thesis files, call a worker or evaluator, contact the network, use a database or model, or annotate real sources.
The eleven-page real development pack stays blank.

Create an owned private parent outside every checkout and hosted document root, then run one command from the repository root:

```bash
umask 077
mkdir -m 700 <private-root>

PYTHONDONTWRITEBYTECODE=1 python3 \
  extensions/WikiKGExtractor/tools/development_annotation_demo.py \
  --output <private-root/demo>
```

Use the real parent path, not a symlink.
The `demo` directory must not exist, even if it is empty.
Pass `--document-root <path>` for additional hosted roots.
The command accepts no real input, protocol or held-out option.
Run it again in a new directory rather than changing or deleting earlier evidence.

The command executes twelve actual annotation CLI calls:

1. Prepare a blank synthetic pack, then verify an identical retry.
2. Locate a fixture quote and validate its explicitly synthetic submission.
3. Retain the original submitted bytes, then verify an identical retry.
4. Compare four synthetic candidates with seven synthetic annotations, then verify an identical retry.
5. Require refusals for byte-based coordinates, a changed revision and tampered candidate bytes.
6. Retain a linked version-two correction in a new directory without changing version one.

The discrepancy groups keep exact possible pairs, a different literal qualifier, an omitted assertion, duplicate ambiguity, an unresolved identity, an outside-scope gap and an unmatched candidate separate.
Every item must appear exactly once.
All decision fields remain blank, provenance stays `synthetic`, and no scientific score is computed.
These are software checks, not annotation agreement or extraction quality results.

`commands.json` records the exact argv, exit code and JSON result of each completed call.
`verification.json` records those results, fixture and tool hashes, and hashes for every generated artifact except itself.
The report has at most 64 artifact entries and is limited to 64 KiB.
Stdout contains only a bounded summary and the report's SHA-256; keep that hash separately if you need an external report anchor.
Receipt timestamps and absolute command paths can differ between fresh runs.
The report is a record of one checked run, not a byte-identical cross-machine benchmark.
Failed command checks leave their artifacts and command records, without a passed verification report.
Keep failed outputs for inspection and choose a new private directory.

## Prepare a blank pack

Run from the repository root with Python 3.11 or later and the existing `jsonschema` dependency.
Create an owned private parent directory outside every checkout and hosted document root.
Use its real path, not a symlink.

```bash
umask 077
mkdir -m 700 <private-root>

python3 extensions/WikiKGExtractor/tools/development_annotation.py prepare \
  --input <pinned-development-input.json> \
  --manifest <pinned-corpus-manifest.json> \
  --output <private-root/source-pack>
```

The default protocol is `extensions/WikiKGExtractor/protocol/rice-engineering-pilot.v2.json`.
Its development-input, manifest and schema checksums must match.
Preparation also requires integer source identities, nonblank titles, distinct predicate lists and qualifier names supported by the annotation schema.
Unsupported pins or vocabulary fail before any output bundle is created.
The pack contains the root page and the selected development varieties, with full original wikitext, source pins, the rubric, a schema and empty forms.
It copies neither other splits' manifest records nor candidate claims, workbook answers, agent answers or selected passages.
An alternate `--protocol <file>` supplies explicit pins for synthetic practice or a separately authorized development corpus; it does not authorize another split or change the frozen pilot.
Do not create a replacement protocol to relabel held-out material as development.

Pass `--document-root <path>` for each additional hosted root known to the operator.
The tool rejects destinations inside those roots and every Git checkout.
It cannot discover every web server, sync service or POSIX ACL on the machine.
Use local private storage with no broader ACL access; mode bits alone are not proof of privacy.
Every generated directory has mode `0700`, every file `0600`.
Windows is refused because this tool does not verify Windows ACLs.

## Annotate without editing Python

Copy `submission-template.json` to a separate working JSON file.
The blank template intentionally fails submission validation until you fill in the submission ID, annotator ID and actual timestamp.
Keep exposure `unknown` unless you can report it.
For practice generated by software, use `synthetic` and say so in the notes.
No human identity, expert approval or independent-reference label is prefilled.

Copy `annotation-form.json` into the working file's `annotations` array once per source assertion.
Fill the blank fields using the rubric.
For relationships, replace `value_literal` with `object` using the same fields as `subject`.
Do not edit the pack's original forms or sources.
Record each page as `not_started`, `partial` or `complete`; leave empty annotations visible rather than treating an unread page as a negative result.

Copy your own exact quote into a UTF-8 file, preserving all whitespace and line endings, then locate it:

```bash
python3 extensions/WikiKGExtractor/tools/development_annotation.py locate \
  --pack <private-root/source-pack> --page-id <page-id> \
  --quote-file <private-quote.txt> --output <private-root/evidence-1>
```

Copy the resulting `evidence.json` object into that annotation's `evidence` field.
If the quote repeats, add `--start <zero-based-codepoint-offset>` to choose the occurrence explicitly.
The command does not suggest what to quote or decide that a quote supports a claim.
Offsets count Unicode codepoints in `[start, end)`, not bytes or UTF-16 code units.

## Validate and retain before machine exposure

```bash
python3 extensions/WikiKGExtractor/tools/development_annotation.py validate \
  --pack <private-root/source-pack> --submission <working-submission.json>

python3 extensions/WikiKGExtractor/tools/development_annotation.py retain \
  --pack <private-root/source-pack> --submission <working-submission.json> \
  --output <private-root/submission-v1>
```

`validate` checks format, pins, quotes, units and qualifiers' explicit states, IDs and page accounting.
It writes nothing and does not judge semantic support.
`retain` saves the exact submitted bytes, their source-pack binding and a dated receipt.
Timestamps require an explicit timezone and seconds in RFC 3339 form, such as `2026-10-10T08:00:00Z`.
This workflow rejects leap-second timestamps and uses its own check, so optional format-validation packages are not required.
Keep the printed `submission_sha256` separately as the original anchor.
`source_only_reported` is self-reported exposure, not established independence.
Actual source-first work, identities, expertise and adjudication still require human confirmation.

For a correction, keep the same submission and annotator IDs, increase `version` by one, and set `previous_submission_sha256` to the earlier printed hash.
Use a new output directory and add `--previous <retained-v1>` and `--previous-sha256 <original-sha256>` to `retain`.
Known machine exposure and synthetic provenance cannot be removed in a correction.
The earlier files stay intact.

## Compare, then leave judgments to people

Only after retaining the submission, select an existing candidate artifact and record its SHA-256 separately.
No worker run is needed.

```bash
python3 extensions/WikiKGExtractor/tools/development_annotation.py compare \
  --pack <private-root/source-pack> --retained <private-root/submission-v1> \
  --submission-sha256 <original-submission-sha256> \
  --candidates <pinned-candidate-claims.json> --candidate-sha256 <candidate-sha256> \
  --output <private-root/comparison-1>
```

Read `discrepancies.txt` for the group summary and `report.json` for all assertions and evidence.
Copy `decisions.csv` out of the retained bundle before recording two views and an adjudicator's decision.
No command imports those decisions into claim review or promotes them to a reference set.

A possible pair has one annotation and one candidate sharing the exact pinned source, resolved subject type and literal, and predicate.
Value or target, unit, qualifiers and coordinates are compared separately.
Units on machine values are only recognized as one of the literal suffixes `ngày`, `cm`, `tấn/ha`, `tạ/ha` or `kg/ha`; the candidate schema has no separate unit field.
There is no range normalization, unit conversion, alias mapping or agricultural interpretation.
Different literal spellings may therefore be unmatched or differ even when a person later considers them equivalent.
Multiple items with the same source, subject and predicate stay ambiguous instead of receiving a convenient one-to-one pairing.
Multi-source machine claims stay in an unresolved group rather than being counted once per page.
Outside-scope annotations, unresolved identities and unstarted pages remain visible.
Neither a possible pair nor an unmatched item is an error or correctness label.

## Persistence and failure behavior

All commands are local and noninteractive.
Successful commands print bounded counts and checksums, never source passages or annotator IDs.
Errors are JSON on stderr with a stable code and exit status `2`; success returns `0`.
Unknown flags fail, and each subcommand has `--help` with a next-step template.
Files are bounded to 16 MiB, bundles to 64 MiB, source pages to 1000 and each side's assertions to 10000.

Identical retries verify every retained file and return `unchanged` without rewriting its receipt.
They repeat file, bundle-directory and parent-directory `fsync` calls before returning success.
A changed or extra file, unsafe mode, symlink, hard-linked retained file or different output is refused.
The writer flushes every payload and a staged receipt, then syncs both the bundle directory and its parent before publishing `receipt.json` without replacement.
Missing, truncated or still-staged receipts leave incomplete artifacts that no command consumes or overwrites.
Two writers never merge their files; one owns the new directory, and the other refuses unfinished output or verifies a complete identical retry.
Commands do not wait for another writer to finish.
A published receipt marks structural completeness, not proof that the writer returned successfully.
An interruption or sync failure after publication can leave a complete, verifiable bundle with unconfirmed final durability.
Keep those bytes and use an identical retry to repeat the persistence checks; do not repair or overwrite an incomplete artifact.
For an incomplete artifact, record the reason and choose a new directory.
Successful persistence checks depend on the local filesystem and device honoring `fsync`; these tests do not simulate power loss or certify storage hardware.
The receipt's `created_at` and `tool_sha256` are unanchored metadata.
The operation checksum binds the bundle kind and file checksums, while the external submission checksum binds only the original submitted bytes.
Keep a separate copy or hash of the entire receipt if its original metadata needs an external anchor.
Checksums detect drift against the provided anchors, not a malicious owner who rewrites both artifacts and every external anchor.
This is not signed, tamper-proof or encrypted storage.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s extensions/WikiKGExtractor/tests/python -p 'test_development_annotation*.py' -v
```

Tests use synthetic sources and explicitly synthetic provenance.
They cover Unicode coordinates, source drift, immutable corrections, substituted predecessors, withheld metadata, ambiguous grouping, private paths, bounded errors and the maintained command.
Controlled two-process races and process exits exercise publication and receipt boundaries without timing-based sleeps.
Injected persistence failures cover both directory barriers and complete-but-unacknowledged publication.
The demonstration and tests also run in the isolated Linux Python environment with only the extension mounted read-only and a fresh private output directory.
Real comparison stops until a person supplies annotations and the research gates are confirmed.
