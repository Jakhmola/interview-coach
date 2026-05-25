"""Phase 27 — typed prep-event protocol (``agents/prep_events.py``).

Covers the four lifecycle models, the ``emit`` writer-call shape, the
reason/outcome Literal gates, and that ``LIFECYCLE_EVENT_NAMES`` stays in
lockstep with the models (the route sources its allowlist from it).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from interview_coach.agents.prep_events import (
    LIFECYCLE_EVENT_NAMES,
    NodeDone,
    NodeError,
    NodeSkipped,
    NodeStarted,
    emit,
)

# --- model_dump shape -------------------------------------------------


def test_node_started_dump_carries_run_reason() -> None:
    assert NodeStarted(node="profile_builder", reason="missing").model_dump() == {
        "event": "node_started",
        "node": "profile_builder",
        "reason": "missing",
    }


def test_node_skipped_dump_carries_skip_reason() -> None:
    assert NodeSkipped(node="job_analyzer", reason="already_analyzed").model_dump() == {
        "event": "node_skipped",
        "node": "job_analyzer",
        "reason": "already_analyzed",
    }


def test_node_done_defaults_to_ok() -> None:
    assert NodeDone(node="job_analyzer").outcome == "ok"


def test_node_done_degraded_carries_code_and_detail() -> None:
    done = NodeDone(node="company_researcher", outcome="degraded", code="NoSearchHits", detail="x")
    assert done.model_dump() == {
        "event": "node_done",
        "node": "company_researcher",
        "outcome": "degraded",
        "code": "NoSearchHits",
        "detail": "x",
    }


# --- emit() writer-call shape ----------------------------------------


def test_emit_calls_writer_with_dump_excluding_none() -> None:
    """A clean node_done is ``{event, node, outcome}`` — exclude_none drops the
    unset code/detail so the wire matches the optional-field contract."""
    calls: list[dict[str, Any]] = []
    emit(calls.append, NodeDone(node="profile_builder"))
    assert calls == [{"event": "node_done", "node": "profile_builder", "outcome": "ok"}]


def test_emit_node_error_drops_unset_node() -> None:
    calls: list[dict[str, Any]] = []
    emit(calls.append, NodeError(code="job_not_found"))
    assert calls == [{"event": "error", "code": "job_not_found"}]


# --- Literal gates ---------------------------------------------------


def test_bad_run_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        NodeStarted(node="profile_builder", reason="cached")  # type: ignore[arg-type]


def test_bad_skip_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        NodeSkipped(node="profile_builder", reason="missing")  # type: ignore[arg-type]


def test_bad_outcome_rejected() -> None:
    with pytest.raises(ValidationError):
        NodeDone(node="company_researcher", outcome="weird")  # type: ignore[arg-type]


# --- allowlist lockstep ----------------------------------------------


def test_lifecycle_event_names_match_models() -> None:
    model_event_names = {
        m.model_fields["event"].default for m in (NodeStarted, NodeSkipped, NodeDone, NodeError)
    }
    assert LIFECYCLE_EVENT_NAMES == model_event_names
