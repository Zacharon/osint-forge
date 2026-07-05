# OSINT Forge v3.1+

OSINT Forge is a governed, async, privacy-hardened OSINT mission skeleton. This consolidation turns the scattered v3.1 fragments into a single runnable package with SQLite-backed mission state, tamper-resistant human review, canonicalized PII scanning, locked schemas, SAT preconditions, and dry-run execution.

## Current Status

This repository is safe to run in dry-run mode. The current mission CLI still uses the fixture-only GDELT connector by default, so governance can be tested before connecting any live source. v3.2 adds a governed Live Map MVP path for GDELT behind explicit `--live`, `--confirm-live`, and `--checklist-approved` flags. v3.3 adds live-source verification audits and captured-response regression fixtures. Reddit, Telegram, and X are registered as disabled scaffolds only.

## Structure

```text
osint_forge/
+-- analytics/
|   +-- evidence_linker.py
|   +-- source_bias.py
|   +-- source_ancestry.py
+-- agents/
|   +-- roles.py
+-- governance/
|   +-- cost_budgeting.py
|   +-- pii_sanitizer.py
|   +-- sat_preconditions.py
|   +-- schema_validator.py
+-- ingestion/
|   +-- gdelt_connector.py
|   +-- live_adapters.py
|   +-- live_pipeline.py
|   +-- registry_inference.py
|   +-- source_registry.py
+-- market/
|   +-- capital_market.py
+-- memory/
|   +-- graph.py
+-- dashboard/
|   +-- events_api.py
|   +-- index.html
|   +-- styles.css
|   +-- app.js
+-- orchestrator/
|   +-- mission_store.py
|   +-- mission_supervisor.py
|   +-- tool_registry.py
|   +-- workspace_jail.py
+-- review/
|   +-- human_review.py
|   +-- review_cli.py
+-- main.py
```

## Governance Guarantees

- `MissionSupervisor` is the single orchestrator for mission lifecycle, checkpointing, resume, review, and report generation.
- `ToolRegistry.run_governed_tool()` is the required wrapper for tool execution.
- Tool arguments and outputs are scanned by `PIISanitizer` after URL decoding, HTML unescaping, and Unicode NFKC normalization.
- Tool outputs must validate against a locked `CaseFileManifest` schema.
- SQLite is authoritative for mission state, review requests, artifact hashes, governance events, tool outputs, and ingested records.
- SQLite `tool_executions` records durable `pending/running/completed/failed/blocked` state for governed tool calls.
- Human review approvals are bound to `mission_id + artifact_path + artifact_hash`.
- Report generation fails closed unless required SAT artifacts exist, pass content checks, match current file hashes, and required reviews are approved for the current artifact hashes.
- Resume reconciles SQLite artifact rows, checkpoint hashes, manifest hashes, action-log entries, and files on disk before allowing progress.
- Canonical distributed memory is proposal-gated: agents submit `MemoryProposal` objects and verifier promotion is required before nodes or edges enter canonical state.
- Live adapter outputs normalize into `NormalizedOSINTEvent` records with provenance; raw payloads are stored separately from normalized events.
- Source discovery is a pre-ingestion layer: directory hubs such as Start.me OSINT4ALL and OSINT Framework generate disabled registry proposals, not live events.
- The Agent Capital Market uses internal control credits only. It does not integrate payments or crypto.
- Live map ingestion uses `raw_payloads` for raw source audit records and `normalized_events` for sanitized dashboard events.
- `SourceBiasEstimator` outputs provenance-backed indicators, not truth verdicts or private-person dossiers.
- v3.3 live runs write `live_source_audit` artifacts with source policy version, requested/returned counts, sanitizer/schema results, and hashes written.

## Agentic Intelligence Layers

- `osint_forge.ingestion.live_adapters` defines `IngestionAdapter`, `NormalizedOSINTEvent`, health tracking, raw-payload storage, deduplication, rate limiting, retries, and source-specific adapters for GDELT, Reddit, Telegram, and X.
- `osint_forge.ingestion.live_pipeline` gates GDELT live ingestion behind source registry policy, explicit operator flags, checklist approval, PII scanning, and separate raw/normalized persistence.
- `osint_forge.ingestion.registry_inference` defines the Source Discovery Engine for OSINT directory hubs, taxonomy classification, legal-risk estimation, disabled registry proposals, and source graph diffs.
- `osint_forge.memory.graph` defines provenance-preserving `MemoryNode`, `MemoryEdge`, `MemoryProposal`, append-only graph events, verifier promotion/rejection, contradiction edges, graph traversal, and local vector-style retrieval.
- `osint_forge.market.capital_market` defines `AgentAccount`, `MemoryBid`, `MarketDecision`, `CreditLedger`, `MemoryAuctioneer`, `ReputationEngine`, `BudgetGovernor`, and `AntiGoodhartGuard`.
- `osint_forge.analytics.source_bias` defines public source/creator indicator scoring with provenance and privacy guardrails.
- `osint_forge.agents.roles` defines static agent role specifications without autonomous spawning.
- `osint_forge/dashboard/index.html` is a dense live dashboard scaffold with event map, filters, mission board, source health, memory graph, agent market, contradiction queue, report builder, inspector, and bottom timeline.

