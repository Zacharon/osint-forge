from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runner.harness import GovernedSkillRunner, RunnerError
from runner.ledger import ArtifactLedger, ArtifactLedgerError
from runner.provider import ProviderResponse


FIXTURE_SKILL = Path("tests") / "fixtures" / "runner" / "sample_skill.md"


class GovernedRunnerTests(unittest.TestCase):
    def test_run_creation_produces_required_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runner = GovernedSkillRunner(tmp_path)

            result = runner.run(skill_path)

            self.assertTrue(result.run_dir.exists())
            self.assertTrue(result.run_state_path.exists())
            self.assertTrue(result.checkpoint_path.exists())
            self.assertTrue(result.review_packet_path.exists())
            self.assertTrue(result.eval_result_path.exists())
            self.assertTrue(result.artifact_summary_path.exists())

    def test_dry_run_appends_kernel_ledger_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)

            result = GovernedSkillRunner(tmp_path).run(skill_path)

            ledger = ArtifactLedger(tmp_path)
            records = ledger.read_records()
            self.assertEqual(
                [record["record_type"] for record in records],
                [
                    "ExecutionContract",
                    "RunState",
                    "MemoryCheckpoint",
                    "EvalResult",
                    "ReviewPacket",
                    "StateTransition",
                ],
            )
            self.assertEqual({record["run_id"] for record in records}, {result.run_id})
            self.assertTrue(ledger.ledger_path.exists())
            artifact_paths = [record["artifact_path"] for record in records if "artifact_path" in record]
            self.assertEqual(
                artifact_paths,
                [
                    f"runs/{result.run_id}/run_state.json",
                    f"runs/{result.run_id}/memory_checkpoint.json",
                    f"runs/{result.run_id}/eval_result.json",
                    f"runs/{result.run_id}/review_packet.md",
                ],
            )
            self.assertEqual(records[-1]["payload"]["to_status"], "needs_review")

    def test_tampered_kernel_ledger_fails_validation_and_next_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            GovernedSkillRunner(tmp_path).run(skill_path)
            ledger = ArtifactLedger(tmp_path)
            ledger_text = ledger.ledger_path.read_text(encoding="utf-8")
            ledger.ledger_path.write_text(ledger_text.replace("ExecutionContract", "TamperedContract", 1), encoding="utf-8")

            with self.assertRaisesRegex(ArtifactLedgerError, "record hash mismatch"):
                ledger.read_records()
            with self.assertRaisesRegex(RunnerError, "Artifact ledger append failed"):
                GovernedSkillRunner(tmp_path).run(skill_path)

    def test_run_state_update_carries_parent_context_and_updates_project_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runs_dir = tmp_path / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            prior = {
                "schema_version": "1.0",
                "state_scope": "project_current",
                "run_id": "prior-run",
                "skill_name": "Prior Skill",
                "skill_path": "prior.md",
                "status": "needs_review",
                "review_required": True,
                "provider": "mock",
                "created_at": "2026-07-04T00:00:00Z",
                "updated_at": "2026-07-04T00:00:00Z",
                "live_ingestion_enabled": False,
                "live_ingestion_attempted": False,
                "recent_memory_count": 0,
                "memory_checkpoint_path": "memory_checkpoint.json",
                "review_packet_path": "review_packet.md",
                "eval_result_path": "eval_result.json",
                "artifact_summary_path": "artifact_summary.md",
                "skill_sha256": "abc",
                "summary": "prior",
            }
            (runs_dir / "run_state.json").write_text(json.dumps(prior), encoding="utf-8")

            runner = GovernedSkillRunner(tmp_path)
            result = runner.run(skill_path)

            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            current_state = json.loads((tmp_path / "runs" / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["parent_run_id"], "prior-run")
            self.assertEqual(current_state["run_id"], run_state["run_id"])
            self.assertEqual(current_state["state_scope"], "project_current")

    def test_memory_checkpoint_creation_writes_run_and_canonical_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            memory_dir = tmp_path / "memory_checkpoints"
            memory_dir.mkdir(parents=True, exist_ok=True)
            seed = {
                "schema_version": "1.0",
                "checkpoint_id": "checkpoint-seed",
                "run_id": "seed-run",
                "created_at": "2026-07-04T00:00:00Z",
                "status": "needs_review",
                "skill_name": "Seed Skill",
                "live_ingestion_enabled": False,
                "recent_memory_inputs": [],
                "extracted_facts": ["seed"],
                "next_review_action": "review",
            }
            (memory_dir / "seed.json").write_text(json.dumps(seed), encoding="utf-8")

            runner = GovernedSkillRunner(tmp_path)
            result = runner.run(skill_path)

            checkpoint = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))
            canonical = json.loads((tmp_path / "memory_checkpoints" / f"{result.run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["run_id"], result.run_id)
            self.assertEqual(canonical["run_id"], result.run_id)
            self.assertEqual(len(checkpoint["recent_memory_inputs"]), 1)

    def test_needs_review_status_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runner = GovernedSkillRunner(tmp_path)

            result = runner.run(skill_path)

            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            eval_result = json.loads(result.eval_result_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "needs_review")
            self.assertTrue(run_state["review_required"])
            self.assertEqual(eval_result["status"], "needs_review")
            self.assertFalse(eval_result["passed"])

    def test_no_live_default_behavior_holds_even_if_flag_is_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runner = GovernedSkillRunner(tmp_path)

            result = runner.run(skill_path, allow_live_ingestion=True)

            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            eval_result = json.loads(result.eval_result_path.read_text(encoding="utf-8"))
            self.assertFalse(run_state["live_ingestion_enabled"])
            self.assertFalse(run_state["live_ingestion_attempted"])
            self.assertTrue(eval_result["live_ingestion_default_respected"])
            self.assertFalse(eval_result["live_ingestion_attempted"])

    def test_review_packet_includes_empty_evidence_and_uncertainty_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runner = GovernedSkillRunner(tmp_path)

            result = runner.run(skill_path)

            packet = result.review_packet_path.read_text(encoding="utf-8")
            self.assertIn("Evidence:\n- Evidence links were not provided.", packet)
            self.assertIn("Uncertainty:\n- Uncertainty assessments were not provided.", packet)
            self.assertLess(packet.index("Review focus:"), packet.index("Evidence:"))
            self.assertLess(packet.index("Evidence:"), packet.index("Uncertainty:"))
            self.assertLess(packet.index("Uncertainty:"), packet.index("Governance notes:"))

    def test_review_packet_renders_valid_evidence_and_uncertainty(self) -> None:
        class EvidenceProvider:
            name = "mock"

            def generate(self, *, skill_markdown: str, context: dict[str, object]) -> ProviderResponse:
                return ProviderResponse(
                    summary="Evidence-backed dry-run summary.",
                    extracted_facts=["claim-summary-001"],
                    review_focus=["Check the cited claim."],
                    score=0.8,
                    source_records=[valid_source_record()],
                    evidence_links=[valid_evidence_link()],
                    uncertainty_assessments=[valid_uncertainty_assessment()],
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runner = GovernedSkillRunner(tmp_path, provider=EvidenceProvider())

            result = runner.run(skill_path)

            packet = result.review_packet_path.read_text(encoding="utf-8")
            self.assertIn("Sources:", packet)
            self.assertIn("`src-review-packet`: Sample review packet", packet)
            self.assertIn("Evidence links:", packet)
            self.assertIn("`ev-001`: `supports` claim `claim-summary-001`", packet)
            self.assertIn("`unc-001` for claim `claim-summary-001`: overall medium (0.6).", packet)
            self.assertIn("corroboration single_source (0.3, 1 independent source(s))", packet)

    def test_invalid_evidence_contract_fails_before_writes(self) -> None:
        class InvalidEvidenceProvider:
            name = "mock"

            def generate(self, *, skill_markdown: str, context: dict[str, object]) -> ProviderResponse:
                link = valid_evidence_link()
                link["source_id"] = "src-missing"
                return ProviderResponse(
                    summary="Invalid evidence summary.",
                    extracted_facts=[],
                    review_focus=[],
                    score=0.2,
                    source_records=[valid_source_record()],
                    evidence_links=[link],
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)

            with self.assertRaisesRegex(RunnerError, "Evidence contract validation failed"):
                GovernedSkillRunner(tmp_path, provider=InvalidEvidenceProvider()).run(skill_path)

            run_dirs = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
            self.assertEqual(run_dirs, [])

    def test_skill_path_must_stay_inside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            tmp_path = Path(tmp)
            self._seed_project(tmp_path)
            outside_skill = Path(outside) / "outside.md"
            outside_skill.write_text("# Outside\n\nShould not be read.\n", encoding="utf-8")

            runner = GovernedSkillRunner(tmp_path)

            with self.assertRaisesRegex(RunnerError, "within the project root"):
                runner.run(outside_skill)

    def test_missing_skill_file_raises_runner_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_project(tmp_path)
            runner = GovernedSkillRunner(tmp_path)

            with self.assertRaisesRegex(RunnerError, "Skill file does not exist"):
                runner.run("tests/fixtures/runner/missing.md")

    def test_malformed_prior_run_state_is_reported_without_parent_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            runs_dir = tmp_path / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            (runs_dir / "run_state.json").write_text("{not-json", encoding="utf-8")

            result = GovernedSkillRunner(tmp_path).run(skill_path)

            run_state = json.loads(result.run_state_path.read_text(encoding="utf-8"))
            eval_result = json.loads(result.eval_result_path.read_text(encoding="utf-8"))
            checks = {item["name"]: item["passed"] for item in eval_result["checks"]}
            self.assertNotIn("parent_run_id", run_state)
            self.assertFalse(checks["prior_run_state_readable"])

    def test_malformed_memory_checkpoint_is_skipped_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            memory_dir = tmp_path / "memory_checkpoints"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "bad.json").write_text("[", encoding="utf-8")

            result = GovernedSkillRunner(tmp_path).run(skill_path)

            checkpoint = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))
            eval_result = json.loads(result.eval_result_path.read_text(encoding="utf-8"))
            checks = {item["name"]: item["passed"] for item in eval_result["checks"]}
            self.assertEqual(checkpoint["recent_memory_inputs"], [])
            self.assertFalse(checks["memory_checkpoints_readable"])

    def test_schema_validation_failure_raises_runner_error_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            schema_path = tmp_path / "schemas" / "eval_result.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            schema["properties"]["provider"]["const"] = "other"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            with self.assertRaisesRegex(RunnerError, "Artifact validation failed for eval_result.schema.json"):
                GovernedSkillRunner(tmp_path).run(skill_path)

            run_dirs = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
            self.assertEqual(run_dirs, [])

    def test_run_id_collision_gets_deterministic_suffix(self) -> None:
        class FixedStampRunner(GovernedSkillRunner):
            def _run_id_stamp(self) -> str:
                return "20260704T000000Z"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_path = self._seed_project(tmp_path)
            existing = tmp_path / "runs" / "20260704T000000Z-sample-osint-forge-skill"
            existing.mkdir(parents=True, exist_ok=True)

            result = FixedStampRunner(tmp_path).run(skill_path)

            self.assertEqual(result.run_id, "20260704T000000Z-sample-osint-forge-skill-2")

    def _seed_schemas(self, tmp_path: Path) -> None:
        source_root = Path(__file__).resolve().parent.parent / "schemas"
        target_root = tmp_path / "schemas"
        target_root.mkdir(parents=True, exist_ok=True)
        for schema_path in source_root.glob("*.json"):
            target = target_root / schema_path.name
            target.write_text(schema_path.read_text(encoding="utf-8"), encoding="utf-8")

    def _seed_project(self, tmp_path: Path) -> Path:
        self._seed_schemas(tmp_path)
        skill_path = tmp_path / FIXTURE_SKILL
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        source_skill = Path(__file__).resolve().parent / "fixtures" / "runner" / "sample_skill.md"
        skill_path.write_text(source_skill.read_text(encoding="utf-8"), encoding="utf-8")
        return skill_path


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


if __name__ == "__main__":
    unittest.main()
