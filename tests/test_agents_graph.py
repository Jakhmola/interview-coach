"""Phase 10 — supervisor graph wiring tests.

These tests exercise the *graph* layer in isolation: nodes are mocked
to write predictable stream events; we assert the graph routes through
them in order, that the prep cache short-circuits emit ``node_skipped``
events, and that the interview graph interrupts cleanly between
question_generator and evaluator.

Real LLM + DB roundtrips are covered by ``test_agents_integration.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


@pytest.fixture
def memory_saver() -> MemorySaver:
    return MemorySaver()


async def _no_unmapped_project_docs(*_a: Any, **_kw: Any) -> list[Any]:
    """Phase 21: every prep_graph test below assumes the user has no
    unmapped project_docs (so the new ``prepare_mapping_suggestion`` node
    emits a single ``node_skipped`` and routes straight to ``job_analyzer``).
    The tests already monkeypatch the other repo reads; this helper keeps
    them DRY for the new one."""
    return []


def _patch_unmapped_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from interview_coach.agents import graph_nodes

    monkeypatch.setattr(
        graph_nodes.repos, "list_unmapped_project_docs_for_user", _no_unmapped_project_docs
    )


async def _no_mapped_doc_ids(*_a: Any, **_kw: Any) -> list[Any]:
    """Phase 25 (B2): node_profile_builder now consults
    ``list_document_mapping_doc_ids_for_user`` when building the cache
    key. Graph tests that stub the docs list also need to stub this so
    the cache key is just the CV id."""
    return []


def _patch_mapped_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from interview_coach.agents import graph_nodes

    monkeypatch.setattr(
        graph_nodes.repos, "list_document_mapping_doc_ids_for_user", _no_mapped_doc_ids
    )


class _UserNoHandle:
    github_handle: str | None = None


async def _get_user_no_handle(*_a: Any, **_kw: Any) -> _UserNoHandle:
    return _UserNoHandle()


async def _no_github_docs(*_a: Any, **_kw: Any) -> list[Any]:
    return []


def _patch_github_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 32: the prep graph now runs a github segment after
    profile_builder. With no handle and no ingested repos it emits a single
    ``node_skipped`` (node="github") and routes to the mapping loop. These
    pure-graph tests stub the two reads it makes so it skips cleanly."""
    from interview_coach.agents import graph_nodes

    monkeypatch.setattr(graph_nodes.repos, "get_user", _get_user_no_handle)
    monkeypatch.setattr(graph_nodes.repos, "list_github_repo_docs_for_user", _no_github_docs)


# --- prep graph ----------------------------------------------------


