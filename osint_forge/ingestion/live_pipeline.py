"""Governed live ingestion pipeline for the v3.2 map MVP."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from osint_forge.governance.pii_sanitizer import PIISanitizer
from osint_forge.ingestion.live_adapters import (
    AdapterError,
    FetchCallable,
    GDELTPollingAdapter,
    NormalizedOSINTEvent,
    RateLimiter,
    RetryPolicy,
)
from osint_forge.ingestion.source_registry import SourceRegistry, SourceRegistryError
from osint_forge.orchestrator.mission_store import MissionStore
from osint_forge.orchestrator.workspace_jail import WorkspaceJail
from osint_forge.provenance import stable_hash, utc_now


class LiveIngestionDenied(Exception):
    """Raised when live ingestion does not satisfy governance preconditions."""


@dataclass(frozen=True)
class LiveIngestionRequest:
    mission_id: str
    query: str
    source_id: str = "gdelt"
    live: bool = False
    confirm_live: bool = False
    checklist_approved: bool = False
    max_records: int = 25
    capture_fixture_path: str | None = None


@dataclass(frozen=True)
class LiveIngestionResult:
    status: str
    source_id: str
    accepted_events: int
    duplicate_events: int
    blocked_events: int
    event_ids: list[str]
    reason: str | None = None
    record_count_requested: int = 0
    record_count_returned: int = 0
    source_policy_version: str | None = None
    audit_artifact_path: str | None = None
    audit_artifact_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_id": self.source_id,
            "accepted_events": self.accepted_events,
            "duplicate_events": self.duplicate_events,
            "blocked_events": self.blocked_events,
            "event_ids": list(self.event_ids),
            "reason": self.reason,
            "record_count_requested": self.record_count_requested,
            "record_count_returned": self.record_count_returned,
            "source_policy_version": self.source_policy_version,
            "audit_artifact_path": self.audit_artifact_path,
            "audit_artifact_hash": self.audit_artifact_hash,
        }


class GDELTDocFetcher:
    """Small official DOC API fetcher; tests inject a fixture fetcher instead."""

    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, *, max_records: int = 25, timeout_seconds: float = 10.0) -> None:
        self.max_records = max(1, min(250, int(max_records)))
        self.timeout_seconds = timeout_seconds

    def __call__(self, query: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": self.max_records,
            }
        )
        request = urllib.request.Request(f"{self.endpoint}?{params}", headers={"User-Agent": "OSINT-Forge/3.2"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        articles = data.get("articles", [])
        if not isinstance(articles, list):
            raise AdapterError("GDELT DOC response missing articles list")
        return [dict(item) for item in articles]


def load_fixture_fetcher(path: str | Path) -> FetchCallable:
    fixture_path = Path(path)

    def fetcher(_query: str) -> list[dict[str, Any]]:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            records = data.get("articles") or data.get("events") or data.get("records")
        else:
            records = data
        if not isinstance(records, list):
            raise AdapterError("GDELT live fixture must be a list or contain articles/events/records")
        return [dict(item) for item in records]

    return fetcher


class LiveMapIngestionService:
    def __init__(
        self,
        *,
        store: MissionStore,
        sanitizer: PIISanitizer | None = None,
        registry: SourceRegistry | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.store = store
        self.sanitizer = sanitizer or PIISanitizer(use_spacy=False)
        self.registry = registry or SourceRegistry.load()
        self.workspace = WorkspaceJail(workspace_root) if workspace_root else None

    async def run_gdelt(
        self,
        request: LiveIngestionRequest,
        *,
        fetcher: FetchCallable | None = None,
    ) -> LiveIngestionResult:
        entry = self._authorize(request)
        audit = self._base_audit(request, entry)
        if request.source_id != "gdelt":
            reason = f"source is not wired for live ingestion: {request.source_id}"
            self.store.log_event(
                request.mission_id,
                "LIVE_DISABLED_SOURCE_ATTEMPT",
                {"source_id": request.source_id, "reason": reason},
            )
            raise LiveIngestionDenied(reason)
        if fetcher is None:
            fetcher = GDELTDocFetcher(max_records=request.max_records)

        adapter = GDELTPollingAdapter(
            query=request.query,
            fetcher=fetcher,
            rateLimiter=RateLimiter(0.2),
            retryPolicy=RetryPolicy(maxAttempts=3, backoffSeconds=0.25),
        )
        try:
            raw_records = await adapter.fetch_with_retry(fetcher, request.query)
        except Exception as exc:
            reason = str(exc)
            audit["status"] = "source_error"
            audit["reason"] = reason
            audit["schema_validation"]["result"] = "not_run"
            self.store.log_event(
                request.mission_id,
                "LIVE_SOURCE_FETCH_FAILED",
                {
                    "source_id": request.source_id,
                    "reason": reason,
                    "record_count_requested": request.max_records,
                    "source_policy_version": audit["source_policy_version"],
                },
            )
            return self._result_from_audit(request, audit, "source_error", reason, [], 0, 0)
        if isinstance(raw_records, dict):
            raw_records = raw_records.get("articles") or raw_records.get("events") or []
        if not isinstance(raw_records, list):
            reason = "GDELT fetcher must return a list or response object"
            audit["status"] = "source_error"
            audit["reason"] = reason
            self.store.log_event(request.mission_id, "LIVE_SOURCE_SCHEMA_FAILED", {"source_id": request.source_id, "reason": reason})
            return self._result_from_audit(request, audit, "source_error", reason, [], 0, 0)

        accepted: list[str] = []
        duplicates = 0
        blocked = 0
        safe_capture_records: list[dict[str, Any]] = []
        audit["record_count_returned"] = len(raw_records)
        for raw in raw_records:
            if not isinstance(raw, dict):
                blocked += 1
                audit["records_rejected"] += 1
                self._log_blocked(request, "malformed payload", None)
                continue

            raw_check = self.sanitizer.check_payload(pii_scan_projection(raw), tool_risk_level="high")
            raw_hash = stable_hash({"source_id": request.source_id, "raw": raw})
            audit["pii_sanitizer"]["raw_checked"] += 1
            if not raw_check.is_safe:
                blocked += 1
                audit["pii_sanitizer"]["raw_blocked"] += 1
                audit["records_rejected"] += 1
                self.store.log_event(
                    request.mission_id,
                    "LIVE_PII_BLOCKED_RAW_PAYLOAD",
                    {
                        "source_id": request.source_id,
                        "payload_hash": raw_hash,
                        "reason": raw_check.reason,
                        "log_entry": raw_check.log_entry,
                    },
                    event_key=f"{request.source_id}:{raw_hash}",
                )
                continue
            safe_capture_records.append(raw)

            try:
                event = adapter.normalize(raw)
            except Exception as exc:
                blocked += 1
                audit["records_rejected"] += 1
                self._log_blocked(request, str(exc), raw_hash)
                continue

            sanitized = normalized_event_to_map_record(event, raw_payload_hash=raw_hash)
            event_check = self.sanitizer.check_payload(pii_scan_projection(sanitized), tool_risk_level="high")
            audit["pii_sanitizer"]["normalized_checked"] += 1
            if not event_check.is_safe:
                blocked += 1
                audit["pii_sanitizer"]["normalized_blocked"] += 1
                audit["records_rejected"] += 1
                self.store.log_event(
                    request.mission_id,
                    "LIVE_PII_BLOCKED_NORMALIZED_EVENT",
                    {
                        "source_id": request.source_id,
                        "event_id": event.id,
                        "payload_hash": raw_hash,
                        "reason": event_check.reason,
                        "log_entry": event_check.log_entry,
                    },
                    event_key=f"{request.source_id}:{event.id}",
                )
                continue

            schema_ok, schema_reason = validate_map_event_schema(sanitized)
            audit["schema_validation"]["checked"] += 1
            if not schema_ok:
                blocked += 1
                audit["schema_validation"]["failed"] += 1
                audit["records_rejected"] += 1
                self.store.log_event(
                    request.mission_id,
                    "LIVE_SCHEMA_BLOCKED_NORMALIZED_EVENT",
                    {
                        "source_id": request.source_id,
                        "event_id": event.id,
                        "payload_hash": raw_hash,
                        "reason": schema_reason,
                    },
                    event_key=f"{request.source_id}:{event.id}:schema",
                )
                continue
            audit["schema_validation"]["passed"] += 1

            event_fingerprint = str(sanitized["dedupe_fingerprint"])
            content_hash = stable_event_content_hash(sanitized)
            self.store.record_raw_payload(
                mission_id=request.mission_id,
                source_id=request.source_id,
                payload_hash=raw_hash,
                payload=raw,
                observed_at=str(sanitized["observed_at"]),
            )
            inserted = self.store.upsert_normalized_event(
                mission_id=request.mission_id,
                source_id=request.source_id,
                event_id=str(sanitized["event_id"]),
                event_fingerprint=event_fingerprint,
                content_hash=content_hash,
                raw_payload_hash=raw_hash,
                event=sanitized,
                observed_at=str(sanitized["observed_at"]),
                published_at=sanitized.get("published_at"),
                confidence=float(sanitized["confidence"]),
            )
            if inserted:
                accepted.append(str(sanitized["event_id"]))
                audit["records_accepted"] += 1
                audit["hashes_written"]["raw_payload_hashes"].append(raw_hash)
                audit["hashes_written"]["event_fingerprints"].append(event_fingerprint)
                audit["hashes_written"]["event_content_hashes"].append(content_hash)
                self.store.log_event(
                    request.mission_id,
                    "LIVE_EVENT_ACCEPTED",
                    {
                        "source_id": request.source_id,
                        "event_id": sanitized["event_id"],
                        "event_fingerprint": event_fingerprint,
                        "raw_payload_hash": raw_hash,
                    },
                    event_key=f"{request.source_id}:{event_fingerprint}",
                )
            else:
                duplicates += 1
                audit["duplicates"] += 1
                self.store.log_event(
                    request.mission_id,
                    "LIVE_DUPLICATE_EVENT",
                    {
                        "source_id": request.source_id,
                        "event_id": sanitized["event_id"],
                        "event_fingerprint": event_fingerprint,
                    },
                    event_key=f"{request.source_id}:{event_fingerprint}:duplicate",
                )

        if request.capture_fixture_path:
            capture_path = write_sanitized_gdelt_capture(
                request.capture_fixture_path,
                safe_capture_records,
                source_policy_version=str(audit["source_policy_version"]),
            )
            audit["captured_response_fixture"] = str(capture_path)

        audit["status"] = "completed"
        audit["records_rejected"] = blocked
        audit["schema_validation"]["result"] = "passed" if audit["schema_validation"]["failed"] == 0 else "failed"
        return self._result_from_audit(request, audit, "completed", None, accepted, duplicates, blocked)

    def _authorize(self, request: LiveIngestionRequest) -> Any:
        try:
            entry = self.registry.assert_live_allowed(
                request.source_id,
                explicit_live=request.live,
                confirm_live=request.confirm_live,
            )
        except SourceRegistryError as exc:
            event_type = "LIVE_DISABLED_SOURCE_ATTEMPT" if "disabled in the source registry" in str(exc) else "LIVE_INGESTION_DENIED"
            self.store.log_event(
                request.mission_id,
                event_type,
                {
                    "source_id": request.source_id,
                    "live": request.live,
                    "confirm_live": request.confirm_live,
                    "reason": str(exc),
                },
            )
            raise LiveIngestionDenied(str(exc)) from exc

        if not request.checklist_approved:
            reason = "live ingestion requires operator-approved pre-ingestion checklist"
            self.store.log_event(
                request.mission_id,
                "LIVE_CHECKLIST_NOT_APPROVED",
                {"source_id": request.source_id, "reason": reason},
            )
            raise LiveIngestionDenied(reason)

        self.store.log_event(
            request.mission_id,
            "LIVE_INGESTION_APPROVED",
            {
                "source_id": request.source_id,
                "query_hash": stable_hash(request.query),
                "checklist_approved": True,
                "live": request.live,
                "confirm_live": request.confirm_live,
            },
        )
        return entry

    def _log_blocked(self, request: LiveIngestionRequest, reason: str, raw_hash: str | None) -> None:
        self.store.log_event(
            request.mission_id,
            "LIVE_RECORD_BLOCKED",
            {"source_id": request.source_id, "reason": reason, "raw_payload_hash": raw_hash},
        )

    def _base_audit(self, request: LiveIngestionRequest, entry: Any) -> dict[str, Any]:
        return {
            "audit_type": "live_source_verification_gate",
            "mission_id": request.mission_id,
            "source_id": request.source_id,
            "source_display_name": entry.display_name,
            "query_hash": stable_hash(request.query),
            "query": request.query,
            "record_count_requested": request.max_records,
            "record_count_returned": 0,
            "records_accepted": 0,
            "records_rejected": 0,
            "duplicates": 0,
            "pii_sanitizer": {
                "raw_checked": 0,
                "raw_blocked": 0,
                "normalized_checked": 0,
                "normalized_blocked": 0,
                "result": "pending",
            },
            "schema_validation": {
                "checked": 0,
                "passed": 0,
                "failed": 0,
                "result": "pending",
            },
            "hashes_written": {
                "raw_payload_hashes": [],
                "event_fingerprints": [],
                "event_content_hashes": [],
            },
            "source_policy_version": entry.policy_version(),
            "source_policy": {
                "enabled": entry.enabled,
                "legal_basis": entry.legal_basis,
                "collection_mode": entry.collection_mode,
                "rate_limit": entry.rate_limit,
                "pii_policy": entry.pii_policy,
                "license_notes": entry.license_notes,
                "operator_approval_required": entry.operator_approval_required,
                "last_verified_at": entry.last_verified_at,
            },
            "started_at": utc_now(),
            "status": "running",
            "reason": None,
            "captured_response_fixture": None,
        }

    def _result_from_audit(
        self,
        request: LiveIngestionRequest,
        audit: dict[str, Any],
        status: str,
        reason: str | None,
        event_ids: list[str],
        duplicates: int,
        blocked: int,
    ) -> LiveIngestionResult:
        audit["status"] = status
        audit["reason"] = reason
        audit["completed_at"] = utc_now()
        audit["pii_sanitizer"]["result"] = (
            "blocked" if audit["pii_sanitizer"]["raw_blocked"] or audit["pii_sanitizer"]["normalized_blocked"] else "passed"
        )
        artifact_path, artifact_hash = self._write_audit_artifact(request, audit)
        return LiveIngestionResult(
            status=status,
            source_id=request.source_id,
            accepted_events=len(event_ids),
            duplicate_events=duplicates,
            blocked_events=blocked,
            event_ids=event_ids,
            reason=reason,
            record_count_requested=int(audit["record_count_requested"]),
            record_count_returned=int(audit["record_count_returned"]),
            source_policy_version=str(audit["source_policy_version"]),
            audit_artifact_path=artifact_path,
            audit_artifact_hash=artifact_hash,
        )

    def _write_audit_artifact(self, request: LiveIngestionRequest, audit: dict[str, Any]) -> tuple[str | None, str | None]:
        if self.workspace is None:
            return None, None
        stamp = utc_now().replace(":", "").replace("-", "").replace("Z", "Z")
        query_key = stable_hash(request.query)[:10]
        relative_path = f"output/live-audit/{request.source_id}-{query_key}-{stamp}.json"
        self.workspace.write_json(relative_path, audit)
        digest = self.workspace.sha256_file(relative_path)
        self.store.record_artifact(request.mission_id, "live_source_audit", relative_path, digest)
        self.store.log_event(
            request.mission_id,
            "LIVE_AUDIT_ARTIFACT_WRITTEN",
            {"source_id": request.source_id, "artifact_path": relative_path, "artifact_hash": digest},
            event_key=f"{relative_path}:{digest}",
        )
        return relative_path, digest


def normalized_event_to_map_record(event: NormalizedOSINTEvent, *, raw_payload_hash: str | None) -> dict[str, Any]:
    event_dict = event.to_dict()
    geo = event_dict.get("geo") or {}
    provenance = event_dict.get("provenance") or []
    provenance_id = provenance[0].get("contentHash") if provenance else None
    title, summary = _split_title_summary(str(event_dict.get("text") or ""))
    record = {
        "event_id": event.id,
        "source_id": event.source,
        "source_record_id": event.id,
        "title": title,
        "summary": summary,
        "url": event_dict.get("url"),
        "published_at": event_dict.get("publishedAt"),
        "observed_at": event_dict.get("observedAt") or utc_now(),
        "lat": _float_or_none(geo.get("lat")),
        "lon": _float_or_none(geo.get("lon")),
        "location_name": geo.get("placeName"),
        "country": geo.get("country"),
        "event_type": _event_type(event_dict),
        "confidence": float(event_dict.get("confidence") or 0.0),
        "provenance_id": provenance_id,
        "raw_payload_ref": raw_payload_hash,
        "dedupe_fingerprint": stable_hash(
            {
                "source_id": event.source,
                "url": event_dict.get("url"),
                "published_at": event_dict.get("publishedAt"),
                "title": title,
                "summary": summary,
                "lat": _float_or_none(geo.get("lat")),
                "lon": _float_or_none(geo.get("lon")),
            }
        ),
        "provenance": provenance,
    }
    return record


def validate_map_event_schema(event: dict[str, Any]) -> tuple[bool, str]:
    required = {
        "event_id": str,
        "source_id": str,
        "source_record_id": str,
        "title": str,
        "summary": str,
        "observed_at": str,
        "event_type": str,
        "confidence": (float, int),
        "provenance_id": str,
        "dedupe_fingerprint": str,
        "provenance": list,
    }
    for field, expected_type in required.items():
        value = event.get(field)
        if value is None or value == "":
            return False, f"missing required map event field: {field}"
        if not isinstance(value, expected_type):
            return False, f"invalid map event field type: {field}"
    return True, "ok"


def stable_event_content_hash(event: dict[str, Any]) -> str:
    volatile = {"observed_at", "raw_payload_ref", "provenance"}
    return stable_hash({key: value for key, value in event.items() if key not in volatile})


PUBLIC_SOURCE_METADATA_KEYS = {
    "country",
    "domain",
    "eventTimeDate",
    "language",
    "lat",
    "location",
    "location_name",
    "lon",
    "published_at",
    "seendate",
    "sourceCollectionIdentifier",
    "sourceCountry",
    "sourcecountry",
    "socialimage",
    "themes",
    "url_mobile",
}


def pii_scan_projection(payload: Any) -> Any:
    """Keep PII scanning focused on content, not public source metadata.

    Country/place metadata can look like a private individual's name to the
    conservative regex gate. This projection avoids that false positive while
    still scanning text-bearing fields such as title, summary, text, and URL.
    """

    if isinstance(payload, dict):
        return {
            key: pii_scan_projection(value)
            for key, value in payload.items()
            if key not in PUBLIC_SOURCE_METADATA_KEYS
        }
    if isinstance(payload, list):
        return [pii_scan_projection(item) for item in payload]
    return payload


def write_sanitized_gdelt_capture(
    path: str | Path,
    records: list[dict[str, Any]],
    *,
    source_policy_version: str,
) -> Path:
    capture = {
        "source_id": "gdelt",
        "capture_type": "sanitized_response_shape",
        "captured_at": utc_now(),
        "source_policy_version": source_policy_version,
        "record_count": len(records),
        "articles": [sanitize_gdelt_record_for_fixture(record) for record in records],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(capture, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return target


def sanitize_gdelt_record_for_fixture(record: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "url",
        "url_mobile",
        "title",
        "text",
        "summary",
        "seendate",
        "eventTimeDate",
        "domain",
        "language",
        "sourcecountry",
        "sourceCountry",
        "lat",
        "lon",
        "location",
        "themes",
        "sourceCollectionIdentifier",
    }
    sanitized: dict[str, Any] = {}
    for key, value in record.items():
        if key not in allowed:
            sanitized[key] = _shape_value(value)
        elif isinstance(value, str):
            sanitized[key] = " ".join(value.split())[:300]
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[key] = value
        else:
            sanitized[key] = _shape_value(value)
    return sanitized


def _shape_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _shape_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape_value(item) for item in value[:3]]
    if isinstance(value, str):
        return {"type": "str", "length": len(value)}
    return value


def _split_title_summary(text: str) -> tuple[str, str]:
    clean = " ".join(text.split())
    if ". " in clean:
        title, summary = clean.split(". ", 1)
        return title[:180], summary[:2000]
    return clean[:180], clean[:2000]


def _event_type(event_dict: dict[str, Any]) -> str:
    topics = event_dict.get("topics")
    if isinstance(topics, list) and topics:
        return str(topics[0])
    return "public_report"


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
