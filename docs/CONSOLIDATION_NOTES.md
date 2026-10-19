# Consolidation Notes

This repository was initially an empty Git repository. The OSINT Forge v3.1+ fragments were found in a local downloads folder outside the repository.

## Source Fragments Found

- `orchestrator_skeleton_v31_*.py`
- `pii_sanitizer_v31_enhanced.py`
- `schema_locking_v31.py`
- `review_cli_hardened.py`
- `source_ancestry.py`
- `human-review-integrity.md`
- `osint_forge_icm_sat_structure.md`
- `osint_forge_v3_1_plan.md`

## Consolidation Choices

- `pii_sanitizer_v31_enhanced.py` supplied the canonicalization-first privacy gate.
- `schema_locking_v31.py` supplied the locked manifest and explicit amendment ceremony.
- `review_cli_hardened.py` and `human-review-integrity.md` supplied the SQLite-authoritative review model and conditional update pattern.
- The orchestrator variants supplied the required governance architecture, async tool execution, SAT fail-closed behavior, workspace jail, and resume requirements.
- `source_ancestry.py` was incomplete, so the final `osint_forge.analytics.source_ancestry` module implements deterministic clustering, relationship classification, and independence scoring directly.

The original downloaded fragment files were not deleted or modified. The canonical working implementation now lives under `osint_forge/`.

## Phase 2: First Ingestion Layer

Added a fixture-only GDELT dry-run connector in `osint_forge/ingestion/gdelt_connector.py`.

This phase deliberately avoided live network access and real data ingestion. The connector reads local JSON/CSV fixtures, normalizes records into the existing evidence format, scans each normalized record for PII before persistence, stores accepted records through `MissionStore`, and logs blocked synthetic privacy probes without retaining raw blocked content.

Checkpoint and idempotency behavior was hardened before adding ingestion:

- Successful governed tool outputs are stored in SQLite `tool_outputs` by deterministic input hash.
- `ToolRegistry` returns cached governed outputs on resume or repeated dry runs without rerunning completed tools.
- Fixture records are keyed by deterministic `record_fingerprint`.
- Review-request and PII governance events use idempotent event keys where repeated dry runs would otherwise duplicate rows.
- SAT validation now recomputes current artifact hashes and fails closed on tampering before checking review approval.

Regression coverage was added for GDELT fixture ingestion, PII blocking before persistence, repeated dry-run idempotency, crash-after-checkpoint resume, missing approval SAT failure, and tampered artifact SAT failure.

## Phase 3: v3.2 Pre-Ingestion Hardening

Added the v3.2 mission lifecycle layer before live ingestion:

- durable `tool_executions` ledger with `pending`, `running`, `completed`, `failed`, and `blocked`
- stable idempotency keys for governed tool calls
- resume-time artifact reconciliation across SQLite rows, checkpoint hashes, manifest hashes, action-log entries, and workspace files
- explicit resume block events for missing artifacts, hash mismatches, and unexpected workspace artifacts
- prompt-injection blocking in fixture source text
- red-team tests for schema drift, duplicate source clustering, single-source overconfidence, and tampered resume paths

See `docs/MISSION_LIFECYCLE_V32.md` for the current lifecycle contract.
