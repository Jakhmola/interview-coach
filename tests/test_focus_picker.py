"""Unit tests for the deterministic focus picker (Phase 13).

Pure-function tests with a seeded `random.Random` — no DB, no LLM, no graph.
"""

from __future__ import annotations

import random

from interview_coach.agents.nodes.question_generator import _pick_focus_target
from interview_coach.db.repos import count_focus_keys

PROFILE = {
    "experiences": [
        {
            "company": "Globex",
            "role": "Senior SWE",
            "highlights": ["Rewrote sync stack to async, 40% latency drop."],
        },
        {
            "company": "Initech",
            "role": "Staff Engineer",
            "highlights": ["Led migration to Kubernetes."],
        },
        {
            "company": "Acme",
            "role": "SWE",
            "highlights": ["Built Java reporting service."],
        },
    ],
    "projects": [
        {
            "name": "AsyncAPI",
            "description": "Internal high-throughput API gateway.",
            "tech": ["python", "fastapi"],
            "role": "tech lead",
        },
        {
            "name": "RustyParser",
            "description": "Side project — toy parser combinator.",
            "tech": ["rust"],
            "role": "solo",
        },
    ],
}

JOB_PYTHON = {"must_have_skills": ["python", "fastapi", "postgres"]}
JOB_NO_SKILLS: dict[str, list[str]] = {"must_have_skills": []}
SNAPSHOT_EMPTY: dict[str, list[str]] = {"values_and_signals": []}


def test_resume_picks_jd_overlapping_candidate_when_history_empty() -> None:
    """No prior history → picker should favor candidates with JD-skill overlap.

    AsyncAPI's tech is ["python", "fastapi"] (overlap=2), the Globex experience
    mentions async (no must-have hits but role text doesn't match either).
    With empty history, weights are dominated by the (1 + overlap) term.
    """
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
    # AsyncAPI (overlap=2) should appear in ≥40% of seeds — a meaningful
    # fraction even though weighted-sample doesn't always pick it.
    assert "project:AsyncAPI" in seen


def test_resume_inverse_frequency_pushes_picker_off_overused_key() -> None:
    """Heavy prior count on AsyncAPI → picker should pick something else most of the time."""
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
    # With inv_freq = 1/101, AsyncAPI's effective weight is ~0.03 vs. siblings
    # at ~1.0–1.5. It should be picked far less than half the time.
    assert asyncapi_share < 0.2, f"AsyncAPI overpicked: {asyncapi_share:.2%}"


def test_resume_with_no_jd_skills_falls_back_to_inv_freq_only() -> None:
    """Empty must_have_skills → all candidates have overlap=0 → weighting
    is pure inv_freq. With empty history, all candidates are equally weighted."""
    picked = _pick_focus_target(
        round_type="resume_walkthrough",
        profile=PROFILE,
        job_analysis=JOB_NO_SKILLS,
        company_snapshot=SNAPSHOT_EMPTY,
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked is not None
    # All 5 candidates are reachable.
    seen: set[str] = set()
    for seed in range(50):
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
    """Keys follow `experience:{company}/{role}` and `project:{name}` shape."""
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
        assert key.startswith(("experience:", "project:"))
    assert any(k.startswith("project:") for k in keys)


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
    """Signal with high prior count → picker rotates to the other one."""
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
    """Empty JD signals → use values_and_signals from the company snapshot."""
    picked = _pick_focus_target(
        round_type="behavioral_star",
        profile=PROFILE,
        job_analysis={"behavioral_signals": []},
        company_snapshot={"values_and_signals": ["written-doc culture"]},
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked == ("written-doc culture", "written-doc culture")


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
    key, label = picked
    assert key == "cross-team communication"
    assert label == "cross-team communication"


def test_count_focus_keys_helper() -> None:
    assert count_focus_keys([]) == {}
    assert count_focus_keys(["a", "b", "a", "c", "a"]) == {"a": 3, "b": 1, "c": 1}


def test_experience_with_missing_company_role_falls_back_to_idx_key() -> None:
    profile = {"experiences": [{"highlights": ["did stuff"]}], "projects": []}
    picked = _pick_focus_target(
        round_type="resume_walkthrough",
        profile=profile,
        job_analysis=JOB_NO_SKILLS,
        company_snapshot=SNAPSHOT_EMPTY,
        prior_focus_counts={},
        rng=random.Random(0),
    )
    assert picked is not None
    assert picked[0] == "experience:idx_0"