async def test_prep_graph_runs_three_nodes_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three nodes execute, in order, when no caches are populated.

    The graph hits real Postgres-backed cache reads inside each node
    wrapper — to keep this test pure-graph we monkeypatch
    `repos.get_profile`, etc., to return None and short-circuit the
    actual `build_profile` / `analyze_job` / `research_company` calls
    to avoid LLM/Tavily traffic.
    """
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    calls: list[str] = []

    async def fake_get_profile(*_a: Any, **_kw: Any) -> None:
        return None

    async def fake_get_job(_s: Any, _job_id: Any, _user_id: Any) -> Any:
        class _Job:
            parsed_json: dict[str, Any] | None = None

        return _Job()

    async def fake_get_snapshot(*_a: Any, **_kw: Any) -> None:
        return None

    async def fake_list_documents(*_a: Any, **_kw: Any) -> list[Any]:
        return []

    monkeypatch.setattr(graph_nodes.repos, "get_profile", fake_get_profile)
    monkeypatch.setattr(graph_nodes.repos, "get_job", fake_get_job)
    monkeypatch.setattr(graph_nodes.repos, "get_company_snapshot_by_job", fake_get_snapshot)
    monkeypatch.setattr(graph_nodes.repos, "list_documents_for_user", fake_list_documents)
    _patch_unmapped_empty(monkeypatch)
    _patch_github_empty(monkeypatch)

    class _FakeProfile:
        def model_dump(self) -> dict[str, Any]:
            return {"summary": "x"}

    class _FakeAnalysis:
        def model_dump(self) -> dict[str, Any]:
            return {"company_name": "Acme"}

    class _FakeSnapshot:
        def model_dump(self) -> dict[str, Any]:
            return {"mission": "x"}

    async def fake_build_profile(*_a: Any, **_kw: Any) -> _FakeProfile:
        calls.append("profile_builder")
        return _FakeProfile()

    async def fake_analyze_job(*_a: Any, **_kw: Any) -> _FakeAnalysis:
        calls.append("job_analyzer")
        return _FakeAnalysis()

    async def fake_research_company(*_a: Any, **_kw: Any) -> _FakeSnapshot:
        calls.append("company_researcher")
        return _FakeSnapshot()

    monkeypatch.setattr(graph_nodes, "build_profile", fake_build_profile)
    monkeypatch.setattr(graph_nodes, "analyze_job", fake_analyze_job)
    monkeypatch.setattr(graph_nodes, "research_company", fake_research_company)

    graph = build_prep_graph(None)
    chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "job_id": "00000000-0000-0000-0000-000000000002",
            "force_refresh": False,
        },
        stream_mode="custom",
    ):
        chunks.append(chunk)

    assert calls == ["profile_builder", "job_analyzer", "company_researcher"]
    started = [c for c in chunks if c.get("event") == "node_started"]
    done = [c for c in chunks if c.get("event") == "node_done"]
    assert [c["node"] for c in started] == [
        "profile_builder",
        "job_analyzer",
        "company_researcher",
    ]
    assert [c["node"] for c in done] == [
        "profile_builder",
        "job_analyzer",
        "company_researcher",
    ]


async def test_prep_graph_short_circuits_on_cache_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all three caches hit, no work is done — three `node_skipped`."""
    import uuid

    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    cached_doc_id = uuid.uuid4()

    class _Profile:
        profile_json: dict[str, Any] = {"summary": "cached"}
        source_doc_ids: list[str] = [str(cached_doc_id)]

    class _Doc:
        id = cached_doc_id
        kind = "cv"

    class _Job:
        parsed_json: dict[str, Any] = {"company_name": "Acme"}

    class _Snapshot:
        snapshot_json: dict[str, Any] = {"mission": "cached"}

    async def fake_get_profile(*_a: Any, **_kw: Any) -> _Profile:
        return _Profile()

    async def fake_list_docs(*_a: Any, **_kw: Any) -> list[_Doc]:
        return [_Doc()]

    async def fake_get_job(*_a: Any, **_kw: Any) -> _Job:
        return _Job()

    async def fake_get_snapshot(*_a: Any, **_kw: Any) -> _Snapshot:
        return _Snapshot()

    monkeypatch.setattr(graph_nodes.repos, "get_profile", fake_get_profile)
    monkeypatch.setattr(graph_nodes.repos, "list_documents_for_user", fake_list_docs)
    _patch_unmapped_empty(monkeypatch)
    _patch_github_empty(monkeypatch)
    _patch_mapped_empty(monkeypatch)
    monkeypatch.setattr(graph_nodes.repos, "get_job", fake_get_job)
    monkeypatch.setattr(graph_nodes.repos, "get_company_snapshot_by_job", fake_get_snapshot)

    # If any of these are called, the test should fail loud — they should
    # be skipped entirely.
    def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("expensive node ran despite cache hit")

    monkeypatch.setattr(graph_nodes, "build_profile", _boom)
    monkeypatch.setattr(graph_nodes, "analyze_job", _boom)
    monkeypatch.setattr(graph_nodes, "research_company", _boom)

    graph = build_prep_graph(None)
    chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {"user_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4()), "force_refresh": False},
        stream_mode="custom",
    ):
        chunks.append(chunk)

    skipped = [c for c in chunks if c.get("event") == "node_skipped"]
    # Phase 21.1: with `_patch_unmapped_empty`, prepare_mapping_suggestion
    # emits a `node_skipped` of its own (node="doc_mapping"). Phase 32 adds
    # the github segment, which skips (node="github") when no handle/repos.
    assert [c["node"] for c in skipped] == [
        "profile_builder",
        "github",
        "doc_mapping",
        "job_analyzer",
        "company_researcher",
    ]


