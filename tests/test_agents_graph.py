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
    # emits a `node_skipped` of its own (node="doc_mapping"). All four
    # prep_graph cache layers short-circuit.
    assert [c["node"] for c in skipped] == [
        "profile_builder",
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


# --- interview graph: question → interrupt → resume → evaluator -


async def test_interview_graph_interrupts_then_resumes(
    monkeypatch: pytest.MonkeyPatch, memory_saver: MemorySaver
) -> None:
    """First astream stops at the interrupt; resume runs evaluator to END.

    `stream_question` and `stream_evaluation` are mocked so this is a
    pure graph-shape test: did the wiring honor the interrupt boundary?
    """
    import uuid

    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_interview_graph

    async def fake_stream_question(**_kw: Any) -> AsyncIterator[tuple[str, Any]]:
        yield ("token", "Q")
        yield ("done", {"question_id": "11111111-1111-1111-1111-111111111111", "turn_index": 0})

    async def fake_stream_evaluation(**_kw: Any) -> AsyncIterator[tuple[str, Any]]:
        yield ("score", 8)
        yield ("feedback_token", "good")
        yield ("feedback_done", None)
        yield ("model_answer_token", "ideal")
        yield ("model_answer_done", None)
        yield ("done", {"turn_index": 0, "session_status": "active", "n_remaining": 0})

    monkeypatch.setattr(graph_nodes, "stream_question", fake_stream_question)
    monkeypatch.setattr(graph_nodes, "stream_evaluation", fake_stream_evaluation)

    graph = build_interview_graph(memory_saver)
    cfg = {"configurable": {"thread_id": "t1"}}

    first_chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {
            "user_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "round_type": "resume_walkthrough",
            "n_questions": 1,
            "turn_index": 0,
        },
        config=cfg,
        stream_mode="custom",
    ):
        first_chunks.append(chunk)

    # Question_generator emitted token + done; evaluator did NOT run yet.
    assert any(c.get("event") == "token" for c in first_chunks)
    assert not any(c.get("event") == "score" for c in first_chunks)

    # Resume — graph re-executes the question_generator (LangGraph 1.x
    # behavior), but our `await_answer` node is a separate single-purpose
    # node so the route only sees the evaluator side-effects this time.
    resume_chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(resume={"answer": "my answer"}), config=cfg, stream_mode="custom"
    ):
        resume_chunks.append(chunk)

    assert any(c.get("event") == "score" for c in resume_chunks)
    feedback_tokens = [c["data"] for c in resume_chunks if c.get("event") == "feedback_token"]
    assert "".join(feedback_tokens) == "good"
