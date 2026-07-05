"""Sanitized dashboard event feed for the live map."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from osint_forge.orchestrator.mission_store import MissionStore


SAFE_EVENT_FIELDS = {
    "event_id",
    "source_id",
    "source_record_id",
    "title",
    "summary",
    "url",
    "published_at",
    "observed_at",
    "lat",
    "lon",
    "location_name",
    "country",
    "event_type",
    "confidence",
    "provenance_id",
    "dedupe_fingerprint",
    "provenance",
}


def dashboard_events_response(
    store: MissionStore,
    *,
    mission_id: str | None = None,
    source_id: str | None = None,
    min_confidence: float | None = None,
    event_type: str | None = None,
    time_window_hours: float | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    rows = store.list_normalized_events(
        mission_id=mission_id,
        source_id=source_id,
        min_confidence=min_confidence,
        limit=limit,
    )
    cutoff = _cutoff(time_window_hours)
    events: list[dict[str, Any]] = []
    for row in rows:
        event = sanitize_dashboard_event(row["event"])
        if event_type and event.get("event_type") != event_type:
            continue
        if cutoff and not _is_after_cutoff(event, cutoff):
            continue
        events.append(event)
    return {
        "events": events,
        "count": len(events),
        "filters": {
            "mission_id": mission_id,
            "source_id": source_id,
            "min_confidence": min_confidence,
            "event_type": event_type,
            "time_window_hours": time_window_hours,
            "limit": limit,
        },
        "raw_payloads_exposed": False,
    }


def sanitize_dashboard_event(event: dict[str, Any]) -> dict[str, Any]:
    safe = {key: event.get(key) for key in SAFE_EVENT_FIELDS if key in event}
    safe.pop("raw_payload_ref", None)
    return safe


def _cutoff(time_window_hours: float | None) -> datetime | None:
    if time_window_hours is None:
        return None
    return datetime.now(timezone.utc) - timedelta(hours=float(time_window_hours))


def _is_after_cutoff(event: dict[str, Any], cutoff: datetime) -> bool:
    value = event.get("published_at") or event.get("observed_at")
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= cutoff
