"""Review decision and execution-plan promotion for governed dry runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from runner.harness import sha256_file, utc_now
from runner.schema_tools import load_schema, validate


DECISIONS = ("approved", "rejected", "changes_requested")
TERMINAL_STATUSES = ("approved", "rejected")
SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def artifact_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class ReviewDecisionResult:
    run_id: str
    run_dir: Path
    run_state_path: Path
    review_decision_path: Path
    execution_plan_path: Path | None = None
    revision_note_path: Path | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "run_state_path": str(self.run_state_path),
            "review_decision_path": str(self.review_decision_path),
        }
        if self.execution_plan_path:
            payload["execution_plan_path"] = str(self.execution_plan_path)
        if self.revision_note_path:
            payload["revision_note_path"] = str(self.revision_note_path)
        return payload


class ReviewDecisionError(ValueError):
    """Raised when a review decision cannot be applied safely."""


class GovernedReviewPromoter:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.schemas_root = self.project_root / "schemas"
        self.runs_root = self.project_root / "runs"

    def decide(
        self,
        run_id: str,
        *,
        decision: str = "approved",
        reviewer: str = "human",
        note: str = "",
    ) -> ReviewDecisionResult:
        if decision not in DECISIONS:
            raise ReviewDecisionError(f"decision must be one of: {', '.join(DECISIONS)}")

        run_dir = self._resolve_run_dir(run_id)
        run_state_path = run_dir / "run_state.json"
        if not run_state_path.exists():
            raise ReviewDecisionError(f"run state not found for run_id={run_id!r}")

        run_state = self._read_json(run_state_path)
        if run_state.get("run_id") != run_id:
            raise ReviewDecisionError("run_state.json run_id does not match requested run_id")
        if run_state.get("live_ingestion_enabled") is not False:
            raise ReviewDecisionError("review promotion requires live_ingestion_enabled=false")
        if run_state.get("live_ingestion_attempted") is not False:
            raise ReviewDecisionError("review promotion refuses runs that attempted live ingestion")

        timestamp = utc_now()
        prior_status = str(run_state.get("status", "unknown"))
        self._validate_transition(prior_status=prior_status, decision=decision)
        decision_id = self._unique_artifact_id(run_dir, decision)
        review_decision_path = run_dir / f"{decision_id}.review_decision.json"
        execution_plan_path = run_dir / f"{decision_id}.execution_plan.json" if decision == "approved" else None
        revision_note_path = run_dir / f"{decision_id}.revision_note.md" if decision == "changes_requested" else None

        history_entry = {
            "decision_id": decision_id,
            "decision": decision,
            "reviewed_at": timestamp,
            "reviewer": reviewer,
            "note": note,
        }
        if execution_plan_path:
            history_entry["execution_plan_path"] = str(execution_plan_path)
        if revision_note_path:
            history_entry["revision_note_path"] = str(revision_note_path)

        review_history = self._existing_review_history(run_state)
        review_history.append(history_entry)

        review_decision_payload = {
            "schema_version": "1.0",
            "decision_id": decision_id,
            "run_id": run_id,
            "decision": decision,
            "reviewed_at": timestamp,
            "reviewer": reviewer,
            "note": note,
            "prior_status": prior_status,
            "resulting_status": decision,
            "live_ingestion_enabled": False,
            "live_ingestion_attempted": False,
            "creates_execution_plan": decision == "approved",
            "review_history": review_history,
        }
        if execution_plan_path:
            review_decision_payload["execution_plan_path"] = str(execution_plan_path)
        if revision_note_path:
            review_decision_payload["revision_note_path"] = str(revision_note_path)

        self._validate_payload("review_decision.schema.json", review_decision_payload)

        if execution_plan_path:
            execution_plan_payload = self._build_execution_plan(
                run_state=run_state,
                decision_id=decision_id,
                review_decision_path=review_decision_path,
                execution_plan_path=execution_plan_path,
                timestamp=timestamp,
            )
            self._validate_payload("execution_plan.schema.json", execution_plan_payload)
            self._write_json(execution_plan_path, execution_plan_payload)

        if revision_note_path:
            revision_note_path.write_text(
                self._render_revision_note(run_state=run_state, decision=review_decision_payload),
                encoding="utf-8",
            )

        self._write_json(review_decision_path, review_decision_payload)

        updated_run_state = dict(run_state)
        updated_run_state.update(
            {
                "status": decision,
                "updated_at": timestamp,
                "review_decision_path": str(review_decision_path),
                "review_history": review_history,
                "live_ingestion_enabled": False,
                "live_ingestion_attempted": False,
            }
        )
        if execution_plan_path:
            updated_run_state["execution_plan_path"] = str(execution_plan_path)
        if revision_note_path:
            updated_run_state["revision_note_path"] = str(revision_note_path)
        self._validate_payload("run_state.schema.json", updated_run_state)
        self._write_json(run_state_path, updated_run_state)
        self._update_project_current(run_id, updated_run_state)
        self._refresh_artifact_summary(run_state_path=run_state_path, run_state=updated_run_state)

        return ReviewDecisionResult(
            run_id=run_id,
            run_dir=run_dir,
            run_state_path=run_state_path,
            review_decision_path=review_decision_path,
            execution_plan_path=execution_plan_path,
            revision_note_path=revision_note_path,
        )

    def _resolve_run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id:
            raise ReviewDecisionError("run_id must be a non-empty folder name under runs/")
        if "/" in run_id or "\\" in run_id:
            raise ReviewDecisionError("run_id must not contain path separators")
        if ".." in run_id:
            raise ReviewDecisionError("run_id must not contain path traversal segments")
        if ":" in run_id:
            raise ReviewDecisionError("run_id must not contain drive prefixes or alternate path syntax")
        if Path(run_id).is_absolute() or PureWindowsPath(run_id).is_absolute() or PurePosixPath(run_id).is_absolute():
            raise ReviewDecisionError("run_id must be a folder name, not an absolute path")
        if PureWindowsPath(run_id).drive:
            raise ReviewDecisionError("run_id must not contain a drive prefix")
        if not SAFE_RUN_ID_RE.fullmatch(run_id):
            raise ReviewDecisionError("run_id must be a safe folder name under runs/")

        try:
            runs_root = self.runs_root.resolve(strict=True)
        except OSError as exc:
            raise ReviewDecisionError(f"runs directory not found or inaccessible: {self.runs_root}") from exc

        candidate = self.runs_root / run_id
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ReviewDecisionError(f"run folder not found for run_id={run_id!r}") from exc
        if not resolved.is_dir():
            raise ReviewDecisionError(f"run_id={run_id!r} is not a run folder")
        if resolved.parent != runs_root or not resolved.is_relative_to(runs_root):
            raise ReviewDecisionError("run_id must resolve to an existing folder directly under runs/")
        return resolved

    def _validate_transition(self, *, prior_status: str, decision: str) -> None:
        if prior_status == "needs_review":
            return
        if prior_status in TERMINAL_STATUSES:
            raise ReviewDecisionError(f"run status {prior_status!r} is terminal and cannot be changed")
        if prior_status == "changes_requested":
            raise ReviewDecisionError("changes_requested runs require a revised run before another decision")
        raise ReviewDecisionError(f"run status {prior_status!r} cannot receive review decision {decision!r}")

    def _build_execution_plan(
        self,
        *,
        run_state: dict[str, Any],
        decision_id: str,
        review_decision_path: Path,
        execution_plan_path: Path,
        timestamp: str,
    ) -> dict[str, Any]:
        run_id = str(run_state["run_id"])
        return {
            "schema_version": "1.0",
            "plan_id": f"plan-{decision_id}",
            "run_id": run_id,
            "created_at": timestamp,
            "status": "plan_ready",
            "execution_mode": "governed_plan_only",
            "source_run_state_path": str(execution_plan_path.parent / "run_state.json"),
            "source_review_decision_path": str(review_decision_path),
            "skill_name": str(run_state.get("skill_name", "")),
            "skill_path": str(run_state.get("skill_path", "")),
            "live_ingestion_enabled": False,
            "live_ingestion_attempted": False,
            "requires_human_execution_review": True,
            "steps": [
                {
                    "name": "confirm_scope",
                    "description": "Confirm the approved dry-run scope still matches the intended task.",
                    "status": "pending",
                },
                {
                    "name": "prepare_governed_execution",
                    "description": "Prepare any execution environment without enabling live ingestion.",
                    "status": "pending",
                },
                {
                    "name": "final_human_gate",
                    "description": "Require a separate human decision before any live work is attempted.",
                    "status": "pending",
                },
            ],
            "non_actions": [
                "No live ingestion is enabled by this plan.",
                "No OSINT Forge runtime pipeline is invoked by this plan.",
                "No API keys are required by this plan.",
            ],
        }

    def _render_revision_note(self, *, run_state: dict[str, Any], decision: dict[str, Any]) -> str:
        note = decision["note"] or "Reviewer requested changes before a governed execution plan can be created."
        return (
            "# Revision Note\n\n"
            f"Run ID: `{decision['run_id']}`\n\n"
            f"Skill: `{run_state.get('skill_name', '')}`\n\n"
            f"Decision: `{decision['decision']}`\n\n"
            f"Reviewer: `{decision['reviewer']}`\n\n"
            f"Requested change:\n{note}\n\n"
            "Governance notes:\n"
            "- No execution plan was created for this decision.\n"
            "- Live ingestion remains disabled.\n"
            "- A revised dry run should be reviewed before promotion.\n"
        )

    def _existing_review_history(self, run_state: dict[str, Any]) -> list[dict[str, Any]]:
        history = run_state.get("review_history")
        if isinstance(history, list):
            return [item for item in history if isinstance(item, dict)]
        return []

    def _unique_artifact_id(self, run_dir: Path, decision: str) -> str:
        base = f"{artifact_stamp()}-{decision}"
        candidate = base
        index = 2
        while (run_dir / f"{candidate}.review_decision.json").exists():
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def _update_project_current(self, run_id: str, run_state: dict[str, Any]) -> None:
        current_path = self.runs_root / "run_state.json"
        if not current_path.exists():
            return
        current_state = self._read_json(current_path)
        if current_state.get("run_id") != run_id:
            return
        canonical = dict(run_state)
        canonical["state_scope"] = "project_current"
        self._validate_payload("run_state.schema.json", canonical)
        self._write_json(current_path, canonical)

    def _refresh_artifact_summary(self, *, run_state_path: Path, run_state: dict[str, Any]) -> None:
        artifact_summary_value = run_state.get("artifact_summary_path")
        if not isinstance(artifact_summary_value, str) or not artifact_summary_value:
            return
        artifact_summary_path = self._resolve_artifact_path(artifact_summary_value)
        rows = [
            ("run_state.json", run_state_path),
            ("memory_checkpoint.json", self._resolve_artifact_path(str(run_state.get("memory_checkpoint_path", "")))),
            ("review_packet.md", self._resolve_artifact_path(str(run_state.get("review_packet_path", "")))),
            ("eval_result.json", self._resolve_artifact_path(str(run_state.get("eval_result_path", "")))),
        ]
        for field in ("review_decision_path", "execution_plan_path", "revision_note_path"):
            value = run_state.get(field)
            if isinstance(value, str) and value:
                path = self._resolve_artifact_path(value)
                rows.append((path.name, path))

        lines = [
            "# Artifact Summary",
            "",
            f"Run ID: `{run_state['run_id']}`",
            "",
            "| Artifact | SHA256 |",
            "|---|---|",
        ]
        for label, path in rows:
            if path.exists():
                lines.append(f"| `{label}` | `{sha256_file(path)}` |")
        lines.extend(
            [
                "",
                f"Review state: `{run_state['status']}`",
                "Live ingestion default: `disabled`",
            ]
        )
        artifact_summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _resolve_artifact_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.project_root / path

    def _validate_payload(self, schema_name: str, payload: dict[str, Any]) -> None:
        schema = load_schema(self.schemas_root, schema_name)
        validate(schema, payload)

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
