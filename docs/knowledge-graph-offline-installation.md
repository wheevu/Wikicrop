# Isolated reviewed-graph installation

This package runs the offline graph and Python tools without changing the site's base Compose file or routing the existing assistant to a new backend.
It is opt-in and separate from ordinary WikiCrop installation.
It does not install MediaWiki, grant permissions on a live wiki, or authorize deployment.
For the separate disposable MediaWiki-plus-Neo4j question demonstration, use the [connected synthetic guide](knowledge-graph-connected-demo.md).
The MediaWiki claim and review tables remain authoritative; Neo4j can be rebuilt from a new eligible export.
Installation does not authorize schema migration, candidate import, claim decisions, or graph loading.
Each is a separate state-changing operation that requires explicit intent and an authorized disposable environment.

## Boundaries and versions

The standalone configuration is `extensions/WikiKGExtractor/demo/docker-compose.reviewed-offline.yml`.
Both services require the `reviewed-offline` profile.
The configuration requests a loopback-only Bolt binding and requires a password.
It does not publish the HTTP browser port.
An internal Docker network blocks outgoing runtime network access.
The tools share the graph's network namespace so the existing loopback-only loader can reach `bolt://127.0.0.1:7687`.
Use the packaged tools commands below rather than a host-side Bolt client.
On the verified Docker Desktop runtime, the internal network did not publish the requested host Bolt port; container-local authenticated commands worked.
The tools mount only the extension source, read-only, plus an explicitly selected private directory.
Neither service mounts the site's `LocalSettings.php` or `.env`.
The Python tools image does not include PHP.
The offline question command therefore needs a compatible local MediaWiki PHP runtime, an existing reviewer account, and a fresh response from `authorizeOfflineEvidence.php`.
It must not substitute an allow-all authorizer or a cached authorization response.
The default question route returns evidence and interpretation only; it makes no model HTTP request.
See the separate [offline question guide](knowledge-graph-offline-question.md) for its current contract and dated synthetic evidence.
That evidence does not establish production compatibility or ordinary-reader access.

| Component | Pin |
| --- | --- |
| Neo4j Community | `2026.09.0`, image digest `29efb5ebfb51ec75545a945bc3ac0bdfbeb6bc2686a8ae5053fe0f4dcc926441` |
| Python tools image | `3.13.16-slim-bookworm`, image digest `5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed` |
| Python graph driver | `neo4j==6.3.1` |
| Schema validator | `jsonschema==4.26.0` |
| Transitive dependencies | `tools/requirements-reviewed-runtime.txt` |

The image digests were resolved on 2026-10-04.
They make the selected images repeatable; they do not establish that an old pin remains suitable for production.
Build dependency downloads require network access.
Do not use the package with private material until the host directory and files have owner-only permissions.

## Start a separate offline project

Run from the repository root.
Use a new directory outside the checkout and every server document root.
Do not point `WIKIKG_PRIVATE_ROOT` at a general home directory or a production export tree.

```bash
PRIVATE="$(mktemp -d "${TMPDIR:-/tmp}/wikikg-offline.XXXXXX")"
chmod 0700 "$PRIVATE"
export WIKIKG_PRIVATE_ROOT="$PRIVATE"
export WIKIKG_UID="$(id -u)" WIKIKG_GID="$(id -g)"
export WIKIKG_NEO4J_PASSWORD="$(openssl rand -hex 24)"
export WIKIKG_BOLT_PORT=7689
COMPOSE=extensions/WikiKGExtractor/demo/docker-compose.reviewed-offline.yml

docker compose --env-file /dev/null -p wikikg-reviewed-offline \
    -f "$COMPOSE" --profile reviewed-offline build tools
docker compose --env-file /dev/null -p wikikg-reviewed-offline \
    -f "$COMPOSE" --profile reviewed-offline up -d --wait graph
```

The project name gives this installation its own data volume.
Change it if that name already belongs to another installation.
Keep the same password when reusing an existing graph volume.
Do not capture `docker compose config` output containing the resolved password into shared logs.
The health check establishes that Bolt listens; the loader still checks authentication and database operations.

## Validate, load, and inspect an export

Put the new eligible export at `$PRIVATE/reviewed.json` with mode `0600`.
The PHP export command must enforce the actor-only offline source scope before writing it.
Do not substitute the legacy provisional worker graph or an unreviewed full-site export.

