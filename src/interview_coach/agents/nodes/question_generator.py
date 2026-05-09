"""QuestionGenerator agent node.

Streams one interview question. The model emits a single JSON object
``{"question": "...", "anchors": [...]}``; we forward the question text to
the SSE client as it arrives, then capture anchors at end-of-stream and
persist a `Turn` row.

Shape: an async generator that yields token strings (visible to the user)
and finally yields a sentinel `("__done__", {"question_id": ..., "turn_index": ...})`
so the API layer can format the SSE `done` event without re-fetching state.
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
from interview_coach.llm.client import chat_model

logger = logging.getLogger(__name__)


class GenerationPrereqsMissing(Exception):
    """Phase 8 routes raise 400 from this; profile / job analysis / snapshot absent."""


async def _load_context(session_row: SessionRow) -> dict[str, Any]:
    """Pull profile, job analysis, company snapshot, prior turns from Postgres."""
    user_id = session_row.user_id
    job_id = session_row.job_id

    async with AsyncSessionLocal() as s:
        profile_row = await repos.get_profile(s, user_id)
        if profile_row is None:
            raise GenerationPrereqsMissing("profile_missing")

        job = await repos.get_job(s, job_id, user_id)
        if job is None:
            raise GenerationPrereqsMissing("job_not_found")
        if not job.parsed_json:
            raise GenerationPrereqsMissing("job_not_analyzed")

        snapshot_row = await repos.get_company_snapshot_by_job(s, job_id)
        if snapshot_row is None:
            raise GenerationPrereqsMissing("company_snapshot_missing")

        turns = await repos.list_turns_for_session(s, session_row.id)

    return {
        "profile": profile_row.profile_json,
        "job_analysis": job.parsed_json,
        "company_snapshot": snapshot_row.snapshot_json,
        "prior_turns": [{"question": t.question, "answer": t.answer or ""} for t in turns],
    }


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#]*")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _experience_focus_key(exp: dict[str, Any], idx: int) -> str:
    company = (exp.get("company") or "").strip()
    role = (exp.get("role") or "").strip()
    if company or role:
        return f"experience:{company}/{role}".strip("/")
    return f"experience:idx_{idx}"


def _project_focus_key(proj: dict[str, Any], idx: int) -> str:
    name = (proj.get("name") or "").strip()
    if name:
        return f"project:{name}"
    return f"project:idx_{idx}"


def _experience_label(exp: dict[str, Any]) -> str:
    role = (exp.get("role") or "").strip()
    company = (exp.get("company") or "").strip()
    if role and company:
        return f"{role} @ {company}"
    return role or company or "(unnamed experience)"


def _project_label(proj: dict[str, Any]) -> str:
    name = (proj.get("name") or "").strip()
    description = (proj.get("description") or "").strip()
    if name and description:
        return f"{name} — {description}"
    return name or description or "(unnamed project)"


def _resume_candidate_corpus(item_kind: str, item: dict[str, Any]) -> set[str]:
    """Token bag used to score JD overlap for a resume candidate."""
    if item_kind == "experience":
        parts: list[str] = [
            str(item.get("role") or ""),
            str(item.get("company") or ""),
            *[str(h) for h in (item.get("highlights") or [])],
        ]
    else:  # project
        parts = [
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            *[str(t) for t in (item.get("tech") or [])],
            str(item.get("role") or ""),
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
) -> tuple[str, str] | None:
    """Pre-pick which experience / project / signal the question must drill into.

    Returns (focus_key, focus_label) or None if no candidates are available.

    Scoring:
      inv_freq(k) = 1 / (1 + prior_focus_counts.get(k, 0))
      resume_walkthrough: weight = (1 + jd_overlap_count) * inv_freq
      behavioral_star:    weight = inv_freq
    Then weighted-sample with `rng` so ties don't always pick the first.
    """
    candidates: list[tuple[str, str, float]] = []  # (key, label, weight)

    if round_type == "resume_walkthrough":
        must_have = _tokens(" ".join(str(s) for s in (job_analysis.get("must_have_skills") or [])))
        for i, exp in enumerate(profile.get("experiences") or []):
            if not isinstance(exp, dict):
                continue
            key = _experience_focus_key(exp, i)
            label = _experience_label(exp)
            overlap = len(_resume_candidate_corpus("experience", exp) & must_have)
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(key, 0))
            candidates.append((key, label, (1.0 + overlap) * inv_freq))
        for i, proj in enumerate(profile.get("projects") or []):
            if not isinstance(proj, dict):
                continue
            key = _project_focus_key(proj, i)
            label = _project_label(proj)
            overlap = len(_resume_candidate_corpus("project", proj) & must_have)
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(key, 0))
            candidates.append((key, label, (1.0 + overlap) * inv_freq))
    elif round_type == "behavioral_star":
        signals: list[str] = list(job_analysis.get("behavioral_signals") or [])
        if not signals:
            signals = list(company_snapshot.get("values_and_signals") or [])
        for sig in signals:
            sig_str = str(sig).strip()
            if not sig_str:
                continue
            inv_freq = 1.0 / (1.0 + prior_focus_counts.get(sig_str, 0))
            candidates.append((sig_str, sig_str, inv_freq))
    else:
        raise ValueError(f"unknown round_type: {round_type!r}")

    if not candidates:
        return None

    weights = [w for _, _, w in candidates]
    keys_labels = [(k, lbl) for k, lbl, _ in candidates]
    chosen_key, chosen_label = rng.choices(keys_labels, weights=weights, k=1)[0]
    return chosen_key, chosen_label


def _build_user_message(
    *,
    round_type: RoundType,
    context: dict[str, Any],
    focus_label: str | None,
    turn_index: int,
) -> str:
    """JSON payload of the structured context we hand to the LLM.

    Sticking to JSON keeps the prompt parseable for the model and easy to
    snapshot for tests — no clever templating.
    """
    payload: dict[str, Any] = {
        "round_type": round_type,
        "turn_index": turn_index,
        "profile": context["profile"],
        "job_analysis": context["job_analysis"],
        "company_snapshot": context["company_snapshot"],
        "prior_turns": context["prior_turns"],
    }
    if focus_label is not None:
        payload["focus_target"] = focus_label
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _system_for(round_type: RoundType) -> str:
    if round_type == "resume_walkthrough":
        return QUESTION_RESUME_WALKTHROUGH_SYSTEM
    if round_type == "behavioral_star":
        return QUESTION_BEHAVIORAL_STAR_SYSTEM
    raise ValueError(f"unknown round_type: {round_type!r}")


async def stream_question(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    temperature: float = 0.7,
) -> AsyncIterator[tuple[str, Any]]:
    """Generate, stream, and persist one question for `session_id`.

    Yields:
        ("token", str) — a chunk of the user-visible question text.
        ("done", {"question_id": str, "turn_index": int}) — once at end.

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

    context = await _load_context(session_row)

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
    if picked is None:
        focus_key, focus_label = None, None
    else:
        focus_key, focus_label = picked

    user_msg = _build_user_message(
        round_type=round_type,
        context=context,
        focus_label=focus_label,
        turn_index=turn_index,
    )

    logger.info(
        "QuestionGenerator: session=%s turn=%d round=%s focus_key=%r prior_counts=%s",
        session_id,
        turn_index,
        round_type,
        focus_key,
        prior_counts,
    )

    llm = chat_model(temperature=temperature).bind(response_format={"type": "json_object"})

    async def _model_deltas() -> AsyncIterator[str]:
        async for chunk in llm.astream(
            [
                SystemMessage(content=_system_for(round_type)),
                HumanMessage(content=user_msg),
            ]
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

    if focus_label is not None:
        label_tokens = _tokens(focus_label)
        question_tokens = _tokens(question_obj.question)
        # Heuristic: if no informative token from the focus label survives in
        # the question, the LLM probably drifted. Warning only — qwen3 isn't
        # perfectly steerable and substring matching misses paraphrases.
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
