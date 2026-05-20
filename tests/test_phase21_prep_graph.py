"""Phase 21.1 — prep_graph checkpointer + doc_mapping HITL loop + G4 fix.

Coverage areas:

1. ``list_unmapped_project_docs_for_user`` — only project_docs without
   document_mappings rows, oldest-first (so the wizard feels sequential).
2. G4 fix — ``apply_mapping`` adds the project_doc id to
   ``source_doc_ids``; ``revert_mapping`` removes it.
3. ``build_profile`` re-applies existing document_mappings on rebuild so
   a CV re-extract doesn't silently wipe prior enrichments.
4. ``node_doc_mapping`` loop end-to-end via prep_graph.astream:
   * happy path: confirm a mapping → graph advances → END.
   * skip: user skips the doc → it stays unmapped + skiplist'd in state.
   * intake failure: ``run_intake`` raises → fanout skips + advances.
   * multi-doc resume: confirm doc 1, skip doc 2, end at job_analyzer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import doc_intake, profile_builder
from interview_coach.db import models, repos
from interview_coach.db.models import Document, User


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def alice(db: async_sessionmaker) -> User:
    async with db() as s:
        return await repos.create_user(s, "alice@example.com", "x")


async def _make_cv(db: async_sessionmaker, user: User) -> Document:
    async with db() as s:
        return await repos.create_document(
            s,
            user_id=user.id,
            kind="cv",
            filename="resume.pdf",
            content_type="application/pdf",
            byte_size=10,
            raw_text="Alice resume body.",
        )


async def _make_project_doc(db: async_sessionmaker, user: User, *, title: str) -> Document:
    async with db() as s:
        doc = await repos.create_document(
            s,
            user_id=user.id,
            kind="project_doc",
            filename=f"{title}.md",
            content_type="text/markdown",
            byte_size=20,
            raw_text=f"Project {title} body.",
        )
        await repos.update_document_title(s, doc.id, user.id, title)
        return doc


# --- repo: list_unmapped_project_docs_for_user ------------------------


async def test_list_unmapped_project_docs_only_returns_project_docs_without_mappings(
    db: async_sessionmaker, alice: User
) -> None:
    await _make_cv(db, alice)  # CV should never be listed.
    unmapped = await _make_project_doc(db, alice, title="A")
    mapped = await _make_project_doc(db, alice, title="B")

    async with db() as s:
        await repos.replace_document_mappings(
            s,
            document_id=mapped.id,
            user_id=alice.id,
            rows=[
                {
                    "mapping_kind": "project",
                    "experience_idx": None,
                    "highlight_idx": None,
                    "project_idx": None,
                    "extracted_json": {},
                }
            ],
        )

    async with db() as s:
        results = await repos.list_unmapped_project_docs_for_user(s, alice.id)
    ids = [d.id for d in results]
    assert unmapped.id in ids
    assert mapped.id not in ids


# --- G4 fix: source_doc_ids tracking ---------------------------------


async def test_apply_mapping_adds_project_doc_to_source_doc_ids(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop_async)

    cv = await _make_cv(db, alice)
    proj = await _make_project_doc(db, alice, title="Proj")

    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=alice.id,
            profile_json=_simple_profile(),
            source_doc_ids=[str(cv.id)],
            model_name="qwen3-8b",
        )

    n = await doc_intake.apply_mapping(
        document_id=proj.id,
        user_id=alice.id,
        rows=[{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
        extracted={"tech_stack": ["python"], "description": "did stuff", "urls": []},
        project_title="Proj",
    )
    assert n == 1

    async with db() as s:
        profile = await repos.get_profile(s, alice.id)
    assert profile is not None
    assert sorted(str(x) for x in (profile.source_doc_ids or [])) == sorted(
        [str(cv.id), str(proj.id)]
    )


async def test_revert_mapping_removes_project_doc_from_source_doc_ids(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop_async)

    cv = await _make_cv(db, alice)
    proj = await _make_project_doc(db, alice, title="Proj")
    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=alice.id,
            profile_json=_simple_profile(),
            source_doc_ids=[str(cv.id)],
            model_name="qwen3-8b",
        )

    await doc_intake.apply_mapping(
        document_id=proj.id,
        user_id=alice.id,
        rows=[{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
        extracted={"tech_stack": ["python"], "description": "did stuff", "urls": []},
        project_title="Proj",
    )

    await doc_intake.revert_mapping(document_id=proj.id, user_id=alice.id)

    async with db() as s:
        profile = await repos.get_profile(s, alice.id)
    assert profile is not None
    ids = list(profile.source_doc_ids or [])
    assert str(proj.id) not in ids
    assert str(cv.id) in ids


# --- build_profile re-applies existing mappings (B1) -----------------


async def test_build_profile_reapplies_existing_mappings(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CV re-extract must preserve any prior project_doc enrichments
    so the user doesn't silently lose mappings they previously confirmed."""
    monkeypatch.setattr(profile_builder, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop_async)

    cv = await _make_cv(db, alice)
    proj = await _make_project_doc(db, alice, title="Bench")

    # Seed a profile that's already had a project_doc mapping applied.
    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=alice.id,
            profile_json=_simple_profile(),
            source_doc_ids=[str(cv.id)],
            model_name="qwen3-8b",
        )
    await doc_intake.apply_mapping(
        document_id=proj.id,
        user_id=alice.id,
        rows=[{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
        extracted={"tech_stack": ["python"], "description": "Bench description.", "urls": []},
        project_title="Bench",
    )

    # Stub the LLM to return a *fresh* profile (the kind a CV re-extract
    # would produce) — same structure but no enrichments.
    from interview_coach.agents.schemas import Profile

    async def fake_chat_model_structured(_schema, _messages, *, temperature, **_overrides):
        return Profile.model_validate(_simple_profile())

    monkeypatch.setattr(profile_builder, "chat_model_structured", fake_chat_model_structured)

    rebuilt = await profile_builder.build_profile(alice.id)

    # The mapping should have been re-applied, so the highlight tracks
    # the project_doc as a source.
    hl = rebuilt.experiences[0].highlights[0]
    assert str(proj.id) in [str(d) for d in hl.source_document_ids]
    assert "python" in hl.tech_stack
    assert hl.description == "Bench description."

    # source_doc_ids reflects CV + project_doc (cache key now matches the
    # user's actual document list).
    async with db() as s:
        profile_row = await repos.get_profile(s, alice.id)
    assert profile_row is not None
    assert sorted(str(x) for x in profile_row.source_doc_ids or []) == sorted(
        [str(cv.id), str(proj.id)]
    )


# --- node_doc_mapping HITL loop --------------------------------------


def _prep_thread(user_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"prep:{user_id}:{job_id}"}}


async def _seed_caches(
    db: async_sessionmaker, *, user: User, job_id: uuid.UUID, doc_ids: list[uuid.UUID]
) -> None:
    """Pre-seed profile / job parsed_json / company snapshot so every
    pre-mapping node in prep_graph short-circuits via its own cache —
    keeps the mapping-loop tests focused on what they're testing.

    Phase 25 (B2): ``source_doc_ids`` now mirrors the profile-contributing
    document set (CV + project_docs with mapping rows), not the user's
    full doc list. Callers pass *all* doc ids they own; we filter to
    what the new cache key actually compares against so the seed
    matches reality.
    """
    seed_source_ids: list[str] = []
    async with db() as s:
        docs = await repos.list_documents_for_user(s, user.id)
        cv_ids = {d.id for d in docs if d.kind == "cv"}
        mapped_ids = set(await repos.list_document_mapping_doc_ids_for_user(s, user.id))
        seed_source_ids = sorted(str(x) for x in (cv_ids | mapped_ids) if x in set(doc_ids))
    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=user.id,
            profile_json=_simple_profile(),
            source_doc_ids=seed_source_ids,
            model_name="qwen3-8b",
        )
        await repos.update_job_parsed_json(
            s,
            job_id,
            user.id,
            {
                "title": "Eng",
                "seniority": "senior",
                "must_have_skills": [],
                "nice_to_have_skills": [],
                "responsibilities": [],
                "behavioral_signals": [],
                "company_name": "Acme",
            },
        )
        await repos.upsert_company_snapshot(
            s,
            job_id=job_id,
            company_name="Acme",
            snapshot_json={
                "mission": "x",
                "products": [],
                "recent_news": [],
                "values_and_signals": [],
            },
            source_urls=[],
            model_name="qwen3-8b",
        )


