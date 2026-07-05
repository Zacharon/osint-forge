# OSINT Forge v3.3 Live Source Verification Gate

v3.3 proves one legal live source before expanding the map.

## Scope

Only GDELT is live-capable. Reddit, Telegram, and X remain disabled scaffolds and fail closed.

## Source Contract

Every source in `config/source_registry.json` must include:

- `enabled`
- `legal_basis`
- `collection_mode`
- `rate_limit`
- `pii_policy`
- `license_notes`
- `operator_approval_required`
- `last_verified_at`

The registry also keeps the v3.2 fields for display, adapter mapping, storage policy, and disabled-source notes.

## Live Run Audit

Each approved live GDELT run writes a mission artifact under:

```text
workspace/<mission>/output/live-audit/
```

The audit records:

- source used
- query and query hash
- requested and returned record counts
- accepted, rejected, and duplicate counts
- PII sanitizer checks and result
- schema validation checks and result
- raw payload hashes
- event fingerprints and event content hashes
- source policy version hash
- optional sanitized captured fixture path

## GDELT Smoke Result

The controlled smoke mission `m-v33-gdelt-smoke` returned 10 records and accepted 10 sanitized events.

Observed real-world shape:

- DOC article results did not include `lat`/`lon`.
- Several records were multilingual or mojibake-encoded.
- URLs were percent encoded and long.
- The article list was suitable for provenance-backed event feed display, but not yet a true geospatial map without GEO enrichment.

This is enough to verify live ingestion, policy gates, PII projection, dedupe, raw/normalized separation, and audit artifacts. It is not enough to enable additional sources.

## Captured Fixture

The sanitized captured response fixture is:

```text
tests/fixtures/live/gdelt_v33_smoke_captured_sanitized.json
```

Regression tests replay that shape through the live pipeline.
