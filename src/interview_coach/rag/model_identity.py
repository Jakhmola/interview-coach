"""Single owner for the embedding model's identity.

The model name and vector dim are referenced by the embedder client
(`rag.client`), the chunker's tokenizer (`rag.tokenizer`), and the
`grounding_chunks` column type (`db.models`). They must agree exactly or
retrieval silently corrupts — `rag.model_lock` asserts the match at boot.
This is deliberately a constants leaf, not a runtime setting: the identity
is *locked* to what existing rows were written with, not configurable.

Dep-free on purpose so any module (including `db.models`) can import it
without pulling a heavy closure.
"""

from __future__ import annotations

EMBEDDING_MODEL_NAME = "jinaai/jina-embeddings-v3"
EMBEDDING_DIM = 1024
