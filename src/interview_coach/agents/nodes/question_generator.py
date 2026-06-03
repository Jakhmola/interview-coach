"""Interviewer / conductor agent node (Phase 8/14.1, reworked Phase 34).

This module drives the interviewer side of a **thread** (one topic). Two entry
points:

* :func:`stream_open_thread` — opens a thread: deterministically picks a focus
  (the same Phase 14.1 picker), retrieves repo grounding once (experience
  round, repo-backed focus), generates the root **question** + anchors, and
  persists a ``Thread`` + its root ``Message``. The anchors are the thread's
  fixed agenda; the grounding is carried in graph state for reuse by every
  conductor step and the thread-close model answer.
* :func:`stream_conduct` — one conductor step on an open thread: reads the
  transcript so far and emits ``{action, message}`` — the interviewer's next
  **move** (``probe`` / ``clarify`` / ``nudge`` / ``advance``). ``action`` is
  emitted first (the graph routes on it); ``message`` streams to the candidate.
  ``advance`` carries no utterance — it closes the thread.

Both are async generators yielding ``(kind, data)`` tuples the graph node
translates into the typed action-envelope on the wire (``agents/interview_events``).
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.profile_view import profile_slice_for_focus
from interview_coach.agents.prompts import CONDUCTOR_SYSTEM
from interview_coach.agents.rounds import (
    FocusMode,
    RoundStrategy,
    conductor_allowed_actions,
    get_round_strategy,
)
from interview_coach.agents.schemas import Question
from interview_coach.agents.state import RoundType
from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_json_object,
)
from interview_coach.db import repos
from interview_coach.db.models import SessionRow
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import astream_with_telemetry, chat_model
from interview_coach.llm.telemetry import set_node_context
from interview_coach.rag.retrieval import GroundingHit, retrieve_grounding

logger = logging.getLogger(__name__)


class GenerationPrereqsMissing(Exception):
    """Phase 8 routes raise 400 from this; profile / job analysis / snapshot absent."""


async def _load_context(
    session_row: SessionRow,
    *,
    profile: dict[str, Any] | None = None,
    job_analysis: dict[str, Any] | None = None,
    company_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pull profile, job analysis, company snapshot from Postgres.

    Phase 20: any of ``profile`` / ``job_analysis`` / ``company_snapshot``
    pre-loaded by the route layer is accepted and skips its DB round-trip.
    Missing values fall back to the per-session DB read so this function
    remains usable from unit tests / lone-node callers.
    """
    user_id = session_row.user_id
    job_id = session_row.job_id

    async with AsyncSessionLocal() as s:
        if profile is None:
            profile_row = await repos.get_profile(s, user_id)
            if profile_row is None:
                raise GenerationPrereqsMissing("profile_missing")
            profile = profile_row.profile_json
        if job_analysis is None:
            job = await repos.get_job(s, job_id, user_id)
            if job is None:
                raise GenerationPrereqsMissing("job_not_found")
            if not job.parsed_json:
                raise GenerationPrereqsMissing("job_not_analyzed")
            job_analysis = job.parsed_json
        if company_snapshot is None:
            snapshot_row = await repos.get_company_snapshot_by_job(s, job_id)
            if snapshot_row is None:
                raise GenerationPrereqsMissing("company_snapshot_missing")
            company_snapshot = snapshot_row.snapshot_json

    return {
        "profile": profile,
        "job_analysis": job_analysis,
        "company_snapshot": company_snapshot,
    }


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#]*")

FOCUS_TOP_N = 3
"""Only the ``FOCUS_TOP_N`` highest-weighted focus candidates are eligible for
the weighted sample. Keeps run-to-run variety among the strongest matches while
ensuring a clearly off-target (e.g. zero-overlap) candidate is never drilled
when relevant ones exist."""


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _truncate(text: str, n: int = 100) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _highlight_label(exp: dict[str, Any], hl: dict[str, Any]) -> str:
    text = (hl.get("text") or "").strip() or "(unnamed highlight)"
    company = (exp.get("company") or "").strip()
    return _truncate(f'"{text}" at {company}' if company else f'"{text}"', 140)


