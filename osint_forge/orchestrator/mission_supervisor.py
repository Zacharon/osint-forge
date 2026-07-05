"""MissionSupervisor: the single orchestrator for governed OSINT missions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from osint_forge.analytics.evidence_linker import evidence_linker_tool
from osint_forge.analytics.source_ancestry import source_ancestry_tool
from osint_forge.governance.cost_budgeting import BudgetTracker
from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.governance.sat_preconditions import ReportPreconditionError, validate_report_preconditions
from osint_forge.governance.schema_validator import CaseFileManifest, FieldSpec, HandoffSchema
from osint_forge.ingestion.gdelt_connector import GDELTDryRunConnector
from osint_forge.orchestrator.mission_store import MissionStore
from osint_forge.orchestrator.tool_registry import GovernedToolError, ToolRegistry, ToolSpec
from osint_forge.orchestrator.workspace_jail import WorkspaceJail
from osint_forge.review.human_review import HumanReviewGate, ReviewRequest


class MissionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    READY_FOR_REPORT = "ready_for_report"
    COMPLETED = "completed"
    PII_REJECTED = "pii_rejected"
    GOVERNANCE_BLOCKED = "governance_blocked"
    RESUME_BLOCKED_TAMPERED = "resume_blocked_tampered"
    RESUME_BLOCKED_MISSING_ARTIFACT = "resume_blocked_missing_artifact"
    RESUME_BLOCKED_HASH_MISMATCH = "resume_blocked_hash_mismatch"


@dataclass
class MissionContext:
    mission_id: str
    user_query: str
    status: MissionStatus = MissionStatus.CREATED
    completed_tools: list[str] = field(default_factory=list)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    pending_reviews: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "status": self.status.value,
            "completed_tools": list(self.completed_tools),
            "artifact_hashes": dict(self.artifact_hashes),
            "pending_reviews": list(self.pending_reviews),
        }


class MissionSupervisor:
    """Owns mission orchestration, governance gates, persistence, and resumption."""

    def __init__(
        self,
        mission_id: str,
        user_query: str,
        *,
        workspace_root: str | Path | None = None,
        db_path: str | Path = "osint_forge.db",
        sanitizer: PIISanitizer | None = None,
        store: MissionStore | None = None,
        gdelt_fixture_dir: str | Path | None = None,
    ) -> None:
        self.context = MissionContext(mission_id=mission_id, user_query=user_query)
        self.workspace = WorkspaceJail(workspace_root or Path("workspace") / mission_id)
        self.store = store or MissionStore(db_path)
        self.pii = sanitizer or PIISanitizer(use_spacy=False)
        self.gdelt_fixture_dir = Path(gdelt_fixture_dir or Path("tests") / "fixtures" / "gdelt")
        self.budget = BudgetTracker()
        self.manifest = CaseFileManifest(mission_id)
        self._register_schemas()
        self.manifest.lock()
        self.registry = ToolRegistry(self.pii, self.manifest, self.store)
        self.review_gate = HumanReviewGate(self.store)
        self._register_default_tools()

    def _register_schemas(self) -> None:
        schemas = [
            HandoffSchema(
                name="collector_output",
                fields={
                    "raw_findings": FieldSpec("list"),
                    "source_hashes": FieldSpec("list"),
                    "ingestion_summary": FieldSpec("dict", required=False),
                },
            ),
            HandoffSchema(
                name="source_ancestry_output",
                fields={
                    "source_clusters": FieldSpec("list"),
                    "ancestry_links": FieldSpec("list"),
                    "summary": FieldSpec("dict"),
                },
            ),
            HandoffSchema(name="evidence_linker_output", fields={"evidence_links": FieldSpec("list")}),
            HandoffSchema(
                name="report_generator_output",
                fields={
                    "report_path": FieldSpec("str"),
                    "status": FieldSpec("str"),
                    "checked_artifacts": FieldSpec("list"),
                },
            ),
        ]
        for schema in schemas:
            self.manifest.register_schema(schema)

    def _register_default_tools(self) -> None:
        gdelt_connector = GDELTDryRunConnector(self.context.mission_id, self.store, self.pii)
        self.registry.register(
            ToolSpec(
                name="collector",
                func=gdelt_connector,
                output_schema="collector_output",
                risk_level="medium",
                timeout_seconds=10,
            )
        )
        self.registry.register(
            ToolSpec(
                name="source_ancestry",
                func=source_ancestry_tool,
                output_schema="source_ancestry_output",
                risk_level="medium",
                timeout_seconds=10,
            )
        )
        self.registry.register(
            ToolSpec(
                name="evidence_linker",
                func=evidence_linker_tool,
                output_schema="evidence_linker_output",
                risk_level="medium",
                timeout_seconds=10,
            )
        )

    async def run_dry_run(self) -> dict[str, Any]:
        existed = self.store.mission_exists(self.context.mission_id)
        self.store.create_mission(self.context.mission_id, self.context.user_query, status=MissionStatus.RUNNING.value)
        if existed:
            self.resume()
        query_check = self.pii.check(self.context.user_query, tool_risk_level="high")
        if not query_check.is_safe:
            self.context.status = MissionStatus.PII_REJECTED
            self.store.update_mission_status(self.context.mission_id, self.context.status.value)
            self.store.log_event(
                self.context.mission_id,
                "PII_REJECTED_USER_QUERY",
                {"reason": query_check.reason, "log_entry": query_check.log_entry},
            )
            self.save_checkpoint()
            return {"status": self.context.status.value, "reason": query_check.reason}

        self.context.status = MissionStatus.RUNNING
        collector = await self._run_step("collector", fixture_dir=str(self.gdelt_fixture_dir))
        ancestry = await self._run_step("source_ancestry", evidence_items=collector["raw_findings"])
        links = await self._run_step(
            "evidence_linker",
            raw_findings=collector["raw_findings"],
            source_clusters=ancestry["source_clusters"],
        )

        self._write_artifact("output/analytics/source-ancestry.json", "source_ancestry", ancestry)
        self._write_artifact("output/sat/evidence-links.json", "sat_evidence_links", links)
        self._write_sat_artifacts()
        self.context.status = MissionStatus.AWAITING_REVIEW
        self.store.update_mission_status(self.context.mission_id, self.context.status.value)
        self.save_checkpoint()
        return self.context.snapshot()

    async def _run_step(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        if self.store.get_tool_output(self.context.mission_id, tool_name, kwargs) is None:
            self.budget.record(tool_name, tokens=0, step=True)
        output = await self.registry.run_governed_tool(self.context.mission_id, tool_name, **kwargs)
        if tool_name not in self.context.completed_tools:
            self.context.completed_tools.append(tool_name)
        self.save_checkpoint()
        return output

    def _write_artifact(self, relative_path: str, artifact_type: str, payload: Any) -> str:
        if isinstance(payload, str):
            self.workspace.write_text(relative_path, payload)
        else:
            self.workspace.write_json(relative_path, payload)
        digest = self.workspace.sha256_file(relative_path)
        self.context.artifact_hashes[relative_path] = digest
        self.store.record_artifact(self.context.mission_id, artifact_type, relative_path, digest)
        return digest

    def _request_review(self, artifact_type: str, relative_path: str, digest: str) -> None:
        request = ReviewRequest(self.context.mission_id, artifact_type, relative_path, digest)
        self.review_gate.request(request)
        pending = {"artifact_type": artifact_type, "artifact_path": relative_path, "artifact_hash": digest}
        if pending not in self.context.pending_reviews:
            self.context.pending_reviews.append(pending)

    def _write_sat_artifacts(self) -> None:
        ach = """# ACH Evidence Matrix

