# Private structured questions for reviewers

Status on 2026-10-08: exercised against the disposable synthetic wiki and synthetic transport fixtures; independent review remains due.
The original no-testing implementation pass did not execute the command.
The subsequent authorized verification demonstrated three evidence-only questions per workflow run with two fresh bridge calls and no generation.
See the [dated verification record](knowledge-graph-remaining-phases.md#authorized-verification-2026-10-08), not earlier October counts.

`extensions/WikiKGExtractor/tools/offline_answer.py` connects the experimental query interpreter to the existing `authorizeOfflineEvidence.php` bridge.
It reads a private question and selected reviewed export, then writes evidence, citation identities, interpretation metadata, and blocking reasons to a new private result.
It never calls a language model or the external `/ask` or `/ingest` backend.
Approval records and traceable citations do not establish agricultural truth.

## Prerequisites and boundaries

- A compatible local MediaWiki runtime with the review extension and its tables already installed.
- PHP with the required MediaWiki extensions and POSIX support, and Python with the tools dependencies installed.
- Python support for `waitid`, `WNOWAIT`, and POSIX signal masking, required by the shared owned-process supervisor.
- An existing named reviewer with `wikikgextractor-review`; the command does not create an account or grant rights.
- A reviewed-projection v1 export and the unchanged pinned development metadata manifest.
- Absolute, non-symlink paths to owner-only directories and files, outside the checkout and actual document root.
- Directories require `0700`; request, export, and manifest files require `0600`.

The separate tools container contains Python, not the PHP wiki runtime.
Run this command where both runtimes and their private paths are available, using the same operating-system owner.
Use a compatible POSIX runtime, preferably the prepared Linux wiki environment.
A native Python runtime without the required non-reaping process APIs refuses before starting PHP; macOS support has not been established.
CPython documents macOS `waitid` availability from Python 3.13, but that API availability is not a demonstration of this workflow on macOS.
Windows ACL enforcement is not implemented.
The actual server document root must be supplied, not a guessed convenient directory.
Installation, migrations, candidate imports, review decisions, and graph loading remain separate actions with their own approval boundaries.
See the [installation guide](knowledge-graph-offline-installation.md).

Real-data authorization remains restricted to the protocol-pinned source page and permitted development pages.
`--synthetic-only` replaces `--corpus-manifest` only for a disposable synthetic wiki.
Neither flag permits held-out access or new parent pages.

## Private question format

The wrapper schema is `schema/offline-question.v1.schema.json`.
The nested `query` uses the [experimental reviewed-query contract](knowledge-graph-query-contract.md).
The command rejects unknown wrapper fields, duplicate JSON keys, non-finite numbers, and malformed requests at runtime.
There is no free-form query parser, Cypher input, or LLM routing.

For an evidence listing, a question file may contain:

```json
{
  "version": "wikikg.offline-question.v1",
  "query": {
    "intent": "lookup",
    "purpose": "evidence",
    "entities": [{"type": "Variety", "id": "OM5451", "crop_id": "Lúa"}]
  }
}
```

This names source labels and does not prove that a matching approved membership exists.
Identity must resolve inside the selected export and pass current authorization.
With `purpose: "answer"`, a requested season or condition must be established by the source scope.
Evidence listing retains original qualifiers; it is not an affirmative answer about an unspecified field or season.

Optional `bindings` refer to exact reviewed membership snapshots and source page identities, rather than alias strings alone.
Their shape is defined in the wrapper schema.
Optional `text_candidates` are page/revision/hash identities, never supplied source text.
The default evidence-only command does not use text to substitute for a blocked graph question.
No generator transport or text-fallback workflow is enabled here.

The brief's compound question can be expressed without connecting the wrong subjects:

```json
{
  "version": "wikikg.offline-question.v1",
  "query": {
    "intent": "conjunction",
    "purpose": "answer",
    "entities": [{"type": "Variety", "id": "OM5451", "crop_id": "Lúa"}],
    "filters": [{"predicate": "growth_duration", "op": "<", "value": "100 ngày"}],
    "branches": [
      {"path": ["HAS_PARENT"], "target": {"type": "Variety", "id": "Jasmine 85"}},
      {"path": ["RESISTANT_TO"], "target": {"type": "Pest", "id": "Bệnh đạo ôn"}}
    ]
  }
}
```

This illustrates one bounded candidate, not a result saying OM5451 qualifies.
A range of 95-105 days straddles the threshold.
A scalar pedigree string does not create a resolved parent edge.
Missing, uncertain, opposing, or unsupported evidence blocks a definitive answer.
Requests accept at most 20 roots, three branches, and three traversal steps per branch.
The query authorizes the entire selected export, with a maximum of 100 claim identities.
The PHP gate also limits that export to 50 evidence entries across its claims; this counts entries, not unique source pages.
Query responses have a separate 50-entry bound, counting claims repeated in different result groups again.
These bounds can refuse or truncate a request even when few roots were supplied.
Use a permitted bounded export and preserve its declared completeness; never relabel a partial selection as a complete full-site export.

## Command template

Use a new output filename for each question.
Use this template only in an explicitly authorized disposable or permitted offline environment.
The passing synthetic demonstration does not authorize real claim decisions or production use.

```bash
python3 extensions/WikiKGExtractor/tools/offline_answer.py \
    --manifest '<private directory>/reviewed.json' \
    --request '<private directory>/question.json' \
    --output '<private directory>/new-result.json' \
    --actor '<existing reviewer>' --offline-only --audience actor-only \
    --wiki-root '<absolute compatible MediaWiki root>' \
    --document-root '<absolute actual server document root>' \
    --corpus-manifest '<private directory>/pinned-manifest.json' \
    --php '<PHP executable>' --timeout 60 \
    --wiki-base 'http://127.0.0.1:8088' --wiki-script-path '/w/index.php'
```

`MW_INSTALL_PATH` and `DOCUMENT_ROOT` are set only for the child PHP process.
No shell interpolates request data or actor names.
Citation URLs must point to an HTTP loopback wiki with a validated `index.php` path.
They are navigation links, not requests made by this command.

## Result and failure behavior

The private result identifies `wikikg.reviewed-answer.v1` and preserves the full experimental query decision.
It contains `status`, `query`, `answer`, `citations`, and `reasons`.
The default `answer` is null; reviewer diagnostics must not be presented as generated agricultural prose.
An authorization failure removes query evidence and citations from the result.
The terminal prints only a bounded JSON summary with status, reason codes, whether the result was written, and authorization-call count.
It does not print question text, claim text, paths, actor names, source spans, or child logs.

Operational errors have stable categories:

| Category | Meaning and next step |
| --- | --- |
| `runtime_missing`, `runtime_failure` | Install or repair the local PHP/wiki/Python runtime before retrying with a new result path |
| `authorization_refused` | Confirm the named reviewer's current permission and permitted source scope |
| `source_revision_changed`, `source_content_changed` | Recollect and review the intended permitted source version; never transplant old revision IDs |
| `claim_review_changed`, `source_approval_changed` | Inspect the current review or source-approval state before making a new export |
| `export_watermark_changed` | Create a fresh reviewed export; do not use cached approval |
| `authorization_timeout` | Inspect the retained private attempt, then choose an explicit new invocation; no automatic retry |
| `malformed_authorization_response` | Repair the bridge/runtime response; do not bypass authorization |
| `authorization_output_limit`, `result_output_limit` | Reduce the bounded request or export; do not silently truncate evidence into an answer |
| `unsafe_private_path`, `private_write_failed` | Choose secure private storage and a new output filename |

Exit 0 denotes a complete evidence interpretation within the selected manifest, not scientific correctness.
Exit 3 denotes a blocked or incomplete interpretation; exit 2 denotes an input or operational failure.
Interruption returns 130 when handled by the command.
Unknown arguments fail without an interactive prompt or an echo of their values.

## Bounds and retained attempts

Question and authorization identity requests are bounded to 64 KiB.
The selected export is bounded to 8,000,000 bytes, each authority response or log to 1 MiB, and the result to 2,000,000 bytes.
At most eight fresh bridge calls share a 12,000,000-byte authorization-work budget and the configured deadline, default 60 seconds and maximum 900.
The child inherits a hard per-file output limit and runs in a new process group.
The command observes child exit without reaping it, cleans up that group, and only then releases the child's PID.
It also removes surviving group members after the direct child exits normally.
On a handled timeout or interruption, the caller terminates only its own process group and reaps its child.
Handled SIGTERM follows the same interruption route as SIGINT.
The [CPython 3.13 `waitid` documentation](https://docs.python.org/3.13/library/os.html#os.waitid) defines the non-reaping `WNOWAIT` behavior used here.
These bounds are not a demonstrated aggregate memory guarantee; the extraction runner has a separate [Linux enforcement contract](knowledge-graph-bounded-runner.md).

Each invocation creates private `question-auth-*` directories, including identity requests, bridge responses, and logs.
They remain private diagnostics, not reusable authorization.
Owner-only staging files remain recoverable; the result is installed without overwriting an existing path.
Attempts are not automatically retried or deleted.

## Verification and remaining checks

The 2026-10-08 verification ran successful and blocked synthetic questions through the PHP bridge and seven synthetic CLI transport tests.
Those fixtures covered malformed responses, changed canonical evidence, timeout, and private paths.
The 2026-10-09 continuation targets real process-bridge rejection, permission loss, source changes, and final-authorization denial.
Output-bound and broader interruption cases must remain explicit where a current passing result is absent.
Independent review is due after the whole deliverable is stable and self-verified.
Until then, this is an implementation handoff, not approval to use or deploy it.
