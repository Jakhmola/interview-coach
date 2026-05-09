"""Evaluator agent node — Phase 14 split.

The evaluation is now split across two sequential LLM calls (single-GPU,
qwen3:8b VRAM-bound; parallelism would queue or spill to CPU):

  1. **Judge call** — emits ``{score, feedback}``. No grounding injected,
     so the rubric stays untouched by retrieval noise.
  2. **Model-answer call** — emits ``{model_answer}``, with retrieval over
     the candidate's own ``project_doc`` chunks injected so the reference
     answer can speak with project-specific detail in the candidate's
     first-person voice.

Wire format to the SSE consumer is unchanged from Phase 9:
    score → feedback_token* → feedback_done → model_answer_token* →
    model_answer_done → done

If the model-answer call fails, the orchestrator persists score+feedback
only and emits ``("model_answer_error", {"reason": str})``. The session
status flip on the last turn happens after both calls succeed (or after
the partial-persist path on failure).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import (
    EVALUATOR_JUDGE_SYSTEM,
    MODEL_ANSWER_SYSTEM,
)
from interview_coach.agents.schemas import Judgment, ModelAnswerOnly
from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_json_object,
)
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model
from interview_coach.rag.retrieval import GroundingHit, retrieve_grounding

logger = logging.getLogger(__name__)


class TurnNotFound(Exception):
    """Raised when the turn doesn't exist or doesn't belong to the user."""


class TurnNotAnswered(Exception):
    """Caller invoked the evaluator before the answer was saved on the turn."""


async def _load_eval_inputs(
    session_id: uuid.UUID, user_id: uuid.UUID, turn_id: uuid.UUID
) -> dict[str, Any]:
    async with AsyncSessionLocal() as s:
        sess = await repos.get_session(s, session_id, user_id)
        if sess is None:
            raise TurnNotFound(f"session {session_id} not found for user {user_id}")
        turn = await repos.get_turn(s, turn_id)
        if turn is None or turn.session_id != session_id:
            raise TurnNotFound(f"turn {turn_id} not in session {session_id}")
        if not turn.answer:
            raise TurnNotAnswered(f"turn {turn_id} has no answer yet")
        profile_row = await repos.get_profile(s, user_id)

    return {
        "session": sess,
        "turn": turn,
        "profile": profile_row.profile_json if profile_row is not None else {},
    }


def _build_judge_message(*, turn: Any, profile: dict[str, Any]) -> str:
    payload = {
        "question": turn.question,
        "evaluation_anchors": list(turn.anchors_json or []),
        "candidate_answer": turn.answer or "",
        "candidate_profile": profile,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_model_answer_message(
    *,
    turn: Any,
    profile: dict[str, Any],
    hits: list[GroundingHit],
) -> str:
    payload = {
        "question": turn.question,
        "evaluation_anchors": list(turn.anchors_json or []),
        "candidate_answer": turn.answer or "",
        "candidate_profile": profile,
        "grounding": [{"source": h.filename, "text": h.text} for h in hits],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _model_deltas(llm, messages):  # noqa: ANN001
    async for chunk in llm.astream(messages):
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


async def _run_judge_call(
    *,
    turn: Any,
    profile: dict[str, Any],
    temperature: float,
) -> AsyncIterator[tuple[str, Any]]:
    """Yields SSE events for the judge call AND finally yields
    ``("__parsed__", Judgment)`` so the orchestrator can persist it.
    Caller is expected to drop ``__parsed__`` before forwarding.
    """
    user_msg = _build_judge_message(turn=turn, profile=profile)
    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})
    messages = [
        SystemMessage(content=EVALUATOR_JUDGE_SYSTEM),
        HumanMessage(content=user_msg),
    ]

    parsed: dict[str, Any] | None = None
    async for event, data in stream_json_object(
        _model_deltas(llm, messages),
        stream_string_fields=("feedback",),
        scalar_fields=("score",),
    ):
        if event == "feedback_chunk":
            yield ("feedback_token", data)
        elif event in ("score", "feedback_done"):
            yield (event, data)
        elif event == "done":
            parsed = data

    if parsed is None:
        raise StreamingJsonError("judge stream ended without a parsed object")
    try:
        judgment = Judgment.model_validate(parsed)
    except Exception as e:
        raise StreamingJsonError(f"judge JSON failed schema validation: {e}") from e
    yield ("__parsed__", judgment)


async def _run_model_answer_call(
    *,
    turn: Any,
    profile: dict[str, Any],
    hits: list[GroundingHit],
    temperature: float,
) -> AsyncIterator[tuple[str, Any]]:
    user_msg = _build_model_answer_message(turn=turn, profile=profile, hits=hits)
    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})
    messages = [
        SystemMessage(content=MODEL_ANSWER_SYSTEM),
        HumanMessage(content=user_msg),
    ]

    parsed: dict[str, Any] | None = None
    async for event, data in stream_json_object(
        _model_deltas(llm, messages),
        stream_string_fields=("model_answer",),
    ):
        if event == "model_answer_chunk":
            yield ("model_answer_token", data)
        elif event == "model_answer_done":
            yield (event, data)
        elif event == "done":
            parsed = data

    if parsed is None:
        raise StreamingJsonError("model-answer stream ended without a parsed object")
    try:
        ma = ModelAnswerOnly.model_validate(parsed)
    except Exception as e:
        raise StreamingJsonError(f"model-answer JSON failed schema validation: {e}") from e
    yield ("__parsed__", ma)


async def _retrieve_for_turn(*, user_id: uuid.UUID, turn: Any) -> list[GroundingHit]:
    metadata = turn.metadata_json or {}
    focus_label = metadata.get("focus_label")
    query = f"{turn.question} {focus_label or ''}".strip()
    try:
        return await retrieve_grounding(user_id=user_id, query=query, k=4)
    except Exception:  # noqa: BLE001
        # Retrieval failure should not derail the evaluation — fall back
        # to profile-only model answer.
        logger.exception("grounding retrieval failed; falling back to []")
        return []


async def stream_evaluation(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    turn_id: uuid.UUID,
    temperature: float = 0.0,
) -> AsyncIterator[tuple[str, Any]]:
    """Run judge call → model-answer call sequentially. See module docstring."""
    inputs = await _load_eval_inputs(session_id, user_id, turn_id)
    sess = inputs["session"]
    turn = inputs["turn"]
    profile = inputs["profile"]

    if turn.score is not None:
        raise TurnNotFound(f"turn {turn_id} already evaluated")

    logger.info(
        "Evaluator: session=%s turn=%s (turn_index=%d)",
        session_id,
        turn_id,
        turn.turn_index,
    )

    # --- Call 1: judge ---
    judgment: Judgment | None = None
    async for event, data in _run_judge_call(turn=turn, profile=profile, temperature=temperature):
        if event == "__parsed__":
            judgment = data
        else:
            yield (event, data)
    assert judgment is not None  # _run_judge_call raises otherwise

    # --- Call 2: model answer (with grounding) ---
    hits = await _retrieve_for_turn(user_id=user_id, turn=turn)
    logger.info(
        "Evaluator grounding: turn=%s hits=%d (kinds=%s)",
        turn_id,
        len(hits),
        [h.source_doc_kind for h in hits],
    )

    is_last = turn.turn_index + 1 >= sess.n_questions
    new_status = "complete" if is_last else "active"
    n_remaining = max(0, sess.n_questions - (turn.turn_index + 1))

    model_answer: str | None = None
    model_answer_failed_reason: str | None = None
    try:
        async for event, data in _run_model_answer_call(
            turn=turn, profile=profile, hits=hits, temperature=temperature
        ):
            if event == "__parsed__":
                model_answer = data.model_answer
            else:
                yield (event, data)
    except Exception as e:  # noqa: BLE001
        logger.exception("model-answer call failed for turn %s", turn_id)
        model_answer_failed_reason = str(e) or e.__class__.__name__
        yield ("model_answer_error", {"reason": model_answer_failed_reason})

    # --- Persist ---
    async with AsyncSessionLocal() as s:
        if model_answer is not None:
            await repos.update_turn_evaluation(
                s,
                turn_id,
                score=judgment.score,
                feedback=judgment.feedback,
                model_answer=model_answer,
            )
        else:
            await repos.update_turn_evaluation_partial(
                s,
                turn_id,
                score=judgment.score,
                feedback=judgment.feedback,
            )
        if is_last:
            await repos.update_session_status(s, session_id, user_id, "complete")

    yield (
        "done",
        {
            "turn_index": turn.turn_index,
            "session_status": new_status,
            "n_remaining": n_remaining,
        },
    )
