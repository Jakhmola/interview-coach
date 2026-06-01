"""GitHub provider — wraps the public GitHub REST API over httpx (Phase 32).

Same shape as ``providers/tavily.py``: top-level async helpers, an app-level
token from ``settings.github_token`` passed in by the caller, and typed
``FetchFailed`` / ``KeyMissing`` from ``ingestion/errors.py`` on failure.

Public repos only — no scopes needed. The token (when present) only lifts
the unauthenticated 60 req/hr rate cap to 5000 req/hr; every endpoint here
works token-less for public data, so we never raise ``KeyMissing`` (the
ingest orchestration logs a hint and hard-caps repo count instead).

Transport is the REST API, not ``git clone``: the trees endpoint returns the
full path list in one call, from which we render a high-level directory
structure and pick the README + dependency manifests to fetch (no source code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from interview_coach.ingestion.errors import FetchFailed

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_TIMEOUT = 30.0


@dataclass(frozen=True)
class GithubUser:
    login: str
    name: str | None
    avatar_url: str | None
    public_repos: int


@dataclass(frozen=True)
class RepoListing:
    full_name: str  # "owner/repo"
    name: str
    description: str | None
    language: str | None
    stars: int
    pushed_at: str | None
    html_url: str
    default_branch: str
    fork: bool
    archived: bool


@dataclass(frozen=True)
class TreeEntry:
    path: str
    size: int  # 0 for non-blob entries (trees)
    type: str  # "blob" | "tree" | ...


def _headers(token: str | None, *, raw: bool = False) -> dict[str, str]:
    accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
    headers = {"Accept": accept, "X-GitHub-Api-Version": _API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def verify_user(handle: str, token: str | None) -> GithubUser | None:
    """Existence check for a GitHub username. ``None`` when the handle 404s.

    Raises:
        FetchFailed: network error or non-2xx/404 response.
    """
    url = f"{API_BASE}/users/{handle}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_headers(token))
    except httpx.HTTPError as e:
        raise FetchFailed(f"Network error contacting GitHub: {e}") from e

    if r.status_code == 404:
        logger.info("github.verify_user: handle=%s not found", handle)
        return None
    if r.status_code != 200:
        raise FetchFailed(f"GitHub returned {r.status_code} for user {handle}: {r.text[:200]}")

    data = r.json()
    return GithubUser(
        login=data.get("login") or handle,
        name=data.get("name"),
        avatar_url=data.get("avatar_url"),
        public_repos=int(data.get("public_repos") or 0),
    )


async def list_public_repos(handle: str, token: str | None) -> list[RepoListing]:
    """List a user's public repos, newest-pushed first, across all pages.

    Raises:
        FetchFailed: network error or non-2xx response.
    """
    out: list[RepoListing] = []
    page = 1
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            while True:
                r = await client.get(
                    f"{API_BASE}/users/{handle}/repos",
                    headers=_headers(token),
                    params={"per_page": 100, "sort": "pushed", "page": page},
                )
                if r.status_code != 200:
                    raise FetchFailed(
                        f"GitHub repos list returned {r.status_code} for {handle}: {r.text[:200]}"
                    )
                batch = r.json()
                if not isinstance(batch, list) or not batch:
                    break
                for item in batch:
                    out.append(
                        RepoListing(
                            full_name=item.get("full_name") or "",
                            name=item.get("name") or "",
                            description=item.get("description"),
                            language=item.get("language"),
                            stars=int(item.get("stargazers_count") or 0),
                            pushed_at=item.get("pushed_at"),
                            html_url=item.get("html_url") or "",
                            default_branch=item.get("default_branch") or "main",
                            fork=bool(item.get("fork")),
                            archived=bool(item.get("archived")),
                        )
                    )
                if len(batch) < 100:
                    break
                page += 1
    except httpx.HTTPError as e:
        raise FetchFailed(f"Network error listing GitHub repos for {handle}: {e}") from e

    logger.info("github.list_public_repos: handle=%s repos=%d", handle, len(out))
    return out


async def get_tree(owner: str, repo: str, branch: str, token: str | None) -> list[TreeEntry]:
    """Recursive git tree for a repo at ``branch``: every path + blob size.

    Raises:
        FetchFailed: network error or non-2xx response.
    """
    url = f"{API_BASE}/repos/{owner}/{repo}/git/trees/{branch}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_headers(token), params={"recursive": "1"})
    except httpx.HTTPError as e:
        raise FetchFailed(f"Network error fetching tree for {owner}/{repo}: {e}") from e
    if r.status_code != 200:
        raise FetchFailed(
            f"GitHub tree returned {r.status_code} for {owner}/{repo}@{branch}: {r.text[:200]}"
        )
    data = r.json()
    entries: list[TreeEntry] = []
    for t in data.get("tree") or []:
        entries.append(
            TreeEntry(
                path=t.get("path") or "",
                size=int(t.get("size") or 0),
                type=t.get("type") or "",
            )
        )
    return entries


async def fetch_blob(owner: str, repo: str, path: str, branch: str, token: str | None) -> str:
    """Fetch a single file's raw text via the contents API.

    Raises:
        FetchFailed: network error or non-2xx response.
    """
    url = f"{API_BASE}/repos/{owner}/{repo}/contents/{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_headers(token, raw=True), params={"ref": branch})
    except httpx.HTTPError as e:
        raise FetchFailed(f"Network error fetching {owner}/{repo}:{path}: {e}") from e
    if r.status_code != 200:
        raise FetchFailed(
            f"GitHub blob {owner}/{repo}:{path} returned {r.status_code}: {r.text[:200]}"
        )
    return r.text
