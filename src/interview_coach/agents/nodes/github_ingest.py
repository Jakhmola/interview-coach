"""GitHub ingestion prep-graph nodes (Phase 32).

Three nodes, placed after ``profile_builder`` and before the doc-mapping loop,
mirroring the doc-mapping HITL split (prepare / await-interrupt / apply):

* ``node_github_discover`` — list the user's public repos (when a verified
  handle exists and no repos are ingested yet), emit a ``repos_available``
  event, and route to the interrupt. When there's no handle and nothing
  ingested, skip the whole segment; when repos are already ingested, skip the
  prompt and route straight to the fold node (re-fold after a profile rebuild).
* ``node_await_repo_selection`` — pure ``interrupt(...)`` holding for the
  user's ``selected_urls`` (no side-effects, so resume replays are free).
* ``node_github_ingest_and_fold`` — upsert/embed each selected repo, delete
  deselected repos (FK cascade wipes chunks), then fold the github
  ``ProjectItem``s (LLM-extracted at ingest, stashed on ``parsed_json``) into
  the Profile. Owns its skip verdict and ``ok``/``degraded`` outcome.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from interview_coach.agents.prep_events import NodeDone, NodeError, NodeSkipped, emit
from interview_coach.agents.schemas import Profile, ProjectItem
from interview_coach.agents.state import InterviewState
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.ingestion.errors import FetchFailed
from interview_coach.ingestion.github_repo import (
    NO_TOKEN_REPO_CAP,
    RepoIngestError,
    extract_repo_full_names_from_cv,
    ingest_repo,
)
from interview_coach.providers import github as gh

logger = logging.getLogger(__name__)


async def _cv_mentioned_full_names(user_id: uuid.UUID) -> set[str]:
    """Repo ``owner/repo`` pairs the user's CV references (for pre-checking)."""
    async with AsyncSessionLocal() as s:
        docs = await repos.list_documents_for_user(s, user_id)
    cv = next((d for d in docs if d.kind == "cv"), None)
    return extract_repo_full_names_from_cv(cv.raw_text) if cv is not None else set()


async def node_github_discover(state: InterviewState) -> dict[str, Any]:
    """List public repos for the verified handle and route into the HITL."""
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()

    async with AsyncSessionLocal() as s:
        user = await repos.get_user(s, user_id)
        existing = await repos.list_github_repo_docs_for_user(s, user_id)
    handle = (user.github_handle if user is not None else None) or None
    has_docs = len(existing) > 0

    # No handle and nothing ingested → the whole segment is a no-op.
    if not handle and not has_docs:
        emit(writer, NodeSkipped(node="github", reason="no_repos_selected"))
        return {"next_step": "prepare_mapping_suggestion", "github_repos": None}

    # Already ingested on a prior run → don't re-prompt; re-fold (cheap) so a
    # profile rebuild upstream can't strand the github projects.
    if has_docs:
        return {
            "next_step": "github_ingest_and_fold",
            "github_repos": None,
            "github_resume": None,
        }

    # Handle present, nothing ingested → list + prompt.
    try:
        repos_list = await gh.list_public_repos(handle, settings.github_token)
    except FetchFailed as e:
        logger.warning("github discover: list failed for handle=%s: %s", handle, e)
        emit(writer, NodeDone(node="github", outcome="degraded", code="FetchFailed", detail=str(e)))
        return {"next_step": "prepare_mapping_suggestion", "github_repos": None}

    # Forks hidden; archived kept-but-flagged.
    visible = [r for r in repos_list if not r.fork]
    if not visible:
        emit(writer, NodeSkipped(node="github", reason="no_repos_selected"))
        return {"next_step": "prepare_mapping_suggestion", "github_repos": None}

    cv_names = await _cv_mentioned_full_names(user_id)
    repos_payload = [
        {
            "full_name": r.full_name,
            "name": r.name,
            "description": r.description,
            "language": r.language,
            "stars": r.stars,
            "pushed_at": r.pushed_at,
            "html_url": r.html_url,
            "default_branch": r.default_branch,
            "archived": r.archived,
            "cv_mentioned": r.full_name.lower() in cv_names,
        }
        for r in visible
    ]
    writer({"event": "repos_available", "payload": {"repos": repos_payload}})
    return {"next_step": "await_repo_selection", "github_repos": repos_payload}


async def node_await_repo_selection(state: InterviewState) -> dict[str, Any]:
    """Pure interrupt — pauses until the user submits ``selected_urls``."""
    resume = interrupt({"awaiting": "repo_selection"})
    return {"github_resume": resume if isinstance(resume, dict) else {"selected_urls": []}}


async def fold_github_projects(user_id: uuid.UUID) -> int:
    """Rewrite the Profile's ``source='github'`` projects to match the current
    set of ingested github_repo docs. Returns the github-project count.

    Reads each doc's ``parsed_json`` (the ProjectItem stashed at ingest) — no
    LLM call. Non-github projects (CV / project_doc) are left untouched.
    """
    async with AsyncSessionLocal() as s:
        profile_row = await repos.get_profile(s, user_id)
        github_docs = await repos.list_github_repo_docs_for_user(s, user_id)
    if profile_row is None:
        return 0

    # Validate each github project on its own so one malformed ``parsed_json``
    # (e.g. an extract from an older schema) is skipped rather than poisoning
    # the whole profile-validate below — that exception would otherwise abort
    # the prep stream and bounce the user back to setup.
    github_projects: list[dict[str, Any]] = []
    for d in github_docs:
        if not d.parsed_json:
            continue
        try:
            github_projects.append(
                ProjectItem.model_validate(d.parsed_json).model_dump(mode="json")
            )
        except Exception:  # noqa: BLE001
            logger.warning("github fold: skipping malformed parsed_json for doc=%s", d.id)

    profile_dict = dict(profile_row.profile_json)
    kept = [
        p
        for p in (profile_dict.get("projects") or [])
        if isinstance(p, dict) and p.get("source") != "github"
    ]
    profile_dict["projects"] = kept + github_projects
    profile = Profile.model_validate(profile_dict)

    async with AsyncSessionLocal() as s:
        source_doc_ids = await repos.current_profile_doc_ids(s, user_id)
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json=profile.model_dump(mode="json"),
            source_doc_ids=source_doc_ids,
            model_name=settings.model_name,
        )
    return len(github_projects)


