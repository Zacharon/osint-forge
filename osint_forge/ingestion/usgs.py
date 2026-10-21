"""USGS earthquake summary feed, written into the ontology as ``event`` objects.

Ported from Live Map's ``src/data/providers/usgs.js`` and
``src/ingestion/usgs-pipeline.js`` so Forge owns the source and Live Map reads
it back through ``/api/events``. The rules carried over:

- One object per USGS feature id (``event:usgs-<id>``). Re-reading an unchanged
  feature writes nothing; a USGS correction writes a new revision.
- A correction older than the stored ``updated_at`` is ignored, so an
  out-of-order feed can't roll an event back.
- A ``deleted`` feature is not erased. The object gets a new revision marked
  ``withdrawn`` and ``public: false``, so it leaves the feed but keeps its history.
- Source links are kept only when they point at ``earthquake.usgs.gov``.

Live fetching goes through the source registry like every other live source.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from osint_forge.ingestion.source_registry import SourceRegistry
from osint_forge.ontology import OntologyError, OntologyObject, OntologyStore
from osint_forge.provenance import ProvenanceRef, stable_hash, utc_now

USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson"
USGS_LANDING_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php"
SOURCE_ID = "usgs"
SOURCE_NAME = "USGS Earthquake Hazards Program"
MAX_FEATURES = 1000
MAX_FEED_BYTES = 20 * 1024 * 1024
WITHDRAWAL_STATUSES = {"deleted", "withdrawn"}


class UsgsFeedError(ValueError):
    """Raised when a feed is not a usable USGS FeatureCollection."""


@dataclass
class IngestSummary:
    retrieved_at: str
    created: int = 0
    revised: int = 0
    unchanged: int = 0
    withdrawn: int = 0
    stale: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieved_at": self.retrieved_at,
            "created": self.created,
            "revised": self.revised,
            "unchanged": self.unchanged,
            "withdrawn": self.withdrawn,
            "stale": self.stale,
            "rejected": list(self.rejected),
        }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _iso_from_ms(value: Any) -> str | None:
    number = _finite(value)
    if number is None:
        return None
    try:
        moment = datetime.fromtimestamp(number / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def usgs_source_url(value: Any) -> str:
    """The feature's own page when it is on earthquake.usgs.gov, else the feed landing page."""
    if isinstance(value, str):
        try:
            parts = urlsplit(value)
        except ValueError:
            parts = None
        if parts and parts.scheme == "https" and parts.hostname == "earthquake.usgs.gov" and not parts.username and not parts.password:
            return value
    return USGS_LANDING_URL


def severity_from_magnitude(magnitude: float | None) -> int:
    if magnitude is None:
        return 15
    return max(0, min(100, round(magnitude / 9.5 * 100)))


def _feature_id(feature: Any) -> str | None:
    value = feature.get("id") if isinstance(feature, dict) else None
    if isinstance(value, str) and 0 < len(value) <= 160 and all(c.isalnum() or c in "._-" for c in value):
        return value
    return None


def _provenance(feature_id: str, feature: dict[str, Any], retrieved_at: str) -> ProvenanceRef:
    return ProvenanceRef(
        source=SOURCE_ID,
        locator=f"usgs:{feature_id}",
        observedAt=retrieved_at,
        contentHash=stable_hash(feature),
        extractor="osint_forge.ingestion.usgs",
    )


def feature_to_event(feature: dict[str, Any], retrieved_at: str) -> OntologyObject:
    """Build the event object for one current (not withdrawn) USGS feature."""
    feature_id = _feature_id(feature)
    if feature_id is None:
        raise UsgsFeedError("missing-stable-id")
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        raise UsgsFeedError("missing-coordinates")
    lon, lat = _finite(coords[0]), _finite(coords[1])
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise UsgsFeedError("missing-coordinates")
    started = _iso_from_ms(props.get("time"))
    if started is None:
        raise UsgsFeedError("invalid-source-time")
    depth = _finite(coords[2]) if len(coords) > 2 else None
    magnitude = _finite(props.get("mag"))
    reviewed = props.get("status") == "reviewed"
    mag_text = f"{magnitude:.1f}" if magnitude is not None else "unknown"
    depth_text = f"{depth:.1f}" if depth is not None else "unknown"
    place = props.get("place") if isinstance(props.get("place"), str) and props.get("place").strip() else None
    return OntologyObject(
        id=f"event:usgs-{feature_id}",
        type="event",
        label=str(props.get("title") or f"Magnitude {mag_text} earthquake"),
        lat=lat,
        lon=lon,
        valid_from=started,
        properties={
            "provider": SOURCE_ID,
            "source_name": SOURCE_NAME,
            "source_url": usgs_source_url(props.get("url")),
            "source_type": "Official",
            "source_tier": "official",
            "category": "earthquake",
            "domain": "natural-disaster",
            "description": (
                f"Magnitude {mag_text} earthquake at approximately {depth_text} km depth. "
                + ("Reviewed by USGS." if reviewed else "Preliminary solution; details may change.")
            ),
            "location_name": place or "Unknown location",
            "country_name": place.split(",")[-1].strip() if place and "," in place else None,
            "updated_at": _iso_from_ms(props.get("updated")),
            "severity": severity_from_magnitude(magnitude),
            "confidence": 0.96 if reviewed else 0.88,
            "status": "active",
            "verification_status": "primary-confirmed" if reviewed else "single-source",
            "tags": ["USGS", "earthquake", str(props.get("status") or "unknown")],
            "magnitude": magnitude,
            "depth_km": depth,
            "tsunami_flag": bool(props.get("tsunami")),
            "felt_reports": props.get("felt") if isinstance(props.get("felt"), int) else None,
            "significance": props.get("sig") if isinstance(props.get("sig"), int) else None,
            "usgs_status": props.get("status"),
            "usgs_id": feature_id,
        },
        provenance=[_provenance(feature_id, feature, retrieved_at)],
    )


