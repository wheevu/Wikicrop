# Synthetic WikiKG verification

This package builds a disposable MediaWiki 1.43.1 runtime and drives the existing bounded extraction, review, export, and offline-question paths against synthetic fixtures.
It is software verification only, not a scientific or production evaluation.

## Requirements

Use Docker with Compose v2, Python 3.10 or newer, and Node/npm on the host.
Run from a regular, non-root POSIX user account; Windows ownership/ACL compatibility is not verified.
The first `setup` builds an image and therefore needs network access for the digest-pinned PHP, Python, and Composer images, Debian build packages, the locked MediaWiki dependencies, and the official MediaWiki archive.
`demo/synthetic/mediawiki-composer.lock` retains the generated dependency resolution; the build installs it rather than updating packages.
These older runtime versions are for this isolated test package, not a production recommendation.
Debian build packages are not individually locked, so this is a pinned application/dependency installation, not a promise of byte-identical image builds.
The archive is downloaded from Wikimedia over HTTPS and checked against SHA-256 `3c85aa9c4cd6c353c67804a3a58e7b79615d4141c3d66b101e5f3aeb40653d6c`.
This is a content integrity pin, not a claim of signature verification.
The container's runtime network is an internal Docker network with no external route.
The extension is mounted read-only.
SQLite files and question/browser evidence stay in the host's private root; run receipts and test scratch use the owned `receipt-state` Linux volume at `/private/wikikg/posix`.
This avoids Docker Desktop host-file-sharing locks, which the runner refuses as `receipt_filesystem_unsupported`.

Prepare the already-pinned corpus metadata file in an existing private input directory.
The directory and manifest must be owned by the current user; directory mode must be `0700` and manifest mode `0600`.
The input must be the exact manifest accepted by `bounded_discovery.py`'s existing frozen-hash check.
Do not place held-out page text, credentials, or real-site exports in the checkout.

Run from the repository root.
Replace the metadata placeholder with its existing absolute path; do not substitute held-out text or rebuild the corpus.

```sh
umask 077
mkdir -p "$HOME/.cache"
export WORK="$(mktemp -d "$HOME/.cache/wikikg-verification.XXXXXXXX")"
export PRIVATE_ROOT="$WORK/wiki"
export WIKIKG_HTTP_PORT=8787
CORPUS_MANIFEST='<existing absolute path to the pinned metadata file>'
PACKAGE=extensions/WikiKGExtractor/demo/synthetic
python3 -B "$PACKAGE/run.py" setup \
  --private-root "$PRIVATE_ROOT" --corpus-manifest "$CORPUS_MANIFEST" --synthetic-only
```

Setup refuses an existing root.
It creates separate `syntheticwiki` and `wikitest` SQLite databases, a random synthetic reviewer password file, generated private settings, and fixture identities.
It does not reuse or reset an existing database.
The package's configured MediaWiki admin name is `Synthetic reviewer`; no password is printed or put in a process argument.

## Run the unchanged fixtures first

```sh
python3 -B "$PACKAGE/run.py" start --private-root "$PRIVATE_ROOT" --synthetic-only
python3 -B "$PACKAGE/run.py" workflow --private-root "$PRIVATE_ROOT" --synthetic-only
python3 -B "$PACKAGE/run.py" test --private-root "$PRIVATE_ROOT" --synthetic-only
python3 -B -m unittest discover -s extensions/WikiKGExtractor/tests/python -p test_reviewed_packaging.py
```

Keep the same `WIKIKG_HTTP_PORT` for setup, startup, relay, and checks.
Change it before setup if the default loopback port is already occupied.
The workflow retains `workflow-results.json`, both reviewed exports, and each question request/result under the private root.
Receipts and extraction artifacts remain under `/private/wikikg/posix/runs/run-1` and `run-2` in the Linux volume.
The `test` action runs the PHP suite against the separate test database, followed by the Python suite with native, executable private scratch storage.
The workflow runs bounded discovery twice, imports synthetic paired outputs as pending claims, approves the fixture claims, exports the reviewed projection, and answers three structured questions per run without generation.
The 95-105 day range is expected to be blocked for a `<100 days` filter as numerically uncertain.

The fixtures use invented crop, variety, and disease statements.
They test application behavior only; they do not support agricultural advice.
The original Main Page remains as a synthetic wiki's default page, so inventory includes eight pages alongside seven seeded fixture pages.

Workflow and verification outputs are write-once.
Use a new setup root for another complete run, not a reset of the existing databases.

## Start the loopback browser relay

In a second terminal at the repository root, set `PRIVATE_ROOT` to the same host directory and `WIKIKG_HTTP_PORT` to the same port.
Then run the relay in the foreground:

```sh
python3 -B extensions/WikiKGExtractor/demo/synthetic/run.py serve \
  --private-root "$PRIVATE_ROOT" --synthetic-only
```

The relay binds only `127.0.0.1` and verifies the owned Compose project, private mount, settings, read-only root filesystem, and internal network before serving requests.
It forwards bounded `/w/` HTTP requests through `docker exec` into that one container, without adding container egress or a published Docker port.
It prints its PID and URL and retains a content-free `relay-<pid>.json` ownership record.
URLs, passwords, and cookies do not enter its request log.
This disposable relay is not a production web server.

In the original terminal, confirm startup before the browser checks:

```sh
curl --noproxy '*' --fail --max-time 30 -o /dev/null \
  "http://127.0.0.1:$WIKIKG_HTTP_PORT/w/index.php?title=Special:UserLogin"
```

## Verify the initial graph and browser journey

Both checks must run before the source-edit and rejection cases below.
Use a new owned graph project and volume, not a production graph or an existing evaluation database.
The [offline installation guide](knowledge-graph-offline-installation.md) explains the separate graph/tools package and its pins.

