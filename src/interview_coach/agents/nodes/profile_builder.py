"""ProfileBuilder agent node — CV-only after Phase 14.1.

Reads the user's CV (one allowed per user) and extracts a structured `Profile`
whose Experience highlights are bare `Highlight(text=...)` objects. Project_doc
uploads no longer trigger a profile rebuild — they go through the `doc_intake`
node and enrich existing highlights via the document_mappings flow.
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

logger = logging.getLogger(__name__)

# qwen3:8b advertises ~32k context; trim defensively against extreme CVs.
MAX_DOC_CHARS = 12000


class NoDocumentsError(Exception):
    """Raised when the user has no CV to build a profile from."""


async def _load_cv(user_id: uuid.UUID) -> tuple[uuid.UUID, str] | None:
    """Return (doc_id, raw_text) of the user's CV, or None if no CV is present."""
    async with AsyncSessionLocal() as s:
        docs = await repos.list_documents_for_user(s, user_id)
    for d in docs:
        if d.kind == "cv":
            return d.id, d.raw_text
    return None


async def build_profile(user_id: uuid.UUID, *, temperature: float = 0.0) -> Profile:
    """Build (and persist) a structured profile for `user_id` from their CV.

    Raises:
        NoDocumentsError: user has no CV.
    """
    loaded = await _load_cv(user_id)
    if loaded is None:
        raise NoDocumentsError(f"user {user_id} has no CV")
    cv_id, cv_text = loaded
    if len(cv_text) > MAX_DOC_CHARS:
        cv_text = cv_text[:MAX_DOC_CHARS] + "\n…[truncated]"

    logger.info("ProfileBuilder: extracting CV for user=%s (cv_doc=%s)", user_id, cv_id)

    llm = chat_model(temperature=temperature).with_structured_output(Profile, method="json_schema")
    profile = await llm.ainvoke(
        [
            SystemMessage(content=PROFILE_BUILDER_SYSTEM),
            HumanMessage(content=cv_text),
        ]
    )
    assert isinstance(profile, Profile)

    async with AsyncSessionLocal() as session:
        await repos.upsert_profile(
            session,
            user_id=user_id,
            profile_json=profile.model_dump(mode="json"),
            source_doc_ids=[str(cv_id)],
            model_name=settings.model_name,
        )

    return profile