def _project_label(proj: dict[str, Any]) -> str:
    name = (proj.get("name") or "").strip()
    description = (proj.get("description") or "").strip()
    if name and description:
        return _truncate(f"{name} — {description}", 140)
    return name or description or "(unnamed project)"


def _highlight_candidate_corpus(exp: dict[str, Any], hl: dict[str, Any]) -> set[str]:
    """Token bag used to score JD overlap for a highlight."""
    parts = [
        str(hl.get("text") or ""),
        str(hl.get("description") or ""),
        str(exp.get("role") or ""),
        str(exp.get("company") or ""),
        *[str(t) for t in (hl.get("tech_stack") or [])],
    ]
    return _tokens(" ".join(parts))


def _project_candidate_corpus(proj: dict[str, Any]) -> set[str]:
    parts = [
        str(proj.get("name") or ""),
        str(proj.get("description") or ""),
        *[str(t) for t in (proj.get("tech") or [])],
        str(proj.get("role") or ""),
    ]
    return _tokens(" ".join(parts))


@dataclass(frozen=True)
class FocusPick:
    """The highlight / project / skill / signal the thread drills into.

    ``repo_backed`` is True only for an ``experience_projects`` focus whose
    profile entry is a github project — it gates the thread-open code
    retrieval. ``document_ids`` are the grounding-doc ids the focus points at
    (a project_doc or github_repo), empty for skills / behavioral signals.
    """

    key: str
    label: str
    document_ids: list[str]
    repo_backed: bool = False


