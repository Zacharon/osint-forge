# OSINT Forge v3.2 Mission Lifecycle

v3.2 is the pre-ingestion hardening layer. It exists to make resume, artifacts, review, and dry-run ingestion trustworthy before live OSINT collection is enabled.

## Tool Execution Ledger

Every governed tool call has a stable idempotency key derived from:

- `mission_id`
- `tool_name`
- deterministic hash of the tool input payload

Tool state is stored in SQLite `tool_executions` with one of:

- `pending`
- `running`
- `completed`
- `failed`
- `blocked`

`ToolRegistry.run_governed_tool()` is still the only execution path. It checks PII on arguments, records `pending/running`, validates output against the locked schema, scans output for PII, saves the governed output, and only then marks the execution `completed`.

Resume never reruns a `completed` tool call. It returns the cached `tool_outputs` row after re-validating the cached output against the current PII and schema gates. Failed retryable executions may run again; blocked executions do not retry automatically.

## Resume Reconciliation

`MissionSupervisor.resume()` reconciles before allowing progress:

- SQLite `artifacts`
- checkpointed `artifact_hashes`
- checkpointed manifest hash
- current manifest hash
- workspace files on disk
- `ARTIFACT_RECORDED` governance-event action log entries

Resume blocks on:

- missing artifact files: `RESUME_BLOCKED_MISSING_ARTIFACT`
- hash mismatches: `RESUME_BLOCKED_HASH_MISMATCH`
- unexpected files in the mission workspace: `RESUME_BLOCKED_TAMPERED`

Approved review artifacts are also rehashed on resume. A reviewed artifact whose current bytes no longer match the approved hash cannot move forward.

## Report Preconditions

Report generation remains fail-closed. `validate_report_preconditions()` requires:

- required SAT artifacts exist
- required SAT artifacts are non-empty
- ACH has at least two hypotheses
- uncertainty notes do not promote single-source claims to high confidence
- current file bytes match checkpointed artifact hashes
- SQLite review approval exists for the current artifact hash where review is required

Markdown status is never authoritative.

## Dry-Run Ingestion Gate

The GDELT connector remains fixture-only. It reads local JSON/CSV fixtures, normalizes synthetic records into the evidence/provenance model, scans records before persistence, and writes accepted records through `MissionStore.ingested_records`.

Synthetic PII probe records are blocked before persistence and logged as safe metadata. Prompt-injection source text blocks the governed tool execution.

No live network access is part of v3.2.

## Red-Team Harness

The test suite covers:

- prompt injection inside source text
- PII leakage attempts before persistence
- schema drift in governed tool output
- duplicate/syndicated source clustering
- single-source overconfidence in uncertainty notes
- missing artifact during resume
- hash-mismatched artifact during resume
- unexpected artifact files during resume
- crash-after-checkpoint and crash-before-artifact-write recovery
