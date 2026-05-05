"""ProfileBuilder agent node.

Reads the user's documents through MCP tools (`list_documents`, `get_document`),
asks the LLM to extract a structured `Profile`, and persists it.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import PROFILE_BUILDER_SYSTEM
from interview_coach.agents.schemas import Profile
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model
from interview_coach.mcp.client import decode_tool_result, get_tools

logger = logging.getLogger(__name__)

# Per-document text cap to avoid blowing context on extreme inputs. qwen3:8b
# advertises ~32k context but practical perf degrades earlier; trim defensively.
MAX_DOC_CHARS = 8000


class NoDocumentsError(Exception):
    """Raised when a user has no documents to build a profile from."""


async def _load_user_docs(user_id: str) -> list[dict]:
    """Pull all of `user_id`'s documents via MCP."""
    tools = {t.name: t for t in await get_tools()}

    metas = decode_tool_result(await tools["list_documents"].ainvoke({"user_id": user_id}))
    docs: list[dict] = []
    for meta in metas:
        full = decode_tool_result(
            await tools["get_document"].ainvoke({"document_id": meta["id"], "user_id": user_id})
        )
        if full:
            docs.append(full[0])
    return docs


def _format_docs(docs: list[dict]) -> str:
    blocks: list[str] = []
    for d in docs:
        text = d["raw_text"]
        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS] + "\n…[truncated]"
        blocks.append(f"# [{d['kind']}] {d['filename']}\n\n{text}")
    return "\n\n---\n\n".join(blocks)


async def build_profile(user_id: uuid.UUID, *, temperature: float = 0.0) -> Profile:
    """Build (and persist) a structured profile for `user_id`.

    Raises:
        NoDocumentsError: user has no documents to read.
    """
    docs = await _load_user_docs(str(user_id))
    if not docs:
        raise NoDocumentsError(f"user {user_id} has no documents")

    logger.info("ProfileBuilder: extracting from %d doc(s) for user=%s", len(docs), user_id)

    llm = chat_model(temperature=temperature).with_structured_output(Profile, method="json_schema")
    profile = await llm.ainvoke(
        [
            SystemMessage(content=PROFILE_BUILDER_SYSTEM),
            HumanMessage(content=_format_docs(docs)),
        ]
    )
    assert isinstance(profile, Profile)

    async with AsyncSessionLocal() as session:
        await repos.upsert_profile(
            session,
            user_id=user_id,
            profile_json=profile.model_dump(),
            source_doc_ids=[d["id"] for d in docs],
            model_name=settings.model_name,
        )

    return profile
