# Knowledge-graph thesis requirements check

Status: 2026-09-24, work on `feat/wikikg-claim-review` after the `7dd1c982` claim-review commit.
Acceptance baseline: the supervisor's original 2026-2027 WikiCrop knowledge-graph topic brief, work items i-v, plus the 2026-08-05 email requiring WikiCrop data and display inside WikiCrop.
The private originals live in `~/Study/Thesis/supervisor-materials/`.
Later pilot and thesis plans set an order of work; they do not remove an original requirement without a confirmed scope change.

| Original work item | Status in this worktree | Still needed to meet the brief |
| --- | --- | --- |
| Agricultural ontology: entities, scalar properties, lineage and pest/disease relationships | Partial: the rules-only pilot models Crop, Variety, and Pest, with scalar claims and four graph edge types. | Check the ontology against representative WikiCrop sources; model evidence-backed lineage as traversable relationships and assess other brief examples. |
| Extract entities, properties, and relations from WikiCrop text using NLP or an LLM | Partial: deterministic rules extract a bounded set of candidates; an optional Gemini path exists but has not been validated as the thesis method. | Evaluate extraction from unstructured text against an independent reference set, including misses, conflicts, and qualifiers. |
| Choose, configure, load, and inspect a graph database | Partial: the legacy worker emits Cypher and offers a direct candidate-graph push, but review decisions do not drive a verified database projection. | Build and verify a repeatable graph-database load from reviewed claims; test source identity, changes, duplicates, and orphan nodes. |
| Integrate GraphRAG with WikiCrop's existing assistant | Not demonstrated in this worktree; `WikiChatbot` calls an external `/ask` endpoint whose backend has not been verified here. | Confirm the backend contract, implement graph retrieval and evidence-backed assistant answers, and demonstrate cross-reference questions. |
| Evaluate cross-reference queries and assistant quality/performance before and after integration | Partial: the frozen rules-only pilot demonstrates reproducibility and traceability, not scientific accuracy. | Prepare independent annotations and question answers; compare the same questions before and after graph integration, with accuracy, evidence, abstention, and runtime recorded. |
| Document and package WikiCrop deployment | Partial: there is a synthetic local demo and extension guidance, but no approved production deployment package. | Document installation, migrations, configuration, rebuild, rollback, versioned results, and privacy boundaries; validate on an isolated production-like setup before requesting deployment approval. |
| Use WikiCrop data and display the graph inside WikiCrop (supervisor email) | Demonstrated for a provisional candidate graph; this slice adds a sysop-only, page-sized view of currently readable approved claims. | Check the final, reviewed graph and its source links against the supervisor's display expectation. |

The companion SQLite change repairs an interrupted review-table installation.
This is local hardening, not a substitute for the required graph database.
The frozen worker, evaluator, and held-out split remain untouched; held-out evaluation still requires explicit approval.