async def test_prep_graph_force_refresh_runs_company_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`force_refresh=True` skips other caches but re-runs company_researcher."""
    import uuid

    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    cached_doc_id = uuid.uuid4()

    class _Profile:
        profile_json: dict[str, Any] = {"summary": "x"}
        source_doc_ids: list[str] = [str(cached_doc_id)]

    class _Doc:
        id = cached_doc_id
        kind = "cv"

    class _Job:
        parsed_json: dict[str, Any] = {"company_name": "Acme"}

    class _Snapshot:
        model_name = "qwen3-8b"
        snapshot_json: dict[str, Any] = {"mission": "x"}

    async def _ret(v: Any) -> Any:
        return v

    async def fake_get_profile(*_a: Any, **_kw: Any) -> _Profile:
        return _Profile()

    async def fake_list_docs(*_a: Any, **_kw: Any) -> list[_Doc]:
        return [_Doc()]

    async def fake_get_job(*_a: Any, **_kw: Any) -> _Job:
        return _Job()

    async def fake_get_snapshot(*_a: Any, **_kw: Any) -> _Snapshot:
        return _Snapshot()

    monkeypatch.setattr(graph_nodes.repos, "get_profile", fake_get_profile)
    monkeypatch.setattr(graph_nodes.repos, "list_documents_for_user", fake_list_docs)
    _patch_unmapped_empty(monkeypatch)
    _patch_github_empty(monkeypatch)
    _patch_mapped_empty(monkeypatch)
    monkeypatch.setattr(graph_nodes.repos, "get_job", fake_get_job)
    monkeypatch.setattr(graph_nodes.repos, "get_company_snapshot_by_job", fake_get_snapshot)

    research_calls: list[bool] = []

    class _NewSnapshot:
        def model_dump(self) -> dict[str, Any]:
            return {"mission": "fresh"}

    async def fake_research(*_a: Any, force_refresh: bool = False, **_kw: Any) -> _NewSnapshot:
        research_calls.append(force_refresh)
        return _NewSnapshot()

    monkeypatch.setattr(graph_nodes, "research_company", fake_research)

    graph = build_prep_graph(None)
    chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {"user_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4()), "force_refresh": True},
        stream_mode="custom",
    ):
        chunks.append(chunk)

    assert research_calls == [True]
    skipped_nodes = [c["node"] for c in chunks if c.get("event") == "node_skipped"]
    started_nodes = [c["node"] for c in chunks if c.get("event") == "node_started"]
    assert "company_researcher" in started_nodes
    assert "company_researcher" not in skipped_nodes


# --- prep graph: profile re-run when CV replaced -----------------


async def test_profile_node_reruns_when_doc_ids_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the user's current docs differ from the cached profile's
    `source_doc_ids`, profile_builder re-runs (no cache hit)."""
    import uuid

    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    old_doc_id = uuid.uuid4()
    new_doc_id = uuid.uuid4()

    class _Profile:
        profile_json: dict[str, Any] = {"summary": "stale"}
        source_doc_ids: list[str] = [str(old_doc_id)]

    class _Doc:
        id = new_doc_id
        kind = "cv"

    class _Job:
        parsed_json: dict[str, Any] = {"company_name": "Acme"}

    class _Snapshot:
        snapshot_json: dict[str, Any] = {"mission": "cached"}

    async def fake_get_profile(*_a: Any, **_kw: Any) -> _Profile:
        return _Profile()

    async def fake_list_docs(*_a: Any, **_kw: Any) -> list[_Doc]:
        return [_Doc()]

    async def fake_get_job(*_a: Any, **_kw: Any) -> _Job:
        return _Job()

    async def fake_get_snapshot(*_a: Any, **_kw: Any) -> _Snapshot:
        return _Snapshot()

    monkeypatch.setattr(graph_nodes.repos, "get_profile", fake_get_profile)
    monkeypatch.setattr(graph_nodes.repos, "list_documents_for_user", fake_list_docs)
    _patch_unmapped_empty(monkeypatch)
    _patch_github_empty(monkeypatch)
    _patch_mapped_empty(monkeypatch)
    monkeypatch.setattr(graph_nodes.repos, "get_job", fake_get_job)
    monkeypatch.setattr(graph_nodes.repos, "get_company_snapshot_by_job", fake_get_snapshot)

    rebuilt = []

    class _FreshProfile:
        def model_dump(self) -> dict[str, Any]:
            return {"summary": "fresh"}

    async def fake_build_profile(*_a: Any, **_kw: Any) -> _FreshProfile:
        rebuilt.append(True)
        return _FreshProfile()

    monkeypatch.setattr(graph_nodes, "build_profile", fake_build_profile)

    graph = build_prep_graph(None)
    chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {"user_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4()), "force_refresh": False},
        stream_mode="custom",
    ):
        chunks.append(chunk)

    assert rebuilt == [True]
    skipped = [c["node"] for c in chunks if c.get("event") == "node_skipped"]
    assert "profile_builder" not in skipped


