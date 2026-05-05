"""JobAnalyzer agent node.

Reads a JD through MCP (`get_job`), asks the LLM to extract a structured
`JobAnalysis`, and persists it into `jobs.parsed_json`.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import JOB_ANALYZER_SYSTEM
from interview_coach.agents.schemas import JobAnalysis
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model
from interview_coach.mcp.client import decode_tool_result, get_tools

logger = logging.getLogger(__name__)

MAX_JD_CHARS = 12000


class JobNotFoundError(Exception):
    pass


async def _load_job(user_id: str, job_id: str) -> dict:
    tools = {t.name: t for t in await get_tools()}
    decoded = decode_tool_result(
        await tools["get_job"].ainvoke({"job_id": job_id, "user_id": user_id})
    )
    if not decoded or decoded[0] is None:
        raise JobNotFoundError(f"job {job_id} not found for user {user_id}")
    return decoded[0]


async def analyze_job(
    job_id: uuid.UUID, user_id: uuid.UUID, *, temperature: float = 0.0
) -> JobAnalysis:
    """Analyze a JD; persist the result to `jobs.parsed_json`.

    Raises:
        JobNotFoundError: no such job for this user.
    """
    job = await _load_job(str(user_id), str(job_id))
    text = job["raw_text"]
    if len(text) > MAX_JD_CHARS:
        text = text[:MAX_JD_CHARS] + "\n…[truncated]"

    logger.info("JobAnalyzer: analyzing job=%s for user=%s", job_id, user_id)

    llm = chat_model(temperature=temperature).with_structured_output(
        JobAnalysis, method="json_schema"
    )
    analysis = await llm.ainvoke(
        [
            SystemMessage(content=JOB_ANALYZER_SYSTEM),
            HumanMessage(content=text),
        ]
    )
    assert isinstance(analysis, JobAnalysis)

    async with AsyncSessionLocal() as session:
        await repos.update_job_parsed_json(session, job_id, user_id, analysis.model_dump())

    return analysis
