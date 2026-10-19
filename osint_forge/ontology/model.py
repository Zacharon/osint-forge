"""Typed object and link model for the Forge ontology.

The vocabulary follows the shared Live Map / OSINT Forge language: a public
event, place or organization is an object; every connection between objects is
a typed, time-bounded link that cites evidence. Shared keywords, locations or
times never create identity or causality on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from osint_forge.provenance import ProvenanceRef, stable_hash, utc_now


class OntologyError(ValueError):
    """Raised when an object or link violates the ontology contract."""


# Private individuals are deliberately not an object type. The legal source
# policy forbids dossiers and individual targeting, so the store cannot hold them.
OBJECT_TYPES: dict[str, str] = {
    "event": "Something that happened at a place and period.",
    "place": "A named location, region or point.",
    "organization": "A public body, company, agency or group.",
    "vessel": "A ship identified by public registry data.",
    "aircraft": "An aircraft identified by public registry data.",
    "facility": "A port, plant, airport, base or other fixed site.",
    "source": "A publisher, feed or dataset that produces observations.",
    "document": "An evidential origin: one filing, notice, article or dataset row.",
    "observation": "What a source measured or published, with limitations.",
    "claim": "One proposition about a subject, place and period.",
    "assessment": "A reviewed answer to a requirement.",
    "requirement": "A question or standing watch with scope and definition of done.",
}

# Links that only record structure (where something is, who published it) still
# need provenance but not separate evidence. Every analytic link must cite evidence.
STRUCTURAL_LINKS = {"located_at", "published_by", "derived_from", "collected_with", "part_of"}

LINK_TYPES: dict[str, str] = {
    "located_at": "Subject is at or within the target place or facility.",
    "part_of": "Subject is a component or sub-unit of the target.",
    "published_by": "Document or observation was published by the target source.",
    "derived_from": "Subject was produced from the target record.",
    "collected_with": "Observation was gathered under the target requirement.",
    "mentions": "Document or observation names the target object.",
    "involves": "Event involves the target object.",
    "supports": "Subject is evidence in favour of the target claim.",
    "contradicts": "Subject is evidence against the target claim.",
    "answers": "Assessment answers the target requirement.",
    "operated_by": "Vessel, aircraft or facility is operated by the target organization.",
    "observed_reroute": "Vessel or aircraft was observed diverting around the target.",
    "possible_association": "Unconfirmed connection worth review; never proof.",
    "same_as": "Two records describe the same real-world object.",
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9._:\-]{1,200}$")


def object_id(object_type: str, key: str) -> str:
    """Build a stable object id such as ``place:strait-of-hormuz``."""
    slug = re.sub(r"[^A-Za-z0-9._:\-]+", "-", key.strip()).strip("-")
    return f"{object_type}:{slug}"


def _check_id(value: str, label: str) -> None:
    if not _ID_RE.match(value or ""):
        raise OntologyError(f"{label} must look like 'type:key', got {value!r}")


def _check_period(valid_from: str | None, valid_to: str | None) -> None:
    if valid_from and valid_to and valid_to < valid_from:
        raise OntologyError("valid_to must not be earlier than valid_from")


@dataclass
class OntologyObject:
    id: str
    type: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)
    provenance: list[ProvenanceRef] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    valid_from: str | None = None
    valid_to: str | None = None

    def __post_init__(self) -> None:
        if self.type not in OBJECT_TYPES:
            raise OntologyError(f"unsupported object type: {self.type}")
        _check_id(self.id, "object id")
        if not self.id.startswith(f"{self.type}:"):
            raise OntologyError(f"object id {self.id!r} must start with '{self.type}:'")
        if not self.label.strip():
            raise OntologyError("object label is required")
        if not self.provenance:
            raise OntologyError("object provenance is required")
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]
        if (self.lat is None) != (self.lon is None):
            raise OntologyError("lat and lon must be given together")
        if self.lat is not None:
            self.lat, self.lon = float(self.lat), float(self.lon)
            if not (-90.0 <= self.lat <= 90.0 and -180.0 <= self.lon <= 180.0):
                raise OntologyError("coordinates out of range")
        _check_period(self.valid_from, self.valid_to)

    def content(self) -> dict[str, Any]:
        """Fields that define a revision. Retrieval times are excluded on purpose."""
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "properties": self.properties,
            "lat": self.lat,
            "lon": self.lon,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "provenance": sorted(
                {(ref.source, ref.locator, ref.contentHash or "") for ref in self.provenance}
            ),
        }

    def content_hash(self) -> str:
        return stable_hash(self.content())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "properties": dict(self.properties),
            "provenance": [ref.to_dict() for ref in self.provenance],
            "lat": self.lat,
            "lon": self.lon,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OntologyObject:
        return cls(
            id=value["id"],
            type=value["type"],
            label=value["label"],
            properties=dict(value.get("properties") or {}),
            provenance=list(value.get("provenance") or []),
            lat=value.get("lat"),
            lon=value.get("lon"),
            valid_from=value.get("valid_from"),
            valid_to=value.get("valid_to"),
        )


@dataclass
class OntologyLink:
    type: str
    from_id: str
    to_id: str
    provenance: list[ProvenanceRef]
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float | None = None
    rationale: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.type not in LINK_TYPES:
            raise OntologyError(f"unsupported link type: {self.type}")
        _check_id(self.from_id, "from_id")
        _check_id(self.to_id, "to_id")
        if self.from_id == self.to_id:
            raise OntologyError("a link cannot point at its own subject")
        if not self.provenance:
            raise OntologyError("link provenance is required")
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]
        for evidence_id in self.evidence_ids:
            _check_id(evidence_id, "evidence id")
        if self.type not in STRUCTURAL_LINKS and not self.evidence_ids:
            raise OntologyError(f"'{self.type}' links must cite at least one evidence record")
        if self.type == "possible_association" and self.confidence is not None and self.confidence > 0.5:
            raise OntologyError("possible_association is a lead; confidence above 0.5 needs a stronger link type")
        if self.confidence is not None:
            self.confidence = max(0.0, min(1.0, float(self.confidence)))
        _check_period(self.valid_from, self.valid_to)
        self.evidence_ids = sorted(set(self.evidence_ids))

    @property
    def id(self) -> str:
        """Same endpoints, type and period always map to the same link."""
        return "link:" + stable_hash([self.type, self.from_id, self.to_id, self.valid_from, self.valid_to])[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "provenance": [ref.to_dict() for ref in self.provenance],
            "created_at": self.created_at,
        }