```bash
docker compose --env-file /dev/null -p wikikg-reviewed-offline \
    -f "$COMPOSE" --profile reviewed-offline run --rm tools \
    extensions/WikiKGExtractor/tools/reviewed_projection.py validate \
    --manifest /private/wikikg/reviewed.json

docker compose --env-file /dev/null -p wikikg-reviewed-offline \
    -f "$COMPOSE" --profile reviewed-offline run --rm tools \
    extensions/WikiKGExtractor/tools/reviewed_projection.py load \
    --manifest /private/wikikg/reviewed.json --uri bolt://127.0.0.1:7687

docker compose --env-file /dev/null -p wikikg-reviewed-offline \
    -f "$COMPOSE" --profile reviewed-offline run --rm tools \
    extensions/WikiKGExtractor/tools/reviewed_projection.py lookup \
    --uri bolt://127.0.0.1:7687 --claim-type property \
    --subject-type Variety --subject-id '<entity-id>' --predicate '<predicate>'
```

Validation is read-only, but loading changes the isolated Neo4j projection and must be an intentional, separately approved action.
Loading unchanged content verifies the active generation without duplicating claims.
A rebuild from a later full export can remove rejected or stale claims from the active projection while retaining MediaWiki review history.
An old retired generation cannot be reactivated as a rollback by reloading its export.
Offline lookup reports export-time evidence only.
It does not check the current reader's MediaWiki permissions or authorize an answer generator.

## MediaWiki schema and storage

Use a disposable MediaWiki installation with PHP 8.1 or newer and the extension loaded.
Create the five authoritative claim/review tables through the existing extension updater:

```bash
php maintenance/run.php update --quick
```

This updater mutates the MediaWiki schema.
Run it only after explicit approval on a disposable local database, never on the live WikiCrop database as part of these instructions.
The table definitions are in `extensions/WikiKGExtractor/sql/tables.json`.
The generated MySQL and SQLite files and per-table updater patches already exist.
Keep snapshots and append-only decisions when applying any later schema change.
The Python tools do not read or back up those authoritative tables.

Private raw jobs, claims, exports, inventories, comparison inputs, and run outputs stay outside Git and the document root.
Use directory mode `0700` and file mode `0600`, owned by the process that reads or writes them.
POSIX mode checks do not verify custom ACLs or unusual shared mounts.

## Stop without erasing state

```bash
docker compose --env-file /dev/null -p wikikg-reviewed-offline \
    -f "$COMPOSE" --profile reviewed-offline stop
```

Stopping retains the graph volume and private files.
This graph package does not automate deletion, production cutover, backup/restore, or rollback of authoritative MediaWiki state.
A separate [synthetic SQLite restore rehearsal](knowledge-graph-engineering-hardening.md#sqlite-backup-and-restore) exercises new disposable wikis only.
Production recovery remains unverified.

## Verification status

Configuration, rebuild, CLI transfer, and remaining acceptance evidence are recorded in the [remaining-phases implementation note](knowledge-graph-remaining-phases.md).
Reader-facing GraphRAG still requires the existing backend contract and audience policy.
Comparative scientific evaluation still requires confirmed independent references and a human-approved protocol.

## Configuration references

Docker's [Compose service reference](https://docs.docker.com/reference/compose-file/services/) defines read-only bind mounts, shared service network namespaces, and dependency health checks.
The [network reference](https://docs.docker.com/reference/compose-file/networks/) defines `internal: true` as externally isolated networking.
Neo4j's [Docker configuration guidance](https://neo4j.com/docs/operations-manual/current/docker/configuration/) documents the memory environment variables used here.
Its [Docker introduction](https://neo4j.com/docs/operations-manual/current/docker/introduction/) states that `NEO4J_AUTH` initializes a new database and does not override authentication in an existing `/data` volume.
These references were checked through Context7 on 2026-10-04.
The isolated build, authenticated commands, idempotent loading, later-generation rebuild, and retired-generation refusal passed on Docker Desktop on that date.
Those results do not verify the query, runner, offline command, or UI work dated 2026-10-08.
Host-side Bolt access and production compatibility remain unverified.
The separate hardening labs must report their dated SQLite and MariaDB results without extending these older graph results to production recovery.
