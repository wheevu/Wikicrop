# Experimental reviewed-query contract

Status: experimental implementation draft, verified on synthetic fixtures on 2026-10-08. This is not ontology approval, a schema freeze, scientific validation, or deployment authorization.

`reviewed-query.v2.schema.json` defines an experimental request contract over the immutable reviewed-projection v1 manifest.
The query engine reauthorizes the exact canonical claim payload before and after interpretation.
It does not modify claims or provide unrestricted graph queries.

Legacy `lookup`, `path`, and `filter` requests remain accepted.
`purpose` defaults to `evidence`; `purpose: "answer"` requires scope support and complete interpretation.
Evidence-purpose lookup lists claims with their source qualifiers intact.
Answer-purpose lookup treats an unresolved, unknown, or invalid-transition relationship from the scoped root, and a relationship whose target Variety has no unique same-crop membership, as diagnostic `unresolved_identity` and blocks generation.
Evidence-purpose lookup still lists those claims.
Unsupported and uncertain evidence remains diagnostic and cannot become an affirmative answer or complete negative.

Example structured compound request:

```json
{
  "intent": "conjunction",
  "purpose": "answer",
  "entities": [{"type": "Variety", "id": "<OM5451 candidate identity>", "crop_id": "Lúa"}],
  "filters": [{"predicate": "growth_duration", "op": "<", "value": "100 days"}],
  "branches": [
    {"path": ["HAS_PARENT"], "target": {"type": "Variety", "id": "<Jasmine 85 source label; unresolved>"}},
    {"path": ["RESISTANT_TO"], "target": {"type": "Pest", "id": "<blast source label>"}}
  ]
}
```

The angle-bracket values are placeholders/source labels, not canonical identifiers.
This example represents only the question: OM5451 as a bounded candidate root, crop label `Lúa`, parent label Jasmine 85, blast resistance, and duration below 100 days.
No qualifying result is established or proven.
Jasmine 85 is not thereby resolved as an identity, parent, cultivar, or approved ontology class.
Branches resolve from the same crop-scoped root; matching display names or arbitrary page IDs do not form joins.
Each required branch must have at least one eligible path from that same root.
The result retains a bounded union of every eligible witness claim ID per branch, with no Cartesian combinations or preferred-assertion selection.
Diagnostics from other visited states remain available.
If any restriction or branch is unresolved, the result is incomplete and generation is blocked.
Missing reviewed relationships mean insufficient evidence, not scientific falsity.

Numeric comparisons use `Decimal`, supported local units and dimensions, decimal points or decimal commas, and the range separators `-`, `–`, `to`, and `đến`.
Ambiguous comma thousands grouping is rejected.
For a strict `< 100 days` test, `95-98 days` is true, `105-110 days` false, and `95-105 days` uncertain.
Original claim values and qualifiers remain unchanged; ranges are never replaced with midpoints.

Qualifier policy `wikikg.qualifier-scope.v1` has no approved disjointness rules.
Inequality is not exclusion.
Known four-season lists can establish coverage or intersection; free-text conditions only match exact nonempty literals.
Blank or missing requested scope never establishes applicability for a scoped assertion.
Original qualifier literals and numeric source values remain intact.
Scope uncertainty, opposing assertions, scalar alternatives, unresolved identity, missing relationship, missing property, confident numeric rejection, numeric uncertainty or unsupported units, incomplete manifest, limits, authorization failure, and invalid request are distinct diagnostic categories.
Failures must not disclose evidence after authorization failure.

`answer_query(document, request, graph_authorizer, text_authorizer=None, generator=None, wiki_base="http://127.0.0.1", *, bindings=None, text_candidates=None, context_budget=20000, wiki_script_path="/w/index.php")` runs the query itself and returns `version`, `offline_only`, `status`, full `query`, `answer`, `citations`, `reasons`, and `text_behavior`.
With no generator it is evidence-only; text is explicitly disabled as a fallback for unsupported graph questions.
Generation is limited to `purpose: "answer"` with status `ok`, complete true, partial false, and an empty reason list.
This gate also blocks a mixed candidate result if another candidate contributes a confident numeric-rejection reason.
That conservative behavior still needs coverage and a later policy decision; it does not establish a global negative.
Composition retains its independent exact-authorizer checks, conflict guard, budgets, and loopback-only revision URL policy.
No generator transport is implemented.

The orchestration freezes the request, bindings, and supplied text identities before authorization callbacks.
Interpretation and composition use the same question even if a callback mutates caller-owned data.
Each result group records `eligible_claim_ids`; optional `diagnostic_claim_ids` and `context_claim_ids` distinguish rejected or unresolved assertions from display-only properties.
Only `eligible_claim_ids` can enter composition, with its own conflict and fresh-authorization checks still required.
The same canonical claim can support an eligible witness and also occur in a conclusive nonmatching path; those roles do not rewrite the claim payload.
A conclusive dead end or another known target does not negate an existential witness that reaches the requested target.
Unknown scope, unresolved identity, opposing assertions, missing required branches, and query limits still block completeness.
An empty branch witness list remains empty rather than reporting root-membership claims as proof of a missing relationship.

Reason codes are stable categories, not quotations of user input: `invalid_request`, `unresolved_identity`, `relationship_missing`, `property_missing`, `scope_unknown`, `opposing_assertions`, `scalar_alternatives`, `numeric_not_satisfied`, `numeric_uncertain`, `numeric_unsupported`, `empty_result`, `partial_manifest`, `limits`, `authorizer_missing`, `text_authorizer_missing`, and `authorization_failed`.
`status: "empty"` is a scoped evidence result, not a global negative, and cannot trigger generation.
Authorization failure returns no query evidence or citations.
On an authorized but blocked result, bounded query diagnostics remain available with `diagnostic_claim_ids`; `branch_claim_ids` identify only eligible path witnesses.

The legacy-test reconciliation on 2026-10-08 updated stale expectations to this contract: response semantic version is v2, responses expose scope-policy metadata and diagnostic reasons, comma decimals and tri-state interval outcomes are applied, and conjunction requests plus branch claim identities are covered.
The synthetic regression suite for this contract passes; the commands and results are in `knowledge-graph-remaining-phases.md`.
