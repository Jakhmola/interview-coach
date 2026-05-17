"""QuestionGenerator agent node.

Streams one interview question. Phase 14.1: focus candidates are now rich
`Highlight` objects (each with provenance back to the project_doc that
enriched it) plus standalone `ProjectItem`s. The picked focus's
`document_ids` flow through to `turn.metadata_json.focus_document_ids` so
the evaluator's RAG retrieval can scope to the right project.

The model emits a single JSON object ``{"question": "...", "anchors": [...]}``;
we forward the question text to the SSE client as it arrives, then capture
anchors at end-of-stream and persist a `Turn` row.

Shape: an async generator that yields token strings (visible to the user)
and finally yields a sentinel
``("done", {"question_id": ..., "turn_index": ...})``.
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.profile_view import profile_slice_for_focus
from interview_coach.agents.prompts import (
    QUESTION_BEHAVIORAL_STAR_SYSTEM,
    QUESTION_RESUME_WALKTHROUGH_SYSTEM,
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
    """Pull profile, job analysis, company snapshot, prior turns from Postgres.

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

        turns = await repos.list_turns_for_session(s, session_row.id)

    return {
        "profile": profile,
        "job_analysis": job_analysis,
        "company_snapshot": company_snapshot,
        "prior_turns": [{"question": t.question, "answer": t.answer or ""} for t in turns],
    }


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#]*")


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


def _pick_focus_target(
    *,
    round_type: str,
    profile: dict[str, Any],
    job_analysis: dict[str, Any],
    company_snapshot: dict[str, Any],
    prior_focus_counts: dict[str, int],
    rng: random.Random,
) -> tuple[str, str, list[str]] | None:
    """Pre-pick which highlight / project / signal the question must drill into.

    Returns ``(focus_key, focus_label, document_ids)`` or None.

    Scoring:
      inv_freq(k) = 1 / (1 + prior_focus_counts.get(k, 0))
      resume_walkthrough: weight = (1 + jd_overlap_count) * inv_freq
      behavioral_star:    weight = inv_freq
    Weighted-sample with `rng` so ties don't always pick the first.
    """
    candidates: list[tuple[str, str, list[str], float]] = []  # (key, label, doc_ids, weight)

    if round_type == "resume_walkthrough":
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
                candidates.append((key, label, doc_ids, (1.0 + overlap) * inv_freq))
        for k, proj in enumerate(profile.get("projects") or []):
            if not isinstance(proj, dict):
                continue
            key = f"project:{(proj.get('name') or '').strip() or f'idx_{k}'}"
            label = _project_label(proj)
            doc_ids = [str(d) for d in (proj.get("source_document_ids") or [])]
            overlap = len(_project_candidate_corpus(proj) & must_have)
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(key, 0))
            candidates.append((key, label, doc_ids, (1.0 + overlap) * inv_freq))
    elif round_type == "behavioral_star":
        signals: list[str] = list(job_analysis.get("behavioral_signals") or [])
        if not signals:
            signals = list(company_snapshot.get("values_and_signals") or [])
        for sig in signals:
            sig_str = str(sig).strip()
            if not sig_str:
                continue
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(sig_str, 0))
            candidates.append((sig_str, sig_str, [], inv_freq))
    else:
        raise ValueError(f"unknown round_type: {round_type!r}")

    if not candidates:
        return None

    weights = [w for _, _, _, w in candidates]
    triples = [(k, lbl, ids) for k, lbl, ids, _ in candidates]
    chosen_key, chosen_label, chosen_doc_ids = rng.choices(triples, weights=weights, k=1)[0]
    return chosen_key, chosen_label, chosen_doc_ids


def _first_sentence(text: str, max_chars: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"[.!?]", text)
    sentence = text[: m.end()].strip() if m else text
    if len(sentence) > max_chars:
        sentence = sentence[: max_chars - 1].rstrip() + "…"
    return sentence


def _build_user_message(
    *,
    role_title: str,
    company_name: str,
    focus_label: str | None,
    profile: dict[str, Any],
    prior_turns: list[dict[str, Any]],
    turn_index: int,
) -> str:
    """JSON payload of the structured context we hand to the LLM.

    Phase 14.1: framing fields go first (`role`, `company`, `focus_target`)
    so the LLM sees them before the bulky profile. ``prior_turns`` is
    questions only — answers are stripped to keep the context tight.
    """
    payload: dict[str, Any] = {
        "role": role_title,
        "company": company_name,
        "turn_index": turn_index,
    }
    if focus_label is not None:
        payload["focus_target"] = focus_label
    payload["profile"] = profile
    payload["prior_turns"] = [{"question": t["question"]} for t in prior_turns]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_system_prompt(
    *,
    round_type: RoundType,
    job_analysis: dict[str, Any],
    company_snapshot: dict[str, Any],
) -> str:
    company_name = (job_analysis.get("company_name") or "").strip() or "the hiring company"
    role_title = (job_analysis.get("title") or "").strip() or "this role"
    seniority = (job_analysis.get("seniority") or "").strip() or "unknown"
    mission_one_line = _first_sentence(company_snapshot.get("mission") or "") or "—"
    values = [str(v).strip() for v in (company_snapshot.get("values_and_signals") or [])][:4]
    values_one_line = ", ".join(v for v in values if v) or "professionalism, ownership, clarity"

    if round_type == "resume_walkthrough":
        template = QUESTION_RESUME_WALKTHROUGH_SYSTEM
    elif round_type == "behavioral_star":
        template = QUESTION_BEHAVIORAL_STAR_SYSTEM
    else:
        raise ValueError(f"unknown round_type: {round_type!r}")

    return template.format(
        company_name=company_name,
        role_title=role_title,
        seniority=seniority,
        mission_one_line=mission_one_line,
        values_one_line=values_one_line,
    )


