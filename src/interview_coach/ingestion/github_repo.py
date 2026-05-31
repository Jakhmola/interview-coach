"""GitHub repo ingestion (Phase 32).

Three responsibilities, kept separable for testing:

1. ``extract_handle_from_cv`` / ``extract_repo_full_names_from_cv`` — regex
   helpers that mine a CV's text for a GitHub handle and any repo URLs (used
   to pre-fill the wizard card and pre-check repos in the picker).
2. ``categorize_paths`` + ``directory_structure`` — pick the README,
   dependency manifests and Dockerfiles out of the tree, and render a bounded
   high-level layout. **No source code is fetched** — the manifests name the
   real frameworks and the tree shows the project's shape.
3. ``ingest_repo`` — fetch README + manifests + Dockerfile + the directory
   structure via ``providers/github``, store as a ``github_repo`` Document,
   embed it through the shared grounding pipeline, then LLM-extract a
   ``ProjectItem`` (tagged ``set_node_context("github_intake")``) persisted on
   the doc's ``parsed_json`` so the prep-graph node can fold it into the
   Profile without re-running the LLM.
"""

from __future__ import annotations

import logging
import re
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from interview_coach.agents.prompts import GITHUB_INTAKE_SYSTEM
from interview_coach.agents.schemas import GithubProjectExtract, ProjectItem
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model_structured
from interview_coach.llm.telemetry import set_node_context
from interview_coach.providers import github as gh
from interview_coach.rag.ingest import embed_and_store_document

logger = logging.getLogger(__name__)

# --- CV mining -------------------------------------------------------------

# A GitHub handle: 1–39 chars, alphanumeric or hyphen, no leading/trailing
# hyphen. Captured as the first path segment after github.com.
_HANDLE = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_GITHUB_HANDLE_RE = re.compile(rf"github\.com/({_HANDLE})", re.IGNORECASE)
_GITHUB_REPO_RE = re.compile(rf"github\.com/({_HANDLE})/({_HANDLE})", re.IGNORECASE)

# Reserved github.com first-path-segments that are never user handles.
_RESERVED = frozenset(
    {"orgs", "settings", "about", "features", "pricing", "marketplace", "explore", "topics"}
)


def extract_handle_from_cv(text: str) -> str | None:
    """First plausible GitHub handle in ``text``, or ``None``.

    Skips reserved paths like ``github.com/settings``.
    """
    for m in _GITHUB_HANDLE_RE.finditer(text or ""):
        handle = m.group(1)
        if handle.lower() not in _RESERVED:
            return handle
    return None


def extract_repo_full_names_from_cv(text: str) -> set[str]:
    """All ``owner/repo`` pairs mentioned in ``text`` (lower-cased).

    Used to pre-check repos in the picker. ``.git`` suffixes are stripped.
    """
    out: set[str] = set()
    for m in _GITHUB_REPO_RE.finditer(text or ""):
        owner, repo = m.group(1), m.group(2)
        if owner.lower() in _RESERVED:
            continue
        repo = re.sub(r"\.git$", "", repo, flags=re.IGNORECASE)
        out.add(f"{owner}/{repo}".lower())
    return out


# --- tree categorisation + layout ------------------------------------------

# Directory path-segments that are vendored / generated / build output.
_EXCLUDE_DIR_PARTS = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        "venv",
        ".venv",
        "__pycache__",
        ".git",
        "target",
        "bin",
        "obj",
        ".next",
        ".nuxt",
        "coverage",
        ".idea",
        ".vscode",
        "third_party",
        "external",
        "deps",
        "site-packages",
        "bower_components",
        ".tox",
        "out",
        ".cache",
        "generated",
        "gen",
    }
)

# Path-segments / markers that indicate test code (excluded from the slice).
_TEST_DIR_PARTS = frozenset({"test", "tests", "spec", "specs", "__tests__", "e2e", "mocks"})

# Exact dependency-manifest filenames — fetched as raw text so the LLM can
# read the real frameworks/libraries straight out of them.
_MANIFEST_FILES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "pipfile",
        "package.json",
        "go.mod",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gemfile",
        "composer.json",
        "environment.yml",
    }
)

