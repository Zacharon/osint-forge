from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from runner.approval import GovernedReviewPromoter, ReviewDecisionError
from runner.cli import main
from runner.harness import GovernedSkillRunner, sha256_file
from runner.schema_tools import validate_payload_against_schema


FIXTURE_SKILL = Path("tests") / "fixtures" / "runner" / "sample_skill.md"


class GovernedRunnerApprovalTests(unittest.TestCase):
    def test_approval_creates_execution_plan_without_live_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL, allow_live_ingestion=True)

            run_state_before = self._read_json(run_result.run_state_path)
            self.assertEqual(run_state_before["status"], "needs_review")

            decision_result = GovernedReviewPromoter(tmp_path).decide(
                run_result.run_id,
                decision="approved",
                reviewer="unit-test",
                note="Approved for plan creation only.",
            )

            self.assertIsNotNone(decision_result.execution_plan_path)
            decision = self._read_json(decision_result.review_decision_path)
            plan = self._read_json(decision_result.execution_plan_path)
            run_state_after = self._read_json(run_result.run_state_path)
            self.assertEqual(decision["decision"], "approved")
            self.assertTrue(decision["creates_execution_plan"])
            self.assertEqual(plan["execution_mode"], "governed_plan_only")
            self.assertFalse(plan["live_ingestion_enabled"])
            self.assertFalse(plan["live_ingestion_attempted"])
            self.assertTrue(plan["requires_human_execution_review"])
            self.assertEqual(run_state_after["status"], "approved")
            self.assertFalse(run_state_after["live_ingestion_enabled"])
            self.assertEqual(len(run_state_after["review_history"]), 1)
            artifact_summary = run_result.artifact_summary_path.read_text(encoding="utf-8")
            self.assertIn("Review state: `approved`", artifact_summary)
            self.assertIn(decision_result.review_decision_path.name, artifact_summary)
            self.assertIn(decision_result.execution_plan_path.name, artifact_summary)
            self.assertIn(sha256_file(run_result.run_state_path), artifact_summary)
            self._assert_schema_valid(tmp_path, "review_decision.schema.json", decision)
            self._assert_schema_valid(tmp_path, "execution_plan.schema.json", plan)
            self._assert_schema_valid(tmp_path, "run_state.schema.json", run_state_after)

    def test_approve_cli_records_default_approved_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)

            with redirect_stdout(StringIO()):
                exit_code = main(["approve", "--run-id", run_result.run_id, "--project-root", str(tmp_path)])

            run_state = self._read_json(run_result.run_state_path)
            plan_path = Path(run_state["execution_plan_path"])
            decision_path = Path(run_state["review_decision_path"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(run_state["status"], "approved")
            self.assertTrue(plan_path.exists())
            self.assertTrue(decision_path.exists())
            self._assert_schema_valid(tmp_path, "run_state.schema.json", run_state)

    def test_rejection_updates_run_state_from_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)
            promoter = GovernedReviewPromoter(tmp_path)

            decision_result = promoter.decide(run_result.run_id, decision="rejected", reviewer="reviewer", note="Scope still too broad.")

            decision = self._read_json(decision_result.review_decision_path)
            run_state = self._read_json(run_result.run_state_path)
            self.assertEqual(run_state["status"], "rejected")
            self.assertNotIn("execution_plan_path", decision)
            self.assertEqual([item["decision"] for item in run_state["review_history"]], ["rejected"])
            self.assertEqual(len(decision["review_history"]), 1)
            self._assert_schema_valid(tmp_path, "review_decision.schema.json", decision)
            self._assert_schema_valid(tmp_path, "run_state.schema.json", run_state)

    def test_changes_requested_creates_revision_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)

            decision_result = GovernedReviewPromoter(tmp_path).decide(
                run_result.run_id,
                decision="changes_requested",
                reviewer="unit-test",
                note="Add a narrower source boundary before approval.",
            )

            self.assertIsNotNone(decision_result.revision_note_path)
            revision_note = decision_result.revision_note_path.read_text(encoding="utf-8")
            run_state = self._read_json(run_result.run_state_path)
            self.assertIn("Add a narrower source boundary before approval.", revision_note)
            self.assertIn("Live ingestion remains disabled.", revision_note)
            self.assertEqual(run_state["status"], "changes_requested")
            self.assertEqual(run_state["revision_note_path"], str(decision_result.revision_note_path))
            self._assert_schema_valid(tmp_path, "run_state.schema.json", run_state)

    def test_repeated_decisions_are_rejected_after_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)
            promoter = GovernedReviewPromoter(tmp_path)
            promoter.decide(run_result.run_id, decision="approved", reviewer="unit-test")

            with self.assertRaisesRegex(ReviewDecisionError, "terminal"):
                promoter.decide(run_result.run_id, decision="rejected", reviewer="unit-test")

            run_state = self._read_json(run_result.run_state_path)
            self.assertEqual(run_state["status"], "approved")
            self.assertEqual([item["decision"] for item in run_state["review_history"]], ["approved"])

    def test_changes_requested_requires_revised_run_before_later_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)
            promoter = GovernedReviewPromoter(tmp_path)
            promoter.decide(run_result.run_id, decision="changes_requested", reviewer="unit-test")

            with self.assertRaisesRegex(ReviewDecisionError, "revised run"):
                promoter.decide(run_result.run_id, decision="approved", reviewer="unit-test")

            run_state = self._read_json(run_result.run_state_path)
            self.assertEqual(run_state["status"], "changes_requested")
            self.assertEqual([item["decision"] for item in run_state["review_history"]], ["changes_requested"])

    def test_run_id_path_traversal_is_rejected_before_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)
            promoter = GovernedReviewPromoter(tmp_path)

            unsafe_run_ids = [
                f"{run_result.run_id}/child",
                f"{run_result.run_id}\\child",
                f"../{run_result.run_id}",
                f"{run_result.run_id}..",
                str(tmp_path / "runs" / run_result.run_id),
                f"C:{run_result.run_id}",
            ]
            for unsafe_run_id in unsafe_run_ids:
                with self.subTest(unsafe_run_id=unsafe_run_id):
                    with self.assertRaisesRegex(ReviewDecisionError, "run_id"):
                        promoter.decide(unsafe_run_id, decision="approved", reviewer="unit-test")

            run_state = self._read_json(run_result.run_state_path)
            self.assertEqual(run_state["status"], "needs_review")
            self.assertNotIn("review_history", run_state)

    def test_run_id_must_be_existing_direct_run_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            runs_root = tmp_path / "runs"
            nested = runs_root / "parent" / "child"
            nested.mkdir(parents=True, exist_ok=True)
            (nested / "run_state.json").write_text("{}", encoding="utf-8")

            promoter = GovernedReviewPromoter(tmp_path)

            with self.assertRaisesRegex(ReviewDecisionError, "path separators"):
                promoter.decide("parent/child", decision="approved", reviewer="unit-test")
            with self.assertRaisesRegex(ReviewDecisionError, "not found"):
                promoter.decide("missing-run", decision="approved", reviewer="unit-test")

    def test_run_state_schema_supports_lifecycle_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_schemas(tmp_path)
            run_result = GovernedSkillRunner(tmp_path).run(FIXTURE_SKILL)
            run_state = self._read_json(run_result.run_state_path)

            for status in (
                "needs_review",
                "approved",
                "rejected",
                "changes_requested",
                "execution_plan_created",
            ):
                candidate = dict(run_state)
                candidate["status"] = status
                self._assert_schema_valid(tmp_path, "run_state.schema.json", candidate)

    def _seed_schemas(self, tmp_path: Path) -> None:
        source_root = Path(__file__).resolve().parent.parent / "schemas"
        target_root = tmp_path / "schemas"
        target_root.mkdir(parents=True, exist_ok=True)
        for schema_path in source_root.glob("*.json"):
            target = target_root / schema_path.name
            target.write_text(schema_path.read_text(encoding="utf-8"), encoding="utf-8")
        source_skill = Path(__file__).resolve().parent.parent / FIXTURE_SKILL
        target_skill = tmp_path / FIXTURE_SKILL
        target_skill.parent.mkdir(parents=True, exist_ok=True)
        target_skill.write_text(source_skill.read_text(encoding="utf-8"), encoding="utf-8")

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _assert_schema_valid(self, project_root: Path, schema_name: str, payload: dict) -> None:
        validate_payload_against_schema(project_root / "schemas", schema_name, payload)


if __name__ == "__main__":
    unittest.main()