## Hypothesis 1
Regional disruptions are materially increasing in the dry-run sample.

## Hypothesis 2
Observed signals reflect normal reporting variance in the dry-run sample.

| Evidence | Hypothesis 1 | Hypothesis 2 |
| --- | --- | --- |
| e1 | consistent | neutral |
| e2 | consistent | inconsistent |
"""
        key_assumptions = """# Key Assumptions Challenge Log

- Dry-run sample records are placeholders and must not be treated as live intelligence.
- Source ancestry clustering is deterministic and should be reviewed before external ingestion.
"""
        devil = """# Devil's Advocate Counterarguments

- The dry-run sample is intentionally synthetic and cannot support a real-world conclusion.
- Similar event language could reflect duplicated summaries rather than independent reporting.
"""
        uncertainty = """# Uncertainty Notes

Confidence is degraded because the collector is running in dry-run mode only. External ingestion remains disabled.
"""

        ach_hash = self._write_artifact("output/sat/ach-evidence-matrix.md", "sat_ach", ach)
        self._write_artifact("output/sat/key-assumptions-challenge-log.md", "sat_key_assumptions", key_assumptions)
        devil_hash = self._write_artifact("output/sat/devil-advocate-counterarguments.md", "sat_devils_advocate", devil)
        self._write_artifact("output/sat/uncertainty-notes.md", "sat_uncertainty", uncertainty)
        self._request_review("sat_ach", "output/sat/ach-evidence-matrix.md", ach_hash)
        self._request_review("sat_devils_advocate", "output/sat/devil-advocate-counterarguments.md", devil_hash)

    def validate_report_preconditions(self) -> dict[str, Any]:
        result = validate_report_preconditions(
            mission_id=self.context.mission_id,
            workspace_root=self.workspace.root,
            store=self.store,
            artifact_hashes=self.context.artifact_hashes,
        )
        self.context.status = MissionStatus.READY_FOR_REPORT
        self.store.update_mission_status(self.context.mission_id, self.context.status.value)
        self.save_checkpoint()
        return {"ok": result.ok, "checked_artifacts": result.checked}

    def generate_report(self) -> dict[str, Any]:
        preconditions = self.validate_report_preconditions()
        report = """# OSINT Forge Dry-Run Report

