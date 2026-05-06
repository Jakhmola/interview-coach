"""Phase 6 end-to-end agent smoke against real Ollama and a live Postgres
api running under docker compose. Opt-in via INTEGRATION=1.

What it does:
- Registers a fresh user via the api.
- Uploads a small generated PDF as the CV.
- Submits a pasted JD.
- Calls ProfileBuilder.build_profile and JobAnalyzer.analyze_job.
- Asserts non-empty extracted fields (skills, must_have_skills).

Slow: ~3-8 minutes depending on host. Skipped by default so `make test` stays fast.
"""

from __future__ import annotations

import io
import os
import uuid

import httpx
import pytest
from reportlab.pdfgen import canvas

from interview_coach.agents.nodes.company_researcher import research_company
from interview_coach.agents.nodes.job_analyzer import analyze_job
from interview_coach.agents.nodes.profile_builder import build_profile

API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="Set INTEGRATION=1 to run; requires docker stack up + ollama on host with qwen3:8b.",
)


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(text.split("\n")):
        c.drawString(72, 720 - i * 14, line[:90])
    c.showPage()
    c.save()
    return buf.getvalue()


CV_TEXT = """Alice Engineer
Senior Backend Engineer
6 years of Python, FastAPI, Postgres.
Worked at Acme building async APIs.
Project: rewrote sync stack to asyncio (40% latency drop).
BS Computer Science, State University, 2014-2018."""

JD_TEXT = """Senior Backend Engineer at Globex.
Required: Python, FastAPI, Postgres, async programming.
Nice to have: Kubernetes, Kafka.
You will: design and own backend services, mentor mid-level engineers,
collaborate with product, write production-grade code.
We value ownership, clear written communication, and pragmatism."""

# A JD that names a real, well-indexed public company so the CompanyResearcher
# loop has something to find. Used only by the company-research integration test.
JD_TEXT_REAL_COMPANY = """Member of Technical Staff at Anthropic.
You will help build safe, beneficial AI systems alongside the research team.
Required: strong Python, distributed systems experience, ML familiarity.
Nice to have: experience with LLMs, evaluations, or applied research.
We value clear written communication, technical rigor, and a focus on safety."""


async def _setup(jd_text: str = JD_TEXT) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (user_id, job_id) after seeding via the live API."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        email = f"agent-int-{uuid.uuid4()}@test.com"
        r = await http.post(
            f"{API_URL}/auth/register",
            json={"email": email, "password": "hunter22a"},
        )
        r.raise_for_status()
        body = r.json()
        token = body["access_token"]
        user_id = uuid.UUID(body["user"]["id"])

        r = await http.post(
            f"{API_URL}/documents",
            headers={"Authorization": f"Bearer {token}"},
            data={"kind": "cv"},
            files={"file": ("alice_cv.pdf", _make_pdf(CV_TEXT), "application/pdf")},
        )
        r.raise_for_status()

        r = await http.post(
            f"{API_URL}/jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": jd_text},
        )
        r.raise_for_status()
        job_id = uuid.UUID(r.json()["id"])

    return user_id, job_id


async def test_profile_builder_real_ollama() -> None:
    user_id, _ = await _setup()
    profile = await build_profile(user_id)
    assert profile.summary
    assert profile.skills, f"expected non-empty skills, got {profile!r}"


async def test_job_analyzer_real_ollama() -> None:
    user_id, job_id = await _setup()
    analysis = await analyze_job(job_id, user_id)
    assert analysis.title
    assert analysis.must_have_skills, f"expected must_have_skills, got {analysis!r}"


async def test_company_researcher_real() -> None:
    """End-to-end CompanyResearcher: real JobAnalyzer → real Tavily → real LLM.

    Requires TAVILY_API_KEY in the api environment + a recognizable company
    in the JD. Asserts non-empty mission and at least one product, plus a
    persisted row.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from interview_coach.config import settings
    from interview_coach.db import repos

    user_id, job_id = await _setup(JD_TEXT_REAL_COMPANY)

    analysis = await analyze_job(job_id, user_id)
    assert analysis.company_name, (
        f"phase 6 must populate company_name for this JD; got {analysis!r}"
    )

    snapshot = await research_company(job_id, user_id)
    assert snapshot.mission, f"empty mission, got {snapshot!r}"
    assert snapshot.products, f"expected at least one product, got {snapshot!r}"

    # Verify the row landed.
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        row = await repos.get_company_snapshot_by_job(s, job_id)
    await engine.dispose()
    assert row is not None
    assert row.source_urls, "expected at least one source URL on the persisted snapshot"
