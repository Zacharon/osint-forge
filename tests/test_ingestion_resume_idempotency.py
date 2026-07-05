from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from osint_forge.governance.sat_preconditions import ReportPreconditionError
from osint_forge.orchestrator.mission_store import MissionStore
from osint_forge.orchestrator.mission_supervisor import MissionStatus, MissionSupervisor
from osint_forge.orchestrator.tool_registry import ToolSpec
from osint_forge.review.human_review import ReviewRequest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gdelt"


def _approve_pending(supervisor: MissionSupervisor) -> None:
    for review in supervisor.context.pending_reviews:
        request = ReviewRequest(
            mission_id=supervisor.context.mission_id,
            artifact_type=review["artifact_type"],
            artifact_path=review["artifact_path"],
            artifact_hash=review["artifact_hash"],
        )
        assert supervisor.review_gate.approve(request, reviewer="tester", reason="test approval")


class IngestionResumeIdempotencyTests(unittest.TestCase):
    def test_resume_does_not_rerun_completed_tool_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "forge.db"
            workspace = tmp_path / "workspace"

            first = MissionSupervisor(
                "m-no-rerun",
                "Analyze synthetic public event records",
                workspace_root=workspace,
                db_path=db_path,
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            asyncio.run(first.run_dry_run())
            self.assertEqual(first.store.count_rows("tool_runs", "m-no-rerun"), 3)
            first.store.close()

            def fail_if_called(**_: Any) -> dict[str, Any]:
                raise AssertionError("collector was rerun despite a completed checkpoint")

            resumed = MissionSupervisor(
                "m-no-rerun",
                "Analyze synthetic public event records",
                workspace_root=workspace,
                db_path=db_path,
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            resumed.registry.register(
                ToolSpec(
                    name="collector",
                    func=fail_if_called,
                    output_schema="collector_output",
                    risk_level="medium",
                    timeout_seconds=1,
                )
            )
            result = asyncio.run(resumed.run_dry_run())

            self.assertEqual(result["status"], MissionStatus.AWAITING_REVIEW.value)
            self.assertEqual(resumed.store.count_rows("tool_runs", "m-no-rerun"), 3)
            resumed.store.close()

    def test_repeated_dry_run_does_not_duplicate_records_artifacts_or_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "forge.db"
            workspace = tmp_path / "workspace"

            first = MissionSupervisor(
                "m-idempotent",
                "Analyze synthetic public event records",
                workspace_root=workspace,
                db_path=db_path,
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            asyncio.run(first.run_dry_run())
            counts = {
                table: first.store.count_rows(table, "m-idempotent")
                for table in ("artifacts", "governance_events", "ingested_records", "review_requests", "tool_runs", "tool_outputs")
            }
            first.store.close()

            second = MissionSupervisor(
                "m-idempotent",
                "Analyze synthetic public event records",
                workspace_root=workspace,
                db_path=db_path,
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            asyncio.run(second.run_dry_run())
            repeated_counts = {
                table: second.store.count_rows(table, "m-idempotent")
                for table in ("artifacts", "governance_events", "ingested_records", "review_requests", "tool_runs", "tool_outputs")
            }

            self.assertEqual(counts, repeated_counts)
            second.store.close()

    def test_crash_after_collector_checkpoint_resumes_from_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "forge.db"
            workspace = tmp_path / "workspace"

            crashed = MissionSupervisor(
                "m-crash",
                "Analyze synthetic public event records",
                workspace_root=workspace,
                db_path=db_path,
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            crashed.store.create_mission("m-crash", crashed.context.user_query)
            asyncio.run(crashed._run_step("collector", fixture_dir=str(FIXTURE_DIR)))
            crashed.store.close()

            def fail_if_called(**_: Any) -> dict[str, Any]:
                raise AssertionError("collector was rerun after durable checkpoint")

            resumed = MissionSupervisor(
                "m-crash",
                "Analyze synthetic public event records",
                workspace_root=workspace,
                db_path=db_path,
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            resumed.registry.register(
                ToolSpec(
                    name="collector",
                    func=fail_if_called,
                    output_schema="collector_output",
                    risk_level="medium",
                    timeout_seconds=1,
                )
            )
            result = asyncio.run(resumed.run_dry_run())

            self.assertEqual(result["status"], MissionStatus.AWAITING_REVIEW.value)
            self.assertEqual(resumed.context.completed_tools, ["collector", "source_ancestry", "evidence_linker"])
            self.assertEqual(resumed.store.count_rows("tool_runs", "m-crash"), 3)
            resumed.store.close()

    def test_tampered_artifact_hash_fails_sat_before_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            supervisor = MissionSupervisor(
                "m-sat-tamper",
                "Analyze synthetic public event records",
                workspace_root=tmp_path / "workspace",
                db_path=tmp_path / "forge.db",
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            asyncio.run(supervisor.run_dry_run())
            _approve_pending(supervisor)

            ach_path = tmp_path / "workspace" / "output" / "sat" / "ach-evidence-matrix.md"
            ach_path.write_text(ach_path.read_text(encoding="utf-8") + "\nTampered after checkpoint.\n", encoding="utf-8")

            with self.assertRaises(ReportPreconditionError):
                supervisor.validate_report_preconditions()
            supervisor.store.close()

    def test_missing_human_approval_fails_sat_before_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            supervisor = MissionSupervisor(
                "m-sat-review",
                "Analyze synthetic public event records",
                workspace_root=tmp_path / "workspace",
                db_path=tmp_path / "forge.db",
                gdelt_fixture_dir=FIXTURE_DIR,
            )
            asyncio.run(supervisor.run_dry_run())

            with self.assertRaises(ReportPreconditionError):
                supervisor.validate_report_preconditions()
            supervisor.store.close()


if __name__ == "__main__":
    unittest.main()