def _pick_focus_target(
    *,
    focus: FocusMode,
    profile: dict[str, Any],
    job_analysis: dict[str, Any],
    company_snapshot: dict[str, Any],
    prior_focus_counts: dict[str, int],
    rng: random.Random,
) -> FocusPick | None:
    """Pre-pick which highlight / project / skill / signal the thread drills into.

    Returns a :class:`FocusPick` or None (no candidates).

    Scoring:
      inv_freq(k) = 1 / (1 + prior_focus_counts.get(k, 0))
      experience_projects: weight = (1 + jd_overlap_count) ** 2 * inv_freq
      jd_skills / behavioral_signals: weight = inv_freq
    Candidates are sorted by weight and truncated to the top ``FOCUS_TOP_N``,
    then weighted-sampled with `rng` so relevance dominates while ties / strong
    matches still vary run-to-run (a zero-overlap candidate can't be picked when
    relevant ones exist).
    """
    candidates: list[tuple[FocusPick, float]] = []

    if focus == FocusMode.experience_projects:
        must_have = _tokens(" ".join(str(s) for s in (job_analysis.get("must_have_skills") or [])))
        for i, exp in enumerate(profile.get("experiences") or []):
            if not isinstance(exp, dict):
                continue
            for j, hl in enumerate(exp.get("highlights") or []):
                if not isinstance(hl, dict):
                    continue
                key = f"highlight:{i}:{j}"
                label = _highlight_label(exp, hl)
                doc_ids = [str(d) for d in (hl.get("source_document_ids") or [])]
                overlap = len(_highlight_candidate_corpus(exp, hl) & must_have)
                inv_freq = 1.0 / (1.0 + prior_focus_counts.get(key, 0))
                pick = FocusPick(key=key, label=label, document_ids=doc_ids)
                candidates.append((pick, (1.0 + overlap) ** 2 * inv_freq))
        for k, proj in enumerate(profile.get("projects") or []):
            if not isinstance(proj, dict):
                continue
            key = f"project:{(proj.get('name') or '').strip() or f'idx_{k}'}"
            label = _project_label(proj)
            doc_ids = [str(d) for d in (proj.get("source_document_ids") or [])]
            overlap = len(_project_candidate_corpus(proj) & must_have)
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(key, 0))
            pick = FocusPick(
                key=key,
                label=label,
                document_ids=doc_ids,
                repo_backed=proj.get("source") == "github",
            )
            candidates.append((pick, (1.0 + overlap) ** 2 * inv_freq))
    elif focus == FocusMode.jd_skills:
        # The role's required skills are the candidate pool. Fall back to
        # nice-to-haves, then a single synthetic focus from the title, so the
        # technical round always has at least one thing to ask about.
        skills: list[str] = list(job_analysis.get("must_have_skills") or [])
        if not skills:
            skills = list(job_analysis.get("nice_to_have_skills") or [])
        if not skills:
            title = (job_analysis.get("title") or "").strip()
            skills = [title] if title else []
        for skill in skills:
            skill_str = str(skill).strip()
            if not skill_str:
                continue
            key = f"skill:{skill_str}"
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(key, 0))
            candidates.append((FocusPick(key=key, label=skill_str, document_ids=[]), inv_freq))
    elif focus == FocusMode.behavioral_signals:
        signals: list[str] = list(job_analysis.get("behavioral_signals") or [])
        if not signals:
            signals = list(company_snapshot.get("values_and_signals") or [])
        for sig in signals:
            sig_str = str(sig).strip()
            if not sig_str:
                continue
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(sig_str, 0))
            candidates.append((FocusPick(key=sig_str, label=sig_str, document_ids=[]), inv_freq))
    else:
        raise ValueError(f"unknown focus mode: {focus!r}")

    if not candidates:
        return None

    # Relevance dominates: only the top-N weighted candidates are eligible, so a
    # clearly off-target (e.g. zero-overlap) one is never sampled when stronger
    # matches exist. Ties at the cutoff weight all stay in (don't arbitrarily
    # drop an equally-strong candidate by sort order), so variety survives among
    # the strongest via weighted sampling.
    candidates.sort(key=lambda c: c[1], reverse=True)
    cutoff = candidates[min(FOCUS_TOP_N, len(candidates)) - 1][1]
    eligible = [c for c in candidates if c[1] >= cutoff]
    picks = [p for p, _ in eligible]
    weights = [w for _, w in eligible]
    return rng.choices(picks, weights=weights, k=1)[0]


def _first_sentence(text: str, max_chars: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"[.!?]", text)
    sentence = text[: m.end()].strip() if m else text
    if len(sentence) > max_chars:
        sentence = sentence[: max_chars - 1].rstrip() + "…"
    return sentence


def _build_question_message(
    *,
    role_title: str,
    company_name: str,
    focus_label: str | None,
    profile: dict[str, Any],
    prior_topics: list[str],
    thread_index: int,
    code_grounding: list[GroundingHit] | None = None,
) -> str:
    """JSON payload of the structured context we hand to the LLM at thread-open.

    Phase 14.1: framing fields go first (`role`, `company`, `focus_target`)
    so the LLM sees them before the bulky profile. ``prior_topics`` are the
    focus labels of already-covered threads, so the new question avoids
    re-asking the same topic.

    Phase 33: ``code_grounding`` (experience round, repo-backed focus only)
    carries repo passages retrieved at thread-open. Included only when
    non-empty, so the prompt's adaptive clause stays at the narrative altitude
    for every other focus.
    """
    payload: dict[str, Any] = {
        "role": role_title,
        "company": company_name,
        "thread_index": thread_index,
    }
    if focus_label is not None:
        payload["focus_target"] = focus_label
    if code_grounding:
        payload["code_grounding"] = [{"source": h.filename, "text": h.text} for h in code_grounding]
    payload["profile"] = profile
    payload["prior_topics"] = list(prior_topics)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_system_prompt(
    *,
    strategy: RoundStrategy,
    job_analysis: dict[str, Any],
    company_snapshot: dict[str, Any],
) -> str:
    company_name = (job_analysis.get("company_name") or "").strip() or "the hiring company"
    role_title = (job_analysis.get("title") or "").strip() or "this role"
    seniority = (job_analysis.get("seniority") or "").strip() or "unknown"
    mission_one_line = _first_sentence(company_snapshot.get("mission") or "") or "—"
    values = [str(v).strip() for v in (company_snapshot.get("values_and_signals") or [])][:4]
    values_one_line = ", ".join(v for v in values if v) or "professionalism, ownership, clarity"

    # Every round's question template accepts the same five framing slots;
    # `str.format` ignores any a given template doesn't reference.
    return strategy.question_system.format(
        company_name=company_name,
        role_title=role_title,
        seniority=seniority,
        mission_one_line=mission_one_line,
        values_one_line=values_one_line,
    )


