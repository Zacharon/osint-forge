# Vector Memory Spike

This is an isolated experiment for retrieving `memory_checkpoints/*.json` by embedding similarity. It does not change the production runner path and does not modify `runner/harness.py`.

## Status

Experimental. The interface is designed so a future runner adapter could request relevant checkpoint context, but the current dry-run runner still uses its existing recent-file loading behavior.

## Install

No extra dependency is required for the file fallback:

```powershell
python -m unittest tests.test_vector_memory_spike
```

ChromaDB is optional. Install it only in an environment where you want to evaluate the persistent vector-store adapter:

```powershell
python -m pip install chromadb
```

If ChromaDB is missing, `build_checkpoint_retriever(..., prefer_chroma=True)` returns the file-based retriever instead.

## Use

```python
from experiments.vector_memory import build_checkpoint_retriever


class Embeddings:
    def embed(self, texts):
        # Replace with a deterministic local embedding implementation.
        return [[float(len(text)), 0.0, 1.0] for text in texts]


retriever = build_checkpoint_retriever(
    "memory_checkpoints",
    Embeddings(),
    prefer_chroma=False,
)

results = retriever.search("gdansk port delays", limit=3)
runner_context = [result.runner_context() for result in results]
```

`runner_context` intentionally extends the compact checkpoint shape currently consumed by the runner:

- `path`
- `run_id`
- `status`
- `skill_name`
- `created_at`
- `retrieval_score`
- `retrieval_backend`

## Interface Design

The future integration boundary is `CheckpointRetriever.search(query, limit=5)`. A runner adapter could build the query from the skill name, prompt summary, or operator-supplied task description, then append each result's `runner_context()` to the existing `recent_memory_inputs` list.

The embedding boundary is `CheckpointEmbeddingProvider.embed(texts)`. This keeps the spike independent from any hosted embedding API and lets tests use deterministic mock vectors without network access.

## Dependency Risks

ChromaDB is intentionally optional. It can add local binary and transitive dependency risk, version-specific client behavior, and persistence format drift. The file fallback is slower and re-embeds checkpoints on each search, but it keeps the baseline project dependency-free and lets tests pass in bare environments.

Before production integration, the project should decide which local embedding implementation is allowed, how embeddings are cached, and whether vector retrieval results require schema validation or review annotations before being passed to a governed run.

This spike is not production memory. Do not wire it into the governed runner or
treat retrieval scores as review evidence until that integration has its own
schema, tests, and approval semantics.