# --- interview graph: open → interrupt → conduct(advance) → evaluator (Phase 34)


def _interview_initial_state(round_type: str = "experience_deep_dive") -> dict[str, Any]:
    import uuid

    return {
        "user_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "round_type": round_type,
        "n_questions": 1,
        "thread_id": None,
        "thread_index": 0,
        "anchors": [],
        "focus_key": None,
        "focus_label": None,
        "focus_document_ids": [],
        "grounding": [],
        "followups_used": 0,
        "pending_message": None,
        "next_move": None,
    }


async def _fake_open_thread(**_kw: Any) -> AsyncIterator[tuple[str, Any]]:
    """Stand-in for stream_open_thread: emit the open envelope + an ``opened``
    carry the node folds into state."""
    yield ("move", {"kind": "question", "thread_index": 0, "message_id": "m0"})
    yield ("token", "Q")
    yield ("move_done", {"anchors": ["a", "b"]})
    yield (
        "opened",
        {
            "thread_id": "33333333-3333-3333-3333-333333333333",
            "thread_index": 0,
            "focus_key": "k",
            "focus_label": "L",
            "focus_document_ids": [],
            "anchors": ["a", "b"],
            "grounding": [],
        },
    )


async def _fake_eval_complete(**_kw: Any) -> AsyncIterator[tuple[str, Any]]:
    yield ("evaluation", {"thread_index": 0})
    yield ("score", 8)
    yield ("feedback_token", "good")
    yield ("feedback_done", None)
    yield ("model_answer_token", "ideal")
    yield ("model_answer_done", None)
    yield ("evaluation_done", {"thread_index": 0, "session_status": "complete", "n_remaining": 0})
    yield ("wrap", {"session_status": "complete"})


async def _noop_persist(_state: Any) -> bool:
    """Skip the candidate-message DB write — these are pure graph-shape tests."""
    return False


