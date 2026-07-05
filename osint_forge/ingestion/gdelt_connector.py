"""GDELT dry-run connector backed only by local fixture files."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.orchestrator.mission_store import MissionStore


class GDELTDryRunConnectorError(Exception):
    """Raised when dry-run fixture ingestion cannot proceed safely."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _collected_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GDELTDryRunConnector:
    """Load synthetic GDELT-like fixtures, gate PII, and persist normalized evidence."""

    def __init__(self, mission_id: str, store: MissionStore, sanitizer: PIISanitizer) -> None:
        self.mission_id = mission_id
        self.store = store
        self.sanitizer = sanitizer

    def __call__(self, fixture_dir: str | Path, fixture_name: str | None = None) -> dict[str, Any]:
        fixture_paths = self._fixture_paths(fixture_dir, fixture_name)
        collected_at = _collected_at()
        safe_records: list[dict[str, Any]] = []
        blocked_count = 0

        for path in fixture_paths:
            for record in self._load_fixture(path):
                normalized = self._normalize_record(record, path.name, collected_at)
                pii_result = self.sanitizer.check_payload(normalized, tool_risk_level="high")
                if not pii_result.is_safe:
                    blocked_count += 1
                    self.store.log_event(
                        self.mission_id,
                        "PII_REJECTED_INGESTION_RECORD",
                        {
                            "source_name": normalized["source_name"],
                            "fixture_name": normalized["fixture_name"],
                            "record_fingerprint": normalized["record_fingerprint"],
                            "reason": pii_result.reason,
                            "log_entry": pii_result.log_entry,
                        },
                        event_key=f"{normalized['fixture_name']}:{normalized['record_fingerprint']}",
                    )
                    if pii_result.blocked_attribute == "prompt injection":
                        raise GDELTDryRunConnectorError("prompt injection detected in fixture source text")
                    continue

                self.store.upsert_ingested_record(
                    mission_id=self.mission_id,
                    source_name=normalized["source_name"],
                    record_fingerprint=normalized["record_fingerprint"],
                    content_hash=normalized["content_hash"],
                    fixture_name=normalized["fixture_name"],
                    record=normalized,
                    collected_at=normalized["collected_at"],
                )
                safe_records.append(normalized)

        return {
            "raw_findings": safe_records,
            "source_hashes": [record["content_hash"] for record in safe_records],
            "ingestion_summary": {
                "connector": "gdelt",
                "mode": "dry_run",
                "fixtures": [path.name for path in fixture_paths],
                "accepted_records": len(safe_records),
                "blocked_records": blocked_count,
            },
        }

    def _fixture_paths(self, fixture_dir: str | Path, fixture_name: str | None) -> list[Path]:
        root = Path(fixture_dir).resolve()
        if not root.exists() or not root.is_dir():
            raise GDELTDryRunConnectorError(f"fixture directory not found: {root}")
        if fixture_name:
            paths = [root / fixture_name]
        else:
            paths = sorted([*root.glob("*.json"), *root.glob("*.csv")])
        paths = [path.resolve() for path in paths]
        for path in paths:
            if root != path.parent and root not in path.parents:
                raise GDELTDryRunConnectorError(f"fixture path escapes fixture directory: {path}")
            if path.suffix.lower() not in {".json", ".csv"}:
                raise GDELTDryRunConnectorError(f"unsupported fixture type: {path.name}")
            if not path.exists():
                raise GDELTDryRunConnectorError(f"fixture file not found: {path.name}")
        if not paths:
            raise GDELTDryRunConnectorError(f"no JSON or CSV fixtures found in {root}")
        return paths

    def _load_fixture(self, path: Path) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [dict(item) for item in data]
            if isinstance(data, dict):
                records = data.get("records") or data.get("events")
                if isinstance(records, list):
                    return [dict(item) for item in records]
            raise GDELTDryRunConnectorError(f"JSON fixture must be a list or contain records/events: {path.name}")

        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def _normalize_record(self, record: dict[str, Any], fixture_name: str, collected_at: str) -> dict[str, Any]:
        source_name = str(record.get("source_name") or record.get("source") or "GDELT Fixture")
        source_url = str(record.get("source_url") or record.get("url") or "")
        published_at = str(record.get("published_at") or record.get("event_time") or record.get("date") or "")
        title = str(record.get("title") or record.get("headline") or "")
        text = str(record.get("text") or record.get("summary") or record.get("description") or "")
        location = str(record.get("location") or "")
        event_code = str(record.get("event_code") or record.get("event_type") or "")
        content_basis = {
            "source_name": source_name,
            "source_url": source_url,
            "published_at": published_at,
            "title": title,
            "text": text,
            "location": location,
            "event_code": event_code,
        }
        content_hash = _sha256_text(_stable_json(content_basis))
        fingerprint_basis = {
            "source_name": source_name,
            "source_url": source_url,
            "published_at": published_at,
            "title": title,
            "content_hash": content_hash,
        }
        record_fingerprint = _sha256_text(_stable_json(fingerprint_basis))
        return {
            "id": record_fingerprint,
            "source_name": source_name,
            "source_url": source_url,
            "published_at": published_at,
            "event_time": published_at,
            "collected_at": collected_at,
            "fixture_name": fixture_name,
            "record_fingerprint": record_fingerprint,
            "content_hash": content_hash,
            "title": title,
            "text": text,
            "location": location,
            "event_code": event_code,
            "publisher": source_name,
            "url": source_url,
            "publication_timestamp": published_at,
        }
