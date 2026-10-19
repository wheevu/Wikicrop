# Connected synthetic questions

This demonstration connects a disposable MediaWiki installation, a reviewed Neo4j projection and the existing structured-question interpreter.
It is a reviewer-only engineering demonstration using invented sources and simulated fixture decisions.
It does not call a model, the external `/ask` backend or `/ingest`.
It does not approve agricultural claims or establish scientific accuracy.
The original thesis brief remains binding.

## What is connected

```text
invented MediaWiki articles
  → frozen-worker output and separately identified direct claim-store fixtures
  → simulated fixture decisions
  → current eligible export
  → actual Neo4j generation
  → bounded graph read and existing query interpretation
  → fresh MediaWiki authorization
  → evidence and refusal reasons inside the synthetic wiki
```

MediaWiki remains authoritative for source revisions, visibility, permissions and append-only decisions.
The graph reader reconstructs claims from Neo4j and checks their generation, canonical payload and completeness against the selected export.
The export is a binding check, not a fallback data source when the graph is unavailable.
The interpreter's existing units, interval handling, qualifiers, conflicts and relationship rules are unchanged.
Neo4j access uses fixed parameterized reads rather than caller-supplied Cypher.
The existing claim and evidence limits remain in force.

The source pages, claim IDs, graph generation and current review versions can be inspected together.
A missing relationship is insufficient evidence, not proof that the corresponding agricultural statement is false.
An uncertain range is not replaced by a midpoint.
No generated prose is presented as a supported answer.

## Page and audience

`Special:WikiKGSyntheticQuestions` is disabled by default.
Enabling its config flag is insufficient on its own: the page also requires the guarded synthetic runtime, the exact disposable SQLite database and owner-private fixture files.
A registered user with the existing reviewer right must sign in.
The server takes the actor from the current request authority, never a posted actor name.
Questions come from a bounded server-owned catalog.
The browser cannot choose source paths, a graph server, arbitrary query JSON or Cypher.

Select an example and run it to inspect its eligible witnesses, diagnostic claims, original qualifiers and source-revision links.
Blocked and incomplete results remain visibly different from supported evidence.
The form and source links work without JavaScript.
The production chatbot configuration remains unchanged.

## Reproducible package

The maintained entry point is `extensions/WikiKGExtractor/demo/synthetic/questions/run.py`.
Its setup, start, serve, verify and stop actions require explicit synthetic-only acknowledgement.
Use a fresh owner-only root outside the checkout and document root, plus the existing pinned metadata manifest.
Never point it at a production database or a general private-data directory.

Setup reuses the existing synthetic wiki installation and frozen-worker workflow.
The additional direct claim-store fixtures have distinct provenance and are not attributed to the frozen extractor.
The declarative fixture defines 37 direct claims across 12 invented articles, separate from the frozen workflow's nine claims.
The combined 46 evidence entries fit the existing 50-entry authorization limit.
Their expected outcomes are written independently in `demo/synthetic/questions/scenarios.json`.
The package records source identities, graph identity, question outcomes and software versions without computing scientific scores.

Runtime networking is internal, with the wiki sharing the graph's network namespace.
Bolt remains loopback-only and no database port is published.
Browser access uses the owned host-loopback relay.
Passwords stay in private files or the deliberately selected graph process environment, never in command arguments or browser configuration.
Build preparation may need network access for pinned public dependencies.

When Docker's automatic subnet pools are exhausted, select a checked, non-overlapping private IPv4 `/24` at setup.
The maintained lifecycle persists that choice and reuses it.
It does not delete earlier networks or alter daemon settings.

Use the maintained commands rather than combining the site's base Compose file with this stack.
Set `WIKIKG_HTTP_PORT` before setup; the connected lifecycle persists that port for later actions.

```sh
umask 077
DEMO=extensions/WikiKGExtractor/demo/synthetic/questions/run.py
ROOT='<new absolute path under an owner-only parent>'
METADATA='<existing absolute path to the pinned corpus metadata>'
PLAYWRIGHT='<absolute path to @playwright/test/cli.js version 1.63.0>'
export WIKIKG_HTTP_PORT='<unused loopback port between 1024 and 65535>'

python3 -B "$DEMO" setup --private-root "$ROOT" \
  --corpus-manifest "$METADATA" --synthetic-only
python3 -B "$DEMO" start --private-root "$ROOT" --synthetic-only
python3 -B "$DEMO" serve --private-root "$ROOT" --synthetic-only
```

The relay stays in the foreground.
Use its recorded loopback URL to sign in as `Synthetic reviewer`, reading the password from `$ROOT/wiki/reviewer-password` without copying it into shell history.
Open `Special:WikiKGSyntheticQuestions` in that wiki.
For an explicit subnet, add `--subnet '<unused private IPv4 /24>'` to setup only after checking Docker networks and host routes.

From another terminal, verification runs the mutating cases and stops the owned wiki and graph.
Stop the foreground relay afterward with Ctrl-C.

```sh
python3 -B "$DEMO" verify --private-root "$ROOT" \
  --playwright-cli "$PLAYWRIGHT" --synthetic-only
# Require exit 0 AND status=passed and cleanup=passed in this installed file:
# "$ROOT/connected/final-report.json"

# To stop a demonstration without verification:
python3 -B "$DEMO" stop --private-root "$ROOT" --synthetic-only
```

Install the pinned browser runner and its browser before starting the isolated runtime.
The explicit CLI path lets this worktree reuse a compatible installation without downloading dependencies during verification.
Runtime databases, graph files, credentials, attempts and browser evidence remain outside the checkout and are not removed by stop.

## Verification scenarios

The static cases cover a supported duration/parentage/resistance conjunction, wrong-root joins, cross-crop equal names, unresolved parentage, an interval crossing the threshold, missing applicability, opposing claims and a missing required relationship.
State and dependency cases cover an incomplete binding, rejection while the old graph still contains a claim, a changed source revision, permission loss during a question and graph unavailability.
Each case must assert its expected status and relevant claim identities, not merely agree with another run of the interpreter.

Verification uses actual MediaWiki PHP, Neo4j and browser requests.
It must not substitute an allow-all authorizer, a canned backend reply, an injected page or a fulfilled application response.
Read routing in the Neo4j driver is not a database security role.
The implementation's fixed reads do not establish that the graph account lacks write privileges.

Some verification steps intentionally change synthetic review or source state.
They may invalidate the original bound export and make the page refuse later questions.
That is an expected safety result, not a reason to bypass the gate.
A later verification stage uses a new export and generation where required, without reactivating a retired export.
Use a new root for a clean demonstration after the mutating verification run.

Stop owned services without erasing their databases, graph volumes, exports or failed attempts.
Accept a completed run only when its exit status and installed result both indicate success.

## Scope limits

This package does not open held-out or alias article text, record real claim decisions, load real development claims into Neo4j, start Phase C or demonstrate the external assistant backend.
It does not implement ordinary-reader graph authorization, a free-form natural-language parser, production migrations or deployment.
Human approval and independently checked references remain prerequisites for agricultural and scientific claims.
The command's existing reviewer-only question contract is documented in the [offline question guide](knowledge-graph-offline-question.md).
Earlier lab checks remain separately dated in the [hardening guide](knowledge-graph-engineering-hardening.md).

## Execution record

Implementation and fresh-runtime verification are in progress.
No completed connected-demo runtime or independent approval is claimed here.
