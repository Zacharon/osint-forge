"""Canonicalized PII and private-individual gate for OSINT Forge."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Literal


ToolRiskLevel = Literal["low", "medium", "high"]

HARD_BLOCKED_ATTRIBUTES = {
    "home address",
    "residential address",
    "home phone",
    "personal phone",
    "phone number",
    "telephone number",
    "financial account",
    "bank account",
    "credit card",
    "ssn",
    "social security number",
    "family members",
    "spouse",
    "children",
    "parents",
    "daily routine",
    "location pattern",
    "private residence",
    "personal email",
}

PUBLIC_FIGURE_ALLOWLIST = {
    "elon musk",
    "donald trump",
    "joe biden",
    "mark zuckerberg",
    "jeff bezos",
    "bill gates",
    "warren buffett",
    "oprah winfrey",
}

PUBLIC_CONDUCT_KEYWORDS = {
    "board decision",
    "company policy",
    "corporate policy",
    "government role",
    "hiring practices",
    "official capacity",
    "official statement",
    "press release",
    "public conduct",
    "public statement",
}

PERSON_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?![A-Za-z0-9])")
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"\bignore (?:all )?(?:previous|prior) instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard (?:the )?(?:system|developer|previous) instructions\b", re.IGNORECASE),
    re.compile(r"\breveal (?:the )?(?:system prompt|hidden prompt|developer message)\b", re.IGNORECASE),
    re.compile(r"\byou are now (?:in|a) developer mode\b", re.IGNORECASE),
]
ORG_HINTS = {
    "agency",
    "area",
    "association",
    "center",
    "company",
    "corp",
    "corporation",
    "county",
    "dataset",
    "department",
    "district",
    "feed",
    "fixture",
    "foundation",
    "institute",
    "laboratory",
    "llc",
    "ministry",
    "office",
    "organization",
    "port",
    "public",
    "synthetic",
    "university",
}


@dataclass(frozen=True)
class SanitizerResult:
    is_safe: bool
    reason: str
    canonical_text: str = ""
    log_entry: str | None = None
    detected_entities: list[str] = field(default_factory=list)
    blocked_attribute: str | None = None


class PIISanitizer:
    """Conservative gate invoked on user input, tool arguments, and tool outputs."""

    def __init__(self, use_spacy: bool = False) -> None:
        self.use_spacy = use_spacy
        self._nlp = None
        if use_spacy:
            try:
                import spacy  # type: ignore

                self._nlp = spacy.load("en_core_web_sm")
            except Exception:
                self.use_spacy = False

    def canonicalize(self, text: str) -> str:
        if not text:
            return ""
        decoded = urllib.parse.unquote(str(text))
        unescaped = html.unescape(decoded)
        normalized = unicodedata.normalize("NFKC", unescaped)
        return normalized.strip()

    def _log_entry(self, text: str, blocked: str) -> str:
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        payload = f"{time.time()}|{content_hash}|{blocked}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def _entities_regex(self, text: str) -> list[str]:
        entities = []
        for match in PERSON_PATTERN.finditer(text):
            candidate = match.group(1)
            words = {part.lower() for part in candidate.split()}
            if words & ORG_HINTS:
                continue
            entities.append(candidate)
        return entities

    def _entities_full(self, text: str) -> list[str]:
        if not self.use_spacy or self._nlp is None:
            return self._entities_regex(text)
        doc = self._nlp(text)
        return [ent.text for ent in doc.ents if ent.label_ in {"PERSON", "GPE", "LOC"}]

    def check(self, text: str, tool_risk_level: ToolRiskLevel = "medium") -> SanitizerResult:
        canonical = self.canonicalize(text)
        if len(canonical) < 3:
            return SanitizerResult(True, "trivial input", canonical_text=canonical)

        lowered = canonical.lower()
        for pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(canonical):
                reason = "blocked prompt injection attempt"
                return SanitizerResult(
                    False,
                    reason,
                    canonical_text=canonical,
                    log_entry=self._log_entry(canonical, reason),
                    blocked_attribute="prompt injection",
                )

        for attr in sorted(HARD_BLOCKED_ATTRIBUTES):
            if attr in lowered:
                return SanitizerResult(
                    False,
                    f"blocked hard attribute: {attr}",
                    canonical_text=canonical,
                    log_entry=self._log_entry(canonical, attr),
                    blocked_attribute=attr,
                )

        if EMAIL_PATTERN.search(canonical):
            return SanitizerResult(
                False,
                "blocked hard attribute: personal email",
                canonical_text=canonical,
                log_entry=self._log_entry(canonical, "personal email"),
                blocked_attribute="personal email",
            )

        if PHONE_PATTERN.search(canonical):
            return SanitizerResult(
                False,
                "blocked hard attribute: phone number",
                canonical_text=canonical,
                log_entry=self._log_entry(canonical, "phone number"),
                blocked_attribute="phone number",
            )

        entities = self._entities_regex(canonical) if tool_risk_level == "low" else self._entities_full(canonical)
        has_public_figure = any(name in lowered for name in PUBLIC_FIGURE_ALLOWLIST)
        has_public_conduct = any(keyword in lowered for keyword in PUBLIC_CONDUCT_KEYWORDS)
        if has_public_figure and has_public_conduct:
            return SanitizerResult(True, "public figure/public conduct allowed", canonical, detected_entities=entities)

        if entities and not has_public_conduct:
            reason = "potential private individual without public-conduct signal"
            return SanitizerResult(
                False,
                reason,
                canonical_text=canonical,
                log_entry=self._log_entry(canonical, reason),
                detected_entities=entities,
                blocked_attribute="potential private individual",
            )

        return SanitizerResult(True, "passed PII gate", canonical, detected_entities=entities)

    def check_payload(self, payload: Any, tool_risk_level: ToolRiskLevel = "medium") -> SanitizerResult:
        """Scan nested tool arguments or outputs after deterministic serialization."""

        if isinstance(payload, str):
            return self.check(payload, tool_risk_level=tool_risk_level)
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return self.check(serialized, tool_risk_level=tool_risk_level)
