# Thesis Phase 1 boundary fixes

This unit repairs the existing extraction and assistant paths before real-data graph integration.
It does not complete GraphRAG or establish scientific accuracy.

## Thesis scope

The acceptance baseline remains the original WikiCrop knowledge-graph brief, summarized in [the requirements check](knowledge-graph-requirements-check.md).
These changes support its existing work items rather than adding a site-administration project.

| Change | Thesis requirement supported | Limit |
| --- | --- | --- |
| Text-only assistant rendering and bounded request completion | Integrate the graph with the existing assistant, item iv | No new backend, retrieval, citation, or audience contract |
| Private raw extraction files | Extract WikiCrop source data and package the implementation, items ii and v | Existing explicit public-export mode remains opt-in |
| Combined collection budget and resolved-page deduplication | Extract revision-traceable WikiCrop source data, item ii | No full-site collector or entity-model migration |
| Draft 2020-12 schema-test oracle | Load validated graph data and verify the implementation, items iii and v | Projection implementation and interchange schema stay unchanged |

## Preserved boundaries

- The frozen worker, evaluator, protocol files, and pinned pilot schemas do not change.
- Held-out pages and aliases remain sealed.
- Existing review decisions, approval rights, page permissions, and ingestion behavior do not change.
- The pre-existing reviewed-projection implementation remains a separate uncommitted candidate.
- Synthetic tests exercise engineering behavior only.
- No site administration, production migration, commit, push, or deployment is part of this unit.

## Verification

Verification on 2026-10-03 used synthetic fixtures, PHP 8.3.14, SQLite, Python 3.11.8, `jsonschema==4.26.0`, and Playwright Test 1.63.0 with installed Google Chrome.
The PHP runtime had no network access or published ports and did not use the working wiki's configuration or database.

| Check | Result |
| --- | --- |
| Full WikiKGExtractor PHP unit and integration suites | 142 tests and 813 assertions passed |
| Full WikiKGExtractor Python suite, from the actual Git worktree | 154 passed, 3 disposable-Neo4j tests skipped |
| Chatbot browser suite, retries disabled | 8 passed, including repeated clean runs |
| PHP syntax lint | 30 files passed |
| JavaScript syntax and diff whitespace checks | Passed |
| Frozen pilot hashes and pre-existing non-test candidate files | Preserved |

The cap regression failed against the original collector with three collected pages under a two-page limit, then passed with the repair.
The original chatbot and export writer also failed their new regressions in isolated copies.
A separate synthetic probe confirmed that an existing directory owned by another user is rejected before payload writes.
The Python source-archive run initially failed two repository-root checks because the archive lacked `.git` metadata; the unchanged full suite passed from the actual worktree.

From a disposable MediaWiki installation, run the PHP and Python checks with:

```bash
php tests/phpunit/phpunit.php extensions/WikiKGExtractor/tests/phpunit
python3 -m unittest discover -s extensions/WikiKGExtractor/tests/python
php vendor/bin/parallel-lint extensions/WikiKGExtractor/src \
    extensions/WikiKGExtractor/tests/phpunit extensions/WikiChatbot/includes
```

The PHP checks require a disposable MediaWiki database with WikiKGExtractor loaded, plus Python and the unchanged `tools/requirements-reviewed-projection.txt` dependencies.
Never run these checks against the production wiki.
The schema oracle uses exit code `0` for a valid document, `1` for a schema violation, and `2` for an input, configuration, or dependency error.
A broken oracle must fail the tests, including tests of invalid documents.

The browser suite loads the production chatbot JavaScript and CSS into synthetic hook-equivalent markup.
It checks what a user sees after sending a question, including literal HTML, failed responses, timeout, and overlapping requests.
It never calls the external assistant backend.

To run it without adding dependencies to the MediaWiki checkout:

```bash
RUNNER="$(mktemp -d)"
npm install --prefix "$RUNNER" --no-audit --no-fund @playwright/test@1.63.0
"$RUNNER/node_modules/.bin/playwright" install chromium
NODE_PATH="$RUNNER/node_modules" \
    "$RUNNER/node_modules/.bin/playwright" test \
    --config=extensions/WikiChatbot/tests/playwright.config.cjs
```

Set `WIKICROP_BROWSER_CHANNEL=chrome` to use an already installed Google Chrome instead of the downloaded Chromium.
`WIKICROP_BROWSER_OUTPUT_DIR` selects the failure-artifact directory; otherwise the config creates a temporary directory.

## Limits

The external `/ask` and `/ingest` services were not tested or changed.
Three live Neo4j tests require an explicitly selected disposable database and were not run for this unit.
Filesystem tests establish POSIX mode and owner checks, not custom ACL behavior or safety on nonstandard mounts.
The collector retains its per-source requested-link discovery bound; this unit does not establish full-site coverage.
The reviewed-projection candidate still needs its own completion review before it is used outside the offline experiment.
