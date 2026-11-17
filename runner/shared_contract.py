"""Read-only, fail-closed projection of a validated local ledger into kernel terms."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from runner.ledger import ArtifactLedger, ArtifactLedgerError


def load_contract(path: str | Path):
    """Load the explicitly selected shared contract; never copy or modify it."""
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix != ".py":
        raise ArtifactLedgerError("trusted contract must be a Python .py file")
    # WHY: docs/SHARED_CONTRACT_ADAPTER.md#trusted-code-boundary
    spec = importlib.util.spec_from_file_location("osint_shared_execution_contract", path)
    if spec is None or spec.loader is None:
        raise ArtifactLedgerError("shared contract cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_ledger(project_root: str | Path, contract) -> tuple[dict, ...]:
    """Validate the entire saved chain before projection; return detached snapshots.

    Source JSON is retained as an immutable string. Lifecycle normalization is
    descriptive only: a projection never authorizes execution or review.
    """
    ledger = ArtifactLedger(project_root)
    # WHY: docs/SHARED_CONTRACT_ADAPTER.md#validation-order
    # The existing ledger reader validates records, but follows the ledger's
    # path directly. Check its resolved target before any ledger bytes are read.
    ledger._resolve_project_path(ledger.ledger_path, strict=False)
    records = ledger.read_records()
    projected = []
    for record in records:
        kind = record["record_type"]
        if kind not in contract.VALID_ARTIFACT_TYPES:
            raise ArtifactLedgerError(f"unknown artifact type: {kind}")
        path = record.get("artifact_path")
        if kind in {"RunState", "MemoryCheckpoint", "EvalResult", "ReviewPacket"}:
            # These types attest saved files, unlike logical transition records.
            # Rehashing a record must not make its file identity optional.
            if not isinstance(path, str) or not path or not isinstance(record.get("artifact_sha256"), str):
                raise ArtifactLedgerError(f"missing saved artifact identity: {kind}")
        if path is not None:
            resolved = ledger._resolve_project_path(Path(path), strict=True)
            if not resolved.is_file() or hashlib.sha256(resolved.read_bytes()).hexdigest() != record["artifact_sha256"]:
                raise ArtifactLedgerError(f"artifact integrity mismatch: {path}")
        raw_state = None
        if kind == "StateTransition":
            raw_state = record.get("payload", {}).get("to_status")
            # Missing states must go through the same strict shared validation.
            state = contract.normalize_lifecycle_state(raw_state)
        elif kind in {"RunState", "MemoryCheckpoint", "EvalResult", "ReviewPacket"}:
            raw_state = record.get("metadata", {}).get("status")
            state = contract.normalize_lifecycle_state(raw_state)
        else:
            state = None
        projected.append({
            "artifact_type": kind,
            "artifact_path": path,
            "lifecycle_state": state,
            "raw_lifecycle_state": raw_state,
            "source_record_json": json.dumps(record, sort_keys=True, separators=(",", ":")),
        })
    return tuple(projected)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a local kernel ledger (read only; no approval effects)")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--contract", required=True, help="Trusted local execution_contract.py")
    args = parser.parse_args(argv)
    try:
        result = project_ledger(args.project_root, load_contract(args.contract))
    except (ValueError, OSError, TypeError, AttributeError, SyntaxError, ImportError) as exc:
        print(f"projection error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
