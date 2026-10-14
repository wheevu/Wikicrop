# Legacy extraction and direct-push reference

> **Historical, opt-in reference.** This document preserves the older extraction workflow for compatibility and context.
> It is not the default reviewed workflow, and its outputs are provisional candidate data, not approved evidence.
> Do not send private wiki data to Gemini or enable direct Neo4j writes as part of the reviewed offline workflow.

The current entry point is the [WikiKGExtractor README](../extensions/WikiKGExtractor/README.md).
Current claim review and offline projection boundaries are described in the [isolated installation guide](knowledge-graph-offline-installation.md).

## Historical extension layout

The earlier documentation described this extension layout:

```text
extensions/WikiKGExtractor/
├── extension.json
├── WikiKGExtractor.alias.php
├── bin/
│   ├── kg_worker.py
│   └── requirements.txt
├── i18n/
├── resources/
│   ├── lib/
│   │   ├── cytoscape/
│   │   └── foreign-resources.yaml
│   ├── wikikg.css
│   └── wikikg.graph.js
├── schema/
├── protocol/
├── src/
├── tools/
└── README.md
```

This is a historical outline, not a complete inventory of the current extension.

## Windows/XAMPP installation example

The old setup copied the extension into `C:\xampp\htdocs\Wikicrop\extensions\WikiKGExtractor\`, then enabled it in `LocalSettings.php`:

```php
wfLoadExtension( 'WikiKGExtractor' );
```

Its optional Python setup used PowerShell:

```powershell
cd C:\xampp\htdocs\Wikicrop\extensions\WikiKGExtractor
python -m pip install -r bin\requirements.txt
```

This example does not grant permission to modify a live wiki or run schema updates.

## Gemini extraction option

The historical configuration used these settings:

```php
$wgWikiKGExtractorEnableKG = true;
$wgWikiKGExtractorPythonCommand = 'python';
$wgWikiKGExtractorGeminiApiKey = getenv( 'GEMINI_API_KEY' );
$wgWikiKGExtractorGeminiModel = 'gemini-2.5-flash';
```

The old instructions recommended an environment variable rather than writing the key into `LocalSettings.php`:

```powershell
setx GEMINI_API_KEY "api-key-cua-ban" /M
```

They then advised restarting Apache.
Without a Gemini key, the rules-only extractor was described as an alternative that could extract less information.
Neither option establishes review or scientific correctness.
Do not send private wiki content to Gemini under this reference.

## Direct Neo4j connection examples

These historical examples enable direct writes from the extension.
They are separate from the isolated, authenticated offline projection and must not be treated as its setup.

Neo4j Desktop example:

```php
$wgWikiKGExtractorPushToNeo4j = true;
$wgWikiKGExtractorNeo4jUri = 'bolt://localhost:7687';
$wgWikiKGExtractorNeo4jUser = 'neo4j';
$wgWikiKGExtractorNeo4jPassword = getenv( 'NEO4J_PASSWORD' );
$wgWikiKGExtractorNeo4jDatabase = 'neo4j';
```

Neo4j Aura example:

```php
$wgWikiKGExtractorPushToNeo4j = true;
$wgWikiKGExtractorNeo4jUri = 'neo4j+s://your-instance.databases.neo4j.io';
$wgWikiKGExtractorNeo4jUser = 'neo4j';
$wgWikiKGExtractorNeo4jPassword = getenv( 'NEO4J_PASSWORD' );
$wgWikiKGExtractorNeo4jDatabase = 'neo4j';
```

The former example stored the password in an environment variable and restarted Apache:

```powershell
setx NEO4J_PASSWORD "mat-khau-neo4j" /M
```

Direct push is not the reviewed workflow.
The isolated projection has separate authentication and deployment gates; see the [installation guide](knowledge-graph-offline-installation.md).

## Historical extraction and export behavior

The earlier UI was `Special:WikiKGExtractor`.
It accepted a crop/species name, collected its page and variety pages under `Danh sách/Giống`, exported the collected data, and optionally extracted entities and relations.
When direct Neo4j push was enabled, the old instructions said the extension loaded the generated graph automatically.
That output remains provisional.

The old result inventory included:

| File | Historical description |
| --- | --- |
| `raw_data.txt` | Text collected from wiki pages |
| `raw_data.json` | Collected page data as JSON |
| `graph_data_raw.json` | Extracted entities and relations |
| `candidate_claims.json` | Candidate claims and revision evidence awaiting review |
| `graph_nodes_edges.json` | Provisional graph nodes and edges |
| `neo4j_import.cypher` | Cypher import script |
| `mediawiki_bang_thuoc_tinh_cay_trong.txt` | Variety-property table in wikitext |
| `kg_summary.json` | Processing counts and warnings |

Raw exports can contain private page content.
Use the current privacy rules instead of assuming that an old export path is safe.
Private raw exports require an empty `WikiKGExtractorOutputBaseUrl`, a private destination outside MediaWiki and server document roots, directories set to `0700`, and files set to `0600`.
Private export fails closed on Windows because this writer does not verify Windows ACLs.
An explicitly configured nonempty `WikiKGExtractorOutputBaseUrl` retains legacy public-export behavior and is not private.

## Provisional graph example

The earlier README proposed these node and relationship types:

```text
Crop       → crop species
Variety    → crop variety
Pest       → pest or disease

