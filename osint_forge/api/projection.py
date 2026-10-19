"""Public projection of Forge ontology events in Live Map's ``/api/events`` shape.

Live Map documents the envelope in ``docs/API_CONTRACTS.md`` and builds events
with ``src/events/normalized-event.js``. This module produces the same envelope
from ``event`` objects so Live Map can read Forge instead of its own providers.
``tests/fixtures/contracts/live_map_events.json`` is the shared conformance
fixture both sides check against.

Only ``event`` objects are projected, and an event whose ``properties.public``
is ``False`` is left out. Raw payloads and provenance metadata never appear.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from osint_forge.ontology import OntologyObject, OntologyStore

FEED_PROVIDER = "osint-forge"
FEED_MODE = "forge-ontology"
HOURS_DEFAULT, HOURS_MIN, HOURS_MAX = 168, 24, 720
FEED_LIMIT = 1000

RECORD_KINDS = ("event", "observation", "discovery-lead", "moving-object")
VALID_STATUS = {"active", "monitoring", "resolved", "unknown"}
_COUNTRY_RE = re.compile(r"^[A-Z]{2,3}$")
_LIST_FIELDS = ("tags", "strategicAreas", "affectedChokepoints", "impactTypes")
_PROPERTY_LIST_KEYS = {
    "tags": "tags",
    "strategicAreas": "strategic_areas",
    "affectedChokepoints": "affected_chokepoints",
    "impactTypes": "impact_types",
}


# -- value semantics (mirrors Live Map's src/events/value-semantics.js) -----
def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text if "T" in text or len(text) > 10 else text + "T00:00:00+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = _parse_time(value)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if parsed else None


def _ms(value: Any) -> int | None:
    parsed = _parse_time(value)
    return int(parsed.timestamp() * 1000) if parsed else None


def safe_source_url(value: Any) -> str | None:
    """Absolute HTTP(S) without embedded credentials, else None."""
    if not isinstance(value, str) or not re.match(r"^https?://", value.strip(), re.I):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or parts.username or parts.password or not parts.hostname:
        return None
    return value.strip()


def severity_label(score: float) -> str:
    if score >= 85:
        return "critical"
    if score >= 65:
        return "high"
    if score >= 35:
        return "moderate"
    return "low"


# -- event projection ----------------------------------------------------
def canonical_event(obj: OntologyObject) -> dict[str, Any]:
    """Project one ``event`` object to Live Map's canonical normalized event.

    Forge stores ``confidence`` in 0..1 like link confidence; Live Map expects 0..100.
    """
    props = obj.properties
    provenance = obj.provenance
    first_source = provenance[0].source if provenance else FEED_PROVIDER
    severity = _finite(props.get("severity"))
    severity = 25.0 if severity is None else max(0.0, min(100.0, severity))
    confidence = _finite(props.get("confidence"))
    geographic = obj.lat is not None
    ingested = max((ref.observedAt for ref in provenance if _parse_time(ref.observedAt)), default=None, key=_ms)
    source_name = props.get("source_name") or first_source
    event = {
        "id": obj.id,
        "provider": props.get("provider") or first_source,
        "providerEventId": obj.id.split(":", 1)[1],
        "recordKind": props.get("record_kind") if props.get("record_kind") in RECORD_KINDS else "event",
        "title": obj.label,
        "description": props.get("description"),
        "category": str(props.get("category") or "other"),
        "domain": props.get("domain"),
        "latitude": obj.lat,
        "longitude": obj.lon,
        "geographic": geographic,
        "mapDisplayStatus": "mapped" if geographic else "not-mapped",
        "nonGeographicReason": None if geographic else "Geography not supplied",
        "countryCode": props.get("country_code"),
        "countryName": props.get("country_name"),
        "region": props.get("region"),
        "locationName": props.get("location_name"),
        "startedAt": _iso(obj.valid_from),
        "updatedAt": _iso(props.get("updated_at")),
        "ingestedAt": _iso(ingested),
        "severity": severity,
        "severityLabel": severity_label(severity),
        "confidence": None if confidence is None else max(0.0, min(100.0, confidence * 100)),
        "independentSourceCount": len({ref.source for ref in provenance}),
        "verificationStatus": props.get("verification_status"),
        "status": props.get("status") if props.get("status") in VALID_STATUS else "unknown",
        "sourceName": source_name,
        "sourceUrl": safe_source_url(props.get("source_url")),
        "sourceType": props.get("source_type") or "Unknown",
        "sourceTier": props.get("source_tier"),
        "publisher": props.get("publisher") or source_name,
        "language": props.get("language"),
        "verification": None,
        "publicationPolicy": None,
        "clusterId": None,
        "sourcePublishedAt": _iso(props.get("source_published_at")),
        "lastVerifiedAt": _iso(props.get("last_verified_at")),
        "geometry": None,
        "chokepointCorrelations": [],
        "metadata": {"forge": {"objectId": obj.id, "contentHash": obj.content_hash()}},
    }
    for field_name in _LIST_FIELDS:
        values = props.get(_PROPERTY_LIST_KEYS[field_name]) or []
        event[field_name] = list(dict.fromkeys(str(v) for v in values if v)) if isinstance(values, list) else []
    return event


def legacy_event(event: dict[str, Any]) -> dict[str, Any]:
    """Mirror of Live Map's ``toLegacyEvent``: epoch-millisecond clocks and UI aliases."""
    ui_severity = "medium" if event["severityLabel"] == "moderate" else event["severityLabel"]
    place = event["locationName"] or event["countryName"] or "Unknown location"
    return {
        **event,
        "providerId": event["providerEventId"],
        "severityScore": event["severity"],
        "severity": ui_severity,
        "uiSeverity": ui_severity,
        "lat": event["latitude"],
        "lon": event["longitude"],
        "country": event["countryName"] or "Multiple/unknown",
        "place": place,
        "location": place,
        "occurredAt": _ms(event["startedAt"]),
        "updatedAt": _ms(event["updatedAt"]),
        "firstReportedAt": _ms(event["sourcePublishedAt"]),
        "ingestedAt": _ms(event["ingestedAt"]),
        "lastVerifiedAt": _ms(event["lastVerifiedAt"]),
        "summary": event["description"] or "",
        "source": event["sourceName"],
        "providerName": event["sourceName"],
        "severityReason": "Severity is recorded on the Forge event object.",
        "coordinateMethod": "Precision not supplied",
        "details": {},
    }


