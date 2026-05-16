"""MCP server exposing read access to user documents (jobs + grounding).

Phase 16: narrowed to `get_job` (deterministic single-caller from
`company_researcher` / `job_analyzer`) and `search_grounding` (semantic
retrieval used by the evaluator). The previous `list_documents`,
`get_document`, `list_jobs`, and `fetch_url` tools have been removed —
`fetch_url` is now exposed via the new `web_server` (see boundary rules
in CLAUDE.md / current-phase plan).

Run as: `python -m interview_coach.mcp.servers.documents_server`
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from interview_coach.db import repos
from interview_coach.db.models import Job
from interview_coach.db.session import AsyncSessionLocal

mcp = FastMCP("documents")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _job_full(j: Job) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "source": j.source,
        "source_url": j.source_url,
        "raw_text": j.raw_text,
        "parsed_json": j.parsed_json,
        "created_at": _iso(j.created_at),
    }


@mcp.tool()
async def get_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    """Get a single job description including its full text.

    Returns None if the job doesn't exist or isn't owned by user_id.
    """
    jid = uuid.UUID(job_id)
    uid = uuid.UUID(user_id)
    async with AsyncSessionLocal() as session:
        job = await repos.get_job(session, jid, uid)
    return _job_full(job) if job is not None else None


@mcp.resource("project_doc://{user_id}/{document_id}")
async def project_doc_resource(user_id: str, document_id: str) -> str:
    """Return the raw text of a project_doc for an LLM/agent consumer.

    URI: ``project_doc://{user_id}/{document_id}``.

    The user_id arg is part of the URI for tenant scoping — a caller can
    only read documents under their own user_id namespace. CV documents
    are intentionally not exposed; only `project_doc` kind is readable.

    Returns an empty string if the doc doesn't exist or isn't a project_doc
    for `user_id` (MCP resource reads can't easily surface a 404).
    """
    try:
        uid = uuid.UUID(user_id)
        did = uuid.UUID(document_id)
    except ValueError:
        return ""
    async with AsyncSessionLocal() as session:
        doc = await repos.get_document(session, did, uid)
    if doc is None or doc.kind != "project_doc":
        return ""
    return doc.raw_text


@mcp.tool()
async def search_grounding(
    user_id: str,
    query: str,
    k: int = 4,
    source_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve top-k semantically similar chunks from a user's documents.

    `source_kind` defaults to ``project_doc``. Pass ``cv`` to search the
    candidate's resume chunks, or ``all`` to include both.
    """
    # Phase 17: embedding happens over HTTP via the embedder sidecar.
    # No heavy imports here — `retrieve_grounding` builds its EmbeddingClient
    # via `get_embedding_client()` (one per MCP subprocess, memoized).
    from interview_coach.rag.retrieval import retrieve_grounding

    uid = uuid.UUID(user_id)
    if source_kind is None:
        kinds: tuple[str, ...] = ("project_doc",)
    elif source_kind == "all":
        kinds = ("project_doc", "cv")
    else:
        kinds = (source_kind,)

    hits = await retrieve_grounding(user_id=uid, query=query, k=k, source_kinds=kinds)
    return [
        {
            "document_id": str(h.document_id),
            "filename": h.filename,
            "source_doc_kind": h.source_doc_kind,
            "chunk_index": h.chunk_index,
            "text": h.text,
            "score": h.score,
        }
        for h in hits
    ]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