async def test_interview_graph_opens_interrupts_then_advances_to_evaluator(
    monkeypatch: pytest.MonkeyPatch, memory_saver: MemorySaver
) -> None:
    """Fresh start opens a thread and stops at the candidate interrupt; the
    resume conducts an ``advance`` which routes to the evaluator → END.

    The agent functions are mocked so this is a pure graph-shape test: did the
    wiring honor the interrupt boundary and the advance→evaluator edge?
    """
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_interview_graph

    async def fake_conduct(**_kw: Any) -> AsyncIterator[tuple[str, Any]]:
        yield ("advance", {})

    monkeypatch.setattr(graph_nodes, "stream_open_thread", _fake_open_thread)
    monkeypatch.setattr(graph_nodes, "stream_conduct", fake_conduct)
    monkeypatch.setattr(graph_nodes, "stream_thread_evaluation", _fake_eval_complete)
    monkeypatch.setattr(graph_nodes, "_persist_pending_candidate", _noop_persist)

    graph = build_interview_graph(memory_saver)
    cfg = {"configurable": {"thread_id": "interview:t1"}}

    first_chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(_interview_initial_state(), config=cfg, stream_mode="custom"):
        first_chunks.append(chunk)

    # The thread opened (move + token); the evaluator did NOT run yet.
    assert any(c.get("event") == "move" for c in first_chunks)
    assert any(c.get("event") == "token" for c in first_chunks)
    assert not any(c.get("event") == "score" for c in first_chunks)

    # Resume with the candidate's answer → conduct returns advance → evaluator.
    resume_chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(resume={"message": "my answer"}), config=cfg, stream_mode="custom"
    ):
        resume_chunks.append(chunk)

    assert any(c.get("event") == "evaluation" for c in resume_chunks)
    assert any(c.get("event") == "score" for c in resume_chunks)
    assert any(c.get("event") == "wrap" for c in resume_chunks)
    feedback_tokens = [c["data"] for c in resume_chunks if c.get("event") == "feedback_token"]
    assert "".join(feedback_tokens) == "good"


async def test_interview_graph_budget_guard_forces_advance(
    monkeypatch: pytest.MonkeyPatch, memory_saver: MemorySaver
) -> None:
    """The per-thread follow-up cap is a node-side guard: once spent, the
    interviewer forces ``advance`` WITHOUT another conductor call.

    behavioral_star caps at max_followups=1. So: open → answer → probe
    (followups_used 0→1) → answer → guard fires (1 >= 1) → advance → evaluate
    → complete. The conductor is mocked to *always* probe, so the only way the
    session can terminate is the guard — and we assert conduct ran exactly
    once.
    """
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_interview_graph

    conduct_calls: list[int] = []

    async def fake_conduct_always_probe(**_kw: Any) -> AsyncIterator[tuple[str, Any]]:
        conduct_calls.append(1)
        yield ("move", {"kind": "probe", "thread_index": 0, "message_id": "p0"})
        yield ("token", "probe?")
        yield ("move_done", {})
        yield ("conducted", {"action": "probe", "message_id": "p0"})

    monkeypatch.setattr(graph_nodes, "stream_open_thread", _fake_open_thread)
    monkeypatch.setattr(graph_nodes, "stream_conduct", fake_conduct_always_probe)
    monkeypatch.setattr(graph_nodes, "stream_thread_evaluation", _fake_eval_complete)
    monkeypatch.setattr(graph_nodes, "_persist_pending_candidate", _noop_persist)

    graph = build_interview_graph(memory_saver)
    cfg = {"configurable": {"thread_id": "interview:t2"}}

    # Fresh start: opens the thread, pauses for the answer.
    async for _ in graph.astream(
        _interview_initial_state(round_type="behavioral_star"), config=cfg, stream_mode="custom"
    ):
        pass

    # Answer #1: conductor probes (consumes the one allowed follow-up), pauses.
    probe_chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(resume={"message": "a1"}), config=cfg, stream_mode="custom"
    ):
        probe_chunks.append(chunk)
    assert any(c.get("event") == "move" and c.get("kind") == "probe" for c in probe_chunks)
    assert not any(c.get("event") == "score" for c in probe_chunks)

    # Answer #2: budget exhausted → forced advance → evaluator → complete.
    final_chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(resume={"message": "a2"}), config=cfg, stream_mode="custom"
    ):
        final_chunks.append(chunk)
    assert any(c.get("event") == "wrap" for c in final_chunks)
    # Conduct ran exactly once (answer #1); answer #2 used the guard, not the LLM.
    assert sum(conduct_calls) == 1