async def _retrieve_code_for_focus(
    *,
    user_id: uuid.UUID,
    query: str,
    document_ids: list[str],
) -> list[GroundingHit]:
    """Retrieve repo passages for a repo-backed experience focus, scoped to the
    focus's ``github_repo`` docs.

    Single-attempt, fail-fast → ``[]`` on any failure, so a slow or dead
    embedder degrades the thread to the narrative altitude instead of stalling
    thread-open. Mirrors the evaluator's retrieval policy.
    """
    doc_ids: list[uuid.UUID] = []
    for d in document_ids:
        try:
            doc_ids.append(uuid.UUID(str(d)))
        except (ValueError, TypeError):
            logger.warning("skipping invalid focus_document_id %r in thread-open grounding", d)
    try:
        return await retrieve_grounding(
            user_id=user_id,
            query=query,
            k=3,
            source_kinds=("github_repo",),
            document_ids=tuple(doc_ids),
            retries=1,
        )
    except Exception:  # noqa: BLE001
        logger.exception("thread-open code grounding failed; degrading to narrative ([])")
        return []


async def _model_deltas(llm: Any, messages: list[Any]) -> AsyncIterator[str]:
    async for chunk in astream_with_telemetry(llm, messages):
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


async def stream_open_thread(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    temperature: float = 0.7,
    profile: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    company: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Open the next thread: pick a focus, ground it, generate + persist the
    root question, and stash the thread's agenda.

    Yields:
        ("move", {"kind": "question", "thread_index": int, "message_id": str})
        ("token", str) — chunks of the question text
        ("move_done", {"anchors": [...]})
        ("opened", {thread_id, thread_index, focus_*, anchors, grounding})

    Raises:
        GenerationPrereqsMissing, ValueError (session state), StreamingJsonError.
    """
    async with AsyncSessionLocal() as s:
        session_row = await repos.get_session(s, session_id, user_id)
        if session_row is None:
            raise ValueError("session_not_found")
        if session_row.status != "active":
            raise ValueError(f"session_status_{session_row.status}")
        thread_index = await repos.count_closed_threads(s, session_id)
        prior_threads = list(await repos.list_threads_for_session(s, session_id))

    if thread_index >= session_row.n_questions:
        raise ValueError("session_complete")

    context = await _load_context(
        session_row,
        profile=profile,
        job_analysis=job,
        company_snapshot=company,
    )

    round_type: RoundType = session_row.round_type  # type: ignore[assignment]
    strategy = get_round_strategy(round_type)

    async with AsyncSessionLocal() as s:
        prior_keys = await repos.list_prior_focus_keys(
            s,
            user_id=session_row.user_id,
            job_id=session_row.job_id,
            round_type=round_type,
        )
    prior_counts = repos.count_focus_keys(prior_keys)

    picked = _pick_focus_target(
        focus=strategy.focus,
        profile=context["profile"],
        job_analysis=context["job_analysis"],
        company_snapshot=context["company_snapshot"],
        prior_focus_counts=prior_counts,
        rng=random.Random(),
    )
    focus_key = picked.key if picked is not None else None
    focus_label = picked.label if picked is not None else None
    focus_document_ids = picked.document_ids if picked is not None else []

    company_name = (
        context["job_analysis"].get("company_name") or ""
    ).strip() or "the hiring company"
    role_title = (context["job_analysis"].get("title") or "").strip() or "this role"

    # Phase 33: for a repo-backed experience focus, retrieve the project's real
    # repo code/manifests once at thread-open so the question can ask an
    # implementation-level question and the grounding can be reused by the
    # conductor and the thread-close model answer.
    code_grounding: list[GroundingHit] = []
    if strategy.qgen_grounding and picked is not None and picked.repo_backed:
        code_grounding = await _retrieve_code_for_focus(
            user_id=user_id,
            query=f"{role_title} {picked.label}",
            document_ids=picked.document_ids,
        )

    prior_topics = [t.focus_label for t in prior_threads if t.focus_label]
    profile_for_prompt = profile_slice_for_focus(context["profile"], focus_key)
    user_msg = _build_question_message(
        role_title=role_title,
        company_name=company_name,
        focus_label=focus_label,
        profile=profile_for_prompt,
        prior_topics=prior_topics,
        thread_index=thread_index,
        code_grounding=code_grounding,
    )
    system_msg = _render_system_prompt(
        strategy=strategy,
        job_analysis=context["job_analysis"],
        company_snapshot=context["company_snapshot"],
    )

    logger.info(
        "Interviewer(open): session=%s thread=%d round=%s focus_key=%r doc_ids=%s",
        session_id,
        thread_index,
        round_type,
        focus_key,
        focus_document_ids,
    )

    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    yield (
        "move",
        {"kind": "question", "thread_index": thread_index, "message_id": str(message_id)},
    )

    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})
    parsed: dict[str, Any] | None = None
    with set_node_context("interviewer_open"):
        async for event, data in stream_json_object(
            _model_deltas(llm, [SystemMessage(content=system_msg), HumanMessage(content=user_msg)]),
            stream_string_fields=("question",),
        ):
            if event == "question_chunk":
                yield ("token", data)
            elif event == "done":
                parsed = data

    if parsed is None:
        raise StreamingJsonError("stream ended without producing a parsed object")
    try:
        question_obj = Question.model_validate(parsed)
    except Exception as e:
        raise StreamingJsonError(f"final JSON failed schema validation: {e}") from e

    if focus_label is not None:
        label_tokens = _tokens(focus_label)
        question_tokens = _tokens(question_obj.question)
        if label_tokens and not (label_tokens & question_tokens):
            logger.warning(
                "focus drift suspected: target=%r question=%r",
                focus_label,
                question_obj.question,
            )

    grounding_payload = [{"source": h.filename, "text": h.text} for h in code_grounding]
    async with AsyncSessionLocal() as s:
        await repos.create_thread(
            s,
            session_id=session_id,
            thread_index=thread_index,
            anchors=list(question_obj.anchors),
            focus_key=focus_key,
            focus_label=focus_label,
            focus_document_ids=focus_document_ids or None,
            thread_id=thread_id,
        )
        await repos.append_message(
            s,
            thread_id=thread_id,
            seq=0,
            role="interviewer",
            kind="question",
            text=question_obj.question,
            message_id=message_id,
        )

    yield ("move_done", {"anchors": list(question_obj.anchors)})
    yield (
        "opened",
        {
            "thread_id": str(thread_id),
            "thread_index": thread_index,
            "focus_key": focus_key,
            "focus_label": focus_label,
            "focus_document_ids": focus_document_ids,
            "anchors": list(question_obj.anchors),
            "grounding": grounding_payload,
        },
    )


def _coerce_action(raw: Any, allowed_actions: list[str]) -> str:
    """Map a model-returned action onto the allowed set; default to ``probe``
    (always offered + always safe) when the model picks something disallowed."""
    a = str(raw or "").strip().lower()
    return a if a in allowed_actions else "probe"


async def stream_conduct(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    thread_index: int,
    anchors: list[str],
    focus_label: str | None,
    grounding: list[dict[str, Any]],
    round_type: str,
    temperature: float = 0.4,
    profile: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    company: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Run one conductor step on the open thread.

    Reads the transcript, asks the conductor LLM for ``{action, message}``, and
    yields either:
        ("advance", {}) — close the thread; no utterance.
    or, for a real move (probe/clarify/nudge):
        ("move", {"kind", "thread_index", "message_id"})
        ("token", str)*
        ("move_done", {})
        ("conducted", {"action", "message_id"})

    ``action`` is coerced onto the round's allowed set; a follow-up Message is
    persisted for real moves only.
    """
    strategy = get_round_strategy(round_type)
    allowed_actions = conductor_allowed_actions(strategy)

    async with AsyncSessionLocal() as s:
        messages = list(await repos.list_messages_for_thread(s, thread_id))

    company_name = ((job or {}).get("company_name") or "").strip() or "the hiring company"
    role_title = ((job or {}).get("title") or "").strip() or "this role"
    transcript = [{"role": m.role, "kind": m.kind, "text": m.text} for m in messages]
    payload: dict[str, Any] = {
        "role": role_title,
        "company": company_name,
        "focus_target": focus_label,
        "anchors": list(anchors or []),
        "allowed_actions": allowed_actions,
        "transcript": transcript,
    }
    if grounding:
        payload["grounding"] = grounding
    user_msg = json.dumps(payload, ensure_ascii=False, indent=2)

    logger.info(
        "Interviewer(conduct): session=%s thread=%d round=%s allowed=%s n_msgs=%d",
        session_id,
        thread_index,
        round_type,
        allowed_actions,
        len(messages),
    )

    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})
    message_id = uuid.uuid4()
    action: str | None = None
    emitted_move = False
    buffered: list[str] = []
    full_message: list[str] = []
    parsed: dict[str, Any] | None = None

    with set_node_context("interviewer_conductor"):
        async for event, data in stream_json_object(
            _model_deltas(
                llm, [SystemMessage(content=CONDUCTOR_SYSTEM), HumanMessage(content=user_msg)]
            ),
            scalar_fields=("action",),
            stream_string_fields=("message",),
        ):
            if event == "action":
                action = _coerce_action(data, allowed_actions)
                if action != "advance":
                    yield (
                        "move",
                        {
                            "kind": action,
                            "thread_index": thread_index,
                            "message_id": str(message_id),
                        },
                    )
                    emitted_move = True
                    for ch in buffered:
                        yield ("token", ch)
                    buffered = []
            elif event == "message_chunk":
                full_message.append(data)
                if action is None:
                    buffered.append(data)
                elif action != "advance":
                    yield ("token", data)
            elif event == "done":
                parsed = data

    if action is None:
        action = _coerce_action((parsed or {}).get("action"), allowed_actions)

    if action == "advance":
        yield ("advance", {})
        return

    message_text = ((parsed or {}).get("message") or "".join(full_message) or "").strip()
    if not message_text:
        # Degenerate model output (real action, empty utterance). Persist a safe
        # generic follow-up so the bubble isn't empty.
        message_text = "Can you walk me through that in a bit more detail?"

    if not emitted_move:
        yield (
            "move",
            {"kind": action, "thread_index": thread_index, "message_id": str(message_id)},
        )
        for ch in buffered:
            yield ("token", ch)

    yield ("move_done", {})

    async with AsyncSessionLocal() as s:
        seq = await repos.count_messages_for_thread(s, thread_id)
        await repos.append_message(
            s,
            thread_id=thread_id,
            seq=seq,
            role="interviewer",
            kind=action,
            text=message_text,
            message_id=message_id,
        )

    yield ("conducted", {"action": action, "message_id": str(message_id)})
