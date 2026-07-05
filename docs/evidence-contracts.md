# Evidence and Uncertainty Contracts

OSINT Forge runner outputs can attach evidence and uncertainty records without enabling live ingestion or changing the review gate.

## Schemas

- `schemas/source_record.schema.json` describes source or reference metadata.
- `schemas/evidence_link.schema.json` connects an output artifact and claim to a source record.
- `schemas/uncertainty_assessment.schema.json` records uncertainty for a claim, artifact, evidence link, or source record.

## Evidence Link Contract

An evidence link must name:

- `output_artifact_id`
- `claim_id`
- `source_id`
- `relationship`
- `reference`

The `source_id` must resolve to a validated source record when validating a bundle. The inline `reference` block gives reviewers enough title, locator, and retrieval metadata to inspect the connection even when only the link is visible.

## Uncertainty Contract

Uncertainty is intentionally split into separate dimensions:

- `source_reliability`
- `extraction_certainty`
- `corroboration_level`
- `analyst_or_model_confidence`
- `overall_confidence`

Each dimension carries a score and rationale. `corroboration_level` also records `independent_source_count`.

## Validation Helpers

`runner.schema_tools` exposes:

- `validate_source_record(schema_root, payload)`
- `validate_evidence_link(schema_root, payload)`
- `validate_uncertainty_assessment(schema_root, payload)`
- `validate_evidence_contract_bundle(schema_root, source_records, evidence_links, uncertainty_assessments=None)`

Bundle validation first applies each schema, then checks that every evidence link references a declared `source_id`. It also checks that uncertainty targets point at a known claim, output artifact, evidence link, or source record present in the bundle.

## Review Packet Integration

The integration point is `GovernedSkillRunner._render_review_packet` in `runner/harness.py`.

Provider responses may include:

- `source_records`
- `evidence_links`
- `uncertainty_assessments`

The harness validates that bundle with `validate_evidence_contract_bundle` before creating a run directory or writing final run artifacts. Invalid source, evidence, or uncertainty records fail the dry run with `RunnerError`.

Validated evidence links and uncertainty assessments render after `Review focus` and before `Governance notes`.

Section order:

1. `Evidence`
2. `Uncertainty`
3. `Governance notes`

If no evidence links are present, the Evidence section states that evidence links were not provided. If no uncertainty assessments are present, the Uncertainty section states that uncertainty assessments were not provided.

This should remain dry-run only: no live source calls, no approval bypass, and no runner lifecycle rewrite.
