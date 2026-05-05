"""Phase 4 MCP smoke test.

Registers a fresh user via the running api, uploads a tiny PDF, then via MCP:
  - lists tools from all configured servers
  - calls list_documents
  - calls get_document
  - calls list_jobs

Prints results. Exits 0 on success, non-zero on failure.

Run from the host:
    docker compose up -d
    uv run python scripts/mcp_smoke.py

The MCP subprocess inherits this script's environment, so DATABASE_URL must
point at a reachable Postgres. We default to localhost:5432 (the compose port
mapping); override by exporting DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid

# Set BEFORE the MCP subprocess inherits our env.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://interview_coach:interview_coach@localhost:5432/interview_coach",
)

import httpx  # noqa: E402
from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from interview_coach.mcp.client import decode_tool_result  # noqa: E402

API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def make_pdf_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


async def setup_user_and_doc() -> tuple[str, str, str]:
    """Returns (user_id, token, document_id)."""
    email = f"smoke-{uuid.uuid4()}@test.com"
    password = "hunter22a"
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(
            f"{API_URL}/auth/register",
            json={"email": email, "password": password},
        )
        r.raise_for_status()
        body = r.json()
        token = body["access_token"]
        user_id = body["user"]["id"]

        r = await http.post(
            f"{API_URL}/documents",
            headers={"Authorization": f"Bearer {token}"},
            data={"kind": "cv"},
            files={
                "file": ("smoke.pdf", make_pdf_bytes(f"Smoke test for {email}"), "application/pdf")
            },
        )
        r.raise_for_status()
        doc_id = r.json()["id"]
    return user_id, token, doc_id


async def main() -> int:
    user_id, _, doc_id = await setup_user_and_doc()
    print(f"setup ok: user={user_id} doc={doc_id}")

    client = MultiServerMCPClient(
        {
            "documents": {
                "command": "python",
                "args": ["-m", "interview_coach.mcp.servers.documents_server"],
                "transport": "stdio",
                "env": dict(os.environ),
            }
        }
    )
    tools = await client.get_tools()
    names = sorted(t.name for t in tools)
    print(f"tools: {names}")

    by_name = {t.name: t for t in tools}

    docs = decode_tool_result(await by_name["list_documents"].ainvoke({"user_id": user_id}))
    print(f"list_documents ({len(docs)}): {[d['filename'] for d in docs]}")
    if not docs:
        print("FAIL: expected at least one document")
        return 1

    # get_document returns a single dict (or None) — comes as one TextContent
    doc_blocks = await by_name["get_document"].ainvoke({"document_id": doc_id, "user_id": user_id})
    doc_list = decode_tool_result(doc_blocks)
    doc = doc_list[0] if doc_list else None
    if not doc or "raw_text" not in doc:
        print(f"FAIL: get_document returned {doc!r}")
        return 1
    print(f"get_document: id={doc['id']} chars={doc['char_count']}")

    jobs = decode_tool_result(await by_name["list_jobs"].ainvoke({"user_id": user_id}))
    print(f"list_jobs ({len(jobs)})")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
