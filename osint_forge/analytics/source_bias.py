"""Creator/source bias indicator scaffold.

This module estimates public content-pattern indicators. It is not a truth
detector and must not be used for private-person dossiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from osint_forge.provenance import ProvenanceRef, utc_now


class SourceBiasEstimatorError(Exception):
    """Raised when a source-bias estimate would violate provenance or privacy rules."""


PROTECTED_CLASS_TERMS = {
    "race",
    "ethnicity",
    "religion",
    "sex",
    "gender",
    "sexual orientation",
    "disability",
    "national origin",
    "citizenship",
    "age",
    "pregnancy",
    "veteran",
    "health status",
}


@dataclass
class CreatorSourceProfile:
    profile_id: str
    public_handle_or_source_name: str
    platform: str
    observed_topics: list[str]
    stance_indicators: dict[str, float]
    citation_quality: float
    affiliation_signals: list[str]
    engagement_anomaly_signals: list[str]
    confidence: float
    provenance: list[ProvenanceRef]
    last_updated: str = field(default_factory=utc_now)
    privacy_notes: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id or not self.public_handle_or_source_name:
            raise SourceBiasEstimatorError("profile id and public source name are required")
        if not self.provenance:
            raise SourceBiasEstimatorError("creator/source profile requires provenance")
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]
        self.citation_quality = _clamp01(self.citation_quality)
        self.confidence = _clamp01(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "public_handle_or_source_name": self.public_handle_or_source_name,
            "platform": self.platform,
            "observed_topics": list(self.observed_topics),
            "stance_indicators": dict(self.stance_indicators),
            "citation_quality": self.citation_quality,
            "affiliation_signals": list(self.affiliation_signals),
            "engagement_anomaly_signals": list(self.engagement_anomaly_signals),
            "confidence": self.confidence,
            "provenance": [item.to_dict() for item in self.provenance],
            "last_updated": self.last_updated,
            "privacy_notes": self.privacy_notes,
        }


@dataclass(frozen=True)
class SourceBiasEstimate:
    profile_id: str
    indicators: dict[str, float]
    confidence: float
    explanation: dict[str, str]
    provenance: list[dict[str, Any]]
    disclaimer: str = "Estimated bias indicators from public content patterns; not a truth verdict."

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "indicators": dict(self.indicators),
            "confidence": self.confidence,
            "explanation": dict(self.explanation),
            "provenance": list(self.provenance),
            "disclaimer": self.disclaimer,
        }


class SourceBiasEstimator:
    def estimate(self, profile: CreatorSourceProfile, *, correction_history: float | None = None) -> SourceBiasEstimate:
        self._guard_profile(profile)
        correction_quality = _clamp01(correction_history) if correction_history is not None else 0.5
        topic_skew = _skew_score(profile.observed_topics)
        stance_distribution = _stance_concentration(profile.stance_indicators)
        emotional_language = _signal_score(profile.stance_indicators, {"anger", "fear", "outrage", "alarm"})
        affiliation = min(1.0, len(profile.affiliation_signals) / 5.0)
        engagement = min(1.0, len(profile.engagement_anomaly_signals) / 5.0)

        indicators = {
            "topic_skew": round(topic_skew, 3),
            "stance_distribution": round(stance_distribution, 3),
            "citation_quality": round(profile.citation_quality, 3),
            "correction_history": round(correction_quality, 3),
            "emotional_language": round(emotional_language, 3),
            "sponsorship_affiliation_signals": round(affiliation, 3),
            "engagement_anomaly_signals": round(engagement, 3),
            "confidence_level": round(profile.confidence, 3),
        }
        explanation = {
            "topic_skew": "Higher when observed public content clusters around few topics.",
            "stance_distribution": "Higher when supplied stance indicators concentrate in one direction.",
            "citation_quality": "Higher is better; based on cited-source quality signals supplied with provenance.",
            "correction_history": "Higher is better; neutral when no correction history is known.",
            "emotional_language": "Higher when public content-pattern indicators include emotionally loaded framing.",
            "sponsorship_affiliation_signals": "Higher when public affiliation/sponsorship signals are present.",
            "engagement_anomaly_signals": "Higher when public engagement anomalies are supplied with provenance.",
            "confidence_level": "Overall confidence supplied by the profile builder.",
        }
        return SourceBiasEstimate(
            profile_id=profile.profile_id,
            indicators=indicators,
            confidence=round(profile.confidence * min(1.0, len(profile.provenance) / 2.0), 3),
            explanation=explanation,
            provenance=[item.to_dict() for item in profile.provenance],
        )

    def _guard_profile(self, profile: CreatorSourceProfile) -> None:
        combined = " ".join(
            [
                profile.public_handle_or_source_name,
                profile.platform,
                " ".join(profile.observed_topics),
                " ".join(profile.stance_indicators),
                " ".join(profile.affiliation_signals),
                profile.privacy_notes,
            ]
        ).lower()
        if "private person" in combined or "private-person" in combined or "dossier" in combined:
            raise SourceBiasEstimatorError("private-person dossiers are prohibited")
        if any(term in combined for term in PROTECTED_CLASS_TERMS):
            raise SourceBiasEstimatorError("protected-class profiling is prohibited")
        if not profile.provenance:
            raise SourceBiasEstimatorError("every source bias score requires provenance")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _skew_score(topics: list[str]) -> float:
    if not topics:
        return 0.0
    unique = len(set(topic.lower() for topic in topics))
    return 1.0 - min(1.0, unique / max(1, len(topics)))


def _stance_concentration(stance: dict[str, float]) -> float:
    values = [abs(float(value)) for value in stance.values()]
    if not values:
        return 0.0
    return min(1.0, max(values))


def _signal_score(stance: dict[str, float], keys: set[str]) -> float:
    matched = [abs(float(value)) for key, value in stance.items() if key.lower() in keys]
    return min(1.0, sum(matched) / max(1, len(keys)))
