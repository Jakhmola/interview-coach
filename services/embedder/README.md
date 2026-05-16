# embedder

Sidecar service that owns the `jinaai/jina-embeddings-v3` model. The `api`
container talks to it over HTTP via `interview_coach.rag.client.EmbeddingClient`.

## Contract

```
POST /embed
  body:  { "texts": ["..."], "task": "retrieval.passage" | "retrieval.query" }
  200:   { "vectors": [[...]], "model": "jinaai/jina-embeddings-v3", "dim": 1024 }
  503:   model not yet loaded

GET /model
  200:   { "name": "jinaai/jina-embeddings-v3", "dim": 1024 }

GET /healthz
  200:   { "ok": true, "model_loaded": true }
  503:   { "ok": false, "model_loaded": false }
```

Encoding mirrors the in-process path that lived in `src/interview_coach/rag/embeddings.py`
before Phase 17: `normalize_embeddings=True`, `convert_to_numpy=True`,
`trust_remote_code=True`.

## Local dev

```
cd services/embedder
uv sync
HF_HOME=$HOME/.cache/hf uv run uvicorn embedder.main:app --port 8001
```

## Tests

The `-m embedder` marker gates tests that load the real model (~500MB
weights, ~5–10s cold load). Default test runs skip them.

```
uv run pytest -m embedder
```
