# Development source annotation rubric

Status: proposed working instructions, not an approved scientific protocol.
Use the pinned development pages only.
This task records what WikiCrop says and how it is represented, not whether its agricultural advice is correct.

## Read sources before machine output

Read every supplied page, including statements that the current vocabulary cannot represent.
Do not consult candidate claims, extraction rules, workbook answers, agent answers, or machine-selected passages before retaining your first submission.
Record earlier exposure honestly, including exposure that happened before this task.
`source_only_reported` is your report of exposure, not a verified claim of independence.
Leave `unknown` when you cannot establish it.

The pack's vocabulary comes from the frozen pilot and is provisional for this practice workflow.
It does not adopt the proposed expanded rice ontology.
`Crop` is a crop-topic concept, not automatically one taxon.
`Variety` and `Pest` are the pilot's local labels, not independently established scientific classifications.
Keep identity unresolved when the source does not establish it.
Do not assign parent roles from token order, or infer crop-level damage from variety-level resistance.

## Write one source assertion per annotation

Use your own annotation ID, never a machine claim ID.
Keep the subject and target literals exactly as you interpret them from the source.
Choose a pilot entity type only when justified, otherwise use `unknown` and `unresolved`.
Choose a predicate from `pack.json`, or mark `outside_scope: true` and explain the missing representation.
Keep both endpoints of a range, its unit, and all material conditions.
Do not replace a range with a midpoint, convert units, remove Vietnamese accents, or silently equate aliases.
For a scalar, `value_literal` contains the full value expression including any unit.
Record the unit separately too.
For a relationship, use `object` instead of `value_literal`.

Record every pilot qualifier key listed in `pack.json`.
Each unit or qualifier has `state` and `literal`:

- `stated`: the source states it, keep its wording in `literal`.
- `not_stated`: the source does not state it, use `literal: null`.
- `uncertain`: you cannot interpret it, keep available wording or `null` and explain why.

Additional qualifier keys can preserve stage or other conditions outside the pilot's vocabulary.
Do not drop them to make a machine comparison look better.
An assertion outside the vocabulary belongs in the gap record, not a false-negative count.
Use `judgment: uncertain` or `abstain` when necessary and give a reason.
Unresolved identities, uncertain units or qualifiers, and outside-scope entries also need a reason.

## Keep evidence exact

Use the complete UTF-8 wikitext in `source-<page_id>.txt`.
Offsets are zero-based Unicode codepoints in `[start, end)`, not bytes or UTF-16 code units.
Do not change line endings, normalize Unicode, or use a rendered page as the coordinate source.
The quote and SHA-256 must agree with that exact slice.
The `locate` command can create an evidence record from your own exact quote file.
It refuses to choose between repeated occurrences without an explicit start offset.
This is a coordinate check only, not confirmation that the passage supports your assertion.

## Submit and revise

Copy `submission-template.json` into a separate working file.
Fill in your ID, submission ID, timestamp and exposure; no person or approval is prefilled.
Update each page review to `partial` or `complete` as you read it.
A complete page with no annotations is an explicit report that you found no in-scope assertions, not proof of recall.
Keep unstarted and partly read pages visible.
Run `validate`, then `retain` before viewing machine output.
The tool preserves the original submitted bytes, adds a checksum receipt, and never labels the file independent, adjudicated, expert-approved, or gold.

Corrections use the same submission ID, the next version, and the previous submission checksum.
Retain each correction in a new directory and keep the earlier submission.
If two people annotate, retain their initial files separately.
Disagreements require a human record of both views, the cited passage, and the adjudicator's reason.
The discrepancy tool supplies an empty decision column; it never decides a disagreement or changes review state.

## Interpret the comparison

A possible match requires the same pinned source and an exactly equal resolved subject type, subject literal, and predicate.
There is no alias resolver or semantic equivalence engine.
Multiple annotations or machine claims in that group remain ambiguous, even if one pairing seems convenient.
Every item appears in one accounting group.
Outside-scope annotations and unresolved judgments stay separate.

For a unique possible pair, inspect value or target, unit, qualifiers and evidence separately.
An exact literal or coordinate is not a correctness judgment.
A qualifier difference does not establish contradiction or disjoint scope.
Unmatched items can indicate missing annotations, missing extraction, name differences or incomplete page reading.
Resolve those possibilities before assigning any error label.
No precision, recall, F1, agreement, accuracy, coverage estimate or agricultural approval comes from this workflow.
