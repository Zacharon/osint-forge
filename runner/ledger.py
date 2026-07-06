"""Project-local artifact ledger for governed runner events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LEDGER_RELATIVE_PATH = Path(".kernel_runs") / "ledger" / "artifact_ledger.jsonl"
LEDGER_SCHEMA_VERSION = "1.0"
GENESIS_HASH = "0" * 64


class ArtifactLedgerError(ValueError):
    """Raised when the local artifact ledger cannot be trusted or updated."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _record_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_hash"}
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))


@dataclass(frozen=True)
class ArtifactLedger:
    project_root: Path

    def __init__(self, project_root: str | Path) -> None:
        root = Path(project_root).expanduser().resolve(strict=True)
        object.__setattr__(self, "project_root", root)
        object.__setattr__(self, "ledger_path", root / LEDGER_RELATIVE_PATH)

    ledger_path: Path

    def append_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        existing = self.read_records()
        previous_hash = existing[-1]["record_hash"] if existing else GENESIS_HASH
        sequence = len(existing) + 1
        self._ensure_ledger_path()

        lines: list[str] = []
        for record in records:
            prepared = dict(record)
            prepared["schema_version"] = LEDGER_SCHEMA_VERSION
            prepared["sequence"] = sequence
            prepared["prev_record_hash"] = previous_hash
            prepared["record_hash"] = _record_hash(prepared)
            self._validate_record(prepared, expected_sequence=sequence, expected_previous_hash=previous_hash)
            lines.append(json.dumps(prepared, sort_keys=True) + "\n")
            previous_hash = prepared["record_hash"]
            sequence += 1

        try:
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.writelines(lines)
        except OSError as exc:
            raise ArtifactLedgerError(f"failed to append artifact ledger: {self.ledger_path}") from exc

    def read_records(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        try:
            raw_lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ArtifactLedgerError(f"failed to read artifact ledger: {self.ledger_path}") from exc

        records: list[dict[str, Any]] = []
        previous_hash = GENESIS_HASH
        for index, line in enumerate(raw_lines, start=1):
            if not line.strip():
                raise ArtifactLedgerError(f"artifact ledger has blank line at sequence {index}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArtifactLedgerError(f"artifact ledger has invalid JSON at sequence {index}") from exc
            if not isinstance(record, dict):
                raise ArtifactLedgerError(f"artifact ledger record {index} must be an object")
            self._validate_record(record, expected_sequence=index, expected_previous_hash=previous_hash)
            previous_hash = str(record["record_hash"])
            records.append(record)
        return records

    def artifact_record(
        self,
        *,
        record_type: str,
        run_id: str,
        artifact_path: Path,
        recorded_at: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolve_project_path(artifact_path, strict=True)
        relative_path = resolved.relative_to(self.project_root).as_posix()
        return {
            "record_type": record_type,
            "run_id": run_id,
            "recorded_at": recorded_at,
            "artifact_path": relative_path,
            "artifact_sha256": _sha256_bytes(resolved.read_bytes()),
            "metadata": metadata or {},
        }

    def logical_record(
        self,
        *,
        record_type: str,
        run_id: str,
        recorded_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "record_type": record_type,
            "run_id": run_id,
            "recorded_at": recorded_at,
            "payload": payload,
            "payload_sha256": _sha256_bytes(_canonical_json(payload).encode("utf-8")),
        }

    def _ensure_ledger_path(self) -> None:
        ledger_dir = self.ledger_path.parent
        try:
            ledger_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArtifactLedgerError(f"failed to create artifact ledger directory: {ledger_dir}") from exc
        self._resolve_project_path(ledger_dir, strict=True)

    def _validate_record(
        self,
        record: dict[str, Any],
        *,
        expected_sequence: int,
        expected_previous_hash: str,
    ) -> None:
        required = {
            "schema_version",
            "sequence",
            "record_type",
            "run_id",
            "recorded_at",
            "prev_record_hash",
            "record_hash",
        }
        missing = sorted(required.difference(record))
        if missing:
            raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} missing fields: {', '.join(missing)}")
        if record["schema_version"] != LEDGER_SCHEMA_VERSION:
            raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} has unsupported schema_version")
        if record["sequence"] != expected_sequence:
            raise ArtifactLedgerError(f"artifact ledger sequence mismatch at {expected_sequence}")
        if record["prev_record_hash"] != expected_previous_hash:
            raise ArtifactLedgerError(f"artifact ledger hash chain mismatch at sequence {expected_sequence}")
        if record["record_hash"] != _record_hash(record):
            raise ArtifactLedgerError(f"artifact ledger record hash mismatch at sequence {expected_sequence}")
        if not isinstance(record["record_type"], str) or not record["record_type"]:
            raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} has invalid record_type")
        if not isinstance(record["run_id"], str) or not record["run_id"]:
            raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} has invalid run_id")

        artifact_path = record.get("artifact_path")
        if artifact_path is not None:
            if not isinstance(artifact_path, str) or not artifact_path:
                raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} has invalid artifact_path")
            self._resolve_project_path(Path(artifact_path), strict=False)
            artifact_hash = record.get("artifact_sha256")
            if not isinstance(artifact_hash, str) or len(artifact_hash) != 64:
                raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} has invalid artifact_sha256")

        payload = record.get("payload")
        if payload is not None:
            if not isinstance(payload, dict):
                raise ArtifactLedgerError(f"artifact ledger record {expected_sequence} has invalid payload")
            if record.get("payload_sha256") != _sha256_bytes(_canonical_json(payload).encode("utf-8")):
                raise ArtifactLedgerError(f"artifact ledger payload hash mismatch at sequence {expected_sequence}")

    def _resolve_project_path(self, path: Path, *, strict: bool) -> Path:
        candidate = path if path.is_absolute() else self.project_root / path
        try:
            resolved = candidate.resolve(strict=strict)
        except OSError as exc:
            raise ArtifactLedgerError(f"artifact ledger path is not accessible: {candidate}") from exc
        if not resolved.is_relative_to(self.project_root):
            raise ArtifactLedgerError(f"artifact ledger path escapes project root: {resolved}")
        return resolved
