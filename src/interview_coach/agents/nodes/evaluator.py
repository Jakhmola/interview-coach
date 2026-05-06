"""Evaluator agent node.

Streams the evaluation of a single turn. The model emits one JSON object
``{"score": int, "feedback": str, "model_answer": str}`` whose fields are
emitted in that order. The streaming JSON parser routes:

- ``score`` → one ``("score", int)`` event as soon as the integer closes.
- ``feedback`` → ``("feedback_token", str)`` per character, then
  ``("feedback_done", None)`` when the value closes.
- ``model_answer`` → ``("model_answer_token", str)`` per character, then
  ``("model_answer_done", None)``.

At end-of-stream the full JSON is validated via Pydantic and the Turn row
is updated with score, feedback, model_answer in a single transaction. If
the just-evaluated turn is the last (`turn_index + 1 == n_questions`), the
session row is flipped to ``status="complete"`` in the same DB write.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import EVALUATOR_SYSTEM
from interview_coach.agents.schemas import Evaluation
from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_json_object,
)
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model

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


def _build_user_message(*, turn: Any, profile: dict[str, Any]) -> str:
    import json

    payload = {
        "question": turn.question,
        "evaluation_anchors": list(turn.anchors_json or []),
        "candidate_answer": turn.answer or "",
        "candidate_profile": profile,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def stream_evaluation(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    turn_id: uuid.UUID,
    temperature: float = 0.0,
) -> AsyncIterator[tuple[str, Any]]:
    """Generate, stream, and persist the evaluation for `turn_id`.

    Yields:
        ("score", int)
        ("feedback_token", str) ... ("feedback_done", None)
        ("model_answer_token", str) ... ("model_answer_done", None)
        ("done", {"turn_index": int, "session_status": str, "n_remaining": int})

    Raises:
        TurnNotFound, TurnNotAnswered: prereqs.
        StreamingJsonError: model emitted invalid JSON / failed schema.
    """
    inputs = await _load_eval_inputs(session_id, user_id, turn_id)
    sess = inputs["session"]
    turn = inputs["turn"]

    if turn.score is not None:
        # Re-evaluation isn't a v1 feature; the API guards against this too
        # but we re-check here so the node is safe to call directly.
        raise TurnNotFound(f"turn {turn_id} already evaluated")

    user_msg = _build_user_message(turn=turn, profile=inputs["profile"])

    logger.info(
        "Evaluator: session=%s turn=%s (turn_index=%d)",
        session_id,
        turn_id,
        turn.turn_index,
    )

    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})

    async def _model_deltas() -> AsyncIterator[str]:
        async for chunk in llm.astream(
            [
                SystemMessage(content=EVALUATOR_SYSTEM),
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

    # Translate parser-internal `*_chunk` events to the SSE-facing `*_token`
    # names that the Phase 9 wire format documents. This mirrors Phase 8's
    # `question_chunk` → `token` rename in the question generator.
    parsed: dict[str, Any] | None = None
    async for event, data in stream_json_object(
        _model_deltas(),
        stream_string_fields=("feedback", "model_answer"),
        scalar_fields=("score",),
    ):
        if event == "feedback_chunk":
            yield ("feedback_token", data)
        elif event == "model_answer_chunk":
            yield ("model_answer_token", data)
        elif event in ("score", "feedback_done", "model_answer_done"):
            yield (event, data)
        elif event == "done":
            parsed = data

    if parsed is None:
        raise StreamingJsonError("evaluator stream ended without a parsed object")

    try:
        eval_obj = Evaluation.model_validate(parsed)
    except Exception as e:
        raise StreamingJsonError(f"final evaluation failed schema validation: {e}") from e

    is_last = turn.turn_index + 1 >= sess.n_questions
    new_status = "complete" if is_last else "active"
    n_remaining = max(0, sess.n_questions - (turn.turn_index + 1))

    async with AsyncSessionLocal() as s:
        await repos.update_turn_evaluation(
            s,
            turn_id,
            score=eval_obj.score,
            feedback=eval_obj.feedback,
            model_answer=eval_obj.model_answer,
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
