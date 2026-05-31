"""Phase 32 — retrieval proof (DoD leg 2).

Ingest a synthetic ``github_repo`` document through the real grounding
pipeline (chunker + embedder sidecar + Postgres), then assert the retriever
returns ``source_doc_kind='github_repo'`` chunks for a query against the repo
content. This is the layer the Phase 33 technical round depends on.

Skipped unless ``INTEGRATION=1`` and the docker stack is up (db + embedder
reachable).
"""

from __future__ import annotations

import os
import uuid

import pytest

from interview_coach.db import models, repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.rag.ingest import embed_and_store_document
from interview_coach.rag.retrieval import retrieve_grounding

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="Set INTEGRATION=1 to run; requires docker stack (db + embedder) up.",
)

REPO_TEXT = """# Repository: octocat/widget

## README
Widget is a high-throughput rate limiter built with a token-bucket
algorithm backed by Redis. It exposes a FastAPI service and uses
pgvector for nearest-neighbour lookups.

## File: src/limiter.py
class TokenBucket:
    def consume(self, n: int) -> bool:
        ...
"""


async def test_retriever_returns_github_repo_chunks() -> None:
    async with AsyncSessionLocal() as s:
        user = await repos.create_user(s, f"ghret-{uuid.uuid4().hex[:8]}@example.com", "x")
        user_id = user.id
        doc = models.Document(
            user_id=user_id,
            kind="github_repo",
            filename="octocat/widget (github)",
            content_type="text/x-github-repo",
            byte_size=len(REPO_TEXT.encode("utf-8")),
            raw_text=REPO_TEXT,
            project_title="widget",
            source_url="https://github.com/octocat/widget",
        )
        s.add(doc)
        await s.commit()
        await s.refresh(doc)
        doc_id = doc.id

    n = await embed_and_store_document(doc_id)
    assert n > 0, "expected the github_repo doc to produce chunks"

    hits = await retrieve_grounding(
        user_id=user_id,
        query="how does the rate limiter token bucket work",
        k=4,
        source_kinds=("github_repo",),
    )
    assert hits, "retriever returned no github_repo chunks"
    assert all(h.source_doc_kind == "github_repo" for h in hits)
