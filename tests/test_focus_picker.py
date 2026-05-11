"""Unit tests for the deterministic focus picker (Phase 13, updated 14.1).

Pure-function tests with a seeded `random.Random` — no DB, no LLM, no graph.

Phase 14.1: candidates are now per-highlight (across all experiences) plus
standalone projects. Focus keys: ``highlight:{exp_idx}:{hl_idx}`` and
``project:{name}``. Picker returns a 3-tuple
``(focus_key, focus_label, document_ids)``.
"""

from __future__ import annotations

import random

from interview_coach.agents.nodes.question_generator import _pick_focus_target
from interview_coach.db.repos import count_focus_keys


def _hl(text: str, *, tech: list[str] | None = None, doc_ids: list[str] | None = None) -> dict:
    return {
        "text": text,
        "tech_stack": tech or [],
        "description": None,
        "urls": [],
        "source_document_ids": doc_ids or [],
    }


PROFILE = {
    "experiences": [
        {
            "company": "Globex",
            "role": "Senior SWE",
            "highlights": [_hl("Rewrote sync stack to async, 40% latency drop.")],
        },
        {
            "company": "Initech",
            "role": "Staff Engineer",
            "highlights": [_hl("Led migration to Kubernetes.")],
        },
        {
            "company": "Acme",
            "role": "SWE",
            "highlights": [_hl("Built Java reporting service.")],
        },
    ],
    "projects": [
        {
            "name": "AsyncAPI",
            "description": "Internal high-throughput API gateway.",
            "tech": ["python", "fastapi"],
            "role": "tech lead",
            "urls": [],
            "source": "project_doc",
            "source_document_ids": ["doc-a"],
        },
        {
            "name": "RustyParser",
            "description": "Side project — toy parser combinator.",
            "tech": ["rust"],
            "role": "solo",
            "urls": [],
            "source": "project_doc",
            "source_document_ids": [],
        },
    ],
}

JOB_PYTHON = {"must_have_skills": ["python", "fastapi", "postgres"]}
JOB_NO_SKILLS: dict[str, list[str]] = {"must_have_skills": []}
SNAPSHOT_EMPTY: dict[str, list[str]] = {"values_and_signals": []}


def test_resume_picks_jd_overlapping_candidate_when_history_empty() -> None:
    """No prior history → picker should favor candidates with JD-skill overlap."""
    seen: set[str] = set()
    for seed in range(50):
        picked = _pick_focus_target(
            round_type="resume_walkthrough",
            profile=PROFILE,
            job_analysis=JOB_PYTHON,
            company_snapshot=SNAPSHOT_EMPTY,
            prior_focus_counts={},
            rng=random.Random(seed),
        )
        assert picked is not None
        seen.add(picked[0])
    assert "project:AsyncAPI" in seen


def test_resume_inverse_frequency_pushes_picker_off_overused_key() -> None:
    counts = {"project:AsyncAPI": 100}
    picks: list[str] = []
    for seed in range(30):
        picked = _pick_focus_target(
            round_type="resume_walkthrough",
            profile=PROFILE,
            job_analysis=JOB_PYTHON,
            company_snapshot=SNAPSHOT_EMPTY,
            prior_focus_counts=counts,
            rng=random.Random(seed),
        )
        assert picked is not None
        picks.append(picked[0])
    asyncapi_share = picks.count("project:AsyncAPI") / len(picks)
    assert asyncapi_share < 0.2, f"AsyncAPI overpicked: {asyncapi_share:.2%}"


