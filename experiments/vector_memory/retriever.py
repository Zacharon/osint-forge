"""Isolated vector retrieval spike for memory checkpoint JSON files."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


class OptionalDependencyUnavailable(RuntimeError):
    """Raised when an optional vector backend cannot be loaded."""


class CheckpointEmbeddingProvider(Protocol):
    """Future runner boundary for deterministic, injectable embeddings."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector for each text in order."""


@dataclass(frozen=True)
class MemoryCheckpointRecord:
    """Normalized checkpoint fields used by the retrieval experiment."""

    path: Path
    checkpoint_id: str
    run_id: str
    created_at: str
    status: str
    skill_name: str
    extracted_facts: tuple[str, ...]
    next_review_action: str
    payload: dict[str, Any]

    @classmethod
    def from_file(cls, path: Path) -> "MemoryCheckpointRecord":
        payload = json.loads(path.read_text(encoding="utf-8"))
        facts = payload.get("extracted_facts", [])
        if not isinstance(facts, list):
            facts = []
        return cls(
            path=path,
            checkpoint_id=str(payload.get("checkpoint_id", "")),
            run_id=str(payload.get("run_id", "")),
            created_at=str(payload.get("created_at", "")),
            status=str(payload.get("status", "")),
            skill_name=str(payload.get("skill_name", "")),
            extracted_facts=tuple(str(item) for item in facts),
            next_review_action=str(payload.get("next_review_action", "")),
            payload=payload,
        )

    def retrieval_text(self) -> str:
        parts = [
            self.skill_name,
            self.status,
            self.created_at,
            self.next_review_action,
            *self.extracted_facts,
        ]
        return "\n".join(part for part in parts if part)

    def runner_context(self) -> dict[str, Any]:
        """Return the compact shape the current runner already understands."""

        return {
            "path": str(self.path),
            "run_id": self.run_id,
            "status": self.status,
            "skill_name": self.skill_name,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CheckpointRetrievalResult:
    """Search result returned by all retrieval backends."""

    checkpoint: MemoryCheckpointRecord
    score: float
    backend: str

    def runner_context(self) -> dict[str, Any]:
        context = self.checkpoint.runner_context()
        context["retrieval_score"] = self.score
        context["retrieval_backend"] = self.backend
        return context


class CheckpointRetriever(Protocol):
    """Interface intended for a future runner adapter."""

    def search(self, query: str, *, limit: int = 5) -> list[CheckpointRetrievalResult]:
        """Return the most relevant checkpoint records for the query."""


class FileCheckpointRetriever:
    """Pure-stdlib vector search over checkpoint files.

    The retriever reads checkpoint JSON from disk on each search, embeds the
    query and checkpoint text, and ranks by cosine similarity.  It is slower
    than a persistent vector store, but it is deterministic and dependency-free.
    """

    backend_name = "file"

    def __init__(self, checkpoint_dir: str | Path, embedding_provider: CheckpointEmbeddingProvider) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.embedding_provider = embedding_provider

    def search(self, query: str, *, limit: int = 5) -> list[CheckpointRetrievalResult]:
        if limit <= 0 or not self.checkpoint_dir.exists():
            return []

        records = self._load_records()
        if not records:
            return []

        texts = [query, *(record.retrieval_text() for record in records)]
        embeddings = self.embedding_provider.embed(texts)
        if len(embeddings) != len(texts):
            raise ValueError("embedding provider must return one vector per text")

        query_embedding = embeddings[0]
        results = [
            CheckpointRetrievalResult(
                checkpoint=record,
                score=cosine_similarity(query_embedding, embedding),
                backend=self.backend_name,
            )
            for record, embedding in zip(records, embeddings[1:])
        ]
        results.sort(key=lambda item: (item.score, item.checkpoint.created_at), reverse=True)
        return results[:limit]

    def _load_records(self) -> list[MemoryCheckpointRecord]:
        records: list[MemoryCheckpointRecord] = []
        for path in sorted(self.checkpoint_dir.glob("*.json")):
            try:
                records.append(MemoryCheckpointRecord.from_file(path))
            except (OSError, json.JSONDecodeError):
                continue
        return records


class ChromaCheckpointRetriever:
    """Optional ChromaDB-backed checkpoint retriever.

    ChromaDB is intentionally imported only inside this adapter so the project
    can run tests and use the file fallback without installing it.
    """

    backend_name = "chroma"

    def __init__(
        self,
        checkpoint_dir: str | Path,
        embedding_provider: CheckpointEmbeddingProvider,
        *,
        persist_directory: str | Path | None = None,
        collection_name: str = "memory_checkpoints",
    ) -> None:
        try:
            import chromadb  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on local extras
            raise OptionalDependencyUnavailable("ChromaDB is not installed") from exc

        self.checkpoint_dir = Path(checkpoint_dir)
        self.embedding_provider = embedding_provider
        if persist_directory is None:
            self.client = chromadb.Client()
        else:
            self.client = chromadb.PersistentClient(path=str(persist_directory))
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def search(self, query: str, *, limit: int = 5) -> list[CheckpointRetrievalResult]:
        if limit <= 0 or not self.checkpoint_dir.exists():
            return []

        records = self._load_records()
        if not records:
            return []

        self._upsert(records)
        query_embedding = self.embedding_provider.embed([query])[0]
        response = self.collection.query(query_embeddings=[query_embedding], n_results=limit)

        ids = response.get("ids", [[]])[0]
        distances = response.get("distances", [[]])[0]
        by_id = {record.checkpoint_id or record.run_id: record for record in records}
        results: list[CheckpointRetrievalResult] = []
        for checkpoint_id, distance in zip(ids, distances):
            record = by_id.get(str(checkpoint_id))
            if record is None:
                continue
            results.append(
                CheckpointRetrievalResult(
                    checkpoint=record,
                    score=1.0 / (1.0 + float(distance)),
                    backend=self.backend_name,
                )
            )
        return results

    def _load_records(self) -> list[MemoryCheckpointRecord]:
        return FileCheckpointRetriever(self.checkpoint_dir, self.embedding_provider)._load_records()

    def _upsert(self, records: Sequence[MemoryCheckpointRecord]) -> None:
        texts = [record.retrieval_text() for record in records]
        embeddings = self.embedding_provider.embed(texts)
        self.collection.upsert(
            ids=[record.checkpoint_id or record.run_id for record in records],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {
                    "path": str(record.path),
                    "run_id": record.run_id,
                    "status": record.status,
                    "skill_name": record.skill_name,
                    "created_at": record.created_at,
                }
                for record in records
            ],
        )


def build_checkpoint_retriever(
    checkpoint_dir: str | Path,
    embedding_provider: CheckpointEmbeddingProvider,
    *,
    prefer_chroma: bool = False,
    persist_directory: str | Path | None = None,
) -> CheckpointRetriever:
    """Build a retriever, falling back to files when ChromaDB is unavailable."""

    if prefer_chroma:
        try:
            return ChromaCheckpointRetriever(
                checkpoint_dir,
                embedding_provider,
                persist_directory=persist_directory,
            )
        except OptionalDependencyUnavailable:
            pass
    return FileCheckpointRetriever(checkpoint_dir, embedding_provider)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding vectors must have the same dimensions")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