async def stream_question(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    temperature: float = 0.7,
    profile: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    company: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Generate, stream, and persist one question for `session_id`.

    Yields:
        ("token", str) — a chunk of the user-visible question text.
        ("done", {"question_id": str, "turn_index": int}) — once at end.

    Phase 20: ``profile`` / ``job`` / ``company`` may be pre-loaded by the
    route layer and forwarded here via state. When provided, DB reads for
    them are skipped.

    Raises:
        GenerationPrereqsMissing: profile/job-analysis/snapshot not ready.
        ValueError: session not found / wrong user / session already complete.
        StreamingJsonError: model emitted invalid JSON.
    """
    async with AsyncSessionLocal() as s:
        session_row = await repos.get_session(s, session_id, user_id)
        if session_row is None:
            raise ValueError("session_not_found")
        if session_row.status != "active":
            raise ValueError(f"session_status_{session_row.status}")
        existing_turns = await repos.list_turns_for_session(s, session_id)

    turn_index = len(existing_turns)
    if turn_index >= session_row.n_questions:
        raise ValueError("session_complete")

    context = await _load_context(
        session_row,
        profile=profile,
        job_analysis=job,
        company_snapshot=company,
    )

    round_type: RoundType = session_row.round_type  # type: ignore[assignment]

    async with AsyncSessionLocal() as s:
        prior_keys = await repos.list_prior_focus_keys_for_user_job(
            s,
            user_id=session_row.user_id,
            job_id=session_row.job_id,
            round_type=round_type,
        )
    prior_counts = repos.count_focus_keys(prior_keys)

    picked = _pick_focus_target(
        round_type=round_type,
        profile=context["profile"],
        job_analysis=context["job_analysis"],
        company_snapshot=context["company_snapshot"],
        prior_focus_counts=prior_counts,
        rng=random.Random(),
    )
    focus_key: str | None
    focus_label: str | None
    focus_document_ids: list[str]
    if picked is None:
        focus_key, focus_label, focus_document_ids = None, None, []
    else:
        focus_key, focus_label, focus_document_ids = picked

    company_name = (
        context["job_analysis"].get("company_name") or ""
    ).strip() or "the hiring company"
    role_title = (context["job_analysis"].get("title") or "").strip() or "this role"

    # Phase 20: ship only the focus-anchored slice of the profile to the
    # LLM, not the full 6-12 KB JSON. The picker's focus_key (or None)
    # selects the right anchor; behavioral signals fall through to a
    # name+headline+top-bullets stub.
    profile_for_prompt = profile_slice_for_focus(context["profile"], focus_key)
    user_msg = _build_user_message(
        role_title=role_title,
        company_name=company_name,
        focus_label=focus_label,
        profile=profile_for_prompt,
        prior_turns=context["prior_turns"],
        turn_index=turn_index,
    )
    system_msg = _render_system_prompt(
        round_type=round_type,
        job_analysis=context["job_analysis"],
        company_snapshot=context["company_snapshot"],
    )

    logger.info(
        "QuestionGenerator: session=%s turn=%d round=%s focus_key=%r doc_ids=%s",
        session_id,
        turn_index,
        round_type,
        focus_key,
        focus_document_ids,
    )

    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})

    async def _model_deltas() -> AsyncIterator[str]:
        async for chunk in astream_with_telemetry(
            llm,
            [
                SystemMessage(content=system_msg),
                HumanMessage(content=user_msg),
            ],
        ):
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

    parsed: dict[str, Any] | None = None
    with set_node_context("question_generator"):
        async for event, data in stream_json_object(
            _model_deltas(), stream_string_fields=("question",)
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

    metadata: dict[str, Any] = {}
    if focus_key is not None:
        metadata["focus_key"] = focus_key
    if focus_label is not None:
        metadata["focus_label"] = focus_label
    if focus_document_ids:
        metadata["focus_document_ids"] = focus_document_ids

    if focus_label is not None:
        label_tokens = _tokens(focus_label)
        question_tokens = _tokens(question_obj.question)
        if label_tokens and not (label_tokens & question_tokens):
            logger.warning(
                "focus drift suspected: target=%r question=%r",
                focus_label,
                question_obj.question,
            )

    async with AsyncSessionLocal() as s:
        turn = await repos.create_turn(
            s,
            session_id=session_id,
            turn_index=turn_index,
            question=question_obj.question,
            anchors=question_obj.anchors,
            metadata=metadata or None,
        )

    yield ("done", {"question_id": str(turn.id), "turn_index": turn_index})
