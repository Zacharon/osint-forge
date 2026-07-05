from __future__ import annotations

import unittest
from pathlib import Path

from runner.schema_tools import (
    SchemaValidationError,
    validate_evidence_contract_bundle,
    validate_evidence_link,
    validate_source_record,
    validate_uncertainty_assessment,
)


SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"


def valid_source_record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "source_id": "src-review-packet",
        "source_kind": "runner_artifact",
        "title": "Sample review packet",
        "locator": "runs/example/review_packet.md",
        "retrieved_at": "2026-07-04T11:27:54Z",
        "access_method": "runner_artifact",
        "live_ingestion_used": False,
        "metadata": {"artifact": "review_packet.md"},
    }


def valid_evidence_link() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "evidence_link_id": "ev-001",
        "output_artifact_id": "review_packet.md",
        "claim_id": "claim-summary-001",
        "source_id": "src-review-packet",
        "relationship": "supports",
        "reference": {
            "title": "Sample review packet",
            "locator": "runs/example/review_packet.md",
            "retrieved_at": "2026-07-04T11:27:54Z",
            "section": "Summary",
        },
        "quoted_or_extracted_text": "Dry-run execution prepared for the sample skill.",
        "notes": ["Runner artifact only; no live ingestion."],
    }


def confidence(score: float = 0.75, band: str = "high") -> dict[str, object]:
    return {
        "score": score,
        "band": band,
        "rationale": "Structured runner artifact with explicit review gate.",
    }


def valid_uncertainty_assessment() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "uncertainty_id": "unc-001",
        "target_type": "claim",
        "target_id": "claim-summary-001",
        "source_reliability": confidence(0.7, "high"),
        "extraction_certainty": confidence(0.8, "high"),
        "corroboration_level": {
            "score": 0.3,
            "band": "single_source",
            "independent_source_count": 1,
            "rationale": "The claim is supported by one generated runner artifact.",
        },
        "analyst_or_model_confidence": confidence(0.6, "medium"),
        "overall_confidence": confidence(0.6, "medium"),
        "rationale": "The output is reviewable but not independently corroborated.",
    }


class EvidenceContractTests(unittest.TestCase):
    def test_source_record_schema_accepts_runner_artifact_metadata(self) -> None:
        validate_source_record(SCHEMA_ROOT, valid_source_record())

    def test_evidence_link_schema_requires_claim_artifact_and_reference(self) -> None:
        payload = valid_evidence_link()
        validate_evidence_link(SCHEMA_ROOT, payload)

        del payload["claim_id"]
        with self.assertRaisesRegex(SchemaValidationError, r"claim_id"):
            validate_evidence_link(SCHEMA_ROOT, payload)

    def test_uncertainty_schema_requires_split_dimensions(self) -> None:
        payload = valid_uncertainty_assessment()
        validate_uncertainty_assessment(SCHEMA_ROOT, payload)

        del payload["corroboration_level"]
        with self.assertRaisesRegex(SchemaValidationError, r"corroboration_level"):
            validate_uncertainty_assessment(SCHEMA_ROOT, payload)

    def test_uncertainty_scores_are_bounded(self) -> None:
        payload = valid_uncertainty_assessment()
        payload["overall_confidence"] = confidence(1.2, "very_high")

        with self.assertRaisesRegex(SchemaValidationError, r"overall_confidence.score"):
            validate_uncertainty_assessment(SCHEMA_ROOT, payload)

    def test_bundle_validation_connects_links_to_sources_and_uncertainty_targets(self) -> None:
        validate_evidence_contract_bundle(
            SCHEMA_ROOT,
            source_records=[valid_source_record()],
            evidence_links=[valid_evidence_link()],
            uncertainty_assessments=[valid_uncertainty_assessment()],
        )

    def test_bundle_validation_rejects_unknown_source_reference(self) -> None:
        link = valid_evidence_link()
        link["source_id"] = "src-missing"

        with self.assertRaisesRegex(SchemaValidationError, r"unknown source_id"):
            validate_evidence_contract_bundle(
                SCHEMA_ROOT,
                source_records=[valid_source_record()],
                evidence_links=[link],
            )

    def test_bundle_validation_rejects_unknown_uncertainty_target(self) -> None:
        uncertainty = valid_uncertainty_assessment()
        uncertainty["target_id"] = "claim-missing"

        with self.assertRaisesRegex(SchemaValidationError, r"unknown uncertainty target"):
            validate_evidence_contract_bundle(
                SCHEMA_ROOT,
                source_records=[valid_source_record()],
                evidence_links=[valid_evidence_link()],
                uncertainty_assessments=[uncertainty],
            )


if __name__ == "__main__":
    unittest.main()
