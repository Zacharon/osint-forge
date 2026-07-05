from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.governance.sat_preconditions import ReportPreconditionError
from osint_forge.governance.schema_validator import CaseFileManifest, FieldSpec, HandoffSchema, SchemaDriftError
from osint_forge.orchestrator.mission_store import MissionStore
from osint_forge.orchestrator.mission_supervisor import MissionStatus, MissionSupervisor
from osint_forge.review.human_review import ReviewRequest


class GovernanceTests(unittest.TestCase):
    def test_pii_sanitizer_canonicalizes_before_blocking(self) -> None:
        result = PIISanitizer().check("Find the home%20address of John Doe", tool_risk_level="high")
        self.assertFalse(result.is_safe)
        self.assertEqual(result.blocked_attribute, "home address")

    def test_schema_manifest_rejects_unknown_fields(self) -> None:
        manifest = CaseFileManifest("m-test")
        manifest.register_schema(HandoffSchema("collector_output", {"raw_findings": FieldSpec("list")}))
        manifest.lock()
        with self.assertRaises(SchemaDriftError):
            manifest.validate_handoff("collector_output", {"raw_findings": [], "surprise": True})

    def test_review_status_is_bound_to_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = MissionStore(tmp_path / "forge.db")
            store.create_mission("m-review", "public dry run")
            store.record_artifact("m-review", "sat_ach", "output/sat/ach.md", "hash-a")
            store.request_review("m-review", "sat_ach", "output/sat/ach.md", "hash-a")

            self.assertFalse(
                store.update_review_status(
                    mission_id="m-review",
                    artifact_path="output/sat/ach.md",
                    artifact_hash="hash-b",
                    status="APPROVED",
                    reviewer="tester",
                    reason="wrong hash",
                )
            )
            self.assertTrue(
                store.update_review_status(
                    mission_id="m-review",
                    artifact_path="output/sat/ach.md",
                    artifact_hash="hash-a",
                    status="APPROVED",
                    reviewer="tester",
                    reason="correct hash",
                )
            )
            self.assertTrue(store.verify_approved_artifact("m-review", "output/sat/ach.md", "hash-a"))
            self.assertFalse(store.verify_approved_artifact("m-review", "output/sat/ach.md", "hash-b"))
            store.close()

    def test_dry_run_creates_pending_reviews_and_blocks_report_until_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            supervisor = MissionSupervisor(
                "m-dry",
                "Analyze public event trends in dry run mode",
                workspace_root=tmp_path / "workspace",
                db_path=tmp_path / "forge.db",
            )
            result = asyncio.run(supervisor.run_dry_run())
            self.assertEqual(result["status"], MissionStatus.AWAITING_REVIEW.value)
            self.assertTrue((tmp_path / "workspace" / "output" / "sat" / "ach-evidence-matrix.md").exists())

            with self.assertRaises(ReportPreconditionError):
                supervisor.validate_report_preconditions()

            for review in supervisor.context.pending_reviews:
                request = ReviewRequest(
                    mission_id="m-dry",
                    artifact_type=review["artifact_type"],
                    artifact_path=review["artifact_path"],
                    artifact_hash=review["artifact_hash"],
                )
                self.assertTrue(supervisor.review_gate.approve(request, reviewer="tester", reason="test approval"))

            report = supervisor.generate_report()
            self.assertEqual(report["status"], MissionStatus.COMPLETED.value)
            self.assertTrue((tmp_path / "workspace" / "output" / "final-report.md").exists())
            supervisor.store.close()

    def test_resume_detects_tampered_approved_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "forge.db"
            workspace = tmp_path / "workspace"
            supervisor = MissionSupervisor("m-tamper", "Analyze public dry-run signals", workspace_root=workspace, db_path=db_path)
            asyncio.run(supervisor.run_dry_run())

            for review in supervisor.context.pending_reviews:
                request = ReviewRequest(
                    mission_id="m-tamper",
                    artifact_type=review["artifact_type"],
                    artifact_path=review["artifact_path"],
                    artifact_hash=review["artifact_hash"],
                )
                self.assertTrue(supervisor.review_gate.approve(request, reviewer="tester", reason="test approval"))
            supervisor.save_checkpoint()
            supervisor.store.close()

            ach_path = workspace / "output" / "sat" / "ach-evidence-matrix.md"
            ach_path.write_text(ach_path.read_text(encoding="utf-8") + "\nTampered after approval.\n", encoding="utf-8")

            resumed = MissionSupervisor("m-tamper", "", workspace_root=workspace, db_path=db_path)
            with self.assertRaises(ReportPreconditionError):
                resumed.resume()
            self.assertEqual(resumed.context.status, MissionStatus.RESUME_BLOCKED_HASH_MISMATCH)
            resumed.store.close()


if __name__ == "__main__":
    unittest.main()
