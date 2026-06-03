"""Phase 34 — typed interview action-envelope protocol
(``agents/interview_events.py``).

Covers the envelope models, the ``emit`` writer-call shape (exclude_none), the
``MoveKind`` / ``Wrap`` Literal gates, and that the route's three allowlists
(``ENVELOPE_EVENT_NAMES`` / ``INNER_EVENT_NAMES`` / ``INTERVIEW_EVENT_NAMES``)
stay in lockstep with the models + the documented inner sub-protocol.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from interview_coach.agents.interview_events import (
    ENVELOPE_EVENT_NAMES,
    INNER_EVENT_NAMES,
    INTERVIEW_EVENT_NAMES,
    EvaluationDone,
    EvaluationStart,
    InterviewError,
    Move,
    MoveDone,
    Wrap,
    emit,
)

# --- model_dump shape -------------------------------------------------


def test_move_dump_carries_kind_and_ids() -> None:
    assert Move(kind="question", thread_index=0, message_id="m0").model_dump() == {
        "event": "move",
        "kind": "question",
        "thread_index": 0,
        "message_id": "m0",
    }


def test_move_done_with_anchors_dump() -> None:
    assert MoveDone(anchors=["a", "b"]).model_dump() == {
        "event": "move_done",
        "anchors": ["a", "b"],
    }


def test_evaluation_done_dump() -> None:
    assert EvaluationDone(thread_index=2, session_status="active", n_remaining=1).model_dump() == {
        "event": "evaluation_done",
        "thread_index": 2,
        "session_status": "active",
        "n_remaining": 1,
    }


def test_wrap_defaults_to_complete() -> None:
    assert Wrap().session_status == "complete"
    assert Wrap().model_dump() == {"event": "wrap", "session_status": "complete"}


# --- emit() writer-call shape ----------------------------------------


def test_emit_move_done_excludes_unset_anchors() -> None:
    """A follow-up ``move_done`` carries no anchors — exclude_none drops the
    field so the wire matches the optional-field contract (anchors ride only on
    a thread-open question)."""
    calls: list[dict[str, Any]] = []
    emit(calls.append, MoveDone())
    assert calls == [{"event": "move_done"}]


def test_emit_move_done_with_anchors() -> None:
    calls: list[dict[str, Any]] = []
    emit(calls.append, MoveDone(anchors=["x"]))
    assert calls == [{"event": "move_done", "anchors": ["x"]}]


def test_emit_error_drops_unset_detail() -> None:
    calls: list[dict[str, Any]] = []
    emit(calls.append, InterviewError(code="session_complete"))
    assert calls == [{"event": "error", "code": "session_complete"}]


# --- Literal gates ---------------------------------------------------


def test_bad_move_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        Move(kind="advance", thread_index=0, message_id="m0")  # type: ignore[arg-type]


def test_bad_wrap_status_rejected() -> None:
    with pytest.raises(ValidationError):
        Wrap(session_status="active")  # type: ignore[arg-type]


# --- allowlist lockstep ----------------------------------------------


def test_envelope_event_names_match_models() -> None:
    model_event_names = {
        m.model_fields["event"].default
        for m in (Move, MoveDone, EvaluationStart, EvaluationDone, Wrap, InterviewError)
    }
    assert ENVELOPE_EVENT_NAMES == model_event_names


def test_interview_event_names_is_union_of_envelope_and_inner() -> None:
    assert INTERVIEW_EVENT_NAMES == ENVELOPE_EVENT_NAMES | INNER_EVENT_NAMES
    # The two halves are disjoint — framing vs. the Phase-9 token sub-protocol.
    assert ENVELOPE_EVENT_NAMES.isdisjoint(INNER_EVENT_NAMES)


def test_inner_event_names_cover_the_phase9_subprotocol() -> None:
    assert INNER_EVENT_NAMES == {
        "token",
        "score",
        "feedback_token",
        "feedback_done",
        "model_answer_token",
        "model_answer_done",
        "model_answer_error",
    }