async def _make_job(db: async_sessionmaker, user: User) -> uuid.UUID:
    async with db() as s:
        job = await repos.create_job(
            s, user_id=user.id, source="pasted", raw_text="JD body for Acme."
        )
        return job.id


async def test_doc_mapping_apply_advances_to_next_doc_then_end(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two unmapped docs → first interrupt emits a suggestion → resume with
    apply → second interrupt for doc 2 → resume with apply → END."""
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop_async)

    cv = await _make_cv(db, alice)
    doc_a = await _make_project_doc(db, alice, title="A")
    doc_b = await _make_project_doc(db, alice, title="B")
    job_id = await _make_job(db, alice)
    await _seed_caches(db, user=alice, job_id=job_id, doc_ids=[cv.id, doc_a.id, doc_b.id])

    async def fake_run_intake(_doc_id: uuid.UUID, _user_id: uuid.UUID):
        from interview_coach.agents.schemas import DocIntakeExtracted, DocIntakeResult

        return DocIntakeResult(
            title="intake-title",
            extracted=DocIntakeExtracted(tech_stack=["python"], description="d", urls=[]),
            suggestions=[],
        )

    monkeypatch.setattr(graph_nodes, "run_intake", fake_run_intake)

    graph = build_prep_graph(MemorySaver())
    config = _prep_thread(alice.id, job_id)

    initial_state = {
        "user_id": str(alice.id),
        "job_id": str(job_id),
        "force_refresh": False,
        "skipped_mapping_doc_ids": [],
        "pending_mapping": None,
        "mapping_resume": None,
    }

    # First astream: runs profile/cache short-circuit, then prepare_mapping
    # for doc A, then interrupts at await_mapping_confirm.
    chunks_1: list[dict[str, Any]] = []
    async for chunk in graph.astream(initial_state, config=config, stream_mode="custom"):
        chunks_1.append(chunk)
    suggestions_1 = [c for c in chunks_1 if c.get("event") == "mapping_suggestion"]
    assert len(suggestions_1) == 1
    assert suggestions_1[0]["document_id"] == str(doc_a.id)
    assert suggestions_1[0]["payload"]["remaining"] == 2

    # Resume: apply doc A → graph loops into prepare_mapping for doc B.
    chunks_2: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(
            resume={
                "action": "apply",
                "rows": [{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
                "title": "A title",
                "extracted": {"tech_stack": ["go"], "description": None, "urls": []},
            }
        ),
        config=config,
        stream_mode="custom",
    ):
        chunks_2.append(chunk)
    applied = [c for c in chunks_2 if c.get("event") == "mapping_applied"]
    assert len(applied) == 1
    assert applied[0]["document_id"] == str(doc_a.id)
    suggestions_2 = [c for c in chunks_2 if c.get("event") == "mapping_suggestion"]
    assert len(suggestions_2) == 1
    assert suggestions_2[0]["document_id"] == str(doc_b.id)
    assert suggestions_2[0]["payload"]["remaining"] == 1

    # Resume: apply doc B → no more docs → prepare_mapping emits
    # node_skipped → job_analyzer + company_researcher cache-hit → END.
    chunks_3: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(
            resume={
                "action": "apply",
                "rows": [{"mapping_kind": "project"}],
                "title": "B title",
                "extracted": {"tech_stack": [], "description": None, "urls": []},
            }
        ),
        config=config,
        stream_mode="custom",
    ):
        chunks_3.append(chunk)
    applied_again = [c for c in chunks_3 if c.get("event") == "mapping_applied"]
    assert len(applied_again) == 1
    assert applied_again[0]["document_id"] == str(doc_b.id)
    final_skipped = [
        c for c in chunks_3 if c.get("event") == "node_skipped" and c.get("node") == "doc_mapping"
    ]
    assert len(final_skipped) == 1
    assert final_skipped[0]["reason"] == "no_unmapped_project_docs"


async def test_doc_mapping_skip_advances_without_persisting(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single doc, user skips → mapping_skipped event, doc stays unmapped,
    skiplist prevents re-asking, graph reaches END."""
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop_async)

    cv = await _make_cv(db, alice)
    doc = await _make_project_doc(db, alice, title="Skip-me")
    job_id = await _make_job(db, alice)
    await _seed_caches(db, user=alice, job_id=job_id, doc_ids=[cv.id, doc.id])

    async def fake_run_intake(_doc_id: uuid.UUID, _user_id: uuid.UUID):
        from interview_coach.agents.schemas import DocIntakeExtracted, DocIntakeResult

        return DocIntakeResult(
            title="t",
            extracted=DocIntakeExtracted(tech_stack=[], description=None, urls=[]),
            suggestions=[],
        )

    monkeypatch.setattr(graph_nodes, "run_intake", fake_run_intake)
    # apply_mapping must NOT be called on a skip.
    apply_calls: list[Any] = []

    async def spy_apply_mapping(**kwargs):
        apply_calls.append(kwargs)
        return 1

    monkeypatch.setattr(graph_nodes, "apply_mapping", spy_apply_mapping)

    graph = build_prep_graph(MemorySaver())
    config = _prep_thread(alice.id, job_id)

    async for _ in graph.astream(
        {
            "user_id": str(alice.id),
            "job_id": str(job_id),
            "force_refresh": False,
            "skipped_mapping_doc_ids": [],
            "pending_mapping": None,
            "mapping_resume": None,
        },
        config=config,
        stream_mode="custom",
    ):
        pass

    skipped_events: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        Command(resume={"action": "skip"}),
        config=config,
        stream_mode="custom",
    ):
        if chunk.get("event") == "mapping_skipped":
            skipped_events.append(chunk)

    assert apply_calls == []
    assert len(skipped_events) == 1
    assert skipped_events[0]["document_id"] == str(doc.id)
    # No mapping row was persisted — doc is still unmapped in DB.
    async with db() as s:
        rows = await repos.list_document_mappings(s, doc.id)
    assert rows == []


