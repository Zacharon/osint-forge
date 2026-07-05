# Pre-Ingestion Checklist

Use this checklist before enabling any real external data source.

## Required Before GDELT Dry Run

- `python -m unittest discover -s tests` passes locally.
- `ToolRegistry.run_governed_tool()` is the only path used by the collector adapter.
- Collector arguments and outputs are scanned with `PIISanitizer.check_payload()`.
- Collector output schema is registered in `CaseFileManifest` before `lock()`.
- The mission database is created on durable storage, not `:memory:`.
- `workspace_root` points to a mission-specific directory controlled by `WorkspaceJail`.
- Review CLI can list, approve, and reject pending reviews.
- Final report generation remains blocked until required SAT reviews are approved.
- Fixture records are loaded only from local JSON/CSV under an explicit fixture directory.
- Accepted fixture records are persisted only through `MissionStore.ingested_records`.
- Repeated dry-run ingestion with the same mission and fixtures does not duplicate records, artifacts, review requests, governance events, tool runs, or tool outputs.
- Resume from a durable checkpoint does not rerun completed governed tool work.
- `tool_executions` contains durable state transitions for governed tool calls.
- Resume reconciliation blocks missing files, hash mismatches, stale manifests, missing action-log entries, and unexpected artifacts.
- PII-like fixture records are blocked before persistence and logged only as safe governance metadata.

## Required Before Live External Ingestion

- GDELT adapter supports explicit dry-run mode and fixture mode.
- A separate live mode design has been reviewed; dry-run code remains the default.
- Live mode has an explicit allow flag and cannot be enabled accidentally by passing a URL.
- Network calls have timeouts, retries, and circuit-breaker behavior.
- Raw source payloads are hashed and stored as artifacts before transformation.
- Any stored raw payloads are covered by workspace jail path checks.
- Review approval is bound to the exact artifact hash.
- Resume verifies every approved artifact hash before continuing.
- ACH artifact contains at least two hypotheses.
- Devil's Advocate and ACH artifacts require human review.
- Governance events are reviewed for PII rejections, schema failures, timeouts, and tamper detections.
- CPU-bound synchronous tools are documented and either moved to process workers or explicitly accepted as thread-backed limitations.

## Stop Conditions

- Any PII rejection in collector arguments or outputs.
- Any PII-like fixture or live record that would be persisted before scanning.
- Any schema drift after manifest lock without explicit approved amendment.
- Any artifact hash mismatch after review approval.
- Any SAT precondition failure.
- Any missing checkpoint or inability to resume budget, completed tools, artifacts, and pending reviews.
- Any repeated dry run that changes row counts for stable fixtures without an intentional fixture change.
- Any resume reconciliation failure, including unexpected workspace files.
