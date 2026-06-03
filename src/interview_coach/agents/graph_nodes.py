"""LangGraph node wrappers (Phase 10).

Each function adapts an existing agent node (Phase 6–9) to LangGraph's
``(state) -> state_update`` signature. The Phase 6–9 functions stay
unchanged — these wrappers are thin glue:

* read identity fields off the state,
* call the Phase 6–9 function (or short-circuit on a cache hit),
* push lifecycle / token events through ``get_stream_writer`` so the
  route layer can forward them as SSE,
* return a state delta.

Cache rules (used by the prep graph): each node asks ``prep_cache`` for a
typed ``SkipVerdict`` and emits ``node_skipped`` with the verdict's reason.

* ``profile_builder`` is skipped iff a ProfileRow exists AND its
  ``source_doc_ids`` equal the current Profile document set
  (``repos.current_profile_doc_ids``). Replace the CV and the set
  differs, so we re-run.
* ``job_analyzer`` is skipped iff ``jobs.parsed_json`` is non-empty.
* ``company_researcher`` is skipped iff a snapshot row exists,
  ``state["force_refresh"]`` is False, and the snapshot isn't a
  *transiently* degraded placeholder — those re-attempt research.

Streaming rules (used by the interview graph, Phase 34):

* ``interviewer`` consumes the candidate's ``pending_message`` (persists it as
  a candidate Message), then either opens a thread (``stream_open_thread``) or
  conducts one step (``stream_conduct``), forwarding the typed action-envelope
  events (``move`` / ``token`` / ``move_done``). It sets ``next_move`` so the
  conditional edge can route ``advance`` → evaluator, else → ``await_candidate``.
* ``await_candidate`` is a pure ``interrupt`` node — it pauses for the
  candidate's answer and stashes it in ``pending_message`` (no side effects, so
  resume replays are free).
* ``evaluator`` evaluates the closed thread over its whole transcript
  (``stream_thread_evaluation``), forwarding ``evaluation`` / ``score`` /
  ``feedback_token`` / ``model_answer_token`` / ``evaluation_done`` / ``wrap``,
  resets the open-thread state, and routes ``complete`` → END else → interviewer.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from interview_coach.agents.interview_events import (
    EvaluationDone,
    EvaluationStart,
    InterviewError,
    Move,
    MoveDone,
    Wrap,
)
from interview_coach.agents.interview_events import (
    emit as emit_interview,
)
from interview_coach.agents.nodes.company_researcher import (
    CompanyNameMissing,
    JobNotAnalyzed,
    NoSearchHits,
    NoUsablePages,
    research_company,
)
from interview_coach.agents.nodes.doc_intake import (
    DocIntakeError,
    ProfileMissing,
    apply_mapping,
    build_mapping_suggestion_payload,
    run_intake,
)
from interview_coach.agents.nodes.evaluator import stream_thread_evaluation
from interview_coach.agents.nodes.job_analyzer import JobNotFoundError, analyze_job
from interview_coach.agents.nodes.profile_builder import NoDocumentsError, build_profile
from interview_coach.agents.nodes.question_generator import (
    GenerationPrereqsMissing,
    stream_conduct,
    stream_open_thread,
)
from interview_coach.agents.prep_cache import (
    decide_company_cache,
    decide_job_cache,
    decide_profile_cache,
)
from interview_coach.agents.prep_events import (
    NodeDone,
    NodeError,
    NodeSkipped,
    emit,
    emit_verdict,
)
from interview_coach.agents.rounds import get_round_strategy
from interview_coach.agents.state import InterviewState
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


# --- prep graph nodes -------------------------------------------------


async def node_profile_builder(state: InterviewState) -> dict[str, Any]:
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()

    async with AsyncSessionLocal() as s:
        existing = await repos.get_profile(s, user_id)
        # Phase 26: the cache key is the canonical Profile document set
        # (CV ∪ confirmed-mapping doc ids), computed by one repo helper at
        # write *and* read. Skipped when no profile exists (the verdict is
        # ``missing`` regardless of the doc set).
        current_doc_ids = (
            await repos.current_profile_doc_ids(s, user_id) if existing is not None else []
        )

    verdict = decide_profile_cache(
        profile_exists=existing is not None,
        stored_doc_ids=existing.source_doc_ids if existing is not None else None,
        current_doc_ids=current_doc_ids,
    )
    if emit_verdict(writer, node="profile_builder", verdict=verdict):
        return {"profile": existing.profile_json}

    try:
        profile = await build_profile(user_id)
    except NoDocumentsError as e:
        emit(writer, NodeError(node="profile_builder", code="no_documents", detail=str(e)))
        raise
    emit(writer, NodeDone(node="profile_builder"))
    return {"profile": profile.model_dump()}


async def node_prepare_mapping_suggestion(state: InterviewState) -> dict[str, Any]:
    """Phase 21.1: pick the next unmapped project_doc and run ``run_intake``
    against it. Three nodes form the per-doc HITL loop:

        prepare_mapping_suggestion → await_mapping_confirm → apply_or_skip_mapping
                       ↑__________________________________________│ (loop while unmapped)

    Splitting "prepare" from "await" matters because LangGraph re-executes
    an interrupted node on resume — running the LLM call in the same node
    as the ``interrupt(...)`` would (a) waste an LLM call per resume and
    (b) produce a different suggestion than the one the user actually
    confirmed. Stashing the result in ``state.pending_mapping`` makes the
    await/apply nodes deterministic on replay.

    When zero unmapped docs remain, emits ``node_skipped`` and routes
    straight to ``job_analyzer``.
    """
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()
    skiplist = set(state.get("skipped_mapping_doc_ids") or [])

    async with AsyncSessionLocal() as s:
        unmapped_all = await repos.list_unmapped_project_docs_for_user(s, user_id)
        profile_row = await repos.get_profile(s, user_id)

    unmapped = [d for d in unmapped_all if str(d.id) not in skiplist]
    if not unmapped:
        emit(writer, NodeSkipped(node="doc_mapping", reason="no_unmapped_project_docs"))
        return {"pending_mapping": None, "next_step": "job_analyzer"}

    next_doc = unmapped[0]
    remaining = len(unmapped)
    profile_json = profile_row.profile_json if profile_row is not None else None

    writer(
        {
            "event": "node_started",
            "node": "doc_mapping",
            "document_id": str(next_doc.id),
            "remaining": remaining,
        }
    )

    try:
        intake = await run_intake(next_doc.id, user_id)
    except (DocIntakeError, ProfileMissing) as e:
        # Per-doc failure: emit, skip this doc, loop. The doc stays
        # unmapped in DB; user can delete + re-upload from ManagePage.
        # We must NOT call interrupt() — there's nothing to confirm.
        logger.warning("doc_mapping: run_intake failed for doc=%s: %s", next_doc.id, e)
        writer(
            {
                "event": "mapping_suggestion_failed",
                "document_id": str(next_doc.id),
                "code": type(e).__name__,
                "detail": str(e),
            }
        )
        # Stash a sentinel so apply_or_skip_mapping knows to advance.
        return {
            "pending_mapping": {
                "document_id": str(next_doc.id),
                "skip_reason": "intake_failed",
            },
            "next_step": "apply_or_skip_mapping",
        }

    payload = build_mapping_suggestion_payload(
        document_id=next_doc.id,
        intake=intake,
        doc_raw_text=next_doc.raw_text,
        profile_json=profile_json,
        remaining=remaining,
    )
    writer({"event": "mapping_suggestion", "document_id": str(next_doc.id), "payload": payload})

    # The await node reads `pending_mapping` to know which doc this
    # interrupt is about. ``intake_extracted`` is what apply_mapping
    # needs as its ``extracted`` arg — saved so we don't re-run the LLM.
    return {
        "pending_mapping": {
            "document_id": str(next_doc.id),
            "intake_extracted": intake.extracted.model_dump(),
            "intake_title": intake.title,
            "remaining": remaining,
        },
        "next_step": "await_mapping_confirm",
    }


async def node_await_mapping_confirm(state: InterviewState) -> dict[str, Any]:
    """Pure interrupt node — pauses until the user confirms or skips the
    pending mapping. Mirror of ``node_await_answer`` in the interview
    graph: no side-effects beyond the ``interrupt(...)`` call, so resume
    replays are free.

    Resume payload shape: ``{"action": "apply"|"skip", "rows": [...],
    "title": str, "extracted": {...}}``. ``rows`` and ``title`` /
    ``extracted`` are only required when ``action=="apply"``.
    """
    pending = state.get("pending_mapping") or {}
    # Failed-intake doc: nothing to confirm — apply_or_skip_mapping will
    # immediately advance. Don't burn an interrupt cycle on it.
    if pending.get("skip_reason"):
        return {"mapping_resume": None}
    resume = interrupt(
        {
            "awaiting": "mapping_confirm",
            "document_id": pending.get("document_id"),
        }
    )
    return {
        "mapping_resume": resume if isinstance(resume, dict) else {"action": "skip"},
    }


async def node_apply_or_skip_mapping(state: InterviewState) -> dict[str, Any]:
    """Persist the user's mapping decision, then route back to
    ``prepare_mapping_suggestion`` to handle the next unmapped doc (or
    fall through to ``job_analyzer`` once they're all done).

    Side-effects (``apply_mapping``) live here, not in the await node,
    so a resume replay can never double-persist.
    """
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()
    pending = state.get("pending_mapping") or {}
    document_id_str = pending.get("document_id")
    skiplist = list(state.get("skipped_mapping_doc_ids") or [])

    # Intake-failed branch: the doc never made it to a suggestion. We
    # add it to the skiplist so prepare_mapping_suggestion doesn't loop
    # forever on the same broken doc.
    if pending.get("skip_reason") == "intake_failed":
        if document_id_str and document_id_str not in skiplist:
            skiplist.append(document_id_str)
        return {
            "pending_mapping": None,
            "mapping_resume": None,
            "skipped_mapping_doc_ids": skiplist,
        }

    resume = state.get("mapping_resume") or {}
    action = resume.get("action", "skip")

    if action == "apply" and document_id_str:
        rows = list(resume.get("rows") or [])
        title = resume.get("title") or pending.get("intake_title") or "Project"
        extracted = resume.get("extracted") or pending.get("intake_extracted") or {}
        try:
            n = await apply_mapping(
                document_id=uuid.UUID(document_id_str),
                user_id=user_id,
                rows=rows,
                extracted=extracted,
                project_title=str(title),
            )
            writer({"event": "mapping_applied", "document_id": document_id_str, "n_rows": n})
        except (ValueError, ProfileMissing) as e:
            # Bad row indices etc. — treat as a skip so the loop advances;
            # the doc stays unmapped in DB and the user can revisit it
            # from ManagePage later.
            logger.warning("doc_mapping: apply_mapping failed for doc=%s: %s", document_id_str, e)
            writer(
                {
                    "event": "mapping_apply_failed",
                    "document_id": document_id_str,
                    "code": type(e).__name__,
                    "detail": str(e),
                }
            )
            if document_id_str not in skiplist:
                skiplist.append(document_id_str)
    else:
        if document_id_str:
            writer({"event": "mapping_skipped", "document_id": document_id_str})
            if document_id_str not in skiplist:
                skiplist.append(document_id_str)

    return {
        "pending_mapping": None,
        "mapping_resume": None,
        "skipped_mapping_doc_ids": skiplist,
    }


async def node_job_analyzer(state: InterviewState) -> dict[str, Any]:
    user_id = uuid.UUID(state["user_id"])
    job_id = uuid.UUID(state["job_id"])
    writer = get_stream_writer()

    async with AsyncSessionLocal() as s:
        job = await repos.get_job(s, job_id, user_id)

    if job is None:
        emit(writer, NodeError(node="job_analyzer", code="job_not_found"))
        raise JobNotFoundError(f"job {job_id} not found")

    verdict = decide_job_cache(parsed_json=job.parsed_json)
    if emit_verdict(writer, node="job_analyzer", verdict=verdict):
        return {"job": job.parsed_json}

    analysis = await analyze_job(job_id, user_id)
    emit(writer, NodeDone(node="job_analyzer"))
    return {"job": analysis.model_dump()}


async def node_company_researcher(state: InterviewState) -> dict[str, Any]:
    """Phase 22: company research is best-effort, not a setup blocker.

    The three soft failure modes (``CompanyNameMissing``,
    ``NoSearchHits``, ``NoUsablePages``) used to surface as
    ``event: error`` and propagate, leaving the user stuck on a
    half-prepped JD with no way to start the interview. None of those
    are actually fatal — ``question_generator`` already degrades
    cleanly when ``company_snapshot`` is empty (``mission`` falls back
    to ``"—"``, ``values_and_signals`` is empty, ``company_name``
    becomes ``"the hiring company"``).

    So we now catch the three soft modes, persist a placeholder
    snapshot (so ``company_researched`` flips true and the user can
    actually proceed), and emit a ``node_done`` with
    ``outcome="degraded"`` + a reason code. The FE renders that as a warning instead of an
    error and points the user at Manage → Re-analyze JD if they want
    to fix it. Only genuinely fatal exceptions (e.g.
    ``JobNotAnalyzed``, which means a logic bug upstream) still
    propagate.
    """
    user_id = uuid.UUID(state["user_id"])
    job_id = uuid.UUID(state["job_id"])
    force_refresh = bool(state.get("force_refresh", False))
    writer = get_stream_writer()

    async with AsyncSessionLocal() as s:
        existing = await repos.get_company_snapshot_by_job(s, job_id)
    verdict = decide_company_cache(
        snapshot_json=existing.snapshot_json if existing is not None else None,
        force_refresh=force_refresh,
    )
    # On ``miss("degraded")`` we fall through to research — the self-heal
    # for a transient soft-fail (Phase 26 / OD-1).
    if emit_verdict(writer, node="company_researcher", verdict=verdict):
        return {"company": existing.snapshot_json, "prep_done": True}

    try:
        snapshot = await research_company(job_id, user_id, force_refresh=force_refresh)
    except JobNotAnalyzed as e:
        # Indicates upstream pipeline bug — analyzer should have run first.
        emit(writer, NodeError(node="company_researcher", code=type(e).__name__, detail=str(e)))
        raise
    except (CompanyNameMissing, NoSearchHits, NoUsablePages) as e:
        logger.warning(
            "company_researcher: soft-degrading job=%s — %s: %s",
            job_id,
            type(e).__name__,
            e,
        )
        from interview_coach.agents.schemas import CompanySnapshot

        # Pull the analyzed company_name (may be empty for
        # CompanyNameMissing) so the placeholder row still ties to a
        # usable label.
        async with AsyncSessionLocal() as s:
            job = await repos.get_job(s, job_id, user_id)
        company_name = ""
        if job is not None and (job.parsed_json or {}).get("company_name"):
            company_name = str(job.parsed_json["company_name"])  # type: ignore[index]
        if not company_name:
            company_name = "Unknown company"
        placeholder = CompanySnapshot(
            mission="", products=[], recent_news=[], values_and_signals=[]
        )
        async with AsyncSessionLocal() as s:
            await repos.upsert_company_snapshot(
                s,
                job_id=job_id,
                company_name=company_name,
                # Embed the degrade reason in the persisted JSON so the
                # FE can render a "company info incomplete" badge later.
                # Pydantic ignores unknown keys on model_validate, so
                # round-tripping through CompanySnapshot drops it — but
                # that's fine, the FE reads the raw row, not the model.
                snapshot_json=placeholder.model_dump() | {"_degraded": type(e).__name__},
                source_urls=[],
                model_name="placeholder",
            )
        emit(
            writer,
            NodeDone(
                node="company_researcher",
                outcome="degraded",
                code=type(e).__name__,
                detail=str(e),
            ),
        )
        return {"company": placeholder.model_dump(), "prep_done": True}
    emit(writer, NodeDone(node="company_researcher"))
    return {"company": snapshot.model_dump(), "prep_done": True}


# --- interview graph nodes -------------------------------------------


async def _persist_pending_candidate(state: InterviewState) -> bool:
    """If the candidate just answered, persist it as a candidate Message on the
    open thread. Returns True when something was appended. Runs in the
    interviewer node (not the pure ``await_candidate`` node) so an interrupt
    replay can never double-persist."""
    pending = state.get("pending_message")
    thread_id = state.get("thread_id")
    if pending is None or not thread_id:
        return False
    tid = uuid.UUID(thread_id)
    async with AsyncSessionLocal() as s:
        seq = await repos.count_messages_for_thread(s, tid)
        await repos.append_message(s, thread_id=tid, seq=seq, role="candidate", text=pending)
    return True


async def node_interviewer(state: InterviewState) -> dict[str, Any]:
    """Open a thread or conduct one step of the open thread.

    Consumes the candidate's ``pending_message`` first (persist as a candidate
    Message). Then: no open thread → open one (focus + grounding + root
    question); open thread → run the conductor (probe/clarify/nudge/advance),
    with a node-side budget guard forcing ``advance`` once ``max_followups`` is
    spent. Sets ``next_move`` for the conditional edge.
    """
    session_id = uuid.UUID(state["session_id"])
    user_id = uuid.UUID(state["user_id"])
    round_type = state["round_type"]
    writer = get_stream_writer()

    delta: dict[str, Any] = {}
    if await _persist_pending_candidate(state):
        delta["pending_message"] = None

    # --- open a fresh thread ---
    if state.get("thread_id") is None:
        opened: dict[str, Any] | None = None
        try:
            async for kind, data in stream_open_thread(
                session_id=session_id,
                user_id=user_id,
                profile=state.get("profile"),
                job=state.get("job"),
                company=state.get("company"),
            ):
                if kind == "move":
                    emit_interview(writer, Move(**data))
                elif kind == "token":
                    writer({"event": "token", "data": data})
                elif kind == "move_done":
                    emit_interview(writer, MoveDone(anchors=data.get("anchors")))
                elif kind == "opened":
                    opened = data
        except GenerationPrereqsMissing as e:
            emit_interview(writer, InterviewError(code=str(e)))
            raise

        assert opened is not None, "stream_open_thread did not emit an opened event"
        delta.update(
            {
                "thread_id": opened["thread_id"],
                "thread_index": opened["thread_index"],
                "anchors": opened["anchors"],
                "focus_key": opened["focus_key"],
                "focus_label": opened["focus_label"],
                "focus_document_ids": opened["focus_document_ids"],
                "grounding": opened["grounding"],
                "followups_used": 0,
                "next_move": "question",
            }
        )
        return delta

    # --- conduct the open thread ---
    followups_used = int(state.get("followups_used", 0) or 0)
    strategy = get_round_strategy(round_type)
    # Budget guard: once the per-thread follow-up cap is spent, force advance
    # without an LLM call — the safety net that guarantees the thread ends.
    if followups_used >= strategy.max_followups:
        delta["next_move"] = "advance"
        return delta

    advanced = False
    conducted: dict[str, Any] | None = None
    async for kind, data in stream_conduct(
        session_id=session_id,
        user_id=user_id,
        thread_id=uuid.UUID(state["thread_id"]),
        thread_index=int(state["thread_index"]),
        anchors=list(state.get("anchors") or []),
        focus_label=state.get("focus_label"),
        grounding=list(state.get("grounding") or []),
        round_type=round_type,
        profile=state.get("profile"),
        job=state.get("job"),
        company=state.get("company"),
    ):
        if kind == "advance":
            advanced = True
        elif kind == "move":
            emit_interview(writer, Move(**data))
        elif kind == "token":
            writer({"event": "token", "data": data})
        elif kind == "move_done":
            emit_interview(writer, MoveDone())
        elif kind == "conducted":
            conducted = data

    if advanced:
        delta["next_move"] = "advance"
    else:
        delta["next_move"] = conducted["action"] if conducted else "probe"
        delta["followups_used"] = followups_used + 1
    return delta


async def node_await_candidate(state: InterviewState) -> dict[str, Any]:
    """Pure interrupt node — pauses until the candidate's next message arrives.

    Mirror of ``node_await_mapping_confirm``: no side-effects beyond the
    ``interrupt(...)`` call, so resume replays are free. The candidate's message
    is stashed in ``pending_message`` for the interviewer node to persist.
    """
    resume = interrupt({"awaiting": "candidate", "thread_index": state.get("thread_index")})
    message = (resume or {}).get("message", "") if isinstance(resume, dict) else ""
    return {"pending_message": message}


async def node_evaluator(state: InterviewState) -> dict[str, Any]:
    """Evaluate the open thread over its whole transcript, then reset the
    open-thread state so the conditional edge opens the next topic (or ENDs)."""
    session_id = uuid.UUID(state["session_id"])
    user_id = uuid.UUID(state["user_id"])
    thread_id = uuid.UUID(state["thread_id"])
    writer = get_stream_writer()

    done_payload: dict[str, Any] | None = None
    async for kind, data in stream_thread_evaluation(
        session_id=session_id,
        user_id=user_id,
        thread_id=thread_id,
        thread_index=int(state["thread_index"]),
        anchors=list(state.get("anchors") or []),
        grounding=list(state.get("grounding") or []),
        focus_key=state.get("focus_key"),
        round_type=state["round_type"],
        profile=state.get("profile"),
    ):
        if kind == "evaluation":
            emit_interview(writer, EvaluationStart(thread_index=data["thread_index"]))
        elif kind in ("score", "feedback_token", "model_answer_token"):
            writer({"event": kind, "data": data})
        elif kind in ("feedback_done", "model_answer_done"):
            writer({"event": kind, "data": {}})
        elif kind == "model_answer_error":
            writer({"event": kind, "data": data})
        elif kind == "evaluation_done":
            done_payload = data
            emit_interview(writer, EvaluationDone(**data))
        elif kind == "wrap":
            emit_interview(writer, Wrap())

    assert done_payload is not None, "stream_thread_evaluation did not emit evaluation_done"

    # Reset the open-thread carry so the interviewer node opens the next topic
    # (thread_id is None ⇒ open) — or the conditional edge routes to END.
    return {
        "session_status": done_payload["session_status"],
        "thread_id": None,
        "anchors": [],
        "focus_key": None,
        "focus_label": None,
        "focus_document_ids": [],
        "grounding": [],
        "followups_used": 0,
        "next_move": None,
    }
