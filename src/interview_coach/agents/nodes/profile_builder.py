"""ProfileBuilder agent node — CV-only after Phase 14.1.

Reads the user's CV (one allowed per user) and extracts a structured `Profile`.
Phase 21.1: after extracting, any previously-confirmed ``document_mappings``
rows are re-applied so a CV re-extract preserves prior project_doc
enrichments. The persisted ``source_doc_ids`` covers the CV plus every
project_doc whose mapping survived the re-apply.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.nodes.doc_intake import reapply_existing_mappings
from interview_coach.agents.prompts import PROFILE_BUILDER_SYSTEM
from interview_coach.agents.schemas import Profile
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model_structured
from interview_coach.llm.telemetry import set_node_context

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

    On a rebuild (e.g. the user replaced their CV), any persisted
    ``document_mappings`` rows are re-applied to the freshly-extracted
    profile so prior project_doc enrichments survive. ``source_doc_ids``
    is rewritten to ``[cv_id, *project_doc_ids_with_surviving_mappings]``
    so the profile_builder cache check in ``node_profile_builder`` stays
    consistent across project_doc additions and deletions.

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

    with set_node_context("profile_builder"):
        profile = await chat_model_structured(
            Profile,
            [
                SystemMessage(content=PROFILE_BUILDER_SYSTEM),
                HumanMessage(content=cv_text),
            ],
            temperature=temperature,
        )
    assert isinstance(profile, Profile)

    # Phase 21.1: fold any existing project_doc mappings back in. Mapping
    # rows live in `document_mappings` and carry their own `extracted_json`
    # so we don't need to re-run the intake LLM call.
    async with AsyncSessionLocal() as session:
        mapping_rows = await repos.list_document_mappings_for_user(session, user_id)
    rows_as_dicts: list[dict[str, object]] = [
        {
            "document_id": r.document_id,
            "mapping_kind": r.mapping_kind,
            "experience_idx": r.experience_idx,
            "highlight_idx": r.highlight_idx,
            "project_idx": r.project_idx,
            "extracted_json": r.extracted_json or {},
        }
        for r in mapping_rows
    ]
    profile = reapply_existing_mappings(profile, rows_as_dicts)

    surviving_project_doc_ids = sorted({str(r.document_id) for r in mapping_rows})
    source_doc_ids = [str(cv_id), *surviving_project_doc_ids]

    async with AsyncSessionLocal() as session:
        await repos.upsert_profile(
            session,
            user_id=user_id,
            profile_json=profile.model_dump(mode="json"),
            source_doc_ids=source_doc_ids,
            model_name=settings.model_name,
        )

    return profile
