"""Tamper-resistant human review gate."""

from __future__ import annotations

from dataclasses import dataclass

from osint_forge.orchestrator.mission_store import MissionStore


@dataclass(frozen=True)
class ReviewRequest:
    mission_id: str
    artifact_type: str
    artifact_path: str
    artifact_hash: str


class HumanReviewGate:
    def __init__(self, store: MissionStore, default_expiry_seconds: int = 86_400) -> None:
        self.store = store
        self.default_expiry_seconds = default_expiry_seconds

    def request(self, request: ReviewRequest) -> None:
        self.store.record_artifact(
            request.mission_id,
            request.artifact_type,
            request.artifact_path,
            request.artifact_hash,
        )
        inserted = self.store.request_review(
            request.mission_id,
            request.artifact_type,
            request.artifact_path,
            request.artifact_hash,
            expires_in_seconds=self.default_expiry_seconds,
        )
        if inserted:
            self.store.log_event(
                request.mission_id,
                "REVIEW_REQUESTED",
                {
                    "artifact_type": request.artifact_type,
                    "artifact_path": request.artifact_path,
                    "artifact_hash": request.artifact_hash,
                },
                event_key=f"{request.artifact_type}:{request.artifact_path}:{request.artifact_hash}",
            )

    def approve(self, request: ReviewRequest, reviewer: str, reason: str, method: str = "api") -> bool:
        return self.store.update_review_status(
            mission_id=request.mission_id,
            artifact_path=request.artifact_path,
            artifact_hash=request.artifact_hash,
            status="APPROVED",
            reviewer=reviewer,
            reason=reason,
            review_method=method,
        )

    def reject(self, request: ReviewRequest, reviewer: str, reason: str, method: str = "api") -> bool:
        return self.store.update_review_status(
            mission_id=request.mission_id,
            artifact_path=request.artifact_path,
            artifact_hash=request.artifact_hash,
            status="REJECTED",
            reviewer=reviewer,
            reason=reason,
            review_method=method,
        )
