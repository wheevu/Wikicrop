# WikiKGExtractor 2.4.0 review candidate

WikiKGExtractor collects MediaWiki crop pages and extracts candidate claims.
The frozen rules-only worker remains version `2.3.1`; `2.4.0` identifies the claim-review extension.

## Safe workflow

1. **Extract candidates.** The worker output is provisional, not reviewed evidence.
   Claims carry page, revision, content-hash, extraction-method, and available source-coordinate data.
2. **Review in WikiCrop.** `Special:WikiKGReview` records explicit decisions in append-only history.
   `Special:WikiKGReviewedGraph` displays only approved claims whose cited pages remain readable at those revisions.
   Approval is a recorded decision, not a finding of agricultural truth.
3. **Build an offline projection.** An isolated, opt-in Neo4j/tools package can hold a rebuildable reviewed projection.
   It does not replace MediaWiki review history or provide reader-facing authorization.
   Follow the [installation guide](../../docs/knowledge-graph-offline-installation.md) only for a disposable, separately approved environment.
4. **External assistant integration is incomplete.** The existing `/ask` and `/ingest` backend contracts and audience policy are unverified.
   The offline query and bounded runner have dated synthetic checks, not evidence for live-site behavior or assistant quality.

The [query contract](../../docs/knowledge-graph-query-contract.md) and [bounded runner](../../docs/knowledge-graph-bounded-runner.md) remain experimental.
Use the [synthetic verification package](../../docs/knowledge-graph-synthetic-verification.md) to reproduce the offline reviewer workflow; the [verification record](../../docs/knowledge-graph-release-verification.md) states what has actually passed and what remains due.
The [development annotation guide](../../docs/knowledge-graph-development-annotation.md#reproduce-the-synthetic-demonstration) has a separate, fixture-only command for source packs, immutable submissions and discrepancy checks.
The [remaining-phases note](../../docs/knowledge-graph-remaining-phases.md) records open gates.
No instructions here authorize production access, migration, deployment, real claim review, or held-out data access.

## Installation and state changes

Code installation alone does not create the review tables.
Schema updates, importing candidates, recording review decisions, and loading Neo4j each change state and require separate intent and approval.
Never run development commands against the production WikiCrop database.
The evidence-only question command is documented separately in the [offline question guide](../../docs/knowledge-graph-offline-question.md).

Private raw exports require an empty `WikiKGExtractorOutputBaseUrl`, a private destination outside MediaWiki and server document roots, directories set to `0700`, and files set to `0600`.
An explicitly configured nonempty `WikiKGExtractorOutputBaseUrl` retains legacy public-export behavior and is not private.
Private export fails closed on Windows because the writer does not verify Windows ACLs.
Do not weaken these restrictions or treat POSIX mode bits as proof of ACL safety.

## Current extension behavior

Candidate claims start as pending and are checked against source revision and content hashes when shown or reviewed.
The review UI checks current read permission and revision visibility; approve and reject actions require a reason and append to audit history.
Those decisions do not rewrite extraction files or write Neo4j.
The reviewed graph remains separate from the provisional extraction graph.

Read the [legacy extraction reference](../../docs/knowledge-graph-legacy-extraction.md) for the earlier Windows/XAMPP setup, Gemini option, direct Neo4j push, provisional graph examples, and historical local demo commands.
Those examples are not the default workflow and do not produce reviewed evidence.
