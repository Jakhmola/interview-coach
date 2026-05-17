"""Unit tests for the Phase 20 profile slicer.

The fixture mirrors the production ``Profile`` schema (see
``interview_coach.agents.schemas.Profile``): summary, skills, experiences
(each with highlights), projects, education. No invented fields.
"""

from __future__ import annotations

from interview_coach.agents.profile_view import profile_slice_for_focus

FULL_PROFILE = {
    "summary": "Staff engineer with 10 years building data systems.",
    "skills": ["python", "postgres", "kafka"],
    "experiences": [
        {
            "company": "Acme",
            "role": "Staff Engineer",
            "start": "2021",
            "end": "present",
            "highlights": [
                {
                    "text": "Built the streaming ingest pipeline.",
                    "tech_stack": ["kafka", "go"],
                    "description": "1M events/sec, sub-100ms p99.",
                    "urls": [],
                    "source_document_ids": [],
                },
                {
                    "text": "Mentored four engineers.",
                    "tech_stack": [],
                    "description": None,
                    "urls": [],
                    "source_document_ids": [],
                },
            ],
        },
        {
            "company": "Globex",
            "role": "Senior Engineer",
            "start": "2018",
            "end": "2021",
            "highlights": [
                {
                    "text": "Migrated monolith to services.",
                    "tech_stack": ["docker"],
                    "description": None,
                    "urls": [],
                    "source_document_ids": [],
                }
            ],
        },
    ],
    "projects": [
        {
            "name": "BernoulliBench",
            "description": "OSS benchmark suite.",
            "tech": ["python"],
            "role": "maintainer",
            "urls": ["https://example.com/bb"],
            "source": "manual",
            "source_document_ids": [],
        },
        {
            "name": "",
            "description": "Unnamed side project.",
            "tech": [],
            "role": None,
            "urls": [],
            "source": "manual",
            "source_document_ids": [],
        },
    ],
    "education": [{"school": "MIT", "degree": "BS CS"}],
}


def test_empty_profile_returns_empty_dict() -> None:
    assert profile_slice_for_focus(None, None) == {}
    assert profile_slice_for_focus({}, "highlight:0:0") == {}


def test_no_focus_returns_full_profile_unchanged() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, None)
    assert out is FULL_PROFILE


def test_behavioral_signal_focus_returns_full_profile_unchanged() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "ownership")
    assert out is FULL_PROFILE


def test_highlight_focus_returns_anchor_plus_other_stubs() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "highlight:0:0")
    assert out["summary"] == FULL_PROFILE["summary"]
    assert out["skills"] == ["python", "postgres", "kafka"]
    assert out["focus"] == {"kind": "highlight", "experience_idx": 0, "highlight_idx": 0}
    # Full anchor experience: all highlights, all enrichment kept.
    anchor = out["anchor_experience"]
    assert anchor["company"] == "Acme"
    assert len(anchor["highlights"]) == 2
    assert anchor["highlights"][0]["description"] == "1M events/sec, sub-100ms p99."
    # The OTHER experience comes back as a stub — no highlights payload.
    assert out["other_experiences"] == [
        {"company": "Globex", "role": "Senior Engineer", "start": "2018", "end": "2021"},
    ]
    # No projects / education leakage on the highlight path.
    assert "projects" not in out
    assert "education" not in out
    assert "anchor_project" not in out


def test_highlight_focus_with_only_one_experience_omits_other_experiences_key() -> None:
    profile = dict(FULL_PROFILE)
    profile["experiences"] = FULL_PROFILE["experiences"][:1]
    out = profile_slice_for_focus(profile, "highlight:0:1")
    assert out["anchor_experience"]["company"] == "Acme"
    assert "other_experiences" not in out


def test_highlight_focus_invalid_experience_falls_back_to_full_profile() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "highlight:99:0")
    assert out is FULL_PROFILE


def test_highlight_focus_malformed_key_falls_back_to_full_profile() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "highlight:not-an-int:0")
    assert out is FULL_PROFILE


def test_project_focus_by_name() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "project:BernoulliBench")
    assert out["summary"] == FULL_PROFILE["summary"]
    assert out["skills"] == ["python", "postgres", "kafka"]
    assert out["focus"] == {"kind": "project", "project_idx": 0}
    assert out["anchor_project"]["name"] == "BernoulliBench"
    assert out["anchor_project"]["description"] == "OSS benchmark suite."
    assert out["anchor_project"]["role"] == "maintainer"
    # Don't drag in experiences / education.
    assert "experiences" not in out
    assert "anchor_experience" not in out


def test_project_focus_by_idx_for_unnamed_project() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "project:idx_1")
    assert out["focus"] == {"kind": "project", "project_idx": 1}
    assert out["anchor_project"]["description"] == "Unnamed side project."


def test_project_focus_unknown_name_falls_back_to_full_profile() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "project:DoesNotExist")
    assert out is FULL_PROFILE


def test_project_focus_bad_idx_falls_back_to_full_profile() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "project:idx_nope")
    assert out is FULL_PROFILE
    out2 = profile_slice_for_focus(FULL_PROFILE, "project:idx_99")
    assert out2 is FULL_PROFILE


def test_unknown_focus_prefix_falls_back_to_full_profile() -> None:
    out = profile_slice_for_focus(FULL_PROFILE, "experience:0")
    assert out is FULL_PROFILE