def test_resume_with_no_jd_skills_falls_back_to_inv_freq_only() -> None:
    """Empty must_have_skills → weights are pure inv_freq."""
    picked = _pick_focus_target(
        round_type="resume_walkthrough",
        profile=PROFILE,
        job_analysis=JOB_NO_SKILLS,
        company_snapshot=SNAPSHOT_EMPTY,
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked is not None
    seen: set[str] = set()
    for seed in range(80):
        p = _pick_focus_target(
            round_type="resume_walkthrough",
            profile=PROFILE,
            job_analysis=JOB_NO_SKILLS,
            company_snapshot=SNAPSHOT_EMPTY,
            prior_focus_counts={},
            rng=random.Random(seed),
        )
        assert p is not None
        seen.add(p[0])
    assert len(seen) >= 4  # at least 4 of 5 candidates should appear


def test_resume_returns_none_when_profile_empty() -> None:
    picked = _pick_focus_target(
        round_type="resume_walkthrough",
        profile={"experiences": [], "projects": []},
        job_analysis=JOB_PYTHON,
        company_snapshot=SNAPSHOT_EMPTY,
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked is None


def test_resume_focus_key_format() -> None:
    """Keys follow `highlight:{i}:{j}` and `project:{name}` shapes."""
    keys: set[str] = set()
    for seed in range(50):
        p = _pick_focus_target(
            round_type="resume_walkthrough",
            profile=PROFILE,
            job_analysis=JOB_PYTHON,
            company_snapshot=SNAPSHOT_EMPTY,
            prior_focus_counts={},
            rng=random.Random(seed),
        )
        assert p is not None
        keys.add(p[0])
    for key in keys:
        assert key.startswith(("highlight:", "project:")), key
    assert any(k.startswith("project:") for k in keys)


def test_resume_picker_returns_document_ids() -> None:
    """Project with source_document_ids should pass them through."""
    seeds: list[tuple[str, list[str]]] = []
    for seed in range(80):
        p = _pick_focus_target(
            round_type="resume_walkthrough",
            profile=PROFILE,
            job_analysis=JOB_NO_SKILLS,
            company_snapshot=SNAPSHOT_EMPTY,
            prior_focus_counts={},
            rng=random.Random(seed),
        )
        assert p is not None
        seeds.append((p[0], p[2]))
    matching = [doc_ids for key, doc_ids in seeds if key == "project:AsyncAPI"]
    assert matching, "AsyncAPI never picked; weight balance broke"
    assert all(doc_ids == ["doc-a"] for doc_ids in matching)


def test_behavioral_returns_none_when_no_signals() -> None:
    picked = _pick_focus_target(
        round_type="behavioral_star",
        profile=PROFILE,
        job_analysis={"behavioral_signals": []},
        company_snapshot={"values_and_signals": []},
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked is None


def test_behavioral_least_used_signal_dominates() -> None:
    counts = {"ownership": 50}
    picks: list[str] = []
    for seed in range(30):
        p = _pick_focus_target(
            round_type="behavioral_star",
            profile=PROFILE,
            job_analysis={"behavioral_signals": ["ownership", "mentorship"]},
            company_snapshot={"values_and_signals": []},
            prior_focus_counts=counts,
            rng=random.Random(seed),
        )
        assert p is not None
        picks.append(p[0])
    mentorship_share = picks.count("mentorship") / len(picks)
    assert mentorship_share > 0.9, f"least-used signal under-picked: {mentorship_share:.2%}"


def test_behavioral_falls_back_to_company_snapshot_signals() -> None:
    picked = _pick_focus_target(
        round_type="behavioral_star",
        profile=PROFILE,
        job_analysis={"behavioral_signals": []},
        company_snapshot={"values_and_signals": ["written-doc culture"]},
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked == ("written-doc culture", "written-doc culture", [])


def test_behavioral_focus_key_is_signal_string_verbatim() -> None:
    picked = _pick_focus_target(
        round_type="behavioral_star",
        profile=PROFILE,
        job_analysis={"behavioral_signals": ["cross-team communication"]},
        company_snapshot={"values_and_signals": []},
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked is not None
    key, label, doc_ids = picked
    assert key == "cross-team communication"
    assert label == "cross-team communication"
    assert doc_ids == []


def test_count_focus_keys_helper() -> None:
    assert count_focus_keys([]) == {}
    assert count_focus_keys(["a", "b", "a", "c", "a"]) == {"a": 3, "b": 1, "c": 1}
