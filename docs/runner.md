# Runner

`runner/` is a minimal dry-run execution harness for OSINT Forge skill prompts.

It is intentionally narrow:

- reads one skill markdown file
- reads an existing project `run_state.json` if present
- loads recent memory checkpoints from `memory_checkpoints/`
- creates a new governed run folder under `runs/`
- emits structured reviewable artifacts
- forces the run state to `needs_review`
- keeps live ingestion disabled by default

## Lifecycle

The current runner lifecycle is artifact-only and review-gated:

1. Dry run creates a run folder and writes `run_state.json` with `status=needs_review`.
2. A review decision records one of `approved`, `rejected`, or `changes_requested`.
3. `approved` updates the run state to `approved` and writes an inert execution plan artifact.
4. `rejected` updates the run state to `rejected` and writes no execution plan.
5. `changes_requested` updates the run state to `changes_requested` and writes a revision note.

Review decisions can only be applied to runs currently in `needs_review`.
`approved` and `rejected` are terminal states. A run in `changes_requested`
must be replaced by a revised dry run before it can be approved or rejected.
There is no override flag in the current CLI.

`run_state.schema.json` also accepts `execution_plan_created` for compatibility with branches that split plan creation into a distinct run-state status. The current CLI does not emit that status; approved runs remain `approved` while the execution plan artifact itself uses `status=plan_ready`.

The runner does not have a separate `dry_run` status. The dry-run phase is represented by `needs_review` plus `review_required=true`.

## Dry-Run Command

```powershell
python -m runner --skill ".\tests\fixtures\runner\sample_skill.md" --project-root "."
```

`--project-root` must point at an existing OSINT Forge project root with a
`schemas/` directory. The runner creates `runs/` and `memory_checkpoints/` when
needed, but it does not create an arbitrary project tree for an unverified path.

`--skill` is resolved relative to `--project-root` unless an absolute path is
provided. In both cases, the resolved file must be a Markdown file inside the
project root. Missing files, non-files, non-Markdown files, and paths that escape
the project root fail before any run artifacts are written.

## Artifacts

Each run creates:

- `runs/<run-id>/run_state.json`
- `runs/<run-id>/memory_checkpoint.json`
- `runs/<run-id>/review_packet.md`
- `runs/<run-id>/eval_result.json`
- `runs/<run-id>/artifact_summary.md`

The harness also updates:

- `runs/run_state.json`
- `memory_checkpoints/<run-id>.json`

## Review Packet Evidence

`review_packet.md` always includes `Evidence` and `Uncertainty` sections between
`Review focus` and `Governance notes`.

When the provider response includes `source_records`, `evidence_links`, or
`uncertainty_assessments`, the harness validates them against the evidence and
uncertainty schemas before creating the run directory. Invalid contract data
fails the dry run before final artifacts are written.

When no evidence links are generated, the packet explicitly says evidence links
were not provided. When no uncertainty assessments are generated, the packet
explicitly says uncertainty assessments were not provided.

## Review Decision Command

A dry run remains `needs_review` until a human records a review decision. Approval does not execute live work; it creates a governed execution plan artifact for later review.

```powershell
python -m runner approve --run-id "<run-id>" --project-root "."
```

The approval command defaults to `--decision approved`. Reviewers can also record non-approval decisions:

```powershell
python -m runner approve --run-id "<run-id>" --project-root "." --decision rejected --note "Out of scope."
python -m runner approve --run-id "<run-id>" --project-root "." --decision changes_requested --note "Narrow source boundaries before approval."
```

Review decisions create:

- `runs/<run-id>/<decision-id>.review_decision.json`
- `runs/<run-id>/<decision-id>.execution_plan.json` for `approved`
- `runs/<run-id>/<decision-id>.revision_note.md` for `changes_requested`

The review decision artifact supports `approved`, `rejected`, and `changes_requested`, and carries the run's review history forward. Rejections update the run state to `rejected` without deleting earlier review history. Changes requested decisions update the run state to `changes_requested` and write a revision note.

Review decisions are append-only at the artifact level for a valid lifecycle:
only `needs_review` can transition to `approved`, `rejected`, or
`changes_requested`. Repeated decisions are rejected instead of overwriting the
current run state.

`--run-id` must be the name of an existing run folder directly under `runs/`.
The approval command rejects absolute paths, drive-prefixed values, path
separators, `..`, unsafe folder-name characters, missing folders, files, nested
folders, and anything that resolves outside `runs/`.

After each review decision, the updated `run_state.json` is validated against `schemas/run_state.schema.json` before it is written.

The execution plan is intentionally inert:

- `status` is `plan_ready`
- `execution_mode` is `governed_plan_only`
- `live_ingestion_enabled` is always `false`
- `live_ingestion_attempted` is always `false`
- a separate human execution review is still required

## Governance Boundaries

- This harness is dry-run only.
- It does not call live OSINT sources.
- It does not require API keys.
- It does not bypass human review.
- It does not claim production readiness.
- `--allow-live-ingestion` is accepted for compatibility, but generated dry-run
  artifacts still force `live_ingestion_enabled=false`.
- Approval `--run-id` must be a safe existing run folder name under `runs/`.
  Path traversal and path-like values are rejected before any run artifact path
  is computed.
- Malformed prior `run_state.json` and malformed memory checkpoints are not used
  as trusted context. The dry run continues with empty/skipped context and marks
  the corresponding eval check as failed for review visibility.
- Run IDs are timestamp-and-skill based. If a run folder already exists for the
  same timestamp and skill slug, the runner appends a deterministic numeric
  suffix instead of overwriting or failing on the first collision.
