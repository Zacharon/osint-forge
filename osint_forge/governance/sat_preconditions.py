"""Fail-closed SAT and review preconditions for report generation."""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ReportPreconditionError(Exception):
    """Raised when report generation must be blocked."""


class ReviewVerifier(Protocol):
    def verify_approved_artifact(self, mission_id: str, artifact_path: str, artifact_hash: str) -> bool:
        ...


@dataclass(frozen=True)
class ArtifactRequirement:
    artifact_path: str
    artifact_type: str
    must_be_reviewed: bool = False
    min_bytes: int = 1
    content_kind: str | None = None


@dataclass(frozen=True)
class PreconditionsResult:
    ok: bool
    checked: list[str] = field(default_factory=list)


DEFAULT_REPORT_REQUIREMENTS = [
    ArtifactRequirement(
        artifact_path="output/sat/ach-evidence-matrix.md",
        artifact_type="sat_ach",
        must_be_reviewed=True,
        min_bytes=50,
        content_kind="ach",
    ),
    ArtifactRequirement(
        artifact_path="output/sat/key-assumptions-challenge-log.md",
        artifact_type="sat_key_assumptions",
        min_bytes=20,
    ),
    ArtifactRequirement(
        artifact_path="output/sat/devil-advocate-counterarguments.md",
        artifact_type="sat_devils_advocate",
        must_be_reviewed=True,
        min_bytes=20,
    ),
    ArtifactRequirement(
        artifact_path="output/sat/uncertainty-notes.md",
        artifact_type="sat_uncertainty",
        min_bytes=20,
        content_kind="uncertainty",
    ),
]


def _count_ach_hypotheses(content: str) -> int:
    heading_count = len(re.findall(r"(?im)^\s*#{1,6}\s*H(?:ypothesis)?\s*\d+\b", content))
    table_labels = set(re.findall(r"(?i)\bH(?:ypothesis)?\s*([0-9]+)\b", content))
    return max(heading_count, len(table_labels))


def validate_artifact_content(path: Path, content_kind: str | None) -> None:
    content = path.read_text(encoding="utf-8")
    if content_kind == "ach" and _count_ach_hypotheses(content) < 2:
        raise ReportPreconditionError("ACH content invalid: at least two hypotheses are required")
    if content_kind == "uncertainty":
        lowered = content.lower()
        if "high confidence" in lowered and ("single source" in lowered or "one source" in lowered):
            raise ReportPreconditionError("uncertainty content invalid: single-source claims cannot be high confidence")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_report_preconditions(
    *,
    mission_id: str,
    workspace_root: Path,
    store: ReviewVerifier,
    artifact_hashes: dict[str, str],
    requirements: list[ArtifactRequirement] | None = None,
) -> PreconditionsResult:
    checked: list[str] = []
    root = workspace_root.resolve()
    for req in requirements or DEFAULT_REPORT_REQUIREMENTS:
        path = (root / req.artifact_path).resolve()
        if path != root and root not in path.parents:
            raise ReportPreconditionError(f"artifact path escapes workspace: {req.artifact_path}")
        if not path.exists():
            raise ReportPreconditionError(f"required artifact missing: {req.artifact_path}")
        if path.stat().st_size < req.min_bytes:
            raise ReportPreconditionError(f"required artifact too small: {req.artifact_path}")

        validate_artifact_content(path, req.content_kind)

        artifact_hash = artifact_hashes.get(req.artifact_path)
        if not artifact_hash:
            raise ReportPreconditionError(f"artifact hash missing from mission state: {req.artifact_path}")
        current_hash = _sha256_file(path)
        if current_hash != artifact_hash:
            raise ReportPreconditionError(f"artifact hash mismatch: {req.artifact_path}")
        if req.must_be_reviewed and not store.verify_approved_artifact(mission_id, req.artifact_path, artifact_hash):
            raise ReportPreconditionError(f"artifact is not approved for current hash: {req.artifact_path}")
        checked.append(req.artifact_path)
    return PreconditionsResult(ok=True, checked=checked)
