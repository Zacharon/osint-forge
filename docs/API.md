# Forge API

`osint_forge.api` serves the ontology store over HTTP. It is read-only: writes go through the Python store until authentication exists. It is a plain WSGI app, so it needs no extra packages.

```bash
osint-forge-api --db forge.db                         # SQLite
osint-forge-api --database-url postgresql://...       # PostgreSQL
```

It listens on `127.0.0.1:8765` by default. For production, mount `ForgeApi(store)` in any WSGI server.

## Routes

All routes are `GET`, return JSON, and send `Access-Control-Allow-Origin: *`. `OPTIONS` returns `204`. Errors are `{"error", "message"}` with status `400`, `404`, `405` or `500`, and never include stack traces.

| Route | Returns |
|---|---|
| `/health` | `{"status": "ok"}` |
| `/objects?type=&q=&bbox=&limit=` | Objects filtered by type, label text and `bbox=minLon,minLat,maxLon,maxLat`. `limit` is 1 to 500, default 100. |
| `/events`, `/claims`, `/assessments` | The same list, fixed to one type. |
| `/search?q=&type=` | Label search. `q` is required. |
| `/objects/{id}` | One object. |
| `/objects/{id}/history` | Every revision. |
| `/objects/{id}/links?direction=&type=` | Links in, out or both, optionally limited to a comma-separated list of link types. |
| `/objects/{id}/neighborhood?depth=&max=` | Breadth-first graph for link analysis. `depth` is 0 to 4. |
| `/api/events?hours=&recordKind=&verification=&domain=&country=` | The Live Map feed, described below. |

Served objects drop provenance `metadata`, which can carry extractor detail. Source, locator, observation time and content hash stay.

## Live Map feed

`/api/events` returns the envelope Live Map documents in its `docs/API_CONTRACTS.md`: `events` (legacy, epoch-millisecond clocks), `canonicalEvents` (ISO clocks), `generatedAt`, `filters`, `sources`, `sourceStatus`, the source registries, `providerResults`, `systemStatus`, `mode`, `errors` and `changeCursor`. Live Map can point at Forge without changing its client.

How an `event` object becomes a feed event:

- Only `event` objects with a `valid_from` inside the window are included. The window is `hours`, clamped to 24 to 720, default 168.
- An event with `properties.public` set to `false` is never published.
- `label` becomes `title`, `valid_from` becomes `startedAt`, and `lat`/`lon` become the coordinates. `(0, 0)` stays a mapped point.
- These properties map directly: `provider`, `source_name`, `source_url`, `source_type`, `source_tier`, `category`, `domain`, `description`, `country_code`, `country_name`, `region`, `location_name`, `severity` (0 to 100), `status`, `verification_status`, `source_published_at`, `last_verified_at`, `updated_at`, `tags`, `strategic_areas`, `affected_chokepoints` and `impact_types`.
- `confidence` is stored as 0 to 1, like link confidence, and published as 0 to 100. A missing value stays `null`, and zero stays zero.
- `source_url` is dropped unless it is absolute HTTP(S) with no embedded credentials.
- `ingestedAt` is the latest provenance retrieval time. It describes processing, never when the event happened.
- `independentSourceCount` counts distinct provenance sources on the event. Entity resolution will later group re-acquisitions of one origin.
- `metadata.forge` carries the object id and content hash so Live Map can link back.
- For USGS events, `metadata` also carries the allowlisted `usgsId`, `magnitude`, `depthKm`, `tsunami`, `feltReports`, `significance`, and `rawStatus` when present on the stored object. These fields appear in both canonical and legacy events; raw source payloads are never projected. The Forge `id` remains `event:usgs-<USGS id>` and `providerEventId` remains `usgs-<USGS id>`; `metadata.usgsId` preserves the original source id.

Invalid filters are ignored and reported in `warnings`, as Live Map does.

## Conformance fixture

`tests/fixtures/contracts/live_map_events.json` holds fixture objects, a fixed `now`, the exact expected envelope, and a type schema for the envelope, canonical events and legacy events. Forge's tests reproduce the expected envelope on SQLite and PostgreSQL. Live Map can validate its own `/api/events` responses against the same `schema` block so the two sides can't drift apart. The fixture is test data, not live records.
