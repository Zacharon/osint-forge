"""Experimental vector retrieval for memory checkpoints.

This package is intentionally outside the production runner path.  It provides
interfaces and adapters that can be evaluated before any runner integration.
"""

from experiments.vector_memory.retriever import (
    CheckpointEmbeddingProvider,
    CheckpointRetrievalResult,
    CheckpointRetriever,
    FileCheckpointRetriever,
    OptionalDependencyUnavailable,
    build_checkpoint_retriever,
)

__all__ = [
    "CheckpointEmbeddingProvider",
    "CheckpointRetrievalResult",
    "CheckpointRetriever",
    "FileCheckpointRetriever",
    "OptionalDependencyUnavailable",
    "build_checkpoint_retriever",
]
