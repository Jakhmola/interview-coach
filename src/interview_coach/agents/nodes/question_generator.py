"""QuestionGenerator agent node.

Streams one interview question. The model emits a single JSON object
``{"question": "...", "anchors": [...]}``; we forward the question text to
the SSE client as it arrives, then capture anchors at end-of-stream and
persist a `Turn` row.

Shape: an async generator that yields token strings (visible to the user)
and finally yields a sentinel `("__done__", {"question_id": ..., "turn_index": ...})`
so the API layer can format the SSE `done` event without re-fetching state.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import (
    QUESTION_BEHAVIORAL_STAR_SYSTEM,
    QUESTION_RESUME_WALKTHROUGH_SYSTEM,
)
from interview_coach.agents.schemas import Question
from interview_coach.agents.state import RoundType
from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_json_object,
)
from interview_coach.db import repos
from interview_coach.db.models import SessionRow
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model

logger = logging.getLogger(__name__)


class GenerationPrereqsMissing(Exception):
    """Phase 8 routes raise 400 from this; profile / job analysis / snapshot absent."""


async def _load_context(session_row: SessionRow) -> dict[str, Any]:
    """Pull profile, job analysis, company snapshot, prior turns from Postgres."""
    user_id = session_row.user_id
    job_id = session_row.job_id

    async with AsyncSessionLocal() as s:
        profile_row = await repos.get_profile(s, user_id)
        if profile_row is None:
            raise GenerationPrereqsMissing("profile_missing")

        job = await repos.get_job(s, job_id, user_id)
        if job is None:
            raise GenerationPrereqsMissing("job_not_found")
        if not job.parsed_json:
            raise GenerationPrereqsMissing("job_not_analyzed")

        snapshot_row = await repos.get_company_snapshot_by_job(s, job_id)
        if snapshot_row is None:
            raise GenerationPrereqsMissing("company_snapshot_missing")

        turns = await repos.list_turns_for_session(s, session_row.id)

    return {
        "profile": profile_row.profile_json,
        "job_analysis": job.parsed_json,
        "company_snapshot": snapshot_row.snapshot_json,
        "prior_turns": [{"question": t.question, "answer": t.answer or ""} for t in turns],
    }


def _pick_focus_signal(
    job_analysis: dict[str, Any], company_snapshot: dict[str, Any]
) -> str | None:
    """For behavioral_star: pick one signal to anchor the question on."""
    candidates: Sequence[str] = job_analysis.get("behavioral_signals") or []
    if not candidates:
        candidates = company_snapshot.get("values_and_signals") or []
    if not candidates:
        return None
    return random.choice(list(candidates))


def _build_user_message(
    *,
    round_type: RoundType,
    context: dict[str, Any],
    focus_signal: str | None,
    turn_index: int,
) -> str:
    """JSON payload of the structured context we hand to the LLM.

    Sticking to JSON keeps the prompt parseable for the model and easy to
    snapshot for tests — no clever templating.
    """
    payload: dict[str, Any] = {
        "round_type": round_type,
        "turn_index": turn_index,
        "profile": context["profile"],
        "job_analysis": context["job_analysis"],
        "company_snapshot": context["company_snapshot"],
        "prior_turns": context["prior_turns"],
    }
    if focus_signal is not None:
        payload["focus_signal"] = focus_signal
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _system_for(round_type: RoundType) -> str:
    if round_type == "resume_walkthrough":
        return QUESTION_RESUME_WALKTHROUGH_SYSTEM
    if round_type == "behavioral_star":
        return QUESTION_BEHAVIORAL_STAR_SYSTEM
    raise ValueError(f"unknown round_type: {round_type!r}")


async def stream_question(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    temperature: float = 0.7,
) -> AsyncIterator[tuple[str, Any]]:
    """Generate, stream, and persist one question for `session_id`.

    Yields:
        ("token", str) — a chunk of the user-visible question text.
        ("done", {"question_id": str, "turn_index": int}) — once at end.

    Raises:
        GenerationPrereqsMissing: profile/job-analysis/snapshot not ready.
        ValueError: session not found / wrong user / session already complete.
        StreamingJsonError: model emitted invalid JSON.
    """
    async with AsyncSessionLocal() as s:
        session_row = await repos.get_session(s, session_id, user_id)
        if session_row is None:
            raise ValueError("session_not_found")
        if session_row.status != "active":
            raise ValueError(f"session_status_{session_row.status}")
        existing_turns = await repos.list_turns_for_session(s, session_id)

    turn_index = len(existing_turns)
    if turn_index >= session_row.n_questions:
        raise ValueError("session_complete")

    context = await _load_context(session_row)

    round_type: RoundType = session_row.round_type  # type: ignore[assignment]
    focus_signal: str | None = None
    if round_type == "behavioral_star":
        focus_signal = _pick_focus_signal(context["job_analysis"], context["company_snapshot"])

    user_msg = _build_user_message(
        round_type=round_type,
        context=context,
        focus_signal=focus_signal,
        turn_index=turn_index,
    )

    logger.info(
        "QuestionGenerator: session=%s turn=%d round=%s signal=%r",
        session_id,
        turn_index,
        round_type,
        focus_signal,
    )

    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})

    async def _model_deltas() -> AsyncIterator[str]:
        async for chunk in llm.astream(
            [
                SystemMessage(content=_system_for(round_type)),
                HumanMessage(content=user_msg),
            ]
        ):
            content = chunk.content
            if isinstance(content, str):
                if content:
                    yield content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str) and part:
                        yield part
                    elif isinstance(part, dict) and "text" in part:
                        text = str(part["text"])
                        if text:
                            yield text

    parsed: dict[str, Any] | None = None
    async for event, data in stream_json_object(
        _model_deltas(), stream_string_fields=("question",)
    ):
        if event == "question_chunk":
            yield ("token", data)
        elif event == "done":
            parsed = data

    if parsed is None:
        raise StreamingJsonError("stream ended without producing a parsed object")

    try:
        question_obj = Question.model_validate(parsed)
    except Exception as e:
        raise StreamingJsonError(f"final JSON failed schema validation: {e}") from e

    metadata: dict[str, Any] = {}
    if focus_signal is not None:
        metadata["focus_signal"] = focus_signal

    async with AsyncSessionLocal() as s:
        turn = await repos.create_turn(
            s,
            session_id=session_id,
            turn_index=turn_index,
            question=question_obj.question,
            anchors=question_obj.anchors,
            metadata=metadata or None,
        )

    yield ("done", {"question_id": str(turn.id), "turn_index": turn_index})