This report was generated only after SAT artifact existence, content validity, artifact hashes, and SQLite-backed review status passed.

## Finding
Dry-run execution completed successfully. No live external data was ingested.
"""
        self._write_artifact("output/final-report.md", "final_report", report)
        self.context.status = MissionStatus.COMPLETED
        self.store.update_mission_status(self.context.mission_id, self.context.status.value)
        self.save_checkpoint()
        return {
            "report_path": "output/final-report.md",
            "status": self.context.status.value,
            "checked_artifacts": preconditions["checked_artifacts"],
        }

    def save_checkpoint(self) -> None:
        self.store.save_state(
            mission_id=self.context.mission_id,
            state=self.context.snapshot(),
            budget=self.budget.snapshot(),
            manifest_hash=self.manifest.content_hash(),
            completed_tools=self.context.completed_tools,
            pending_reviews=self.context.pending_reviews,
        )

    def resume(self) -> dict[str, Any]:
        bundle = self.store.load_resume_bundle(self.context.mission_id)
        state = bundle.state.get("state", {})
        self.context.status = MissionStatus(state.get("status", bundle.mission["status"]))
        self.context.completed_tools = list(state.get("completed_tools", []))
        self.context.artifact_hashes = dict(state.get("artifact_hashes", {}))
        self.context.pending_reviews = list(state.get("pending_reviews", []))
        if bundle.state.get("budget"):
            self.budget = BudgetTracker.from_snapshot(bundle.state["budget"])

        self._reconcile_resume_artifacts(bundle)

        for review in bundle.reviews:
            if review["status"] != "APPROVED":
                continue
            relative_path = review["artifact_path"]
            try:
                current_hash = self.workspace.sha256_file(relative_path)
            except FileNotFoundError:
                current_hash = ""
            if current_hash != review["artifact_hash"]:
                self._block_resume(
                    "RESUME_BLOCKED_TAMPERED",
                    f"approved artifact hash mismatch: {relative_path}",
                    {"artifact_path": relative_path, "expected_hash": review["artifact_hash"], "current_hash": current_hash},
                )

        self.save_checkpoint()
        return self.context.snapshot()

    def _reconcile_resume_artifacts(self, bundle: Any) -> None:
        expected_manifest_hash = bundle.state.get("manifest_hash")
        current_manifest_hash = self.manifest.content_hash()
        if expected_manifest_hash and expected_manifest_hash != current_manifest_hash:
            self._block_resume(
                "RESUME_BLOCKED_HASH_MISMATCH",
                "manifest hash mismatch during resume",
                {"expected_manifest_hash": expected_manifest_hash, "current_manifest_hash": current_manifest_hash},
            )

        rows_by_path: dict[str, dict[str, Any]] = {}
        for row in bundle.artifacts:
            path = row["artifact_path"]
            previous = rows_by_path.get(path)
            if previous is not None and previous["artifact_hash"] != row["artifact_hash"]:
                self._block_resume(
                    "RESUME_BLOCKED_HASH_MISMATCH",
                    f"multiple hashes recorded for artifact path: {path}",
                    {"artifact_path": path, "hashes": sorted({previous["artifact_hash"], row["artifact_hash"]})},
                )
            rows_by_path[path] = row

        state_hashes = dict(self.context.artifact_hashes)
        for artifact_path, artifact_hash in state_hashes.items():
            row = rows_by_path.get(artifact_path)
            if row is None:
                self._block_resume(
                    "RESUME_BLOCKED_MISSING_ARTIFACT",
                    f"checkpoint artifact missing from SQLite artifact ledger: {artifact_path}",
                    {"artifact_path": artifact_path, "artifact_hash": artifact_hash},
                )
            if row["artifact_hash"] != artifact_hash:
                self._block_resume(
                    "RESUME_BLOCKED_HASH_MISMATCH",
                    f"checkpoint artifact hash differs from SQLite ledger: {artifact_path}",
                    {
                        "artifact_path": artifact_path,
                        "checkpoint_hash": artifact_hash,
                        "sqlite_hash": row["artifact_hash"],
                    },
                )

        for artifact_path, row in rows_by_path.items():
            path = self.workspace.resolve(artifact_path)
            if not path.exists():
                self._block_resume(
                    "RESUME_BLOCKED_MISSING_ARTIFACT",
                    f"artifact file missing on disk: {artifact_path}",
                    {"artifact_path": artifact_path, "artifact_hash": row["artifact_hash"]},
                )
            current_hash = self.workspace.sha256_file(artifact_path)
            if current_hash != row["artifact_hash"]:
                self._block_resume(
                    "RESUME_BLOCKED_HASH_MISMATCH",
                    f"artifact file hash mismatch: {artifact_path}",
                    {"artifact_path": artifact_path, "expected_hash": row["artifact_hash"], "current_hash": current_hash},
                )
            event_key = f"{artifact_path}:{row['artifact_hash']}"
            if not self.store.has_event(self.context.mission_id, "ARTIFACT_RECORDED", event_key):
                self._block_resume(
                    "RESUME_BLOCKED_HASH_MISMATCH",
                    f"artifact ledger missing action-log entry: {artifact_path}",
                    {"artifact_path": artifact_path, "artifact_hash": row["artifact_hash"], "missing_event_key": event_key},
                )

        expected_paths = set(rows_by_path)
        if self.workspace.root.exists():
            actual_paths = {
                path.relative_to(self.workspace.root).as_posix()
                for path in self.workspace.root.rglob("*")
                if path.is_file()
            }
            unexpected = sorted(actual_paths - expected_paths)
            if unexpected:
                self._block_resume(
                    "RESUME_BLOCKED_TAMPERED",
                    "unexpected artifact files found in workspace",
                    {"unexpected_artifacts": unexpected},
                )

    def _block_resume(self, event_type: str, message: str, metadata: dict[str, Any]) -> None:
        status_map = {
            "RESUME_BLOCKED_TAMPERED": MissionStatus.RESUME_BLOCKED_TAMPERED,
            "RESUME_BLOCKED_MISSING_ARTIFACT": MissionStatus.RESUME_BLOCKED_MISSING_ARTIFACT,
            "RESUME_BLOCKED_HASH_MISMATCH": MissionStatus.RESUME_BLOCKED_HASH_MISMATCH,
        }
        self.context.status = status_map.get(event_type, MissionStatus.RESUME_BLOCKED_TAMPERED)
        self.store.update_mission_status(self.context.mission_id, self.context.status.value)
        self.store.log_event(self.context.mission_id, event_type, metadata)
        self.save_checkpoint()
        raise ReportPreconditionError(message)

    async def shutdown(self) -> None:
        await self.registry.cancel_active()


async def run_dry_run_mission(
    mission_id: str,
    user_query: str,
    workspace_root: str | Path,
    db_path: str | Path,
    gdelt_fixture_dir: str | Path | None = None,
) -> dict[str, Any]:
    supervisor = MissionSupervisor(
        mission_id,
        user_query,
        workspace_root=workspace_root,
        db_path=db_path,
        gdelt_fixture_dir=gdelt_fixture_dir,
    )
    try:
        return await supervisor.run_dry_run()
    except GovernedToolError as exc:
        supervisor.context.status = MissionStatus.GOVERNANCE_BLOCKED
        supervisor.store.update_mission_status(supervisor.context.mission_id, supervisor.context.status.value)
        supervisor.store.log_event(supervisor.context.mission_id, "GOVERNANCE_BLOCKED", {"error": str(exc)})
        supervisor.save_checkpoint()
        return {"status": supervisor.context.status.value, "error": str(exc)}
    finally:
        supervisor.store.close()
