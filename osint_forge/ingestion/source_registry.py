"""Legal source registry and compliance validation."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


PIIRisk = Literal["low", "medium", "high"]


class SourceRegistryError(Exception):
    """Raised when source policy metadata is missing or a source is disabled."""


@dataclass(frozen=True)
class SourceRegistryEntry:
    source_id: str
    display_name: str
    adapter: str
    api_docs_url: str
    auth_required: bool
    enabled: bool
    legal_basis: str
    collection_mode: str
    rate_limit: str
    rate_limit_policy: str
    allowed_use: str
    pii_risk: PIIRisk
    pii_policy: str
    license_notes: str
    storage_policy: str
    live_enabled_default: bool = False
    live_capable: bool = False
    operator_approval_required: bool = True
    last_verified_at: str = ""
    raw_payload_retention: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        required_text = {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "adapter": self.adapter,
            "api_docs_url": self.api_docs_url,
            "legal_basis": self.legal_basis,
            "collection_mode": self.collection_mode,
            "rate_limit": self.rate_limit,
            "rate_limit_policy": self.rate_limit_policy,
            "allowed_use": self.allowed_use,
            "pii_policy": self.pii_policy,
            "license_notes": self.license_notes,
            "storage_policy": self.storage_policy,
            "last_verified_at": self.last_verified_at,
            "raw_payload_retention": self.raw_payload_retention,
            "notes": self.notes,
        }
        missing = [name for name, value in required_text.items() if not str(value).strip()]
        if missing:
            raise SourceRegistryError(f"source registry entry missing fields: {', '.join(missing)}")
        if self.pii_risk not in {"low", "medium", "high"}:
            raise SourceRegistryError(f"unsupported pii_risk for {self.source_id}: {self.pii_risk}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRegistryEntry:
        expected = {
            "source_id",
            "display_name",
            "adapter",
            "api_docs_url",
            "auth_required",
            "enabled",
            "legal_basis",
            "collection_mode",
            "rate_limit",
            "rate_limit_policy",
            "allowed_use",
            "pii_risk",
            "pii_policy",
            "license_notes",
            "storage_policy",
            "live_enabled_default",
            "live_capable",
            "operator_approval_required",
            "last_verified_at",
            "raw_payload_retention",
            "notes",
        }
        missing = sorted(expected - set(data))
        if missing:
            raise SourceRegistryError(f"source registry entry missing fields: {', '.join(missing)}")
        return cls(
            source_id=str(data["source_id"]),
            display_name=str(data["display_name"]),
            adapter=str(data["adapter"]),
            api_docs_url=str(data["api_docs_url"]),
            auth_required=bool(data["auth_required"]),
            enabled=bool(data["enabled"]),
            legal_basis=str(data["legal_basis"]),
            collection_mode=str(data["collection_mode"]),
            rate_limit=str(data["rate_limit"]),
            rate_limit_policy=str(data["rate_limit_policy"]),
            allowed_use=str(data["allowed_use"]),
            pii_risk=str(data["pii_risk"]),  # type: ignore[arg-type]
            pii_policy=str(data["pii_policy"]),
            license_notes=str(data["license_notes"]),
            storage_policy=str(data["storage_policy"]),
            live_enabled_default=bool(data["live_enabled_default"]),
            live_capable=bool(data["live_capable"]),
            operator_approval_required=bool(data["operator_approval_required"]),
            last_verified_at=str(data["last_verified_at"]),
            raw_payload_retention=str(data["raw_payload_retention"]),
            notes=str(data["notes"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "adapter": self.adapter,
            "api_docs_url": self.api_docs_url,
            "auth_required": self.auth_required,
            "enabled": self.enabled,
            "legal_basis": self.legal_basis,
            "collection_mode": self.collection_mode,
            "rate_limit": self.rate_limit,
            "rate_limit_policy": self.rate_limit_policy,
            "allowed_use": self.allowed_use,
            "pii_risk": self.pii_risk,
            "pii_policy": self.pii_policy,
            "license_notes": self.license_notes,
            "storage_policy": self.storage_policy,
            "live_enabled_default": self.live_enabled_default,
            "live_capable": self.live_capable,
            "operator_approval_required": self.operator_approval_required,
            "last_verified_at": self.last_verified_at,
            "raw_payload_retention": self.raw_payload_retention,
            "notes": self.notes,
        }

    def policy_version(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SourceRegistry:
    def __init__(self, entries: list[SourceRegistryEntry]) -> None:
        self.entries = {entry.source_id: entry for entry in entries}
        if len(self.entries) != len(entries):
            raise SourceRegistryError("source registry contains duplicate source_id values")

    @classmethod
    def load(cls, path: str | Path | None = None) -> SourceRegistry:
        registry_path = Path(path) if path else Path("config") / "source_registry.json"
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise SourceRegistryError("source registry must contain a sources list")
        return cls([SourceRegistryEntry.from_dict(dict(item)) for item in sources])

    def get(self, source_id: str) -> SourceRegistryEntry:
        entry = self.entries.get(source_id)
        if entry is None:
            raise SourceRegistryError(f"source is not registered: {source_id}")
        return entry

    def assert_live_allowed(self, source_id: str, *, explicit_live: bool, confirm_live: bool) -> SourceRegistryEntry:
        entry = self.get(source_id)
        if not explicit_live:
            raise SourceRegistryError("live ingestion requires --live")
        if not confirm_live:
            raise SourceRegistryError("live ingestion requires --confirm-live")
        if not entry.enabled or not entry.live_capable:
            raise SourceRegistryError(f"{source_id} is disabled in the source registry")
        if entry.operator_approval_required and not confirm_live:
            raise SourceRegistryError("source requires explicit operator approval")
        if not entry.live_enabled_default and not (explicit_live and confirm_live):
            raise SourceRegistryError(
                f"{source_id} is disabled by default; operator approval and an injected client are required"
            )
        return entry

    def list_sources(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in sorted(self.entries.values(), key=lambda item: item.source_id)]
