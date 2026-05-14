"""CompanyResearcher agent node.

Loads the JD analysis (which already names the company), runs a small Tavily
research loop (search → extract top pages), and asks the LLM to compress the
text into a structured `CompanySnapshot`. Result is cached per `job_id`.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import COMPANY_RESEARCHER_SYSTEM
from interview_coach.agents.schemas import CompanySnapshot
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.ingestion.errors import FetchFailed
from interview_coach.ingestion.web import SearchResult, fetch_url_text, tavily_search
from interview_coach.llm.client import ainvoke_with_telemetry, chat_model
from interview_coach.llm.telemetry import set_node_context
from interview_coach.mcp.client import decode_tool_result, get_tools

logger = logging.getLogger(__name__)

MAX_PAGE_CHARS = 6000


class CompanyNameMissing(Exception):
    """Raised when JobAnalysis didn't capture a company name to research."""


class JobNotAnalyzed(Exception):
    """Raised when `jobs.parsed_json` is empty — run JobAnalyzer first."""


class NoSearchHits(Exception):
    """Raised when Tavily returns zero results for the company queries."""


class NoUsablePages(Exception):
    """Raised when every candidate URL failed to extract."""


async def _load_job(user_id: str, job_id: str) -> dict:
    tools = {t.name: t for t in await get_tools()}
    decoded = decode_tool_result(
        await tools["get_job"].ainvoke({"job_id": job_id, "user_id": user_id})
    )
    if not decoded or decoded[0] is None:
        raise JobNotAnalyzed(f"job {job_id} not found for user {user_id}")
    return decoded[0]


def _dedupe_by_url(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        out.append(r)
    return out


async def _gather_pages(
    company: str, *, max_search_results: int, max_extracts: int
) -> tuple[list[str], list[str]]:
    """Run two searches, dedupe, extract the top N. Returns (page_texts, source_urls).

    Per-URL extract failures are logged + skipped. Caller raises if zero pages
    came back.
    """
    api_key = settings.tavily_api_key

    queries = [f"{company} company overview", f"{company} recent news"]
    hits: list[SearchResult] = []
    for q in queries:
        hits.extend(await tavily_search(q, api_key, max_results=max_search_results))
    hits = _dedupe_by_url(hits)
    if not hits:
        raise NoSearchHits(f"Tavily returned no search results for '{company}'")

    hits.sort(key=lambda r: r["score"], reverse=True)
    candidates = hits[:max_extracts]

    page_texts: list[str] = []
    source_urls: list[str] = []
    for hit in candidates:
        try:
            text = await fetch_url_text(hit["url"], api_key)
        except FetchFailed as e:
            logger.warning("CompanyResearcher: extract failed for %s: %s", hit["url"], e)
            continue
        if len(text) > MAX_PAGE_CHARS:
            text = text[:MAX_PAGE_CHARS] + "\n…[truncated]"
        page_texts.append(f"# {hit['title']} — {hit['url']}\n\n{text}")
        source_urls.append(hit["url"])

    return page_texts, source_urls


async def research_company(
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    force_refresh: bool = False,
    temperature: float = 0.0,
    max_search_results: int = 5,
    max_extracts: int = 2,
) -> CompanySnapshot:
    """Research the company named in `job.parsed_json.company_name`.

    Caches per `job_id`. Pass `force_refresh=True` to bypass the cache.

    Raises:
        JobNotAnalyzed: job missing or `parsed_json` empty.
        CompanyNameMissing: JD analysis has no company_name.
        NoSearchHits: Tavily returned zero results.
        NoUsablePages: every candidate URL failed to extract.
    """
    job = await _load_job(str(user_id), str(job_id))
    parsed = job.get("parsed_json") or {}
    if not parsed:
        raise JobNotAnalyzed(f"job {job_id} has no parsed_json; run JobAnalyzer first")

    company = (parsed.get("company_name") or "").strip()
    if not company:
        raise CompanyNameMissing(f"job {job_id} has no company_name")

    if not force_refresh:
        async with AsyncSessionLocal() as session:
            existing = await repos.get_company_snapshot_by_job(session, job_id)
        if existing is not None:
            logger.info("CompanyResearcher: cache hit for job=%s", job_id)
            return CompanySnapshot.model_validate(existing.snapshot_json)

    logger.info("CompanyResearcher: researching %r for job=%s", company, job_id)
    page_texts, source_urls = await _gather_pages(
        company, max_search_results=max_search_results, max_extracts=max_extracts
    )
    if not page_texts:
        raise NoUsablePages(f"all candidate URLs failed to extract for '{company}'")

    body = "\n\n---\n\n".join(page_texts)

    with set_node_context("company_researcher"):
        llm = chat_model(temperature=temperature).with_structured_output(
            CompanySnapshot, method="json_schema"
        )
        snapshot = await ainvoke_with_telemetry(
            llm,
            [
                SystemMessage(content=COMPANY_RESEARCHER_SYSTEM),
                HumanMessage(content=f"Company: {company}\n\nSources:\n\n{body}"),
            ],
        )
    assert isinstance(snapshot, CompanySnapshot)

    async with AsyncSessionLocal() as session:
        await repos.upsert_company_snapshot(
            session,
            job_id=job_id,
            company_name=company,
            snapshot_json=snapshot.model_dump(),
            source_urls=source_urls,
            model_name=settings.model_name,
        )

    return snapshot