(Crop)-[:HAS_VARIETY]->(Variety)
(Variety)-[:RESISTANT_TO]->(Pest)
(Variety)-[:SUSCEPTIBLE_TO]->(Pest)
(Crop)-[:AFFECTED_BY]->(Pest)
```

Its sample used rice, OM5451, Jasmine 85, blast disease, and brown planthopper.
Those names illustrate the proposed shape only; they are not agricultural evidence or approved ontology decisions.
The earlier text also used `Chưa rõ` for missing information.

The historic manual Cypher examples were:

```cypher
MATCH (c:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
RETURN c, v;
```

```cypher
MATCH p = (:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
WITH p, v LIMIT 3
MATCH p2 = (v)-[:RESISTANT_TO|SUSCEPTIBLE_TO]->(:Pest)
RETURN p, p2;
```

```cypher
MATCH (:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
RETURN v.id, v.crossbred_from, v.growth_duration, v.plant_height,
       v.yield_amount, v.pest_resistance
ORDER BY v.id;
```

```cypher
MATCH (c:Crop {id: "Lúa"})
RETURN c.scientific_name, c.family, c.genus, c.variety_count, c.pest_count;
```

```cypher
MATCH (:Crop {id: "Lúa"})-[:HAS_VARIETY]->(v:Variety)
WITH v LIMIT 3
MATCH path = (v)-[:RESISTANT_TO|SUSCEPTIBLE_TO]->(:Pest)
RETURN path;
```

These queries inspect a provisional model and data.
They do not apply current-reader authorization or convert extraction output into reviewed claims.

## Historical corpus and pilot commands

The frozen rice corpus utility and pilot evaluator are historical, approval-gated tools, not the default extraction route.
Do not open held-out content or run the held-out evaluator without explicit approval after reviewing the freeze packet.
Their old commands are retained here for reference only:

```bash
python3 extensions/WikiKGExtractor/tools/rice_corpus.py \
    --dump <private-mediawiki-dump.sql> \
    --output-dir <private-output-dir>
```

The former evaluator invocation was:

```bash
python3 extensions/WikiKGExtractor/tools/evaluate_pilot.py \
    --protocol extensions/WikiKGExtractor/protocol/rice-engineering-pilot.v2.json \
    --manifest <private-corpus-manifest.json> \
    --input <private-development-input.json> \
    --output-dir <private-output-directory> \
    --split development
```

Keep detailed outputs private and outside Git.
Historical synthetic examples, including any expected node or claim counts, are fixtures rather than new measured evidence.