_DOCKERFILES = frozenset(
    {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"}
)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_excluded(path: str) -> bool:
    parts = [p.lower() for p in path.split("/")]
    if any(p in _EXCLUDE_DIR_PARTS for p in parts):
        return True
    if any(p in _TEST_DIR_PARTS for p in parts):
        return True
    base = _basename(path).lower()
    if base.startswith("test_") or base.startswith("test."):
        return True
    if ".test." in base or ".spec." in base or "_test." in base or base.endswith(".d.ts"):
        return True
    if base.endswith(".min.js") or base.endswith(".min.css"):
        return True
    return False


def categorize_paths(entries: list[gh.TreeEntry]) -> dict[str, list[str]]:
    """Pick the README, dependency manifests, and Dockerfiles out of a tree.

    README: the shallowest ``readme*`` file (root preferred). Manifests and
    Dockerfiles match exact filenames anywhere not under an excluded dir.
    """
    readmes: list[str] = []
    manifests: list[str] = []
    dockerfiles: list[str] = []
    for e in entries:
        if e.type != "blob":
            continue
        if _is_excluded(e.path):
            continue
        base = _basename(e.path).lower()
        if base.startswith("readme"):
            readmes.append(e.path)
        elif base in _MANIFEST_FILES:
            manifests.append(e.path)
        elif base in _DOCKERFILES or base.startswith("dockerfile"):
            dockerfiles.append(e.path)

    # Shallowest wins everywhere: the root README/manifest/Dockerfile is the
    # most representative, and capping manifests/Dockerfiles keeps a monorepo's
    # long list from crowding out the README + tree in the extractor's window.
    def _shallowest(paths: list[str]) -> list[str]:
        return sorted(paths, key=lambda p: (p.count("/"), len(p)))

    return {
        "readme": _shallowest(readmes)[:1],
        "manifests": _shallowest(manifests)[:MAX_MANIFESTS],
        "dockerfiles": _shallowest(dockerfiles)[:MAX_DOCKERFILES],
    }


# High-level layout: dirs + files no deeper than this, bounded line count, so a
# giant monorepo can't blow the LLM context or the grounding blob.
MAX_TREE_DEPTH = 2
MAX_TREE_LINES = 40

# A monorepo's tree can name 15 manifests; the root ones are the most
# representative of the whole project, so keep only the shallowest few.
MAX_MANIFESTS = 2
MAX_DOCKERFILES = 2


def directory_structure(entries: list[gh.TreeEntry]) -> str:
    """A compact, high-level view of the repo layout — no source code, just the
    shape of the project (top dirs + files ≤ ``MAX_TREE_DEPTH`` levels deep).

    Vendored / build / test directories are dropped (``_is_excluded``); the
    output is indented by depth so the LLM (and the reader) can see the
    project's components at a glance.
    """
    lines: list[str] = []
    for e in sorted(entries, key=lambda x: x.path):
        if not e.path or _is_excluded(e.path):
            continue
        depth = e.path.count("/")
        if depth > MAX_TREE_DEPTH:
            continue
        indent = "  " * depth
        suffix = "/" if e.type == "tree" else ""
        lines.append(f"{indent}{_basename(e.path)}{suffix}")
        if len(lines) >= MAX_TREE_LINES:
            lines.append("  …")
            break
    return "\n".join(lines)


# --- repo ingestion --------------------------------------------------------

MAX_README_CHARS = 4500
MAX_MANIFEST_CHARS = 2200
# Without a token GitHub allows ~60 req/hr unauthenticated — even a lean ingest
# run (tree + README + a few manifests per repo) eats into that fast. Hard-cap
# to one repo so verify + a single ingest still work, and log a clear hint.
NO_TOKEN_REPO_CAP = 1


def parse_owner_repo(full_name_or_url: str) -> tuple[str, str]:
    """``owner/repo`` from either a ``full_name`` or an ``html_url``."""
    s = full_name_or_url.strip()
    m = re.search(rf"github\.com/({_HANDLE})/({_HANDLE})", s, re.IGNORECASE)
    if m:
        return m.group(1), re.sub(r"\.git$", "", m.group(2), flags=re.IGNORECASE)
    parts = s.strip("/").split("/")
    if len(parts) >= 2:
        return parts[-2], re.sub(r"\.git$", "", parts[-1], flags=re.IGNORECASE)
    raise ValueError(f"cannot parse owner/repo from {full_name_or_url!r}")


def _assemble_repo_text(
    *,
    full_name: str,
    description: str | None,
    readme: str | None,
    manifests: list[tuple[str, str]],
    dockerfiles: list[tuple[str, str]],
    tree: str | None,
) -> str:
    """One labelled text blob: description + manifests + Dockerfiles +
    high-level directory structure + README. **No source code.**

    Each section is explicitly headed (``# README``, ``# Description``, …) and
    skipped entirely when its content is missing, so the LLM never sees an
    empty header. The LLM reads manifests as raw text (no per-ecosystem parser)
    and the same blob is chunked for grounding.

    Ordering is deliberate: the short, high-signal sources (description,
    manifests, Dockerfiles, tree) come *first* so they always survive the
    extractor's input truncation, with the README — often long and prose-heavy
    — last, where a verbose README can be clipped without losing the concrete
    tech signal the manifests already carry.
    """
    parts: list[str] = [f"# Repository: {full_name}"]
    if description:
        parts.append("# Description\n" + description)
    for path, text in manifests:
        parts.append(f"# Manifest: {path}\n" + text[:MAX_MANIFEST_CHARS])
    for path, text in dockerfiles:
        parts.append(f"# {path}\n" + text[:MAX_MANIFEST_CHARS])
    if tree:
        parts.append("# Directory structure\n" + tree)
    if readme:
        parts.append("# README\n" + readme[:MAX_README_CHARS])
    return "\n\n".join(parts)


async def _fetch_repo_text(
    *, owner: str, repo: str, branch: str, description: str | None, token: str | None
) -> str:
    """Fetch tree → README → dependency manifests / Dockerfiles → layout.

    Deliberately fetches **no source files**: the manifests name the real
    frameworks and the directory tree shows the project's shape, which is what
    the extraction LLM and the grounding layer need — and skipping per-file
    blob fetches keeps a repo's ingest to a handful of API calls (fast, and
    well under the unauthenticated rate cap).
    """
    full_name = f"{owner}/{repo}"
    entries = await gh.get_tree(owner, repo, branch, token)
    cats = categorize_paths(entries)

    readme_text: str | None = None
    if cats["readme"]:
        try:
            readme_text = await gh.fetch_blob(owner, repo, cats["readme"][0], branch, token)
        except Exception:  # noqa: BLE001
            logger.warning("github ingest: README fetch failed for %s", full_name)

    async def _grab(path: str) -> tuple[str, str] | None:
        try:
            return path, await gh.fetch_blob(owner, repo, path, branch, token)
        except Exception:  # noqa: BLE001
            logger.warning("github ingest: blob fetch failed for %s:%s", full_name, path)
            return None

    manifests = [m for p in cats["manifests"] if (m := await _grab(p))]
    dockerfiles = [d for p in cats["dockerfiles"] if (d := await _grab(p))]

    return _assemble_repo_text(
        full_name=full_name,
        description=description,
        readme=readme_text,
        manifests=manifests,
        dockerfiles=dockerfiles,
        tree=directory_structure(entries),
    )


async def ingest_repo(
    *,
    user_id: uuid.UUID,
    full_name: str,
    html_url: str,
    description: str | None,
    default_branch: str,
    token: str | None,
) -> uuid.UUID:
    """Ingest one selected repo into grounding + a folded ``ProjectItem``.

    Upserts a ``github_repo`` Document (keyed on ``source_url=html_url``),
    embeds it via the shared pipeline, then LLM-extracts a ``GithubProjectExtract``
    and stores the assembled ``ProjectItem`` dict on the doc's ``parsed_json``.
    Returns the document id.
    """
    owner, repo = parse_owner_repo(full_name)
    raw_text = await _fetch_repo_text(
        owner=owner, repo=repo, branch=default_branch, description=description, token=token
    )

    async with AsyncSessionLocal() as s:
        doc = await repos.upsert_github_repo_document(
            s,
            user_id=user_id,
            source_url=html_url,
            project_title=repo,
            raw_text=raw_text,
        )
        doc_id = doc.id

    # Embed through the shared grounding pipeline (source_doc_kind='github_repo').
    await embed_and_store_document(doc_id)

    # LLM-extract the project narrative + tech stack.
    #
    # enable_thinking=False is load-bearing here: qwen3's <think> block plus a
    # json_schema-constrained completion routinely blows the 8192-token ctx
    # before the JSON object closes ("Could not parse … length limit reached"),
    # so every repo failed to parse. This extraction (README → description+tech)
    # needs no chain-of-thought; turning thinking off keeps the completion small.
    with set_node_context("github_intake"):
        extract = await chat_model_structured(
            GithubProjectExtract,
            [
                SystemMessage(content=GITHUB_INTAKE_SYSTEM),
                HumanMessage(content=raw_text[:10000]),
            ],
            temperature=0.0,
            enable_thinking=False,
        )
    assert isinstance(extract, GithubProjectExtract)

    project = ProjectItem(
        name=repo,
        description=extract.description,
        tech=extract.tech,
        role=None,  # public repos rarely state a role; we no longer extract one
        urls=[html_url],
        key_features=extract.key_features,
        architecture=extract.architecture,
        source="github",
        source_document_ids=[doc_id],
    )
    async with AsyncSessionLocal() as s:
        await repos.set_document_parsed_json(s, doc_id, project.model_dump(mode="json"))

    logger.info("github ingest: %s → doc=%s tech=%s", full_name, doc_id, extract.tech)
    return doc_id
