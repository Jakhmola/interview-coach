"""Sessions + interview streaming endpoints (Phase 8/9, rewritten Phase 10).

Phase 10 routes the per-session interview lifecycle through a LangGraph
``StateGraph`` (compiled once at lifespan startup, stashed on
``app.state``). The on-the-wire SSE format is unchanged from Phase 9 —
the route is a thin translator from the graph's custom-stream writer
events to SSE events.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.agents.interview_events import (
    ENVELOPE_EVENT_NAMES,
    INNER_EVENT_NAMES,
)
from interview_coach.agents.nodes.evaluator import ThreadNotFound
from interview_coach.agents.nodes.profile_builder import NoDocumentsError
from interview_coach.agents.nodes.question_generator import GenerationPrereqsMissing
from interview_coach.agents.prep_events import LIFECYCLE_EVENT_NAMES
from interview_coach.agents.streaming_json import StreamingJsonError
from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.sessions.schemas import (
    MessageOut,
    MessageRequest,
    PrepareMappingResumeRequest,
    PrepareRepoResumeRequest,
    PrepareRequest,
    PrepStatusOut,
    RoundType,
    SessionCreateRequest,
    SessionDetail,
    SessionOut,
    SessionStatus,
    ThreadOut,
)
from interview_coach.api.streaming import SSE_HEADERS, sse_event
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db
from interview_coach.observability.langfuse import (
    flush_langfuse,
    langfuse_callback,
    trace_attributes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionOut:
    job = await repos.get_job(session, body.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    if not job.parsed_json:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "job_not_analyzed")

    profile = await repos.get_profile(session, user.id)
    if profile is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "profile_missing")

    snapshot = await repos.get_company_snapshot_by_job(session, body.job_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "company_snapshot_missing")

    row = await repos.create_session(
        session,
        user_id=user.id,
        job_id=body.job_id,
        round_type=body.round_type.value,
        n_questions=body.n_questions,
    )
    return SessionOut.model_validate(row)


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[SessionOut]:
    rows = await repos.list_sessions_for_user(session, user.id)
    return [SessionOut.model_validate(r) for r in rows]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionDetail:
    row = await repos.get_session(session, session_id, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    threads = await repos.list_threads_for_session(session, session_id)
    thread_outs: list[ThreadOut] = []
    for t in threads:
        msgs = await repos.list_messages_for_thread(session, t.id)
        thread_outs.append(
            ThreadOut(
                id=t.id,
                session_id=t.session_id,
                thread_index=t.thread_index,
                focus_key=t.focus_key,
                focus_label=t.focus_label,
                focus_document_ids=t.focus_document_ids,
                anchors_json=list(t.anchors_json or []),
                status=t.status,  # type: ignore[arg-type]
                score=t.score,
                feedback=t.feedback,
                model_answer=t.model_answer,
                created_at=t.created_at,
                messages=[MessageOut.model_validate(m) for m in msgs],
            )
        )
    return SessionDetail(
        id=row.id,
        user_id=row.user_id,
        job_id=row.job_id,
        round_type=RoundType(row.round_type),
        status=SessionStatus(row.status),
        n_questions=row.n_questions,
        created_at=row.created_at,
        threads=thread_outs,
    )


@router.post("/{session_id}/abandon", response_model=SessionOut)
async def abandon_session(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionOut:
    row = await repos.get_session(session, session_id, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    if row.status == "active":
        await repos.update_session_status(session, session_id, user.id, "abandoned")
        row = await repos.get_session(session, session_id, user.id)
        assert row is not None
    return SessionOut.model_validate(row)


# --- Phase 10: prepare endpoint -------------------------------------


@router.get("/prepare/status", response_model=PrepStatusOut)
async def prepare_status(
    job_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    detail: bool = False,
) -> PrepStatusOut:
    """Read-only readiness view for the frontend setup flow.

    Phase 21: default response is readiness booleans only. Pass
    ``?detail=true`` to include the full profile / job / company
    payloads (callers should pass that flag explicitly — SetupPage's
    2-4 s poll loop doesn't need them).
    """
    readiness = await repos.prep_readiness(session, user.id, job_id)
    if readiness is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")

    profile = readiness.profile
    snapshot = readiness.snapshot
    return PrepStatusOut(
        job_id=readiness.job.id,
        has_cv=readiness.has_cv,
        profile_ready=readiness.profile_ready,
        job_analyzed=readiness.job_analyzed,
        company_researched=readiness.company_researched,
        can_start=readiness.can_start,
        missing=readiness.missing,
        unmapped_project_doc_count=readiness.unmapped_project_doc_count,
        profile=(profile.profile_json if (detail and profile is not None) else None),
        job=(readiness.job.parsed_json if detail else None),
        company=(
            {
                "company_name": snapshot.company_name,
                "snapshot": snapshot.snapshot_json,
                "source_urls": snapshot.source_urls,
                "updated_at": snapshot.updated_at,
            }
            if (detail and snapshot is not None)
            else None
        ),
    )


# The doc-mapping HITL sub-protocol — untyped, owned here (no verdict reason
# rides these; see docs/adr/0001). The per-doc ``node_started`` the mapping
# loop emits reuses a lifecycle name and is covered by LIFECYCLE_EVENT_NAMES.
MAPPING_EVENT_NAMES = frozenset(
    {
        "mapping_suggestion",
        "mapping_suggestion_failed",
        "mapping_applied",
        "mapping_skipped",
        "mapping_apply_failed",
    }
)

# Phase 32: the github HITL sub-protocol — also untyped (no verdict reason).
# ``repos_available`` mirrors ``mapping_suggestion`` (pauses the graph on an
# interrupt); the rest are progress/outcome events the FE renders.
GITHUB_EVENT_NAMES = frozenset({"repos_available", "repo_ingest_failed", "github_folded"})

# The node-lifecycle half is sourced from agents/prep_events so the allowlist
# can't drift from the typed models. ``error`` is in this set but is
# special-cased below (forward, then terminate the stream).
PREP_FORWARDED_EVENTS = LIFECYCLE_EVENT_NAMES | MAPPING_EVENT_NAMES | GITHUB_EVENT_NAMES


def _prep_event_stream(
    *,
    prep_graph: Any,
    graph_input: Any,
    prep_config: dict[str, Any],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    trace_meta: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Shared SSE pump for the prep_graph. Used by both the fresh
    ``/prepare`` POST and the ``/prepare/resume`` POST that the FE sends
    after the user confirms / skips a mapping suggestion.

    The stream ends in one of these states:
    * ``done``  — prep_graph reached END.
    * ``awaiting_repos`` — prep_graph hit the repo-selection interrupt; the FE
      already received the ``repos_available`` event.
    * ``awaiting_mapping`` — prep_graph hit a mapping interrupt; the FE
      already received the ``mapping_suggestion`` event for the doc.
    * ``error`` — a node-level failure surfaced before END.
    """

    async def gen() -> AsyncIterator[bytes]:
        saw_mapping_interrupt = False
        saw_repo_interrupt = False
        try:
            with trace_attributes(
                user_id=str(user_id),
                metadata=trace_meta,
                tags=["graph:prep"],
            ):
                async for chunk in prep_graph.astream(
                    graph_input, config=prep_config, stream_mode="custom"
                ):
                    event = chunk.get("event")
                    if event == "mapping_suggestion":
                        saw_mapping_interrupt = True
                    if event == "repos_available":
                        saw_repo_interrupt = True
                    # ``error`` is in PREP_FORWARDED_EVENTS but keeps its own
                    # forward-then-terminate control flow (checked first).
                    if event == "error":
                        yield sse_event("error", {k: v for k, v in chunk.items() if k != "event"})
                        return
                    if event in PREP_FORWARDED_EVENTS:
                        yield sse_event(event, {k: v for k, v in chunk.items() if k != "event"})
                # astream exit: the graph reached END, or it paused at the
                # repo-selection interrupt, or at a mapping interrupt. The
                # interrupt paths end the generator without a langgraph-internal
                # frame because we run stream_mode="custom".
                if saw_repo_interrupt:
                    yield sse_event("awaiting_repos", {"job_id": str(job_id)})
                elif saw_mapping_interrupt:
                    yield sse_event(
                        "awaiting_mapping",
                        {"job_id": str(job_id)},
                    )
                else:
                    yield sse_event("done", {"job_id": str(job_id), "ready": True})
        except NoDocumentsError as e:
            # Phase 22: company-research soft errors (CompanyNameMissing,
            # NoSearchHits, NoUsablePages) are swallowed *inside* the
            # graph node and surfaced as ``node_done {outcome: "degraded"}``,
            # not as fatal stream errors — the user can still proceed
            # with a placeholder snapshot. Only genuinely fatal prep
            # exceptions terminate the stream here.
            yield sse_event("error", {"code": type(e).__name__, "detail": str(e)})
        except Exception as e:  # noqa: BLE001
            # Any *other* node failure (e.g. a github ingest/fold raising) must
            # still close the stream with a typed ``error`` frame. Without this
            # the exception escapes the generator mid-SSE, the connection drops
            # with no terminal event, and the FE renders a bare "internal server
            # error" and silently reverts the user to setup. Log + surface it.
            logger.exception("prep stream failed for user=%s job=%s", user_id, job_id)
            yield sse_event("error", {"code": type(e).__name__, "detail": str(e)})
        finally:
            await flush_langfuse()

    return gen()


@router.post("/prepare")
async def prepare_session(
    body: PrepareRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Start (or restart from scratch) the prep_graph run for a job.

    SSE stream of node lifecycle + mapping events. The graph pauses on
    each unmapped project_doc — the FE then POSTs to ``/prepare/resume``
    with the user's confirmation to advance. Node-level errors come back
    as ``event: error`` mid-stream; pre-stream input errors come back as
    HTTP 4xx.
    """
    job = await repos.get_job(session, body.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    docs = await repos.list_documents_for_user(session, user.id)
    if not docs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_documents")

    prep_graph = request.app.state.prep_graph
    # Phase 22 fix: a fresh /prepare POST must start the graph from
    # START, not pick up from a prior END checkpoint. LangGraph
    # short-circuits ``astream(input, config=thread_at_end)`` when the
    # input is structurally the same as the prior run, so running prep
    # twice on the same job — exactly what the work-driven auto-prep
    # does after a project_doc upload — silently no-ops without this
    # reset. The resume path (POST /prepare/resume) deliberately keeps
    # the thread so the interrupt() handshake survives the round-trip.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None:
        thread_id = f"prep:{user.id}:{body.job_id}"
        prep_config_for_peek = _thread_config_for_prep(user.id, body.job_id)
        # Phase 25 (B17): peek at graph state before nuking the thread.
        # If the prior run is paused on an interrupt (mapping HITL), a
        # fresh /prepare POST would destroy that interrupt state —
        # exactly what happens when the user opens setup in a second
        # tab while tab 1 has the mapping modal open. Refuse with 409
        # unless the caller explicitly forces a restart.
        try:
            state = await prep_graph.aget_state(prep_config_for_peek)
        except Exception:  # noqa: BLE001
            state = None
        if state is not None and state.next and not body.force_refresh:
            raise HTTPException(status.HTTP_409_CONFLICT, "prep_in_progress")
        try:
            await checkpointer.adelete_thread(thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("prep thread reset failed for %s", thread_id)

    initial_state: dict[str, Any] = {
        "user_id": str(user.id),
        "job_id": str(body.job_id),
        "force_refresh": body.force_refresh,
        # Phase 21.1: each fresh /prepare run starts with an empty
        # skiplist; the user gets a fresh chance to confirm any
        # previously-skipped project_doc.
        "skipped_mapping_doc_ids": [],
        "pending_mapping": None,
        "mapping_resume": None,
    }
    prep_config = _with_callbacks(_thread_config_for_prep(user.id, body.job_id))
    trace_meta = {
        "graph": "prep",
        "user_id": str(user.id),
        "job_id": str(body.job_id),
        "force_refresh": str(body.force_refresh),
    }
    stream = _prep_event_stream(
        prep_graph=prep_graph,
        graph_input=initial_state,
        prep_config=prep_config,
        user_id=user.id,
        job_id=body.job_id,
        trace_meta=trace_meta,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/prepare/resume")
async def prepare_session_resume(
    body: PrepareMappingResumeRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Resume an interrupted prep_graph after the user confirms or skips
    a mapping suggestion. The body carries the user's decision; LangGraph
    threads it into the ``await_mapping_confirm`` interrupt and the graph
    advances to the next unmapped doc (or to ``job_analyzer`` if none).
    """
    prep_graph = request.app.state.prep_graph
    prep_config = _with_callbacks(_thread_config_for_prep(user.id, body.job_id))
    trace_meta = {
        "graph": "prep",
        "phase": "resume_mapping",
        "user_id": str(user.id),
        "job_id": str(body.job_id),
    }
    resume_payload = {
        "action": body.action,
        "rows": [r.model_dump() for r in body.rows] if body.action == "apply" else [],
        "title": body.title,
        "extracted": body.extracted.model_dump() if body.extracted is not None else None,
    }
    stream = _prep_event_stream(
        prep_graph=prep_graph,
        graph_input=Command(resume=resume_payload),
        prep_config=prep_config,
        user_id=user.id,
        job_id=body.job_id,
        trace_meta=trace_meta,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/prepare/resume_repos")
async def prepare_session_resume_repos(
    body: PrepareRepoResumeRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Resume an interrupted prep_graph after the user picks GitHub repos.

    Phase 32: same resume pump as mapping-confirm — the body's ``selected_urls``
    are threaded into the ``await_repo_selection`` interrupt; the graph ingests
    + folds the chosen repos, then proceeds into the doc-mapping loop.
    """
    prep_graph = request.app.state.prep_graph
    prep_config = _with_callbacks(_thread_config_for_prep(user.id, body.job_id))
    trace_meta = {
        "graph": "prep",
        "phase": "resume_repos",
        "user_id": str(user.id),
        "job_id": str(body.job_id),
    }
    stream = _prep_event_stream(
        prep_graph=prep_graph,
        graph_input=Command(resume={"selected_urls": body.selected_urls}),
        prep_config=prep_config,
        user_id=user.id,
        job_id=body.job_id,
        trace_meta=trace_meta,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


# --- Phase 8/9 routes, rewritten to drive interview_graph -----------


def _thread_config(session_id: uuid.UUID) -> dict[str, Any]:
    """One graph thread per **session** (Phase 34).

    The interview graph owns the whole session loop as a single logical run
    that spans many ``/message`` round-trips (ADR 0004), so the checkpoint
    thread is keyed to the session — not, as before, to each turn.
    """
    return {"configurable": {"thread_id": f"interview:{session_id}"}}


def _thread_config_for_prep(user_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    """Phase 21: per-(user, job) prep_graph thread.

    Mid-prep crash + resume reads the last completed checkpoint from
    this thread. Distinct ``prep:`` prefix keeps the namespace clear of
    interview-graph threads sharing the same AsyncSqliteSaver.
    """
    return {"configurable": {"thread_id": f"prep:{user_id}:{job_id}"}}


def _with_callbacks(config: dict[str, Any]) -> dict[str, Any]:
    """Attach the Langfuse callback to a graph config when tracing is enabled.

    No-op when Langfuse env is unset. Mutates a copy — never the input.
    Trace-level attributes (user_id, session_id, metadata, tags) are
    applied by the surrounding ``trace_attributes`` context manager.
    """
    cb = langfuse_callback()
    if cb is None:
        return config
    new = dict(config)
    new["callbacks"] = [*new.get("callbacks", []), cb]
    return new


async def _hydrate_interview_context(
    session: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID
) -> dict[str, Any | None]:
    """Phase 20: pre-load profile / job analysis / company snapshot once per
    request so the graph nodes can skip per-turn DB round-trips.

    All three are gathered concurrently against the same session. Missing
    rows surface as ``None`` — the graph nodes still raise
    ``GenerationPrereqsMissing`` if a required value is absent, so this
    helper stays liberal.
    """
    profile_row, job_row, snap_row = await asyncio.gather(
        repos.get_profile(session, user_id),
        repos.get_job(session, job_id, user_id),
        repos.get_company_snapshot_by_job(session, job_id),
    )
    return {
        "profile": profile_row.profile_json if profile_row is not None else None,
        "job": job_row.parsed_json if job_row is not None else None,
        "company": snap_row.snapshot_json if snap_row is not None else None,
    }


@router.post("/{session_id}/message")
async def session_message(
    session_id: uuid.UUID,
    body: MessageRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """The single conversational endpoint (Phase 34 / ADR 0004).

    An empty body opens the session (the first thread); otherwise ``message``
    is the candidate's answer to the interviewer's last move. The SSE stream
    carries the interviewer's next move(s) as a typed action envelope
    (``move`` / ``move_done`` / ``evaluation`` / ``evaluation_done`` /
    ``wrap``, framing the Phase-9 token streams). The graph pauses on an
    ``interrupt`` after each move that awaits an answer — the stream then ends
    FE-awaiting, like prep. Each call resumes the session-keyed checkpoint;
    a fresh session is told apart from a paused one by the checkpoint state,
    not the request shape.
    """
    sess = await repos.get_session(session, session_id, user.id)
    if sess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    if sess.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"session_status_{sess.status}")

    interview_graph = request.app.state.interview_graph
    config = _with_callbacks(_thread_config(session_id))

    # Paused (mid-thread, awaiting an answer) vs. fresh: read the checkpoint.
    try:
        gstate = await interview_graph.aget_state(config)
    except Exception:  # noqa: BLE001
        gstate = None
    is_paused = gstate is not None and bool(gstate.next)

    graph_input: Any
    if is_paused:
        msg = (body.message or "").strip()
        if not msg:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_message")
        graph_input = Command(resume={"message": msg})
    else:
        hydrated = await _hydrate_interview_context(session, user_id=user.id, job_id=sess.job_id)
        graph_input = {
            "user_id": str(user.id),
            "session_id": str(session_id),
            "round_type": sess.round_type,
            "n_questions": sess.n_questions,
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
            "profile": hydrated["profile"],
            "job": hydrated["job"],
            "company": hydrated["company"],
        }

    trace_meta = {
        "graph": "interview",
        "phase": "message",
        "user_id": str(user.id),
        "session_id": str(session_id),
        "round_type": sess.round_type,
        "resume": str(is_paused),
    }
    trace_tags = ["graph:interview", f"round:{sess.round_type}", "phase:message"]

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            with trace_attributes(
                user_id=str(user.id),
                session_id=str(session_id),
                metadata=trace_meta,
                tags=trace_tags,
            ):
                async for chunk in interview_graph.astream(
                    graph_input, config=config, stream_mode="custom"
                ):
                    event = chunk.get("event")
                    # ``error`` keeps its own forward-then-terminate flow.
                    if event == "error":
                        yield sse_event("error", {k: v for k, v in chunk.items() if k != "event"})
                        return
                    if event in INNER_EVENT_NAMES:
                        # Phase-9 sub-protocol: the payload rides under ``data``.
                        yield sse_event(event, chunk.get("data"))
                    elif event in ENVELOPE_EVENT_NAMES:
                        # Framing events carry their fields at the top level.
                        yield sse_event(event, {k: v for k, v in chunk.items() if k != "event"})
        except GenerationPrereqsMissing as e:
            logger.warning("Generation prereqs missing for session=%s: %s", session_id, e)
            yield sse_event("error", {"code": str(e)})
        except ThreadNotFound as e:
            yield sse_event("error", {"code": type(e).__name__, "detail": str(e)})
        except StreamingJsonError as e:
            logger.exception("Interview streaming failed for session=%s", session_id)
            yield sse_event("error", {"code": "streaming_json_error", "detail": str(e)})
        finally:
            await flush_langfuse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
