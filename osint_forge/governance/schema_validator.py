"""Locked schema manifests for inter-agent and tool handoffs."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


class SchemaDriftError(Exception):
    """Raised when a handoff violates a locked schema."""


@dataclass(frozen=True)
class FieldSpec:
    type_name: str
    required: bool = True
    description: str = ""


@dataclass
class HandoffSchema:
    name: str
    fields: dict[str, FieldSpec]
    version: int = 1
    parent_schema: str | None = None


@dataclass(frozen=True)
class AmendmentRecord:
    schema_name: str
    field_name: str
    field_spec: FieldSpec
    approved_by: str
    reason: str
    timestamp: float = field(default_factory=time.time)


class CaseFileManifest:
    """Tamper-evident schema registry with explicit post-lock amendment flow."""

    _TYPE_MAP = {
        "any": object,
        "bool": bool,
        "dict": dict,
        "float": (float, int),
        "int": int,
        "list": list,
        "str": str,
    }

    def __init__(self, mission_id: str, parent: CaseFileManifest | None = None) -> None:
        self.mission_id = mission_id
        self.parent = parent
        self.schemas: dict[str, HandoffSchema] = {}
        self.amendment_log: list[AmendmentRecord] = []
        self._locked = False

    @property
    def locked(self) -> bool:
        return self._locked

    def register_schema(self, schema: HandoffSchema) -> None:
        if self._locked:
            raise SchemaDriftError("manifest is locked; use propose_amendment()")
        self.schemas[schema.name] = schema

    def lock(self) -> None:
        self._locked = True

    def get_schema(self, name: str) -> HandoffSchema | None:
        local = self.schemas.get(name)
        if local is not None:
            return local
        if self.parent is not None:
            return self.parent.get_schema(name)
        return None

    def propose_amendment(self, record: AmendmentRecord) -> None:
        if not self._locked:
            raise SchemaDriftError("cannot amend before lock()")
        if not record.approved_by.strip() or not record.reason.strip():
            raise SchemaDriftError("approved_by and reason are required for amendments")
        schema = self.get_schema(record.schema_name)
        if schema is None:
            raise SchemaDriftError(f"unknown schema: {record.schema_name}")
        schema.fields[record.field_name] = record.field_spec
        schema.version += 1
        self.amendment_log.append(record)

    def validate_handoff(self, schema_name: str, payload: dict[str, Any]) -> None:
        schema = self.get_schema(schema_name)
        if schema is None:
            raise SchemaDriftError(f"unknown handoff schema: {schema_name}")
        if not isinstance(payload, dict):
            raise SchemaDriftError(f"{schema_name} payload must be a dict")

        unknown = sorted(set(payload) - set(schema.fields))
        if unknown:
            raise SchemaDriftError(f"[{schema_name} v{schema.version}] unknown field(s): {unknown}")

        missing = sorted(name for name, spec in schema.fields.items() if spec.required and name not in payload)
        if missing:
            raise SchemaDriftError(f"[{schema_name} v{schema.version}] missing required field(s): {missing}")

        for name, value in payload.items():
            spec = schema.fields[name]
            expected = self._TYPE_MAP.get(spec.type_name)
            if expected is None:
                raise SchemaDriftError(f"[{schema_name}] unsupported type '{spec.type_name}' for field '{name}'")
            if spec.type_name != "any" and not isinstance(value, expected):
                raise SchemaDriftError(
                    f"[{schema_name} v{schema.version}] field '{name}' expected {spec.type_name}, got {type(value).__name__}"
                )

    def content_hash(self) -> str:
        snapshot = {
            "mission_id": self.mission_id,
            "schemas": {
                name: {
                    "version": schema.version,
                    "parent_schema": schema.parent_schema,
                    "fields": {
                        field_name: {
                            "type_name": spec.type_name,
                            "required": spec.required,
                            "description": spec.description,
                        }
                        for field_name, spec in sorted(schema.fields.items())
                    },
                }
                for name, schema in sorted(self.schemas.items())
            },
            "amendments": [
                {
                    "schema_name": item.schema_name,
                    "field_name": item.field_name,
                    "field_type": item.field_spec.type_name,
                    "approved_by": item.approved_by,
                    "reason": item.reason,
                    "timestamp": round(item.timestamp, 3),
                }
                for item in self.amendment_log
            ],
            "parent_hash": self.parent.content_hash() if self.parent else None,
        }
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()

    def to_case_file_entry(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "manifest_hash": self.content_hash(),
            "locked": self.locked,
            "schema_versions": {name: schema.version for name, schema in sorted(self.schemas.items())},
            "amendments": [record.__dict__ for record in self.amendment_log],
        }