def _is_older(candidate: str | None, stored: str | None) -> bool:
    return bool(candidate and stored and candidate < stored)


def ingest_usgs_feed(
    store: OntologyStore,
    feed: Any,
    *,
    retrieved_at: str | None = None,
    actor: str = "ingestion:usgs",
) -> IngestSummary:
    """Write a USGS GeoJSON FeatureCollection into the store and return what changed."""
    if not isinstance(feed, dict) or feed.get("type") != "FeatureCollection" or not isinstance(feed.get("features"), list):
        raise UsgsFeedError("USGS feed must be a GeoJSON FeatureCollection")
    features = feed["features"]
    if len(features) > MAX_FEATURES:
        raise UsgsFeedError(f"USGS feed has more than {MAX_FEATURES} features")
    summary = IngestSummary(retrieved_at=retrieved_at or utc_now())
    accepted = 0
    for feature in features:
        feature_id = _feature_id(feature)
        if feature_id is None:
            summary.rejected.append({"id": "", "reason": "missing-stable-id"})
            continue
        props = feature.get("properties") or {}
        updated = _iso_from_ms(props.get("updated"))
        if updated is None:
            summary.rejected.append({"id": feature_id, "reason": "invalid-source-update-time"})
            continue
        existing = store.get_object(f"event:usgs-{feature_id}")
        if str(props.get("status") or "").lower() in WITHDRAWAL_STATUSES:
            accepted += 1
            if existing is None:
                summary.unchanged += 1
                continue
            if _is_older(updated, existing.properties.get("updated_at")):
                summary.stale += 1
                continue
            existing.properties = {
                **existing.properties,
                "withdrawn": True,
                "public": False,
                "status": "resolved",
                "usgs_status": props.get("status"),
                "updated_at": updated,
            }
            existing.provenance = [*existing.provenance, _provenance(feature_id, feature, summary.retrieved_at)]
            _, changed = store.upsert_object(existing, actor=actor)
            if changed:
                summary.withdrawn += 1
            else:
                summary.unchanged += 1
            continue
        try:
            event = feature_to_event(feature, summary.retrieved_at)
        except (UsgsFeedError, OntologyError) as exc:
            summary.rejected.append({"id": feature_id, "reason": str(exc)})
            continue
        accepted += 1
        if existing is not None and _is_older(event.properties["updated_at"], existing.properties.get("updated_at")):
            summary.stale += 1
            continue
        revision, changed = store.upsert_object(event, actor=actor)
        if not changed:
            summary.unchanged += 1
        elif revision == 1:
            summary.created += 1
        else:
            summary.revised += 1
    if features and accepted == 0:
        raise UsgsFeedError("USGS feed contained no acceptable records")
    return summary


def fetch_usgs_feed(url: str = USGS_FEED_URL, *, timeout: float = 30.0) -> dict[str, Any]:
    """Fetch the public summary feed. Only https://earthquake.usgs.gov is allowed."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "earthquake.usgs.gov":
        raise UsgsFeedError("USGS feed URL must be on https://earthquake.usgs.gov")
    request = urllib.request.Request(url, headers={"Accept": "application/geo+json, application/json", "User-Agent": "osint-forge"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - host checked above
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise UsgsFeedError("USGS feed is larger than the allowed size")
    return json.loads(body)


def main(argv: list[str] | None = None) -> None:
    from osint_forge.ontology import PostgresOntologyStore, SQLiteOntologyStore

    parser = argparse.ArgumentParser(description="Load the USGS earthquake feed into the Forge ontology.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--db", help="SQLite ontology database path")
    target.add_argument("--database-url", help="PostgreSQL connection string")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="Read a saved GeoJSON feed instead of fetching")
    source.add_argument("--live", action="store_true", help="Fetch the live USGS feed")
    parser.add_argument("--confirm-live", action="store_true", help="Confirm live collection")
    parser.add_argument("--registry", help="Source registry path (default config/source_registry.json)")
    args = parser.parse_args(argv)

    if args.live:
        SourceRegistry.load(args.registry).assert_live_allowed(SOURCE_ID, explicit_live=True, confirm_live=args.confirm_live)
        feed = fetch_usgs_feed()
    else:
        feed = json.loads(Path(args.file).read_text(encoding="utf-8"))
    store = SQLiteOntologyStore(args.db) if args.db else PostgresOntologyStore(args.database_url)
    try:
        summary = ingest_usgs_feed(store, feed)
    finally:
        store.close()
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
