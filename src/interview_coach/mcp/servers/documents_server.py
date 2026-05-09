"""MCP server exposing read access to user documents and jobs, plus a Tavily
URL-fetch tool. Spawned as a stdio subprocess by `MultiServerMCPClient`.

Run as: `python -m interview_coach.mcp.servers.documents_server`
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.models import Document, Job
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.ingestion.web import fetch_url_text

PREVIEW_CHARS = 200

mcp = FastMCP("documents")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _doc_meta(d: Document) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "kind": d.kind,
        "filename": d.filename,
        "content_type": d.content_type,
        "byte_size": d.byte_size,
        "char_count": len(d.raw_text),
        "created_at": _iso(d.created_at),
    }


def _doc_full(d: Document) -> dict[str, Any]:
    return {
        **_doc_meta(d),
        "raw_text": d.raw_text,
        "parsed_json": d.parsed_json,
    }


def _job_meta(j: Job) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "source": j.source,
        "source_url": j.source_url,
        "char_count": len(j.raw_text),
        "preview": j.raw_text[:PREVIEW_CHARS],
        "created_at": _iso(j.created_at),
    }


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
async def list_documents(user_id: str) -> list[dict[str, Any]]:
    """List all documents (CVs, project docs) owned by a user.

    Returns a list of metadata dicts (no raw_text) ordered by most recent first.
    """
    uid = uuid.UUID(user_id)
    async with AsyncSessionLocal() as session:
        docs = await repos.list_documents_for_user(session, uid)
    return [_doc_meta(d) for d in docs]


@mcp.tool()
async def get_document(document_id: str, user_id: str) -> dict[str, Any] | None:
    """Get a single document including its full extracted text.

    Returns None if the document doesn't exist or isn't owned by user_id.
    """
    did = uuid.UUID(document_id)
    uid = uuid.UUID(user_id)
    async with AsyncSessionLocal() as session:
        doc = await repos.get_document(session, did, uid)
    return _doc_full(doc) if doc is not None else None


@mcp.tool()
async def list_jobs(user_id: str) -> list[dict[str, Any]]:
    """List all job descriptions submitted by a user.

    Returns metadata + a 200-character preview, ordered most recent first.
    """
    uid = uuid.UUID(user_id)
    async with AsyncSessionLocal() as session:
        jobs = await repos.list_jobs_for_user(session, uid)
    return [_job_meta(j) for j in jobs]


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
    # Heavy import deferred so the MCP process doesn't pull sentence-transformers
    # unless this tool is actually called.
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


@mcp.tool()
async def fetch_url(url: str) -> str:
    """Fetch and extract readable text from a URL via Tavily.

    Requires `TAVILY_API_KEY` to be set in the server environment.
    Raises if the key is missing or the fetch fails.
    """
    return await fetch_url_text(url, settings.tavily_api_key)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
