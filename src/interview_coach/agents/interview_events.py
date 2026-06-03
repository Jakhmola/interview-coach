"""Interview action-envelope events (Phase 34) — one typed owner for the wire.

The conversational interview streams a **typed action envelope** over the
single ``POST /sessions/{id}/message`` endpoint (ADR 0004). This module is that
envelope's single owner, mirroring ``prep_events.py``: the framing events
(``move`` / ``move_done`` / ``evaluation`` / ``evaluation_done`` / ``wrap`` /
``error``) are pydantic models, and the route's forwarding allowlist sources
its names from here so the three copies of the contract (graph node, route
pump, ``api.ts`` union) can't drift.

The envelope only *frames*; the Phase-9 token streams it wraps —
``token`` (the move's utterance), and ``score`` / ``feedback_token`` /
``feedback_done`` / ``model_answer_token`` / ``model_answer_done`` /
``model_answer_error`` (a thread-close evaluation) — survive verbatim inside
it (``INNER_EVENT_NAMES``), kept the same restraint ADR 0001 used.

``emit(writer, event)`` is the only way a node should push a framing event; it
funnels through ``model_dump(exclude_none=True)`` so a bad Literal raises at
emit time instead of putting a junk dict on the wire.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

MoveKind = Literal["question", "probe", "clarify", "nudge"]


class Move(BaseModel):
    """An interviewer utterance is starting — its tokens stream next as
    ``token`` events, closed by ``move_done``."""

    event: Literal["move"] = "move"
    kind: MoveKind
    thread_index: int
    message_id: str


class MoveDone(BaseModel):
    """The interviewer utterance finished. ``anchors`` rides only on a
    thread-open ``question`` (the thread's fixed agenda)."""

    event: Literal["move_done"] = "move_done"
    anchors: list[str] | None = None


class EvaluationStart(BaseModel):
    """A thread is closing — its evaluation streams next (score → feedback →
    model answer), closed by ``evaluation_done``."""

    event: Literal["evaluation"] = "evaluation"
    thread_index: int


class EvaluationDone(BaseModel):
    """The thread-close evaluation finished."""

    event: Literal["evaluation_done"] = "evaluation_done"
    thread_index: int
    session_status: str
    n_remaining: int


class Wrap(BaseModel):
    """The session terminal — reached when ``advance`` fires with the topic
    budget spent. Not an interviewer move."""

    event: Literal["wrap"] = "wrap"
    session_status: Literal["complete"] = "complete"


class InterviewError(BaseModel):
    """A node hit a failure. The route forwards this then terminates the
    stream."""

    event: Literal["error"] = "error"
    code: str
    detail: str | None = None


InterviewEnvelopeEvent = Move | MoveDone | EvaluationStart | EvaluationDone | Wrap | InterviewError

# The framing half — the events this module owns.
ENVELOPE_EVENT_NAMES: frozenset[str] = frozenset(
    {"move", "move_done", "evaluation", "evaluation_done", "wrap", "error"}
)

# The Phase-9 sub-protocol reused verbatim INSIDE the envelope. Owned by the
# evaluator / interviewer nodes (emitted as raw dicts), listed here so the
# route's single allowlist covers them too.
INNER_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "token",
        "score",
        "feedback_token",
        "feedback_done",
        "model_answer_token",
        "model_answer_done",
        "model_answer_error",
    }
)

# The route's forwarding allowlist sources from here so it can't drift from the
# models / inner protocol above. Kept in lockstep by ``test_interview_events``.
INTERVIEW_EVENT_NAMES: frozenset[str] = ENVELOPE_EVENT_NAMES | INNER_EVENT_NAMES


def emit(writer: Callable[[dict[str, Any]], Any], event: InterviewEnvelopeEvent) -> None:
    """Push a typed envelope event onto the graph's custom stream.

    ``exclude_none`` keeps the wire shape matching the optional fields in the
    contract (a thread-open ``move_done`` carries ``anchors``; a follow-up
    ``move_done`` is bare)."""
    writer(event.model_dump(exclude_none=True))
