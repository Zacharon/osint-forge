# OSINT Forge v3.2 Live Map MVP

The v3.2 live map path is governed live ingestion for normalized public OSINT events.

Dry-run remains the default operational mode. Live collection requires all of:

- Source exists in `config/source_registry.json`.
- Source is marked `live_capable`.
- Operator passes `--live`.
- Operator passes `--confirm-live`.
- Operator passes `--checklist-approved` after completing `PRE_INGESTION_CHECKLIST.md`.
- Adapter uses official/API-governed access and obeys source policy.

## GDELT First

GDELT is the first live-capable source because it provides public news/event discovery and geography-bearing records suitable for map display. Reddit, Telegram, and X are registered as disabled scaffolds only.

## Live Ingestion Flow

1. `LiveMapIngestionService` checks the legal source registry and operator flags.
2. Raw source payload is scanned by `PIISanitizer` before SQLite persistence.
3. Safe payloads are normalized into `NormalizedOSINTEvent`.
4. Normalized dashboard records are scanned again.
5. Raw payloads are stored in `raw_payloads`.
6. Sanitized normalized events are stored in `normalized_events`.
7. Duplicate events are suppressed by deterministic fingerprint.
8. Governance events are logged for approvals, denials, duplicates, PII blocks, and accepted events.

## Local Dashboard Event Feed

The repo does not run a web server by default. The local endpoint equivalent is:

```powershell
python -m osint_forge.main --db .\osint_forge.db events --mission m-live-001
```

The feed returns sanitized normalized events only. It does not expose raw payload bodies.

## Commands

Dry-run mission:

```powershell
python -m osint_forge.main --db .\osint_forge.db dry-run --mission m-v32-dry --query "Analyze synthetic public event records"
```

Live GDELT collection with operator approval:

```powershell
python -m osint_forge.main --db .\osint_forge.db live-map --mission m-v32-live --query "port delays" --source gdelt --live --confirm-live --checklist-approved --max-records 25
```

Use `--gdelt-live-fixture path\to\fixture.json` for offline fixture-backed live-path testing.

## v3.3 Verification Note

The v3.3 controlled GDELT smoke test proved the live path can accept real DOC article results, write an audit artifact, and expose sanitized events. The returned DOC records did not include `lat`/`lon`, so a true event map still needs GDELT GEO enrichment or another approved geocoding layer before broad operational use.
