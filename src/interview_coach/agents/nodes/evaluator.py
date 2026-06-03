"""Thread-close evaluator agent node — Phase 14 split, Phase 34 thread grain.

A thread (one topic) is evaluated **once at close**, over its whole transcript
(the root question, the interviewer's follow-up moves, and the candidate's
answers). The evaluation is two sequential LLM calls (single-GPU, qwen3:8b
VRAM-bound; parallelism would queue or spill to CPU):

  1. **Judge call** — emits ``{score, feedback}`` over the transcript. No
     grounding injected, so the rubric stays untouched by retrieval noise.
  2. **Model-answer call** — emits ``{model_answer}``, reusing the grounding
     retrieved ONCE at thread-open (carried in graph state; ``[]`` for
     non-experience rounds), so the reference answer can speak with
     project-specific detail in the candidate's first-person voice.

Wire format to the SSE consumer (framed by the action envelope upstream):
    evaluation → score → feedback_token* → feedback_done →
    model_answer_token* → model_answer_done → evaluation_done [→ wrap]

If the model-answer call fails, the orchestrator persists score+feedback only
(``close_thread`` with ``model_answer=None``) and emits ``model_answer_error``.
The session status flip to ``complete`` on the last thread happens after both
calls, on the persist path.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.profile_view import profile_slice_for_focus
from interview_coach.agents.prompts import EVALUATOR_JUDGE_SYSTEM
from interview_coach.agents.rounds import get_round_strategy
from interview_coach.agents.schemas import Judgment, ModelAnswerOnly
from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_json_object,
)
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import astream_with_telemetry, chat_model
from interview_coach.llm.telemetry import set_node_context

logger = logging.getLogger(__name__)


class ThreadNotFound(Exception):
    """Raised when the thread doesn't exist, isn't in the session, or has
    already been closed (evaluated)."""


def _build_judge_message(
    *, root_question: str, transcript: list[dict[str, Any]], anchors: list[str], profile: dict
) -> str:
    payload = {
        "question": root_question,
        "transcript": transcript,
        "evaluation_anchors": list(anchors or []),
        "candidate_profile": profile,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_model_answer_message(
    *,
    root_question: str,
    transcript: list[dict[str, Any]],
    anchors: list[str],
    profile: dict,
    grounding: list[dict[str, Any]],
) -> str:
    payload = {
        "question": root_question,
        "transcript": transcript,
        "evaluation_anchors": list(anchors or []),
        "candidate_profile": profile,
        "grounding": grounding,
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


async def _run_judge_call(*, user_msg: str, temperature: float) -> AsyncIterator[tuple[str, Any]]:
    """Yields SSE events for the judge call AND finally yields
    ``("__parsed__", Judgment)`` so the orchestrator can persist it."""
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
    *, user_msg: str, temperature: float, model_answer_system: str
) -> AsyncIterator[tuple[str, Any]]:
    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})
    messages = [
        SystemMessage(content=model_answer_system),
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


async def stream_thread_evaluation(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    thread_index: int,
    anchors: list[str],
    grounding: list[dict[str, Any]],
    focus_key: str | None,
    round_type: str,
    temperature: float = 0.0,
    profile: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Evaluate one thread over its whole transcript, persist on the Thread,
    close it, and flip the session ``complete`` on the last thread.

    Yields the framed evaluation events (see module docstring), then
    ``("evaluation_done", {...})`` and, if this was the last thread,
    ``("wrap", {"session_status": "complete"})``.
    """
    async with AsyncSessionLocal() as s:
        sess = await repos.get_session(s, session_id, user_id)
        if sess is None:
            raise ThreadNotFound(f"session {session_id} not found for user {user_id}")
        thread = await repos.get_thread(s, thread_id)
        if thread is None or thread.session_id != session_id:
            raise ThreadNotFound(f"thread {thread_id} not in session {session_id}")
        if thread.status != "open":
            raise ThreadNotFound(f"thread {thread_id} already closed")
        messages = list(await repos.list_messages_for_thread(s, thread_id))
        if profile is None:
            profile_row = await repos.get_profile(s, user_id)
            profile = profile_row.profile_json if profile_row is not None else {}

    transcript = [{"role": m.role, "kind": m.kind, "text": m.text} for m in messages]
    root_question = next((m.text for m in messages if m.role == "interviewer"), "")
    slim_profile = profile_slice_for_focus(profile, focus_key)
    strategy = get_round_strategy(round_type)

    logger.info(
        "Evaluator(thread-close): session=%s thread=%s (thread_index=%d) round=%s grounding=%d",
        session_id,
        thread_id,
        thread_index,
        round_type,
        len(grounding or []),
    )

    yield ("evaluation", {"thread_index": thread_index})

    # --- Call 1: judge ---
    judge_msg = _build_judge_message(
        root_question=root_question,
        transcript=transcript,
        anchors=anchors,
        profile=slim_profile,
    )
    judgment: Judgment | None = None
    async for event, data in _run_judge_call(user_msg=judge_msg, temperature=temperature):
        if event == "__parsed__":
            judgment = data
        else:
            yield (event, data)
    assert judgment is not None  # _run_judge_call raises otherwise

    # --- Call 2: model answer (reuses carried thread-open grounding) ---
    model_answer_msg = _build_model_answer_message(
        root_question=root_question,
        transcript=transcript,
        anchors=anchors,
        profile=slim_profile,
        grounding=list(grounding or []),
    )
    model_answer: str | None = None
    try:
        async for event, data in _run_model_answer_call(
            user_msg=model_answer_msg,
            temperature=temperature,
            model_answer_system=strategy.model_answer_system,
        ):
            if event == "__parsed__":
                model_answer = data.model_answer
            else:
                yield (event, data)
    except Exception as e:  # noqa: BLE001
        logger.exception("model-answer call failed for thread %s", thread_id)
        yield ("model_answer_error", {"reason": str(e) or e.__class__.__name__})

    # --- Persist: close the thread, flip the session on the last one ---
    async with AsyncSessionLocal() as s:
        await repos.close_thread(
            s,
            thread_id,
            score=judgment.score,
            feedback=judgment.feedback,
            model_answer=model_answer,
        )
        n_closed = await repos.count_closed_threads(s, session_id)
        is_last = n_closed >= sess.n_questions
        if is_last:
            await repos.update_session_status(s, session_id, user_id, "complete")

    new_status = "complete" if is_last else "active"
    n_remaining = max(0, sess.n_questions - n_closed)
    yield (
        "evaluation_done",
        {
            "thread_index": thread_index,
            "session_status": new_status,
            "n_remaining": n_remaining,
        },
    )
    if is_last:
        yield ("wrap", {"session_status": "complete"})