## Run A Dry-Run Mission

```powershell
python -m osint_forge.main --db .\osint_forge.db dry-run --mission m-001 --query "Analyze public event trends in dry run mode"
```

## Run V3.2 Live Map

Dry-run remains default. Live collection requires operator approval:

```powershell
python -m osint_forge.main --db .\osint_forge.db live-map --mission m-live-001 --query "port delays" --source gdelt --live --confirm-live --checklist-approved --max-records 25
```

Capture a sanitized GDELT response fixture during an approved run:

```powershell
python -m osint_forge.main --db .\osint_forge.db live-map --mission m-live-001 --query "port delays" --source gdelt --live --confirm-live --checklist-approved --max-records 10 --capture-sanitized-fixture .\tests\fixtures\live\gdelt_capture.json
```

Read sanitized dashboard events:

```powershell
python -m osint_forge.main --db .\osint_forge.db events --mission m-live-001
```

## Source Discovery / Registry Bootstrap

Directory hubs are registry seed inputs only. Use `discover-sources` to turn a captured Start.me, OSINT Framework, or GitHub OSINT list page into disabled source proposals:

```powershell
python -m osint_forge.main discover-sources --hub-id osint4all --hub-kind startme --hub-url https://start.me/p/L1rEYQ/osint4all --html-file .\workspace\source-discovery\osint4all.html
```

The output includes extracted links, inferred taxonomy, legal/rate-limit metadata, and `registry_entries` that remain disabled until reviewed and wired to a real connector. See `docs/SOURCE_DISCOVERY_LAYER.md`.

## Dry-Run GDELT Ingestion

The first ingestion slice is fixture-only. It reads synthetic GDELT-like JSON or CSV records from `tests/fixtures/gdelt/`, runs PII scanning before persistence, writes accepted records through `MissionStore.ingested_records`, and logs blocked records as governance events without storing their raw content.

Use the default fixture directory:

```powershell
python -m osint_forge.main --db .\osint_forge.db dry-run --mission m-gdelt-001 --query "Analyze synthetic public event records"
```

Or pass an explicit local fixture directory:

```powershell
python -m osint_forge.main --db .\osint_forge.db dry-run --mission m-gdelt-001 --query "Analyze synthetic public event records" --gdelt-fixture-dir .\tests\fixtures\gdelt
```

No live network calls are made. Re-running the same mission uses durable `tool_outputs` checkpoints and deterministic record fingerprints, so completed tool work and already-ingested fixture records are not duplicated.

List pending reviews:

```powershell
python -m osint_forge.review.review_cli --db .\osint_forge.db --mission m-001 list
```

Approve the generated SAT artifacts:

```powershell
python -m osint_forge.review.review_cli --db .\osint_forge.db --mission m-001 approve output/sat/ach-evidence-matrix.md --reviewer "analyst" --reason "Dry-run SAT artifact reviewed"
python -m osint_forge.review.review_cli --db .\osint_forge.db --mission m-001 approve output/sat/devil-advocate-counterarguments.md --reviewer "analyst" --reason "Dry-run SAT artifact reviewed"
```

Generate the final report:

```powershell
python -m osint_forge.main --db .\osint_forge.db report --mission m-001
```

## Tests

```powershell
python -m unittest discover -s tests
```

The tests cover canonicalized PII blocking, schema drift rejection, review hash binding, GDELT fixture PII gating, ingestion idempotency, resume without rerunning completed tools, artifact reconciliation, SAT fail-closed behavior, report generation after review, red-team cases, live adapter health/dedup/outage behavior, live map safety gates, live audit artifacts, captured GDELT response regression, raw/normalized event separation, source registry validation, source discovery inference/diffing, source-bias provenance requirements, memory graph promotion/rejection/provenance, market bid approval/budget/penalty behavior, and dashboard scaffold coverage.

## Next Engineering Step

Keep GDELT as the only live-capable source until DOC-to-GEO enrichment and real response drift handling are stable. The v3.3 smoke showed DOC article results can lack coordinates, so the map should not be broadened until GEO enrichment is verified. Other sources stay disabled until official clients, credentials, quota policy, and retention controls are reviewed.
