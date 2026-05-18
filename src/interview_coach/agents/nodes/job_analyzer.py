"""JobAnalyzer agent node.

Reads a JD straight from Postgres (Phase 21: dropped MCP wrapper — JD
text is app-owned CRUD, not external-world I/O per CLAUDE.md boundary
rules), asks the LLM to extract a structured `JobAnalysis`, and persists
it into `jobs.parsed_json`.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import JOB_ANALYZER_SYSTEM
from interview_coach.agents.schemas import JobAnalysis
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model_structured
from interview_coach.llm.telemetry import set_node_context

logger = logging.getLogger(__name__)

MAX_JD_CHARS = 12000


class JobNotFoundError(Exception):
    pass


async def analyze_job(
    job_id: uuid.UUID, user_id: uuid.UUID, *, temperature: float = 0.0
) -> JobAnalysis:
    """Analyze a JD; persist the result to `jobs.parsed_json`.

    Raises:
        JobNotFoundError: no such job for this user.
    """
    async with AsyncSessionLocal() as session:
        job = await repos.get_job(session, job_id, user_id)
    if job is None:
        raise JobNotFoundError(f"job {job_id} not found for user {user_id}")

    text = job.raw_text
    if len(text) > MAX_JD_CHARS:
        text = text[:MAX_JD_CHARS] + "\n…[truncated]"

    logger.info("JobAnalyzer: analyzing job=%s for user=%s", job_id, user_id)

    with set_node_context("job_analyzer"):
        analysis = await chat_model_structured(
            JobAnalysis,
            [
                SystemMessage(content=JOB_ANALYZER_SYSTEM),
                HumanMessage(content=text),
            ],
            temperature=temperature,
        )
    assert isinstance(analysis, JobAnalysis)

    async with AsyncSessionLocal() as session:
        await repos.update_job_parsed_json(session, job_id, user_id, analysis.model_dump())

    return analysis