async def test_doc_mapping_intake_failure_skips_without_interrupt(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_intake raises → fanout emits mapping_suggestion_failed, never
    interrupts, advances to next unmapped doc (or to job_analyzer)."""
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.graph import build_prep_graph

    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop_async)

    cv = await _make_cv(db, alice)
    bad = await _make_project_doc(db, alice, title="Bad")
    job_id = await _make_job(db, alice)
    await _seed_caches(db, user=alice, job_id=job_id, doc_ids=[cv.id, bad.id])

    async def boom(_doc_id: uuid.UUID, _user_id: uuid.UUID):
        raise doc_intake.DocIntakeError("LLM melted")

    monkeypatch.setattr(graph_nodes, "run_intake", boom)

    graph = build_prep_graph(MemorySaver())
    config = _prep_thread(alice.id, job_id)

    chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {
            "user_id": str(alice.id),
            "job_id": str(job_id),
            "force_refresh": False,
            "skipped_mapping_doc_ids": [],
            "pending_mapping": None,
            "mapping_resume": None,
        },
        config=config,
        stream_mode="custom",
    ):
        chunks.append(chunk)

    failed = [c for c in chunks if c.get("event") == "mapping_suggestion_failed"]
    assert len(failed) == 1
    assert failed[0]["document_id"] == str(bad.id)
    assert failed[0]["code"] == "DocIntakeError"
    # No interrupt happened — the graph ran to END in a single astream.
    final_skipped = [
        c for c in chunks if c.get("event") == "node_skipped" and c.get("node") == "doc_mapping"
    ]
    assert len(final_skipped) == 1


# --- helpers ---------------------------------------------------------


async def _noop_async(*_a: Any, **_k: Any) -> None:
    return None


def _simple_profile() -> dict[str, Any]:
    """Minimum-viable profile with one experience + one highlight so the
    apply_mapping highlight-row tests have a target to mutate."""
    return {
        "summary": "x",
        "skills": [],
        "experiences": [
            {
                "company": "Acme",
                "role": "Eng",
                "highlights": [{"text": "h0", "tech_stack": [], "source_document_ids": []}],
            }
        ],
        "projects": [],
        "education": [],
    }


# Keep the AsyncSession import alive for type annotations elsewhere.
_ = AsyncSession