# -- request parsing -----------------------------------------------------
def parse_feed_params(query: dict[str, str]) -> tuple[int, dict[str, Any], list[str]]:
    """Return ``(hours, filters, warnings)``. Invalid values are ignored with a warning, like Live Map."""
    warnings: list[str] = []
    hours = HOURS_DEFAULT
    raw_hours = query.get("hours")
    if raw_hours not in (None, ""):
        number = _finite(raw_hours)
        if number is None:
            warnings.append("Invalid hours ignored.")
        else:
            hours = int(max(HOURS_MIN, min(HOURS_MAX, number)))
    filters: dict[str, Any] = {"recordKind": None, "verification": None, "domain": None, "country": None}
    record_kind = query.get("recordKind")
    if record_kind:
        if record_kind in RECORD_KINDS:
            filters["recordKind"] = record_kind
        else:
            warnings.append("Invalid recordKind filter ignored.")
    for key in ("verification", "domain"):
        value = (query.get(key) or "").strip()
        if value:
            if re.match(r"^[a-z][a-z0-9-]{0,40}$", value):
                filters[key] = value
            else:
                warnings.append(f"Invalid {key} filter ignored.")
    country = (query.get("country") or "").strip().upper()
    if country:
        if _COUNTRY_RE.match(country):
            filters["country"] = country
        else:
            warnings.append("Invalid country filter ignored.")
    return hours, filters, warnings


def _matches(event: dict[str, Any], filters: dict[str, Any], cutoff: datetime) -> bool:
    started = _parse_time(event["startedAt"])
    if started is None or started < cutoff:
        return False
    if filters["recordKind"] and event["recordKind"] != filters["recordKind"]:
        return False
    if filters["domain"] and event["domain"] != filters["domain"]:
        return False
    if filters["verification"] and (event["verificationStatus"] or "single-source") != filters["verification"]:
        return False
    if filters["country"] and (event["countryCode"] or "").upper() != filters["country"]:
        return False
    return True


# -- envelope ------------------------------------------------------------
def events_feed(store: OntologyStore, query: dict[str, str] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Build the ``/api/events`` envelope from the store's public event objects.

    Events need a ``valid_from`` inside the requested window; undated events are not projected.
    """
    hours, filters, warnings = parse_feed_params(query or {})
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    objects = store.list_objects(object_type="event", limit=FEED_LIMIT)
    canonical = [
        canonical_event(obj)
        for obj in objects
        if obj.properties.get("public") is not False
    ]
    canonical = [event for event in canonical if _matches(event, filters, cutoff)]
    canonical.sort(key=lambda e: (e["startedAt"], e["id"]), reverse=True)
    generated_at = int(now.timestamp() * 1000)
    sources = sorted({event["sourceName"] for event in canonical})
    status = {
        "ok": True,
        "mode": FEED_MODE,
        "count": len(canonical),
        "lastSuccessAt": generated_at,
        "stale": False,
        "truncated": len(objects) >= FEED_LIMIT,
    }
    return {
        "events": [legacy_event(event) for event in canonical],
        "canonicalEvents": canonical,
        "generatedAt": generated_at,
        "filters": filters,
        "hours": hours,
        "sources": sources,
        "sourceStatus": {FEED_PROVIDER: status},
        "sourceRegistry": {},
        "providerSourceRegistry": {},
        "domainSourceStatus": {},
        "providerResults": [{"provider": FEED_PROVIDER, "ok": True, "count": len(canonical), "mode": FEED_MODE}],
        "observationSummary": {"total": 0, "retained": "metadata-only", "coverageGaps": []},
        "storylines": [],
        "observations": [],
        "systemStatus": "operational",
        "changeCursor": None,
        "mode": FEED_MODE,
        "errors": [],
        "warnings": warnings,
    }
