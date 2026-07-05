"""Small schema validator for local runner artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when an artifact does not match its declared schema."""


def load_schema(schema_root: Path, name: str) -> dict[str, Any]:
    path = schema_root / name
    return json.loads(path.read_text(encoding="utf-8"))


def validate(schema: dict[str, Any], payload: Any, *, path: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(payload, dict):
            raise SchemaValidationError(f"{path} must be an object")
        required = schema.get("required", [])
        for key in required:
            if key not in payload:
                raise SchemaValidationError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(payload) - set(properties))
            if unknown:
                raise SchemaValidationError(f"{path} has unknown field(s): {unknown}")
        for key, value in payload.items():
            if key in properties:
                validate(properties[key], value, path=f"{path}.{key}")
    elif expected_type == "array":
        if not isinstance(payload, list):
            raise SchemaValidationError(f"{path} must be an array")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(payload):
                validate(item_schema, item, path=f"{path}[{index}]")
    elif expected_type == "string":
        if not isinstance(payload, str):
            raise SchemaValidationError(f"{path} must be a string")
        if "enum" in schema and payload not in schema["enum"]:
            raise SchemaValidationError(f"{path} must be one of {schema['enum']}")
        if "minLength" in schema and len(payload) < schema["minLength"]:
            raise SchemaValidationError(f"{path} must have length >= {schema['minLength']}")
    elif expected_type == "boolean":
        if not isinstance(payload, bool):
            raise SchemaValidationError(f"{path} must be a boolean")
    elif expected_type == "number":
        if not isinstance(payload, (int, float)) or isinstance(payload, bool):
            raise SchemaValidationError(f"{path} must be a number")
        _validate_numeric_bounds(schema, payload, path)
    elif expected_type == "integer":
        if not isinstance(payload, int) or isinstance(payload, bool):
            raise SchemaValidationError(f"{path} must be an integer")
        _validate_numeric_bounds(schema, payload, path)
    elif expected_type == "null":
        if payload is not None:
            raise SchemaValidationError(f"{path} must be null")

    if "const" in schema and payload != schema["const"]:
        raise SchemaValidationError(f"{path} must equal {schema['const']!r}")


def validate_payload_against_schema(schema_root: Path, schema_name: str, payload: Any) -> None:
    validate(load_schema(schema_root, schema_name), payload)


def validate_source_record(schema_root: Path, payload: dict[str, Any]) -> None:
    validate_payload_against_schema(schema_root, "source_record.schema.json", payload)


def validate_evidence_link(schema_root: Path, payload: dict[str, Any]) -> None:
    validate_payload_against_schema(schema_root, "evidence_link.schema.json", payload)


def validate_uncertainty_assessment(schema_root: Path, payload: dict[str, Any]) -> None:
    validate_payload_against_schema(schema_root, "uncertainty_assessment.schema.json", payload)


def validate_evidence_contract_bundle(
    schema_root: Path,
    *,
    source_records: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
    uncertainty_assessments: list[dict[str, Any]] | None = None,
) -> None:
    for source in source_records:
        validate_source_record(schema_root, source)
    for link in evidence_links:
        validate_evidence_link(schema_root, link)
    for uncertainty in uncertainty_assessments or []:
        validate_uncertainty_assessment(schema_root, uncertainty)

    source_ids = {str(source["source_id"]) for source in source_records}
    for link in evidence_links:
        source_id = str(link["source_id"])
        if source_id not in source_ids:
            raise SchemaValidationError(f"unknown source_id for evidence link {link['evidence_link_id']!r}: {source_id!r}")

    known_targets = _known_uncertainty_targets(source_records, evidence_links)
    for uncertainty in uncertainty_assessments or []:
        target_key = (str(uncertainty["target_type"]), str(uncertainty["target_id"]))
        if target_key not in known_targets:
            raise SchemaValidationError(
                f"unknown uncertainty target for {uncertainty['uncertainty_id']!r}: {target_key[0]} {target_key[1]!r}"
            )


def _known_uncertainty_targets(
    source_records: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
) -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    for source in source_records:
        targets.add(("source_record", str(source["source_id"])))
    for link in evidence_links:
        targets.add(("evidence_link", str(link["evidence_link_id"])))
        targets.add(("claim", str(link["claim_id"])))
        targets.add(("output_artifact", str(link["output_artifact_id"])))
    return targets


def _validate_numeric_bounds(schema: dict[str, Any], payload: int | float, path: str) -> None:
    if "minimum" in schema and payload < schema["minimum"]:
        raise SchemaValidationError(f"{path} must be >= {schema['minimum']}")
    if "maximum" in schema and payload > schema["maximum"]:
        raise SchemaValidationError(f"{path} must be <= {schema['maximum']}")