async def node_github_ingest_and_fold(state: InterviewState) -> dict[str, Any]:
    """Apply the user's repo selection, then fold github projects into the Profile.

    With a resume payload: delete deselected repos, ingest selected ones, fold.
    Without one (the re-fold path): just re-fold the existing docs' projects.

    **prep⊥interview barrier (follow-up 3):** if any selected repo fails to
    ingest, prep does *not* finalize. The node routes back to
    ``await_repo_selection`` and re-emits ``repos_available`` with per-repo
    ``ingest_error`` annotations, so the user retries or deselects the broken
    repos before entering the interview. A repo that already fully ingested (has
    chunks + ``parsed_json``) is skipped, so Retry re-attempts only the broken
    ones.
    """
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()
    resume = state.get("github_resume")
    discovered = {r["html_url"]: r for r in (state.get("github_repos") or [])}
    token = settings.github_token
    degraded_code: str | None = None
    failures: list[dict[str, Any]] = []
    selected_urls: list[str] = []

    if resume is not None:
        selected_urls = [u for u in (resume.get("selected_urls") or []) if isinstance(u, str)]

        # Deselect = delete (FK cascade wipes chunks; fold drops the project).
        async with AsyncSessionLocal() as s:
            removed = await repos.delete_github_repo_docs_not_selected(s, user_id, selected_urls)
            # Skip repos that already fully ingested on a prior pass so Retry
            # re-attempts only the ones that broke (no needless re-embed).
            already_done = await repos.list_fully_ingested_github_urls(s, user_id)
        if removed:
            logger.info("github ingest: deselected %d repo(s) for user=%s", len(removed), user_id)

        # Token-less runs blow the 60 req/hr cap fast — hard-cap repo count.
        to_ingest = selected_urls
        if not token and len(to_ingest) > NO_TOKEN_REPO_CAP:
            logger.warning(
                "github ingest: no GITHUB_TOKEN — capping %d selected repos to %d. "
                "Set GITHUB_TOKEN to ingest more.",
                len(to_ingest),
                NO_TOKEN_REPO_CAP,
            )
            to_ingest = to_ingest[:NO_TOKEN_REPO_CAP]
            degraded_code = "no_github_token"

        for url in to_ingest:
            if url in already_done:
                continue
            meta = discovered.get(url)
            if meta is None:
                logger.warning("github ingest: no discovered metadata for %s; skipping", url)
                continue
            try:
                await ingest_repo(
                    user_id=user_id,
                    full_name=meta["full_name"],
                    html_url=meta["html_url"],
                    description=meta.get("description"),
                    default_branch=meta.get("default_branch") or "main",
                    token=token,
                )
            except RepoIngestError as e:
                logger.warning("github ingest failed for %s at step=%s: %s", url, e.step, e.reason)
                failures.append(
                    {
                        "html_url": url,
                        "full_name": meta.get("full_name"),
                        "step": e.step,
                        "code": e.code,
                        "reason": e.reason,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("github ingest failed for %s", url)
                failures.append(
                    {
                        "html_url": url,
                        "full_name": meta.get("full_name"),
                        "step": "unknown",
                        "code": type(e).__name__,
                        "reason": str(e),
                    }
                )

    # Barrier: any unresolved failure → re-open the picker instead of finalizing.
    if failures:
        err_by_url = {f["html_url"]: f for f in failures}
        selected_set = set(selected_urls)
        annotated: list[dict[str, Any]] = []
        for r in state.get("github_repos") or []:
            rr = dict(r)
            # Keep the user's whole current selection pre-checked so a plain
            # Retry resubmits the same set (the succeeded ones are skip-guarded).
            if r.get("html_url") in selected_set:
                rr["already_ingested"] = True
            err = err_by_url.get(r.get("html_url"))
            if err is not None:
                rr["ingest_error"] = {
                    "step": err["step"],
                    "code": err["code"],
                    "reason": err["reason"],
                }
            annotated.append(rr)
        writer({"event": "repos_available", "payload": {"repos": annotated}})
        return {
            "next_step": "await_repo_selection",
            "github_repos": annotated,
            "github_resume": None,
            "github_failures": failures,
        }

    try:
        n = await fold_github_projects(user_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("github fold failed for user=%s", user_id)
        emit(writer, NodeError(node="github", code=type(e).__name__, detail=str(e)))
        raise

    if degraded_code is not None:
        emit(writer, NodeDone(node="github", outcome="degraded", code=degraded_code))
    else:
        emit(writer, NodeDone(node="github"))
    writer({"event": "github_folded", "n_projects": n})
    # Explicit next_step: ``next_step`` is sticky in the checkpoint (discover set
    # it to await/ingest), and the edge out of this node is now conditional —
    # without resetting it a clean run could re-route back to await.
    return {
        "next_step": "prepare_mapping_suggestion",
        "github_repos": None,
        "github_resume": None,
        "github_failures": [],
    }
