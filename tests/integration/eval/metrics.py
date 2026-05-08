"""Three question-quality metrics for the Phase 12a baseline harness.

- `distinctness` — TF-IDF cosine distance over a same-session set of 5
  questions. Pure sklearn; no LLM, no embedding model. Higher = more variety.
  Phase 14 will swap the vector source to Jina embeddings; the rest of the
  code path stays the same.
- `profile_groundedness` — G-Eval score (1-10 scaled to 0.0-1.0 by deepeval)
  over (question, profile_json + raw_cv).
- `jd_relevance` — G-Eval over (question, job_analysis_json).

`GEval` instances are built once at module load — building them is cheap, but
the `LocalChatLLM` cached on each metric is reused across calls, which keeps
imports tidy.
"""

from __future__ import annotations

import json
from typing import Any

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_distances

from tests.integration.eval.local_llm import LocalChatLLM


def distinctness(questions: list[str]) -> float:
    """Mean pairwise cosine distance over TF-IDF (1,2)-gram vectors.

    For N questions there are N*(N-1)/2 pairs; we return the mean of the
    upper-triangle. With N<2 or all questions identical we return 0.0.
    """
    if len(questions) < 2:
        return 0.0
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), lowercase=True)
    try:
        matrix = vectorizer.fit_transform(questions)
    except ValueError:
        # Empty vocabulary (all stopwords / blank input).
        return 0.0
    dists = cosine_distances(matrix)
    n = dists.shape[0]
    if n < 2:
        return 0.0
    # Sum upper triangle, divide by pair count.
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += float(dists[i, j])
            pairs += 1
    return total / pairs if pairs else 0.0


# --- G-Eval metric singletons (lazy so import doesn't try to build the LLM
#     on a `make test` run). ---

_PROFILE_GROUNDEDNESS: GEval | None = None
_JD_RELEVANCE: GEval | None = None


def _profile_groundedness_metric() -> GEval:
    global _PROFILE_GROUNDEDNESS
    if _PROFILE_GROUNDEDNESS is None:
        _PROFILE_GROUNDEDNESS = GEval(
            name="profile_groundedness",
            criteria=(
                "Determine whether the candidate-facing INTERVIEW QUESTION "
                "(actual_output) probes a SPECIFIC detail attested in the "
                "candidate's profile or CV (context). A strong score means a "
                "good answer would necessarily draw on the candidate's "
                "actual experience — a project, role, technology, or "
                "outcome named in the profile. A weak score means the "
                "question is generic and any candidate with the job title "
                "could answer it without reference to the profile."
            ),
            evaluation_params=[
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.CONTEXT,
            ],
            model=LocalChatLLM(),
            async_mode=False,
        )
    return _PROFILE_GROUNDEDNESS


def _jd_relevance_metric() -> GEval:
    global _JD_RELEVANCE
    if _JD_RELEVANCE is None:
        _JD_RELEVANCE = GEval(
            name="jd_relevance",
            criteria=(
                "Determine whether the candidate-facing INTERVIEW QUESTION "
                "(actual_output) targets a competency, must-have skill, "
                "responsibility, or behavioral signal named in the job "
                "description analysis (context). A strong score means the "
                "question clearly probes something the JD calls out. A "
                "weak score means the question is unrelated to the role's "
                "stated requirements."
            ),
            evaluation_params=[
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.CONTEXT,
            ],
            model=LocalChatLLM(),
            async_mode=False,
        )
    return _JD_RELEVANCE


def profile_groundedness(question: str, profile: dict[str, Any], cv_text: str) -> float:
    """Returns the GEval score in [0.0, 1.0]. Higher is more grounded."""
    metric = _profile_groundedness_metric()
    test_case = LLMTestCase(
        input="(question generation context)",
        actual_output=question,
        context=[
            "PROFILE_JSON:\n" + json.dumps(profile, ensure_ascii=False, indent=2),
            "RAW_CV:\n" + cv_text[:4000],
        ],
    )
    metric.measure(test_case)
    return float(metric.score) if metric.score is not None else 0.0


def jd_relevance(question: str, job: dict[str, Any]) -> float:
    """Returns the GEval score in [0.0, 1.0]. Higher is more JD-relevant."""
    metric = _jd_relevance_metric()
    test_case = LLMTestCase(
        input="(question generation context)",
        actual_output=question,
        context=["JOB_ANALYSIS_JSON:\n" + json.dumps(job, ensure_ascii=False, indent=2)],
    )
    metric.measure(test_case)
    return float(metric.score) if metric.score is not None else 0.0
