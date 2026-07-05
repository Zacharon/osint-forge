"""Filesystem jail for mission artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class WorkspaceJailError(Exception):
    """Raised when a path escapes the configured workspace."""


class WorkspaceJail:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        path = (self.root / relative_path).resolve()
        if path != self.root and self.root not in path.parents:
            raise WorkspaceJailError(f"path escapes workspace: {relative_path}")
        return path

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, relative_path: str, payload: Any) -> Path:
        return self.write_text(relative_path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))

    def read_text(self, relative_path: str) -> str:
        return self.resolve(relative_path).read_text(encoding="utf-8")

    def sha256_file(self, relative_path: str) -> str:
        digest = hashlib.sha256()
        with self.resolve(relative_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
