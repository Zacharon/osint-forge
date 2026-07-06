"""Governed dry-run harness for turning a skill markdown file into a reviewable run."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.ledger import ArtifactLedger, ArtifactLedgerError
from runner.provider import MockLLMProvider
from runner.schema_tools import load_schema, validate, validate_evidence_contract_bundle


class RunnerError(ValueError):
    """Raised when the dry-run harness cannot safely continue."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "skill"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_dir: Path
    run_state_path: Path
    checkpoint_path: Path
    eval_result_path: Path
    review_packet_path: Path
    artifact_summary_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "run_state_path": str(self.run_state_path),
            "checkpoint_path": str(self.checkpoint_path),
            "eval_result_path": str(self.eval_result_path),
            "review_packet_path": str(self.review_packet_path),
            "artifact_summary_path": str(self.artifact_summary_path),
        }


class GovernedSkillRunner:
    def __init__(self, project_root: str | Path, *, provider: MockLLMProvider | None = None) -> None:
        try:
            self.project_root = Path(project_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RunnerError(f"Project root does not exist or is not accessible: {project_root}") from exc
        if not self.project_root.is_dir():
            raise RunnerError(f"Project root must be a directory: {self.project_root}")
        self.runner_root = self.project_root / "runner"
        self.schemas_root = self.project_root / "schemas"
        self.runs_root = self.project_root / "runs"
        self.memory_root = self.project_root / "memory_checkpoints"
        self.docs_root = self.project_root / "docs"
        self.provider = provider or MockLLMProvider()
        self.ledger = ArtifactLedger(self.project_root)

    def run(self, skill_path: str | Path, *, allow_live_ingestion: bool = False) -> RunResult:
        self._ensure_directories()
        skill_file = self._resolve_skill_path(skill_path)
        try:
            skill_markdown = skill_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RunnerError(f"Skill file must be UTF-8 text: {skill_file}") from exc
        prior_state_path, prior_state, prior_state_ok = self._load_prior_run_state()
        recent_memory, memory_checkpoints_ok = self._load_recent_memory_checkpoints()
        skill_name = self._extract_skill_name(skill_file, skill_markdown)
        run_id = self._build_unique_run_id(skill_name)
        run_dir = self.runs_root / run_id

        # The harness records the CLI option at the boundary but always keeps
        # generated artifacts in review-only, no-live-ingestion mode.
        live_ingestion_enabled = False

        provider_response = self.provider.generate(
            skill_markdown=skill_markdown,
            context={
                "prior_state": prior_state,
                "recent_memory": recent_memory,
                "live_ingestion_enabled": live_ingestion_enabled,
            },
        )

        timestamp = utc_now()
        checkpoint_payload = {
            "schema_version": "1.0",
            "checkpoint_id": f"checkpoint-{run_id}",
            "run_id": run_id,
            "created_at": timestamp,
            "status": "needs_review",
            "skill_name": skill_name,
            "live_ingestion_enabled": live_ingestion_enabled,
            "recent_memory_inputs": recent_memory,
            "extracted_facts": provider_response.extracted_facts,
            "next_review_action": "Human review required before any governed promotion or execution.",
        }

        run_state_payload = {
            "schema_version": "1.0",
            "state_scope": "run",
            "run_id": run_id,
            "skill_name": skill_name,
            "skill_path": str(skill_file),
            "status": "needs_review",
            "review_required": True,
            "provider": self.provider.name,
            "created_at": timestamp,
            "updated_at": timestamp,
            "live_ingestion_enabled": live_ingestion_enabled,
            "live_ingestion_attempted": False,
            "recent_memory_count": len(recent_memory),
            "memory_checkpoint_path": str(run_dir / "memory_checkpoint.json"),
            "review_packet_path": str(run_dir / "review_packet.md"),
            "eval_result_path": str(run_dir / "eval_result.json"),
            "artifact_summary_path": str(run_dir / "artifact_summary.md"),
            "skill_sha256": sha256_text(skill_markdown),
            "summary": provider_response.summary,
        }
        if prior_state_path:
            run_state_payload["source_run_state_path"] = str(prior_state_path)
        parent_run_id = prior_state.get("run_id") if isinstance(prior_state, dict) else None
        if parent_run_id:
            run_state_payload["parent_run_id"] = str(parent_run_id)

        eval_payload = {
            "schema_version": "1.0",
            "run_id": run_id,
            "provider": self.provider.name,
            "status": "needs_review",
            "passed": False,
            "score": provider_response.score,
            "live_ingestion_default_respected": not live_ingestion_enabled,
            "live_ingestion_attempted": False,
            "checks": [
                {"name": "skill_loaded", "passed": True},
                {"name": "prior_run_state_readable", "passed": prior_state_ok},
                {"name": "memory_checkpoints_readable", "passed": memory_checkpoints_ok},
                {"name": "review_gate_enforced", "passed": True},
                {"name": "no_live_default_behavior", "passed": True},
            ],
            "notes": [
                "This harness is dry-run only.",
                "Run status remains needs_review until a human approves next steps.",
                "Live ingestion is disabled by default and not attempted here.",
            ],
        }

        evidence_bundle = self._evidence_bundle_from_response(provider_response)

        self._validate_payload("run_state.schema.json", run_state_payload)
        self._validate_payload("memory_checkpoint.schema.json", checkpoint_payload)
        self._validate_payload("eval_result.schema.json", eval_payload)
        self._validate_evidence_contracts(evidence_bundle)

        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunnerError(f"Run directory already exists after collision check: {run_dir}") from exc

        run_state_path = run_dir / "run_state.json"
        checkpoint_path = run_dir / "memory_checkpoint.json"
        eval_path = run_dir / "eval_result.json"
        review_packet_path = run_dir / "review_packet.md"
        artifact_summary_path = run_dir / "artifact_summary.md"

        self._write_json(run_state_path, run_state_payload)
        self._write_json(checkpoint_path, checkpoint_payload)
        self._write_json(eval_path, eval_payload)
        review_packet_path.write_text(
            self._render_review_packet(run_state_payload, provider_response.review_focus, evidence_bundle=evidence_bundle),
            encoding="utf-8",
        )

        canonical_run_state = dict(run_state_payload)
        canonical_run_state["state_scope"] = "project_current"
        self._write_json(self.runs_root / "run_state.json", canonical_run_state)
        self._write_json(self.memory_root / f"{run_id}.json", checkpoint_payload)

        artifact_summary_path.write_text(
            self._render_artifact_summary(
                run_id=run_id,
                run_state_path=run_state_path,
                checkpoint_path=checkpoint_path,
                review_packet_path=review_packet_path,
                eval_path=eval_path,
            ),
            encoding="utf-8",
        )

        try:
            self._append_dry_run_ledger(
                run_id=run_id,
                recorded_at=timestamp,
                skill_file=skill_file,
                allow_live_ingestion=allow_live_ingestion,
                live_ingestion_enabled=live_ingestion_enabled,
                run_state_path=run_state_path,
                checkpoint_path=checkpoint_path,
                eval_path=eval_path,
                review_packet_path=review_packet_path,
                prior_state=prior_state,
                run_state=run_state_payload,
            )
        except ArtifactLedgerError as exc:
            raise RunnerError(f"Artifact ledger append failed: {exc}") from exc

        return RunResult(
            run_id=run_id,
            run_dir=run_dir,
            run_state_path=run_state_path,
            checkpoint_path=checkpoint_path,
            eval_result_path=eval_path,
            review_packet_path=review_packet_path,
            artifact_summary_path=artifact_summary_path,
        )

    def _append_dry_run_ledger(
        self,
        *,
        run_id: str,
        recorded_at: str,
        skill_file: Path,
        allow_live_ingestion: bool,
        live_ingestion_enabled: bool,
        run_state_path: Path,
        checkpoint_path: Path,
        eval_path: Path,
        review_packet_path: Path,
        prior_state: dict[str, Any],
        run_state: dict[str, Any],
    ) -> None:
        prior_status = prior_state.get("status") if isinstance(prior_state, dict) else None
        records = [
            self.ledger.logical_record(
                record_type="ExecutionContract",
                run_id=run_id,
                recorded_at=recorded_at,
                payload={
                    "skill_path": str(skill_file.relative_to(self.project_root)),
                    "skill_sha256": run_state["skill_sha256"],
                    "provider": self.provider.name,
                    "allow_live_ingestion_requested": allow_live_ingestion,
                    "live_ingestion_enabled": live_ingestion_enabled,
                    "live_ingestion_attempted": False,
                    "review_required": True,
                },
            ),
            self.ledger.artifact_record(
                record_type="RunState",
                run_id=run_id,
                recorded_at=recorded_at,
                artifact_path=run_state_path,
                metadata={"state_scope": "run", "status": run_state["status"]},
            ),
            self.ledger.artifact_record(
                record_type="MemoryCheckpoint",
                run_id=run_id,
                recorded_at=recorded_at,
                artifact_path=checkpoint_path,
                metadata={"status": "needs_review"},
            ),
            self.ledger.artifact_record(
                record_type="EvalResult",
                run_id=run_id,
                recorded_at=recorded_at,
                artifact_path=eval_path,
                metadata={"status": "needs_review", "passed": False},
            ),
            self.ledger.artifact_record(
                record_type="ReviewPacket",
                run_id=run_id,
                recorded_at=recorded_at,
                artifact_path=review_packet_path,
                metadata={"status": "needs_review"},
            ),
            self.ledger.logical_record(
                record_type="StateTransition",
                run_id=run_id,
                recorded_at=recorded_at,
                payload={
                    "from_status": prior_status or "none",
                    "to_status": "needs_review",
                    "reason": "dry_run_created",
                    "live_ingestion_enabled": live_ingestion_enabled,
                    "live_ingestion_attempted": False,
                },
            ),
        ]
        self.ledger.append_records(records)

    def _ensure_directories(self) -> None:
        if not self.schemas_root.is_dir():
            raise RunnerError(f"Project root is missing schemas directory: {self.schemas_root}")
        for path in (self.runs_root, self.memory_root):
            path.mkdir(parents=True, exist_ok=True)

    def _resolve_skill_path(self, skill_path: str | Path) -> Path:
        raw_path = Path(skill_path).expanduser()
        candidate = raw_path if raw_path.is_absolute() else self.project_root / raw_path
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise RunnerError(f"Skill file does not exist or is not accessible: {candidate}") from exc
        if not resolved.is_file():
            raise RunnerError(f"Skill path must be a file: {resolved}")
        if resolved.suffix.lower() not in {".md", ".markdown"}:
            raise RunnerError(f"Skill file must be markdown: {resolved}")
        if not resolved.is_relative_to(self.project_root):
            raise RunnerError(f"Skill file must stay within the project root: {resolved}")
        return resolved

    def _load_prior_run_state(self) -> tuple[Path | None, dict[str, Any], bool]:
        candidates = [self.project_root / "run_state.json", self.runs_root / "run_state.json"]
        for candidate in candidates:
            if candidate.exists():
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    return candidate, {}, False
                if not isinstance(payload, dict):
                    return candidate, {}, False
                return candidate, payload, True
        return None, {}, True

    def _load_recent_memory_checkpoints(self, limit: int = 5) -> tuple[list[dict[str, Any]], bool]:
        if not self.memory_root.exists():
            return [], True
        checkpoints: list[dict[str, Any]] = []
        all_readable = True
        files = sorted(self.memory_root.glob("*.json"), key=self._checkpoint_mtime, reverse=True)
        for path in files[:limit]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                all_readable = False
                continue
            if not isinstance(payload, dict):
                all_readable = False
                continue
            checkpoints.append(
                {
                    "path": str(path),
                    "run_id": payload.get("run_id"),
                    "status": payload.get("status"),
                    "skill_name": payload.get("skill_name"),
                    "created_at": payload.get("created_at"),
                }
            )
        return checkpoints, all_readable

    def _checkpoint_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _extract_skill_name(self, skill_file: Path, skill_markdown: str) -> str:
        for line in skill_markdown.splitlines():
            if line.startswith("#"):
                return line.lstrip("# ").strip() or skill_file.stem
        return skill_file.stem

    def _run_id_stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _build_run_id(self, skill_name: str, suffix: int = 0) -> str:
        suffix_text = "" if suffix == 0 else f"-{suffix + 1}"
        return f"{self._run_id_stamp()}-{slugify(skill_name)[:40]}{suffix_text}"

    def _build_unique_run_id(self, skill_name: str) -> str:
        for suffix in range(100):
            run_id = self._build_run_id(skill_name, suffix=suffix)
            if not (self.runs_root / run_id).exists():
                return run_id
        raise RunnerError("Unable to allocate a unique run ID after 100 attempts")

    def _validate_payload(self, schema_name: str, payload: dict[str, Any]) -> None:
        try:
            schema = load_schema(self.schemas_root, schema_name)
            validate(schema, payload)
        except Exception as exc:
            raise RunnerError(f"Artifact validation failed for {schema_name}: {exc}") from exc

    def _evidence_bundle_from_response(self, provider_response: Any) -> dict[str, list[dict[str, Any]]]:
        return {
            "source_records": self._payload_list(provider_response, "source_records"),
            "evidence_links": self._payload_list(provider_response, "evidence_links"),
            "uncertainty_assessments": self._payload_list(provider_response, "uncertainty_assessments"),
        }

    def _payload_list(self, provider_response: Any, field_name: str) -> list[dict[str, Any]]:
        value = getattr(provider_response, field_name, [])
        if value is None:
            return []
        if not isinstance(value, list):
            raise RunnerError(f"Provider response field {field_name} must be a list")
        return value

    def _validate_evidence_contracts(self, evidence_bundle: dict[str, list[dict[str, Any]]]) -> None:
        try:
            validate_evidence_contract_bundle(
                self.schemas_root,
                source_records=evidence_bundle["source_records"],
                evidence_links=evidence_bundle["evidence_links"],
                uncertainty_assessments=evidence_bundle["uncertainty_assessments"],
            )
        except Exception as exc:
            raise RunnerError(f"Evidence contract validation failed: {exc}") from exc

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _render_review_packet(
        self,
        run_state: dict[str, Any],
        review_focus: list[str],
        *,
        evidence_bundle: dict[str, list[dict[str, Any]]] | None = None,
    ) -> str:
        review_lines = "\n".join(f"- {item}" for item in review_focus)
        evidence_bundle = evidence_bundle or {"source_records": [], "evidence_links": [], "uncertainty_assessments": []}
        evidence_section = self._render_evidence_section(
            evidence_bundle["source_records"],
            evidence_bundle["evidence_links"],
        )
        uncertainty_section = self._render_uncertainty_section(evidence_bundle["uncertainty_assessments"])
        return (
            f"# Review Packet\n\n"
            f"Run ID: `{run_state['run_id']}`\n\n"
            f"Status: `{run_state['status']}`\n\n"
            f"Skill: `{run_state['skill_name']}`\n\n"
            f"Summary:\n{run_state['summary']}\n\n"
            f"Review focus:\n{review_lines}\n\n"
            f"{evidence_section}\n"
            f"{uncertainty_section}\n"
            f"Governance notes:\n"
            f"- Human review is required before this run can be promoted.\n"
            f"- Live ingestion was not attempted.\n"
            f"- This harness does not claim production readiness.\n"
        )

    def _render_evidence_section(
        self,
        source_records: list[dict[str, Any]],
        evidence_links: list[dict[str, Any]],
    ) -> str:
        lines = ["Evidence:"]
        if source_records:
            lines.append("Sources:")
            for source in source_records:
                live_ingestion = "yes" if source.get("live_ingestion_used") else "no"
                lines.append(
                    "- "
                    f"`{source.get('source_id')}`: {source.get('title')} "
                    f"({source.get('source_kind')}, {source.get('access_method')}; "
                    f"live ingestion: {live_ingestion}) - {source.get('locator')} "
                    f"[retrieved {source.get('retrieved_at')}]"
                )
        if not evidence_links:
            lines.append("- Evidence links were not provided.")
            return "\n".join(lines) + "\n\n"

        lines.append("Evidence links:")
        for link in evidence_links:
            reference = link.get("reference", {})
            lines.append(
                "- "
                f"`{link.get('evidence_link_id')}`: `{link.get('relationship')}` "
                f"claim `{link.get('claim_id')}` in `{link.get('output_artifact_id')}` "
                f"using source `{link.get('source_id')}`."
            )
            lines.append(
                f"  Reference: {reference.get('title')} - {reference.get('locator')} "
                f"[retrieved {reference.get('retrieved_at')}]"
            )
            extracted_text = link.get("quoted_or_extracted_text")
            if extracted_text:
                lines.append(f"  Extract: {extracted_text}")
            notes = link.get("notes") or []
            if notes:
                lines.append(f"  Notes: {'; '.join(str(note) for note in notes)}")
        return "\n".join(lines) + "\n\n"

    def _render_uncertainty_section(self, uncertainty_assessments: list[dict[str, Any]]) -> str:
        lines = ["Uncertainty:"]
        if not uncertainty_assessments:
            lines.append("- Uncertainty assessments were not provided.")
            return "\n".join(lines) + "\n\n"

        for assessment in uncertainty_assessments:
            overall = assessment.get("overall_confidence", {})
            source = assessment.get("source_reliability", {})
            extraction = assessment.get("extraction_certainty", {})
            corroboration = assessment.get("corroboration_level", {})
            analyst = assessment.get("analyst_or_model_confidence", {})
            lines.append(
                "- "
                f"`{assessment.get('uncertainty_id')}` for "
                f"{assessment.get('target_type')} `{assessment.get('target_id')}`: "
                f"overall {overall.get('band')} ({overall.get('score')})."
            )
            lines.append(f"  Rationale: {assessment.get('rationale')}")
            lines.append(
                "  Dimensions: "
                f"source reliability {source.get('band')} ({source.get('score')}); "
                f"extraction certainty {extraction.get('band')} ({extraction.get('score')}); "
                f"corroboration {corroboration.get('band')} "
                f"({corroboration.get('score')}, {corroboration.get('independent_source_count')} independent source(s)); "
                f"analyst/model confidence {analyst.get('band')} ({analyst.get('score')})."
            )
        return "\n".join(lines) + "\n\n"

    def _render_artifact_summary(
        self,
        *,
        run_id: str,
        run_state_path: Path,
        checkpoint_path: Path,
        review_packet_path: Path,
        eval_path: Path,
    ) -> str:
        artifact_rows = [
            ("run_state.json", run_state_path),
            ("memory_checkpoint.json", checkpoint_path),
            ("review_packet.md", review_packet_path),
            ("eval_result.json", eval_path),
        ]
        lines = [
            "# Artifact Summary",
            "",
            f"Run ID: `{run_id}`",
            "",
            "| Artifact | SHA256 |",
            "|---|---|",
        ]
        for label, path in artifact_rows:
            lines.append(f"| `{label}` | `{sha256_file(path)}` |")
        lines.extend(
            [
                "",
                "Review state: `needs_review`",
                "Live ingestion default: `disabled`",
            ]
        )
        return "\n".join(lines) + "\n"
