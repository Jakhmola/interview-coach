"""GitHub handle + repo endpoints (Phase 32).

Backs the setup-wizard GitHub card: the card extracts/accepts a handle and
calls ``GET /github/verify`` to confirm it exists on GitHub *before* prep
runs (fail-fast on typos). On a hit the handle is persisted on the user row
so the prep-graph github segment can list and ingest repos later.

During *setup*, repo selection is an in-graph HITL that resumes the prep
graph via ``POST /sessions/prepare/resume_repos``. *After* setup, the Manage
page edits the repo set out-of-graph through ``GET /github/repos`` +
``POST /github/repos/select`` here — the same out-of-graph pattern as
``project_doc`` remap. Both paths converge on ``fold_github_projects`` so the
Profile, the ``github_repo`` docs and their grounding chunks stay consistent
no matter which entry point made the change.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.api.auth.deps import get_current_user
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db
from interview_coach.ingestion.errors import FetchFailed
from interview_coach.ingestion.github_repo import (
    NO_TOKEN_REPO_CAP,
    extract_handle_from_cv,
    extract_repo_full_names_from_cv,
    ingest_repo,
)
from interview_coach.providers import github as gh

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


class VerifyResult(BaseModel):
    exists: bool
    handle: str | None = None
    name: str | None = None
    avatar_url: str | None = None
    public_repos: int | None = None


class SuggestResult(BaseModel):
    """What the wizard card pre-fills on mount: the handle already persisted
    on the user (if any) plus a fresh suggestion mined from their CV text."""

    current: str | None = None
    cv_suggested: str | None = None


@router.get("/suggest", response_model=SuggestResult)
async def suggest_handle(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SuggestResult:
    """Pre-fill source for the GitHub card: persisted handle + CV regex hit."""
    docs = await repos.list_documents_for_user(session, user.id)
    cv = next((d for d in docs if d.kind == "cv"), None)
    cv_suggested = extract_handle_from_cv(cv.raw_text) if cv is not None else None
    return SuggestResult(current=user.github_handle, cv_suggested=cv_suggested)


@router.get("/verify", response_model=VerifyResult)
async def verify_handle(
    handle: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VerifyResult:
    """Confirm ``handle`` is a real GitHub account; persist it on a hit.

    Persisting here (rather than in a separate call) makes the card a single
    "verify → remembered" action — the prep graph reads ``users.github_handle``
    when it lists repos. A miss returns ``{exists: false}`` and leaves any
    previously-stored handle untouched.
    """
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_handle")

    try:
        ghuser = await gh.verify_user(handle, settings.github_token)
    except FetchFailed as e:
        logger.warning("github verify failed for handle=%s: %s", handle, e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "github_unreachable") from e

    if ghuser is None:
        return VerifyResult(exists=False)

    await repos.set_user_github_handle(session, user.id, ghuser.login)
    return VerifyResult(
        exists=True,
        handle=ghuser.login,
        name=ghuser.name,
        avatar_url=ghuser.avatar_url,
        public_repos=ghuser.public_repos,
    )


# --- post-setup repo management (Manage page) ------------------------------


class RepoItem(BaseModel):
    """One repo in the post-setup picker. Mirrors the in-graph ``repos_available``
    payload plus ``already_ingested`` so the modal can pre-check current repos."""

    full_name: str
    name: str
    description: str | None = None
    language: str | None = None
    stars: int = 0
    pushed_at: str | None = None
    html_url: str
    default_branch: str
    archived: bool = False
    cv_mentioned: bool = False
    already_ingested: bool = False


class RepoListResult(BaseModel):
    handle: str
    repos: list[RepoItem]


class SelectReposRequest(BaseModel):
    selected_urls: list[str] = []


class SelectReposResult(BaseModel):
    n_projects: int
    ingested: int
    removed: int


async def _require_handle(user: User) -> str:
    handle = (user.github_handle or "").strip()
    if not handle:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_handle")
    return handle


@router.get("/repos", response_model=RepoListResult)
async def list_repos(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> RepoListResult:
    """List the verified handle's public repos for the Manage picker.

    Forks are hidden, archived kept-but-flagged (mirrors the prep-graph
    discover node). ``cv_mentioned`` + ``already_ingested`` drive the modal's
    pre-checks. Requires a stored handle — ``400 no_handle`` tells the FE to
    surface the verify card first.
    """
    handle = await _require_handle(user)
    try:
        repos_list = await gh.list_public_repos(handle, settings.github_token)
    except FetchFailed as e:
        logger.warning("github list_repos failed for handle=%s: %s", handle, e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "github_unreachable") from e

    docs = await repos.list_documents_for_user(session, user.id)
    cv = next((d for d in docs if d.kind == "cv"), None)
    cv_names = extract_repo_full_names_from_cv(cv.raw_text) if cv is not None else set()
    ingested_urls = {
        d.source_url for d in docs if d.kind == "github_repo" and d.source_url is not None
    }

    items = [
        RepoItem(
            full_name=r.full_name,
            name=r.name,
            description=r.description,
            language=r.language,
            stars=r.stars,
            pushed_at=r.pushed_at,
            html_url=r.html_url,
            default_branch=r.default_branch,
            archived=r.archived,
            cv_mentioned=r.full_name.lower() in cv_names,
            already_ingested=r.html_url in ingested_urls,
        )
        for r in repos_list
        if not r.fork
    ]
    return RepoListResult(handle=handle, repos=items)


@router.post("/repos/select", response_model=SelectReposResult)
async def select_repos(
    body: SelectReposRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SelectReposResult:
    """Apply a post-setup repo selection: ingest newly-checked repos, delete
    deselected ones (FK cascade wipes their chunks), then re-fold the Profile.

    Idempotent and consistent with the prep-graph path: re-folding via
    ``fold_github_projects`` rewrites the Profile's ``source='github'`` projects
    to exactly match the surviving ``github_repo`` docs, so the Profile, the
    docs, and their grounding chunks never drift. Already-ingested repos left
    checked are not re-embedded (no needless LLM/embedder work).
    """
    from interview_coach.agents.nodes.github_ingest import fold_github_projects

    handle = await _require_handle(user)
    token = settings.github_token
    selected = [u for u in body.selected_urls if isinstance(u, str)]

    existing = await repos.list_github_repo_docs_for_user(session, user.id)
    existing_urls = {d.source_url for d in existing if d.source_url is not None}

    # Deselect = delete (commits internally; FK cascade clears chunks).
    removed = await repos.delete_github_repo_docs_not_selected(session, user.id, selected)

    # Only ingest repos that aren't already stored — leaving a checked repo
    # checked is a no-op, not a re-embed.
    to_ingest = [u for u in selected if u not in existing_urls]
    if not token and len(to_ingest) > NO_TOKEN_REPO_CAP:
        logger.warning(
            "github select: no GITHUB_TOKEN — capping %d new repos to %d. "
            "Set GITHUB_TOKEN to ingest more.",
            len(to_ingest),
            NO_TOKEN_REPO_CAP,
        )
        to_ingest = to_ingest[:NO_TOKEN_REPO_CAP]

    ingested = 0
    if to_ingest:
        try:
            repos_list = await gh.list_public_repos(handle, token)
        except FetchFailed as e:
            logger.warning("github select: list failed for handle=%s: %s", handle, e)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "github_unreachable") from e
        by_url = {r.html_url: r for r in repos_list}
        for url in to_ingest:
            meta = by_url.get(url)
            if meta is None:
                logger.warning("github select: no metadata for %s; skipping", url)
                continue
            try:
                await ingest_repo(
                    user_id=user.id,
                    full_name=meta.full_name,
                    html_url=meta.html_url,
                    description=meta.description,
                    default_branch=meta.default_branch or "main",
                    token=token,
                )
                ingested += 1
            except Exception:  # noqa: BLE001
                logger.exception("github select: ingest failed for %s", url)

    # The consistency anchor — same fold the prep graph + Manage-delete use.
    n = await fold_github_projects(user.id)
    return SelectReposResult(n_projects=n, ingested=ingested, removed=len(removed))
