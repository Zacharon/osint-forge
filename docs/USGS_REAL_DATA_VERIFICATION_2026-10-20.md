# USGS real-data path verification — 2026-10-20

## Result and scope

**PASS for the local SQLite → HTTP API path**, with the corrections in this branch. **Hosted Live Map integration is unrun.** No hosted settings, shared database, source-registry defaults, or publication permissions were changed.

Inspected base: `6b5efe7e342db5e05d0261891f0fef061c7de2f8` (PR #3 merge). At inspection, no open Forge PRs existed. Read ingestion, registry authorization, ontology model/store, API routes/projection, API documentation, existing tests, and Live Map's current provider and API contract before execution.

## Executed evidence

| Stage | Observed result |
|---|---|
| Official source fetch | Live CLI succeeded at `2026-10-20T05:50:47Z`; weekly M2.5 summary feed, 332 features. |
| Isolated durable ingestion | New local SQLite file: 332 created; zero rejected, revised, stale, or withdrawn. One revision per object. No production database used. |
| Source comparison | One additional read-only comparison fetch at `05:51:48Z`, more than a minute later, saved as a verification snapshot. All 332 original stored provenance hashes exactly matched the source features in this snapshot. |
| Repeat ingestion | Replayed the saved source snapshot into a copy of the live database: 332 unchanged, zero writes/revisions/rejections. No additional USGS polling. |
| Actual HTTP API | Started the API CLI on an ephemeral loopback port, using the original durable live database. `/health` and `/api/events?hours=168` returned HTTP 200. API process stopped after verification. |
| API records | 332 canonical + 332 legacy records. Envelope and event field types matched the shared contract fixture. |
| Source field comparison | All 332 matched source coordinates, occurrence/update clocks, magnitude and depth, severity calculation, source URL, verification mapping, confidence, and stable identities. Object endpoint provenance matched source hashes. |
| Source status mix | 289 reviewed, 43 automatic. `primary-confirmed` and 96 confidence represent the configured USGS-reviewed mapping; they do not imply independent corroboration. Each event has one independent provenance source. |
| Source corrections/withdrawals | Tested with clearly synthetic fixtures only. No live correction or withdrawal was observed or fabricated. |
| PostgreSQL | Not run locally: no PostgreSQL server/client or psycopg installed. Existing CI PostgreSQL 16 job extended to run the USGS tests, each in an isolated schema. |
| Hosted integration | Not run: no deployed Forge endpoint or Live Map configuration was changed or exercised. |

Official source: https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson

USGS format: https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php

Live Map contract: https://github.com/Zacharon/live-map/blob/main/docs/API_CONTRACTS.md

The comparison snapshot's `metadata.generated` was `1792475470000`. SHA-256 of the saved JSON snapshot: `a0ccd7d8b96bd68c65c2ec53469f9f9b7d2a06bcf0016796f8c0a78834b36182`. It preserves source JSON values rather than the original wire formatting. Source payloads and test databases are retained separately as verification evidence; they are not committed or added to Forge's raw-payload store.

## Field checks

| Field | Verified mapping |
|---|---|
| Longitude / latitude | GeoJSON `coordinates[0]` / `[1]` → stored `lon` / `lat` → API `longitude` / `latitude` and legacy `lon` / `lat`. |
| Occurrence | Source `properties.time` → `valid_from` → canonical UTC ISO `startedAt` / legacy epoch-ms `occurredAt`. |
| Update | Source `properties.updated` → `updated_at` → canonical UTC ISO / legacy epoch-ms `updatedAt`. Retrieval time remains separate. |
| Magnitude / depth | Exact source numbers in ontology properties; additive API `metadata.magnitude` / `depthKm`. The observed magnitude range was 2.45–6.9; no stricter minimum was invented for the official feed. |
| Severity | Clamped `round(magnitude / 9.5 * 100)`; observed source values agree with Live Map's magnitude rule. This is an application score, not an impact assessment. |
| Source | `usgs`, official tier/type, fixed USGS publisher, allowlisted event-page URL. Provenance stores `usgs:<source id>`, retrieval clock, source-feature hash. |
| Verification | USGS reviewed → `primary-confirmed`, confidence 96; automatic → `single-source`, confidence 88. |
| Identity | Forge `event:usgs-<source id>` stays stable; existing `providerEventId` stays `usgs-<source id>`; additive `metadata.usgsId` exposes the original source identity. Forge IDs differ from Live Map's native `usgs:<source id>` convention, so downstream cross-provider deduplication is not established here. |

Example actually returned over local HTTP: `event:usgs-us6000u2mq`, magnitude 6.9, depth 135 km, latitude 51.9848, longitude 157.2701, occurrence `2026-10-16T03:35:31.925Z`, source update `2026-10-17T03:41:49.338Z`, severity 73, reviewed/confidence 96. Source URL: https://earthquake.usgs.gov/earthquakes/eventpage/us6000u2mq

## Reproduced defects and bounded corrections

1. **Withdrawal watermark loss:** active at T0, deleted at T1, deleted again at T3, then active at T2 incorrectly resurfaced. Previously, any already-withdrawn object skipped newer deletion updates. Newer withdrawal revisions now advance source time; exact replays remain no-ops, and a truly newer restoration can still revise the same identity.
2. **Missing update bypass:** missing/invalid source-update timestamps bypassed ordering; deletion could substitute retrieval time. Ingestion now rejects such records with `invalid-source-update-time`. Nothing in the captured live snapshot was rejected by this stricter check.
3. **Measurement loss at API projection:** stored numeric magnitude/depth and original USGS id disappeared into a rounded description. Both API event forms now include seven explicitly allowlisted USGS metadata fields. Arbitrary properties, raw payloads, and provenance metadata are not copied into the feed. Existing identity fields and the shared expected envelope remain unchanged.

Regression tests failed on the original production implementations (two failures, two errors), then passed after the corrections. Tests cover newer/repeated withdrawal, stale active replay, valid restoration, malformed update clocks without writes, absence from a rolling feed, measurement preservation, raw-payload exclusion, and fixture CLI ingestion followed by reopening the durable database and serving the WSGI API at a frozen fixture clock. The same USGS ingestion test cases are registered for PostgreSQL CI.

## Validation

- Original suite: 137 tests discovered; 124 passed, 13 PostgreSQL tests skipped.
- Corrected full suite: 153 discovered; 129 passed, 24 PostgreSQL tests skipped.
- Final targeted USGS suite after adding the raw-payload assertion: 26 discovered; 15 passed, 11 PostgreSQL tests skipped.
- `git diff --check` passed.
- Actual source-snapshot → store → loopback HTTP comparison passed all 332 records. No polling, deployments, or API writes remain running.

Deterministic replay: `python -m unittest tests.test_usgs_ingestion tests.test_api -v`. Full suite: `python -m unittest discover -s tests -v`. PostgreSQL runs require an explicit isolated `FORGE_TEST_DATABASE_URL` and psycopg; see the CI service configuration. A fresh live run still requires `--live --confirm-live` and registry authorization and should target a newly created disposable database.

## Remaining limits and next milestone

The rolling summary feed does not prove withdrawals by omission; a record aging out, changing magnitude classification, or disappearing is not automatically deleted. An explicit deletion for an unknown id is currently ignored; this verification establishes retained withdrawal history for previously ingested objects. Source health/freshness in the API describes ontology projection, not a durable USGS polling-health monitor. Country names remain a source-place-string heuristic; no independent geocoding was verified.

Next milestone: run PostgreSQL CI, then use a separately authorized staging Forge deployment and configured Live Map client to verify the same source identities and fields over the hosted boundary. Local compatibility and actual local HTTP success do not establish hosted connectivity, cross-source deduplication, or production readiness.
