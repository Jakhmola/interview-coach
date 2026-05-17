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

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.profile_view import profile_slice_for_focus
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
from interview_coach.llm.client import astream_with_telemetry, chat_model
from interview_coach.llm.telemetry import set_node_context
from interview_coach.rag.retrieval import GroundingHit, retrieve_grounding

logger = logging.getLogger(__name__)


class TurnNotFound(Exception):
    """Raised when the turn doesn't exist or doesn't belong to the user."""


class TurnNotAnswered(Exception):
    """Caller invoked the evaluator before the answer was saved on the turn."""


async def _load_eval_inputs(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load session, turn, and (when not state-hydrated) profile.

    Phase 20: ``profile`` may be forwarded from interview_graph state and
    skips its DB round-trip when provided.
    """
    async with AsyncSessionLocal() as s:
        sess = await repos.get_session(s, session_id, user_id)
        if sess is None:
            raise TurnNotFound(f"session {session_id} not found for user {user_id}")
        turn = await repos.get_turn(s, turn_id)
        if turn is None or turn.session_id != session_id:
            raise TurnNotFound(f"turn {turn_id} not in session {session_id}")
        if not turn.answer:
            raise TurnNotAnswered(f"turn {turn_id} has no answer yet")
        if profile is None:
            profile_row = await repos.get_profile(s, user_id)
            profile = profile_row.profile_json if profile_row is not None else {}

    return {
        "session": sess,
        "turn": turn,
        "profile": profile,
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
    async for chunk in astream_with_telemetry(llm, messages):
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
    with set_node_context("evaluator_judge"):
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
    with set_node_context("evaluator_model_answer"):
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
    raw_doc_ids = metadata.get("focus_document_ids") or []
    doc_ids: tuple[uuid.UUID, ...] = ()
    if raw_doc_ids:
        parsed: list[uuid.UUID] = []
        for d in raw_doc_ids:
            try:
                parsed.append(uuid.UUID(str(d)))
            except (ValueError, TypeError):
                logger.warning("skipping invalid focus_document_id %r on turn %s", d, turn.id)
        doc_ids = tuple(parsed)
    query = f"{turn.question} {focus_label or ''}".strip()
    try:
        return await retrieve_grounding(user_id=user_id, query=query, k=4, document_ids=doc_ids)
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
    profile: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Run judge call (streaming) with retrieval kicked off in parallel,
    then the model-answer call. See module docstring.

    Phase 20:

    * ``profile`` may be forwarded from interview_graph state to skip the
      per-turn profile DB read.
    * Prompts ship only the focus-anchored slice of the profile (via
      ``profile_slice_for_focus``), not the full 6-12 KB JSON.
    * Retrieval is started concurrently with the judge stream — embedder
      + pgvector and llama.cpp use different resources, so the wall-clock
      cost overlaps with judge token streaming instead of stacking.
    """
    inputs = await _load_eval_inputs(session_id, user_id, turn_id, profile=profile)
    sess = inputs["session"]
    turn = inputs["turn"]
    full_profile = inputs["profile"]

    if turn.score is not None:
        raise TurnNotFound(f"turn {turn_id} already evaluated")

    metadata = turn.metadata_json or {}
    focus_key = metadata.get("focus_key")
    slim_profile = profile_slice_for_focus(full_profile, focus_key)

    logger.info(
        "Evaluator: session=%s turn=%s (turn_index=%d)",
        session_id,
        turn_id,
        turn.turn_index,
    )

    # Kick off retrieval BEFORE starting the judge stream. Judge runs on
    # the llama.cpp service (GPU + small CPU footprint), retrieval hits the
    # embedder sidecar + pgvector (CPU + DB) — different resources, fully
    # overlappable. If the judge fails we cancel the task to avoid a
    # dangling coroutine.
    retrieve_task: asyncio.Task[list[GroundingHit]] = asyncio.create_task(
        _retrieve_for_turn(user_id=user_id, turn=turn)
    )

    # --- Call 1: judge ---
    judgment: Judgment | None = None
    try:
        async for event, data in _run_judge_call(
            turn=turn, profile=slim_profile, temperature=temperature
        ):
            if event == "__parsed__":
                judgment = data
            else:
                yield (event, data)
    except BaseException:
        retrieve_task.cancel()
        raise
    assert judgment is not None  # _run_judge_call raises otherwise

    # --- Wait on the (most likely already-finished) retrieval task ---
    try:
        hits = await retrieve_task
    except Exception:  # noqa: BLE001
        # _retrieve_for_turn already swallows exceptions to []; this is a
        # belt-and-braces guard in case asyncio plumbing surfaces one.
        logger.exception("retrieve task raised unexpectedly; degrading to []")
        hits = []
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
            turn=turn, profile=slim_profile, hits=hits, temperature=temperature
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
