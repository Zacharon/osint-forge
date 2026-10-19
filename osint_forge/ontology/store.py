"""Durable ontology store with SQLite and PostgreSQL backends.

Both backends share one contract: objects are revisioned by content hash, links
must connect objects that already exist and cite evidence that already exists,
and every write appends an audit row. SQLite needs no extra packages; PostgreSQL
uses ``psycopg`` (``pip install osint-forge[postgres]``).
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from osint_forge.ontology.model import OntologyError, OntologyLink, OntologyObject
from osint_forge.provenance import stable_hash, stable_json, utc_now


class OntologyStore:
    """Backend-neutral ontology operations. Use ``SQLiteOntologyStore`` or ``PostgresOntologyStore``."""

    placeholder = "?"

    def __init__(self, connection: Any) -> None:
        self.conn = connection

    # -- backend hooks -------------------------------------------------
    def _json_param(self, value: Any) -> Any:
        return stable_json(value)

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.loads(value) if isinstance(value, (str, bytes)) else value

    def _sql(self, statement: str) -> str:
        return statement if self.placeholder == "?" else statement.replace("?", self.placeholder)

    def _execute(self, statement: str, params: Iterable[Any] = ()) -> Any:
        cursor = self.conn.cursor()
        cursor.execute(self._sql(statement), tuple(params))
        return cursor

    def _commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- objects -------------------------------------------------------
    def upsert_object(self, obj: OntologyObject, *, actor: str = "system") -> tuple[int, bool]:
        """Write an object. Returns ``(revision, changed)``; identical content is a no-op."""
        content_hash = obj.content_hash()
        row = self._execute("SELECT revision, content_hash, type FROM ontology_objects WHERE id = ?", (obj.id,)).fetchone()
        if row and row[1] == content_hash:
            return int(row[0]), False
        if row and row[2] != obj.type:
            raise OntologyError(f"object {obj.id} already exists with type {row[2]}")
        revision = int(row[0]) + 1 if row else 1
        now = utc_now()
        body = obj.to_dict()
        values = (
            obj.type,
            obj.label,
            self._json_param(obj.properties),
            self._json_param(body["provenance"]),
            obj.lat,
            obj.lon,
            obj.valid_from,
            obj.valid_to,
            revision,
            content_hash,
            now,
        )
        try:
            if row:
                self._execute(
                    """UPDATE ontology_objects SET type = ?, label = ?, properties = ?, provenance = ?, lat = ?, lon = ?,
                       valid_from = ?, valid_to = ?, revision = ?, content_hash = ?, updated_at = ? WHERE id = ?""",
                    values + (obj.id,),
                )
            else:
                self._execute(
                    """INSERT INTO ontology_objects (type, label, properties, provenance, lat, lon, valid_from, valid_to,
                       revision, content_hash, updated_at, id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values + (obj.id,),
                )
            self._execute(
                "INSERT INTO ontology_object_revisions (id, revision, content_hash, body, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (obj.id, revision, content_hash, self._json_param(body), now),
            )
            self._audit("object.revised" if row else "object.created", obj.id, content_hash, actor, now)
            self._commit()
        except Exception:
            self.conn.rollback()
            raise
        return revision, True

    def get_object(self, object_id: str) -> OntologyObject | None:
        row = self._execute(
            """SELECT id, type, label, properties, provenance, lat, lon, valid_from, valid_to
               FROM ontology_objects WHERE id = ?""",
            (object_id,),
        ).fetchone()
        return self._object_from_row(row) if row else None

    def object_history(self, object_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT revision, content_hash, body, recorded_at FROM ontology_object_revisions WHERE id = ? ORDER BY revision",
            (object_id,),
        ).fetchall()
        return [
            {"revision": int(r[0]), "content_hash": r[1], "object": self._json_value(r[2]), "recorded_at": r[3]}
            for r in rows
        ]

    def list_objects(
        self,
        *,
        object_type: str | None = None,
        text: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        limit: int = 100,
    ) -> list[OntologyObject]:
        """Filter by type, label substring, and ``bbox=(min_lon, min_lat, max_lon, max_lat)``."""
        clauses: list[str] = []
        params: list[Any] = []
        if object_type:
            clauses.append("type = ?")
            params.append(object_type)
        if text:
            clauses.append("LOWER(label) LIKE ?")
            params.append(f"%{text.lower()}%")
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            clauses.append("lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
            params.extend([min_lat, max_lat, min_lon, max_lon])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 1000))
        rows = self._execute(
            f"""SELECT id, type, label, properties, provenance, lat, lon, valid_from, valid_to
                FROM ontology_objects {where} ORDER BY id LIMIT {limit}""",
            params,
        ).fetchall()
        return [self._object_from_row(row) for row in rows]

    # -- links ---------------------------------------------------------
    def add_link(self, link: OntologyLink, *, actor: str = "system") -> bool:
        """Write a link. Returns False when an identical link already exists."""
        missing = [i for i in {link.from_id, link.to_id, *link.evidence_ids} if not self._object_exists(i)]
        if missing:
            raise OntologyError(f"link references unknown objects: {sorted(missing)}")
        existing = self._execute("SELECT evidence_ids, confidence, rationale FROM ontology_links WHERE id = ?", (link.id,)).fetchone()
        body = link.to_dict()
        if existing and (sorted(self._json_value(existing[0])), existing[1], existing[2]) == (
            link.evidence_ids,
            link.confidence,
            link.rationale,
        ):
            return False
        now = utc_now()
        try:
            if existing:
                self._execute(
                    "UPDATE ontology_links SET evidence_ids = ?, confidence = ?, rationale = ?, provenance = ? WHERE id = ?",
                    (self._json_param(link.evidence_ids), link.confidence, link.rationale, self._json_param(body["provenance"]), link.id),
                )
            else:
                self._execute(
                    """INSERT INTO ontology_links (id, type, from_id, to_id, evidence_ids, confidence, rationale,
                       valid_from, valid_to, provenance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        link.id,
                        link.type,
                        link.from_id,
                        link.to_id,
                        self._json_param(link.evidence_ids),
                        link.confidence,
                        link.rationale,
                        link.valid_from,
                        link.valid_to,
                        self._json_param(body["provenance"]),
                        link.created_at,
                    ),
                )
            link_hash = stable_hash({k: v for k, v in body.items() if k != "created_at"})
            self._audit("link.revised" if existing else "link.created", link.id, link_hash, actor, now)
            self._commit()
        except Exception:
            self.conn.rollback()
            raise
        return True

    def links_for(
        self,
        object_id: str,
        *,
        direction: str = "both",
        link_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if direction not in {"out", "in", "both"}:
            raise OntologyError("direction must be 'out', 'in' or 'both'")
        clauses = {"out": "from_id = ?", "in": "to_id = ?", "both": "(from_id = ? OR to_id = ?)"}[direction]
        params: list[Any] = [object_id] * (2 if direction == "both" else 1)
        types = sorted(set(link_types or []))
        if types:
            clauses += f" AND type IN ({', '.join('?' for _ in types)})"
            params.extend(types)
        rows = self._execute(
            f"""SELECT id, type, from_id, to_id, evidence_ids, confidence, rationale, valid_from, valid_to, provenance, created_at
                FROM ontology_links WHERE {clauses} ORDER BY created_at, id""",
            params,
        ).fetchall()
        keys = ["id", "type", "from_id", "to_id", "evidence_ids", "confidence", "rationale", "valid_from", "valid_to", "provenance", "created_at"]
        result = []
        for row in rows:
            item = dict(zip(keys, row))
            item["evidence_ids"] = self._json_value(item["evidence_ids"])
            item["provenance"] = self._json_value(item["provenance"])
            result.append(item)
        return result

    def neighborhood(self, object_id: str, *, depth: int = 1, max_objects: int = 200) -> dict[str, Any]:
        """Breadth-first expansion for link-analysis views. Evidence links are followed like any other."""
        if not self._object_exists(object_id):
            raise OntologyError(f"unknown object: {object_id}")
        depth = max(0, min(int(depth), 4))
        seen = {object_id}
        links: dict[str, dict[str, Any]] = {}
        queue: deque[tuple[str, int]] = deque([(object_id, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for link in self.links_for(current):
                links[link["id"]] = link
                for nxt in (link["from_id"], link["to_id"]):
                    if nxt not in seen and len(seen) < max_objects:
                        seen.add(nxt)
                        queue.append((nxt, level + 1))
        objects = [obj.to_dict() for obj in (self.get_object(i) for i in sorted(seen)) if obj]
        kept = [l for l in links.values() if l["from_id"] in seen and l["to_id"] in seen]
        return {"root": object_id, "objects": objects, "links": kept, "truncated": len(seen) >= max_objects}

    def audit_log(self, subject_id: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE subject_id = ?", [subject_id]) if subject_id else ("", [])
        rows = self._execute(f"SELECT seq, action, subject_id, content_hash, actor, recorded_at FROM ontology_audit {where} ORDER BY seq", params).fetchall()
        return [dict(zip(["seq", "action", "subject_id", "content_hash", "actor", "recorded_at"], r)) for r in rows]

    # -- helpers -------------------------------------------------------
    def _object_exists(self, object_id: str) -> bool:
        return self._execute("SELECT 1 FROM ontology_objects WHERE id = ?", (object_id,)).fetchone() is not None

    def _audit(self, action: str, subject_id: str, content_hash: str, actor: str, now: str) -> None:
        self._execute(
            "INSERT INTO ontology_audit (action, subject_id, content_hash, actor, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (action, subject_id, content_hash, actor, now),
        )

    def _object_from_row(self, row: Any) -> OntologyObject:
        return OntologyObject(
            id=row[0],
            type=row[1],
            label=row[2],
            properties=self._json_value(row[3]),
            provenance=self._json_value(row[4]),
            lat=row[5],
            lon=row[6],
            valid_from=row[7],
            valid_to=row[8],
        )


def _schema(name: str) -> str:
    return resources.files("osint_forge.ontology").joinpath("sql", *name.split("/")).read_text(encoding="utf-8")


class SQLiteOntologyStore(OntologyStore):
    def __init__(self, path: str | Path = ":memory:") -> None:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_schema("sqlite.sql"))
        super().__init__(conn)


class PostgresOntologyStore(OntologyStore):
    placeholder = "%s"

    def __init__(self, dsn: str, *, migrate: bool = True) -> None:
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise OntologyError("PostgresOntologyStore needs psycopg: pip install 'osint-forge[postgres]'") from exc
        self._jsonb = Jsonb
        conn = psycopg.connect(dsn)
        super().__init__(conn)
        if migrate:
            self.migrate()

    def migrate(self) -> None:
        self.conn.execute(_schema("postgres/001_ontology.sql"))
        self.conn.commit()

    def _json_param(self, value: Any) -> Any:
        return self._jsonb(value)
