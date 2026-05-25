"""Drift guard for the embedding model identity (Phase 29, candidate G).

The model name + dim live in `rag.model_identity`; the embedder client, the
chunker tokenizer, and the `grounding_chunks` column type all derive from it.
If any site re-types a literal that drifts from the leaf, retrieval silently
corrupts. These tests fail loudly if the derivation is ever broken.
"""

from interview_coach.db.models import GroundingChunk
from interview_coach.rag import client, tokenizer
from interview_coach.rag.model_identity import EMBEDDING_DIM, EMBEDDING_MODEL_NAME


def test_model_name_owners_agree() -> None:
    assert client.EXPECTED_MODEL_NAME == EMBEDDING_MODEL_NAME
    assert tokenizer.MODEL_NAME == EMBEDDING_MODEL_NAME


def test_dim_owners_agree() -> None:
    assert client.EXPECTED_DIM == EMBEDDING_DIM
    # The ORM column's vector dim is the third site that must not drift.
    assert GroundingChunk.__table__.c.embedding.type.dim == EMBEDDING_DIM
