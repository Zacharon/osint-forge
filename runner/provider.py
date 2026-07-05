"""Mock-only provider layer for governed dry runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderResponse:
    summary: str
    extracted_facts: list[str]
    review_focus: list[str]
    score: float
    source_records: list[dict[str, Any]] = field(default_factory=list)
    evidence_links: list[dict[str, Any]] = field(default_factory=list)
    uncertainty_assessments: list[dict[str, Any]] = field(default_factory=list)


class MockLLMProvider:
    """Deterministic provider used for tests and local dry runs."""

    name = "mock"

    def generate(self, *, skill_markdown: str, context: dict[str, Any]) -> ProviderResponse:
        lines = [line.strip() for line in skill_markdown.splitlines() if line.strip()]
        heading = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), "Untitled Skill")
        first_body = next((line for line in lines if not line.startswith("#")), "No body content provided.")
        memory_count = len(context.get("recent_memory", []))
        summary = f"Dry-run execution prepared for '{heading}' using {memory_count} recent memory checkpoint(s)."
        extracted_facts = [
            f"skill_heading={heading}",
            f"body_preview={first_body[:120]}",
            f"live_ingestion_default={context.get('live_ingestion_enabled', False)}",
        ]
        review_focus = [
            "Confirm the skill scope matches the intended project task.",
            "Confirm no live ingestion or external source calls are needed before approval.",
            "Review the proposed next action and artifact summary before promotion.",
        ]
        return ProviderResponse(summary=summary, extracted_facts=extracted_facts, review_focus=review_focus, score=0.51)