Create its owner-only environment file without printing the password:

```sh
export GRAPH_ENV="$WORK/graph.env"
GRAPH_COMPOSE=extensions/WikiKGExtractor/demo/docker-compose.reviewed-offline.yml
python3 -B - <<'PY'
import json, os, pathlib, secrets
values = {
    'WIKIKG_PRIVATE_ROOT': os.environ['PRIVATE_ROOT'],
    'WIKIKG_NEO4J_PASSWORD': secrets.token_hex(32),
    'WIKIKG_UID': str(os.getuid()), 'WIKIKG_GID': str(os.getgid()),
    'WIKIKG_BOLT_PORT': '7689',
    'COMPOSE_PROJECT_NAME': 'wikikg-graph-synthetic-' + secrets.token_hex(8),
}
with pathlib.Path(os.environ['GRAPH_ENV']).open('x') as output:
    output.write(''.join(key + '=' + json.dumps(value) + '\n' for key, value in values.items()))
PY
graph_compose() {
  env -i PATH="$PATH" HOME="$HOME" DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}" \
    docker compose --env-file "$GRAPH_ENV" -f "$GRAPH_COMPOSE" --profile reviewed-offline "$@"
}
graph_compose build tools
graph_compose up -d --wait graph
graph_compose run --no-deps -e WIKIKG_GRAPH_SYNTHETIC=1 tools \
  extensions/WikiKGExtractor/demo/synthetic/verify_graph.py \
  --phase initial --private-root /private/wikikg --synthetic-only
```

The verifier requires an empty graph, proves that invalid credentials fail, loads all nine fixture claims, repeats the load, and checks each exposed claim/evidence identity through the existing lookup API.
Its evidence is `$PRIVATE_ROOT/graph-initial-results.json`.
The tools use container-local Bolt access; a host-side Bolt connection is not part of this check.

Run the maintained browser suite against the unchanged nine-claim export:

```sh
(
  cd extensions/WikiKGExtractor/tests/browser
  npm ci
  npx playwright install chromium --only-shell
  WIKIKG_SYNTHETIC_URL="http://127.0.0.1:$WIKIKG_HTTP_PORT/" \
  WIKIKG_SYNTHETIC_PASSWORD_FILE="$PRIVATE_ROOT/reviewer-password" \
  WIKIKG_SYNTHETIC_IDENTITIES_FILE="$PRIVATE_ROOT/seed-identities.json" \
  WIKIKG_SYNTHETIC_MANIFEST_FILE="$PRIVATE_ROOT/evidence-2/reviewed.json" \
  WIKIKG_SYNTHETIC_BROWSER_ROOT="$WORK" npm test
)
```

The journey uses a real pointer click on the rendered Swift edge, then opens its claim, review form, and exact seed revision.
The other tests exercise keyboard/no-JavaScript navigation and signed-out refusal.
Reports, screenshots, and failure evidence stay in new private `browser-run-*` directories.
Playwright also loads the configuration in its worker, so the custom screenshot can be in a separate directory beside the JSON report's directory.
No test changes review state.

## Exercise real authorization refusal, then rebuild

This step intentionally changes only the guarded synthetic wiki.
It temporarily removes read/review permission, restores it, changes the exact seeded `Other crop Later` revision, and rejects one version-bound Swift property snapshot.
Do not run the happy-path/browser tests again on this modified fixture state.

```sh
WIKI_PROJECT="$(cat "$PRIVATE_ROOT/.compose-project")"
docker exec --user "$(id -u):$(id -g)" "$WIKI_PROJECT-wiki-1" \
  python3 -B /var/www/html/w/extensions/WikiKGExtractor/demo/synthetic/verify_authorization.py \
  --wiki-root /var/www/html/w --private-root /private/wikikg \
  --document-root /var/www/html --synthetic-only
graph_compose run --no-deps -e WIKIKG_GRAPH_SYNTHETIC=1 tools \
  extensions/WikiKGExtractor/demo/synthetic/verify_graph.py \
  --phase rebuilt --private-root /private/wikikg --synthetic-only
```

`authorization-results.json` records six cases and their fresh PHP-call counts.
Permission loss, the stale source export, and the export made stale by rejection must suppress query payload, answer, and citations.
The fresh export after a source edit retains authorized surviving evidence but reports `partial_manifest`, so it cannot claim completeness or generate an answer.
`snapshot_count` counts all nine stored snapshots; eligible claims fall from nine to seven after the source edit and to six after rejection.
The verifiers check those distinct counts rather than treating excluded snapshots as deleted.

`graph-rebuilt-results.json` records activation of the six-claim projection, exact identity lookup, absence of the three removed claims from active lookup, and refusal to reactivate the retired original export.
Neo4j remains export-time retrieval; only the PHP bridge checks current MediaWiki authority.

Case logs, later exports, question results, databases, and credentials stay under the private root.
An early permission-only failure may be retried with a new `--case-directory authorization-cases-<suffix>` if no source edit or later export occurred.
After any source edit or rejection, use a fresh setup root for another full verification.

## Stop and keep evidence

Stop only the owned wiki container when finished.
Evidence, databases, and the receipt volume are kept:

```sh
graph_compose stop graph
python3 -B "$PACKAGE/run.py" stop --private-root "$PRIVATE_ROOT" --synthetic-only
```

There is intentionally no cleanup command.
Stop the relay with Ctrl+C in its terminal, or send SIGTERM only after confirming that its recorded PID still runs this `serve` command and private root.
Retain or remove the exact private directory and owned volume yourself only after reviewing the evidence.

Run the package's targeted host-side guard tests from the repository root:

```sh
python3 -B -m unittest discover -s extensions/WikiKGExtractor/tests/python -p test_synthetic_package.py
```
