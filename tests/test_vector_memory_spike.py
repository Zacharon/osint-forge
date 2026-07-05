from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Sequence

from experiments.vector_memory import FileCheckpointRetriever, build_checkpoint_retriever


class MockEmbeddingProvider:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            float(lowered.count("port") + lowered.count("gdansk")),
            float(lowered.count("sanction") + lowered.count("export")),
            float(lowered.count("review")),
        ]


class VectorMemorySpikeTests(unittest.TestCase):
    def test_file_retriever_ranks_checkpoint_with_mock_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_dir = Path(tmp)
            self._write_checkpoint(
                checkpoint_dir / "port.json",
                run_id="run-port",
                skill_name="Port Delay Skill",
                facts=["Gdansk port delays increased.", "Harbor queue reports require review."],
            )
            self._write_checkpoint(
                checkpoint_dir / "sanctions.json",
                run_id="run-sanctions",
                skill_name="Sanctions Skill",
                facts=["Export controls changed for semiconductors."],
            )

            retriever = FileCheckpointRetriever(checkpoint_dir, MockEmbeddingProvider())
            results = retriever.search("gdansk port queue", limit=1)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].checkpoint.run_id, "run-port")
            self.assertEqual(results[0].backend, "file")
            self.assertGreater(results[0].score, 0.0)

    def test_builder_falls_back_to_file_retriever_when_chroma_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_import = __import__

            def import_without_chroma(name, *args, **kwargs):
                if name == "chromadb":
                    raise ImportError("simulated missing chromadb")
                return original_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=import_without_chroma):
                retriever = build_checkpoint_retriever(
                    Path(tmp),
                    MockEmbeddingProvider(),
                    prefer_chroma=True,
                )

            self.assertIsInstance(retriever, FileCheckpointRetriever)

    def test_results_expose_runner_context_shape_for_future_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_dir = Path(tmp)
            self._write_checkpoint(
                checkpoint_dir / "review.json",
                run_id="run-review",
                skill_name="Review Skill",
                facts=["Human review remains required."],
            )

            result = FileCheckpointRetriever(checkpoint_dir, MockEmbeddingProvider()).search("review", limit=1)[0]
            context = result.runner_context()

            self.assertEqual(context["run_id"], "run-review")
            self.assertEqual(context["status"], "needs_review")
            self.assertEqual(context["retrieval_backend"], "file")
            self.assertIn("retrieval_score", context)

    def _write_checkpoint(self, path: Path, *, run_id: str, skill_name: str, facts: list[str]) -> None:
        payload = {
            "schema_version": "1.0",
            "checkpoint_id": "checkpoint-" + run_id,
            "run_id": run_id,
            "created_at": "2026-07-04T00:00:00Z",
            "status": "needs_review",
            "skill_name": skill_name,
            "live_ingestion_enabled": False,
            "recent_memory_inputs": [],
            "extracted_facts": facts,
            "next_review_action": "Human review required before integration.",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
