"""Shared provenance primitives for intelligence records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256_text(stable_json(value))


@dataclass
class ProvenanceRef:
    source: str
    locator: str
    observedAt: str
    contentHash: str | None = None
    extractor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "locator": self.locator,
            "observedAt": self.observedAt,
            "contentHash": self.contentHash,
            "extractor": self.extractor,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_value(cls, value: ProvenanceRef | dict[str, Any]) -> ProvenanceRef:
        if isinstance(value, ProvenanceRef):
            return value
        return cls(
            source=str(value.get("source", "")),
            locator=str(value.get("locator", "")),
            observedAt=str(value.get("observedAt") or value.get("observed_at") or utc_now()),
            contentHash=value.get("contentHash") or value.get("content_hash"),
            extractor=value.get("extractor"),
            metadata=dict(value.get("metadata", {})),
        )
