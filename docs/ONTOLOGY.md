# Ontology Core

`osint_forge.ontology` is Forge's object model: typed objects joined by typed, time-bounded, evidence-backed links, stored durably with full revision history. It is the shared record store that Live Map will read from, so its vocabulary follows Live Map's `CONTEXT.md`.

## Objects

| Type | Meaning |
|---|---|
| `event` | Something that happened at a place and period |
| `place` | A named location, region or point |
| `organization` | A public body, company, agency or group |
| `vessel`, `aircraft` | Identified from public registry data |
| `facility` | A port, plant, airport, base or other fixed site |
| `source` | A publisher, feed or dataset |
| `document` | An evidential origin: one filing, notice, article or dataset row |
| `observation` | What a source measured or published, with limitations |
| `claim` | One proposition about a subject, place and period |
| `assessment` | A reviewed answer to a requirement |
| `requirement` | A question or standing watch |

Ids are `type:key`, for example `place:strait-of-hormuz`. Every object needs provenance. Coordinates are optional, must be given as a pair, and `(0, 0)` is a valid point rather than a missing one.

There is deliberately no `person` type. The [legal source policy](LEGAL_SOURCE_POLICY.md) forbids dossiers and individual targeting, so the store cannot hold private individuals.

## Links

Structural links (`located_at`, `part_of`, `published_by`, `derived_from`, `collected_with`) need provenance. Every analytic link (`supports`, `contradicts`, `mentions`, `involves`, `answers`, `operated_by`, `observed_reroute`, `possible_association`, `same_as`) must also cite at least one evidence object that already exists in the store. Shared keywords, locations or times never create a link on their own.

`possible_association` is a lead, not proof, so it cannot carry confidence above 0.5. A link's id is derived from its type, endpoints and validity period, so writing the same link twice is a no-op.

## Store

```python
from osint_forge.ontology import SQLiteOntologyStore, PostgresOntologyStore, OntologyObject

store = SQLiteOntologyStore("forge.db")             # no extra packages
store = PostgresOntologyStore("postgresql://...")   # pip install 'osint-forge[postgres]'
```

Both backends pass the same contract tests (`tests/test_ontology.py`):

- `upsert_object` writes a new revision only when content changes. Retrieval timestamps are excluded from the content hash, so re-fetching a source doesn't create fake revisions.
- `object_history` returns every revision.
- `list_objects` filters by type, label text and bounding box.
- `add_link` refuses links to unknown objects or unknown evidence.
- `links_for` and `neighborhood` support link-analysis views.
- Every write appends to `ontology_audit`.

The PostgreSQL schema lives in `osint_forge/ontology/sql/postgres/` and is applied automatically on connect. PostGIS spatial indexing will come in a later migration.

Set `FORGE_TEST_DATABASE_URL` to run the PostgreSQL tests locally. CI runs them against a PostgreSQL 16 service.

The read-only HTTP API and the Live Map feed are described in [API.md](API.md).

## Not yet

- Moving the existing `NormalizedOSINTEvent` and memory-graph data onto these objects.
- Entity resolution that proposes `same_as` links.
