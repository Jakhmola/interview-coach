"""Placeholder for the LangGraph state graph (Phase 10).

Defined here in Phase 6 so node code and downstream agents can reference a
single shared type. Phase 10 wires this into a `StateGraph` and the supervisor.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

RoundType = Literal["resume_walkthrough", "behavioral_star"]


class InterviewState(TypedDict, total=False):
    user_id: str
    session_id: str
    round_type: RoundType

    # Built by ProfileBuilder + JobAnalyzer (Phase 6)
    profile: dict[str, Any] | None
    job: dict[str, Any] | None

    # Built by CompanyResearcher (Phase 7)
    company: dict[str, Any] | None

    # Per-turn fields (Phase 8/9)
    current_question: dict[str, Any] | None
    current_answer: str | None
    evaluation: dict[str, Any] | None
    turn_index: int

    # Supervisor routing (Phase 10)
    next_step: str

    # LangGraph chat history (Phase 10)
    messages: list[Any]
