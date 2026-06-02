"""Phase 32 — GitHub ingestion.

Pure / mocked coverage (host, no GitHub network, no LLM, no embedder):

* CV handle + repo-URL regex extraction.
* high-level directory-structure rendering (excludes, depth + line caps).
* manifest/README/Dockerfile categorisation.
* ``ingest_repo`` end-to-end with mocked provider + LLM + embed.
* the github prep-node skip verdict (no handle / no repos → no_repos_selected).
* fold + deselect-cascade (a github project drops when its doc is deleted).
* focus-weighting fires for a github ProjectItem.
* the ``/github/verify`` + ``/github/suggest`` endpoints.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from interview_coach.agents.schemas import GithubProjectExtract
from interview_coach.providers import github as gh


@pytest.fixture(autouse=True)
def _github_token_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``settings.github_token`` for every test in this file.

    The ingest node reads ``settings.github_token`` to decide the no-token repo
    cap (``NO_TOKEN_REPO_CAP``). Without this, the node tests inherit the dev's
    ambient ``.env`` — green locally when a token is set, but red in CI where it
    isn't (the cap truncates a multi-repo selection). No test here exercises the
    settings-level no-token path (the ``ingest_repo`` no-token test passes
    ``token=None`` explicitly), so pinning a token keeps them deterministic.
    """
    from interview_coach.agents.nodes import github_ingest

    monkeypatch.setattr(github_ingest.settings, "github_token", "test-token")


# --- CV mining -------------------------------------------------------------


def test_extract_handle_from_cv_finds_handle_skips_reserved() -> None:
    from interview_coach.ingestion.github_repo import extract_handle_from_cv

    assert extract_handle_from_cv("see https://github.com/octocat for code") == "octocat"
    # Reserved first-segment is skipped in favour of a real handle later.
    assert extract_handle_from_cv("github.com/settings then github.com/torvalds") == "torvalds"
    assert extract_handle_from_cv("no github here") is None


def test_extract_repo_full_names_from_cv() -> None:
    from interview_coach.ingestion.github_repo import extract_repo_full_names_from_cv

    text = "Projects: github.com/octocat/Hello-World.git and https://github.com/octocat/spoon"
    names = extract_repo_full_names_from_cv(text)
    assert "octocat/hello-world" in names
    assert "octocat/spoon" in names


# --- directory structure ---------------------------------------------------


def _blob(path: str, size: int) -> gh.TreeEntry:
    return gh.TreeEntry(path=path, size=size, type="blob")


def _tree(path: str) -> gh.TreeEntry:
    return gh.TreeEntry(path=path, size=0, type="tree")


def test_directory_structure_excludes_vendored_and_caps_depth() -> None:
    from interview_coach.ingestion.github_repo import directory_structure

    entries = [
        _tree("src"),
        _blob("src/app.py", 2000),
        _tree("node_modules"),
        _blob("node_modules/lib/index.js", 5000),
        _tree("tests"),
        _blob("tests/test_app.py", 3000),
        _blob("src/deep/nested/way/too/far.py", 100),  # depth > MAX_TREE_DEPTH
        _blob("README.md", 1000),
    ]
    out = directory_structure(entries)
    assert "src/" in out
    assert "app.py" in out
    assert "README.md" in out
    # Vendored + test dirs and their contents are dropped.
    assert "node_modules" not in out
    assert "test_app.py" not in out
    # Too-deep paths are pruned.
    assert "far.py" not in out


def test_directory_structure_respects_line_cap() -> None:
    from interview_coach.ingestion.github_repo import MAX_TREE_LINES, directory_structure

    entries = [_blob(f"file_{i}.py", 100) for i in range(MAX_TREE_LINES + 20)]
    out = directory_structure(entries)
    # Capped at MAX_TREE_LINES rendered entries plus the truncation marker.
    assert out.count("\n") + 1 <= MAX_TREE_LINES + 1
    assert out.rstrip().endswith("…")


def test_categorize_paths_picks_readme_manifests_dockerfile() -> None:
    from interview_coach.ingestion.github_repo import categorize_paths

    entries = [
        _blob("README.md", 100),
        _blob("docs/README.md", 100),
        _blob("pyproject.toml", 100),
        _blob("frontend/package.json", 100),
        _blob("Dockerfile", 100),
        _blob("src/app.py", 100),
    ]
    cats = categorize_paths(entries)
    assert cats["readme"] == ["README.md"]  # shallowest wins
    assert set(cats["manifests"]) == {"pyproject.toml", "frontend/package.json"}
    assert cats["dockerfiles"] == ["Dockerfile"]


# --- ingest_repo (mocked provider + LLM + embed) ---------------------------


async def test_ingest_repo_stores_github_project(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.db import repos
    from interview_coach.ingestion import github_repo

    user = await repos.create_user(db_session, "gh@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_repo, "AsyncSessionLocal", factory)

    async def fake_get_tree(*_a: Any, **_kw: Any) -> list[gh.TreeEntry]:
        return [_blob("README.md", 200), _blob("pyproject.toml", 100), _blob("src/app.py", 500)]

    async def fake_fetch_blob(_o: str, _r: str, path: str, *_a: Any, **_kw: Any) -> str:
        return f"content of {path}"

    async def fake_embed(_doc_id: uuid.UUID) -> int:
        return 3

    async def fake_extract(*_a: Any, **_kw: Any) -> GithubProjectExtract:
        return GithubProjectExtract(description="I built a thing.", tech=["fastapi", "pgvector"])

    monkeypatch.setattr(github_repo.gh, "get_tree", fake_get_tree)
    monkeypatch.setattr(github_repo.gh, "fetch_blob", fake_fetch_blob)
    monkeypatch.setattr(github_repo, "embed_and_store_document", fake_embed)
    monkeypatch.setattr(github_repo, "chat_model_structured", fake_extract)

    doc_id = await github_repo.ingest_repo(
        user_id=user.id,
        full_name="octocat/widget",
        html_url="https://github.com/octocat/widget",
        description="a widget",
        default_branch="main",
        token=None,
    )

    docs = await repos.list_github_repo_docs_for_user(db_session, user.id)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.id == doc_id
    assert doc.source_url == "https://github.com/octocat/widget"
    assert doc.kind == "github_repo"
    # The LLM-extracted ProjectItem is stashed on parsed_json.
    proj = doc.parsed_json
    assert proj["source"] == "github"
    assert proj["name"] == "widget"
    assert "fastapi" in proj["tech"]
    assert proj["urls"] == ["https://github.com/octocat/widget"]


async def test_ingest_repo_upserts_on_same_url(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-selecting the same repo URL refreshes the one row, no duplicate."""
    from interview_coach.db import repos
    from interview_coach.ingestion import github_repo

    user = await repos.create_user(db_session, "gh2@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_repo, "AsyncSessionLocal", factory)
    monkeypatch.setattr(
        github_repo.gh, "get_tree", lambda *a, **k: _async([_blob("src/a.py", 100)])
    )
    monkeypatch.setattr(github_repo.gh, "fetch_blob", lambda *a, **k: _async("x"))
    monkeypatch.setattr(github_repo, "embed_and_store_document", lambda _d: _async(1))
    monkeypatch.setattr(
        github_repo,
        "chat_model_structured",
        lambda *a, **k: _async(GithubProjectExtract(description="d", tech=["t"])),
    )

    url = "https://github.com/octocat/widget"
    for _ in range(2):
        await github_repo.ingest_repo(
            user_id=user.id,
            full_name="octocat/widget",
            html_url=url,
            description=None,
            default_branch="main",
            token="tok",
        )
    docs = await repos.list_github_repo_docs_for_user(db_session, user.id)
    assert len(docs) == 1


async def _async(value: Any) -> Any:
    return value


# --- fold + deselect cascade -----------------------------------------------


async def test_fold_then_deselect_drops_github_project(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.db import repos

    user = await repos.create_user(db_session, "fold@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)

    # Seed a profile with one project_doc project (must survive folding).
    await repos.upsert_profile(
        db_session,
        user_id=user.id,
        profile_json={
            "summary": "x",
            "skills": [],
            "experiences": [],
            "projects": [
                {
                    "name": "DocProj",
                    "description": "from a doc",
                    "tech": ["python"],
                    "role": None,
                    "urls": [],
                    "source": "project_doc",
                    "source_document_ids": [],
                }
            ],
            "education": [],
        },
        source_doc_ids=[],
        model_name="m",
    )

    # Two ingested github repos, each with a stashed ProjectItem.
    for repo_name, url in [
        ("alpha", "https://github.com/u/alpha"),
        ("beta", "https://github.com/u/beta"),
    ]:
        doc = await repos.upsert_github_repo_document(
            db_session, user_id=user.id, source_url=url, project_title=repo_name, raw_text="r"
        )
        await repos.set_document_parsed_json(
            db_session,
            doc.id,
            {
                "name": repo_name,
                "description": f"{repo_name} project",
                "tech": ["go"],
                "role": None,
                "urls": [url],
                "source": "github",
                "source_document_ids": [str(doc.id)],
            },
        )

    # Fold → profile has the project_doc project + both github projects.
    n = await github_ingest.fold_github_projects(user.id)
    assert n == 2
    profile = await repos.get_profile(db_session, user.id)
    await db_session.refresh(profile)
    names = {p["name"] for p in profile.profile_json["projects"]}
    assert names == {"DocProj", "alpha", "beta"}

    # Deselect beta (keep only alpha) → its doc is deleted → fold drops it.
    removed = await repos.delete_github_repo_docs_not_selected(
        db_session, user.id, ["https://github.com/u/alpha"]
    )
    assert removed == ["https://github.com/u/beta"]
    n = await github_ingest.fold_github_projects(user.id)
    assert n == 1
    profile = await repos.get_profile(db_session, user.id)
    await db_session.refresh(profile)
    names = {p["name"] for p in profile.profile_json["projects"]}
    assert names == {"DocProj", "alpha"}


# --- prep-node skip verdict ------------------------------------------------


async def test_github_discover_skips_when_no_handle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.db import repos

    user = await repos.create_user(db_session, "nohandle@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(github_ingest, "get_stream_writer", lambda: events.append)

    out = await github_ingest.node_github_discover({"user_id": str(user.id)})
    assert out["next_step"] == "prepare_mapping_suggestion"
    assert events == [{"event": "node_skipped", "node": "github", "reason": "no_repos_selected"}]


async def test_github_discover_prompts_when_handle_and_repos(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.db import repos

    user = await repos.create_user(db_session, "haz@example.com", "x")
    await repos.set_user_github_handle(db_session, user.id, "octocat")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)

    async def fake_list(*_a: Any, **_kw: Any) -> list[gh.RepoListing]:
        return [
            gh.RepoListing(
                full_name="octocat/widget",
                name="widget",
                description="d",
                language="Python",
                stars=3,
                pushed_at="2026-01-01T00:00:00Z",
                html_url="https://github.com/octocat/widget",
                default_branch="main",
                fork=False,
                archived=False,
            ),
            gh.RepoListing(
                full_name="octocat/aforkrepo",
                name="aforkrepo",
                description=None,
                language=None,
                stars=0,
                pushed_at=None,
                html_url="https://github.com/octocat/aforkrepo",
                default_branch="main",
                fork=True,  # forks hidden
                archived=False,
            ),
        ]

    monkeypatch.setattr(github_ingest.gh, "list_public_repos", fake_list)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(github_ingest, "get_stream_writer", lambda: events.append)

    out = await github_ingest.node_github_discover({"user_id": str(user.id)})
    assert out["next_step"] == "await_repo_selection"
    repos_payload = out["github_repos"]
    assert [r["full_name"] for r in repos_payload] == ["octocat/widget"]  # fork filtered
    assert any(e.get("event") == "repos_available" for e in events)


# --- focus weighting -------------------------------------------------------


def test_focus_weighting_fires_for_github_project() -> None:
    """A github ProjectItem is an ordinary focus candidate and its JD∩tech
    overlap is scored — no new weighting code, just proof it counts."""
    from interview_coach.agents.nodes.question_generator import _pick_focus_target
    from interview_coach.agents.rounds import FocusMode

    profile = {
        "experiences": [],
        "projects": [
            {
                "name": "GhRepo",
                "description": "A service.",
                "tech": ["fastapi", "pgvector"],
                "role": "solo",
                "urls": ["https://github.com/u/ghrepo"],
                "source": "github",
                "source_document_ids": ["d1"],
            },
            {
                "name": "Unrelated",
                "description": "toy",
                "tech": ["cobol"],
                "role": None,
                "urls": [],
                "source": "project_doc",
                "source_document_ids": [],
            },
        ],
    }
    job = {"must_have_skills": ["fastapi", "pgvector", "python"]}
    seen: set[str] = set()
    for seed in range(50):
        picked = _pick_focus_target(
            focus=FocusMode.experience_projects,
            profile=profile,
            job_analysis=job,
            company_snapshot={"values_and_signals": []},
            prior_focus_counts={},
            rng=random.Random(seed),
        )
        assert picked is not None
        seen.add(picked.key)
    # The JD-overlapping github project must be reachable and is favoured.
    assert "project:GhRepo" in seen


# --- follow-up: leaner ingest ordering + caps ------------------------------


def test_assemble_repo_text_orders_readme_last() -> None:
    """High-signal short sources (description, manifests, tree) precede the
    README so the extractor's input truncation can't drop them."""
    from interview_coach.ingestion.github_repo import _assemble_repo_text

    blob = _assemble_repo_text(
        full_name="u/r",
        description="a desc",
        readme="the readme body",
        manifests=[("pyproject.toml", "deps")],
        dockerfiles=[("Dockerfile", "FROM x")],
        tree="src/\n  app.py",
    )
    assert blob.index("# Description") < blob.index("# Manifest")
    assert blob.index("# Manifest") < blob.index("# README")
    assert blob.index("# Directory structure") < blob.index("# README")


def test_assemble_repo_text_truncates_to_caps() -> None:
    from interview_coach.ingestion import github_repo as gr

    # Digits (never in section headers) so the count isolates the body.
    blob = gr._assemble_repo_text(
        full_name="u/r",
        description=None,
        readme="7" * (gr.MAX_README_CHARS + 5000),
        manifests=[("pyproject.toml", "3" * (gr.MAX_MANIFEST_CHARS + 2000))],
        dockerfiles=[],
        tree=None,
    )
    assert blob.count("7") == gr.MAX_README_CHARS
    assert blob.count("3") == gr.MAX_MANIFEST_CHARS


def test_categorize_paths_caps_manifests_to_shallowest() -> None:
    from interview_coach.ingestion.github_repo import MAX_MANIFESTS, categorize_paths

    entries = [
        _blob("package.json", 100),
        _blob("a/package.json", 100),
        _blob("a/b/package.json", 100),
        _blob("pyproject.toml", 100),
        _blob("services/x/go.mod", 100),
    ]
    cats = categorize_paths(entries)
    assert len(cats["manifests"]) == MAX_MANIFESTS
    # The two root-level manifests win over the deeper ones.
    assert set(cats["manifests"]) == {"package.json", "pyproject.toml"}


# --- follow-up 2: code grounding (decoupled from extraction) ---------------


def test_select_source_files_excludes_and_caps() -> None:
    from interview_coach.ingestion import github_repo as gr

    entries = [
        _blob("README.md", 100),  # readme — not code
        _blob("pyproject.toml", 100),  # manifest — not code
        _blob("Dockerfile", 100),  # dockerfile — not code
        _blob("node_modules/x/index.js", 100),  # vendored — excluded
        _blob("tests/test_app.py", 100),  # test — excluded
        _blob("data.csv", 100),  # not a source ext
        _blob("huge.py", gr.MAX_CODE_FILE_BYTES + 1),  # oversize — skipped
        _blob("src/app.py", 200),
        _blob("src/util.py", 150),
        _blob("main.py", 120),
    ]
    picked = gr.select_source_files(entries)
    assert set(picked) == {"src/app.py", "src/util.py", "main.py"}


def test_select_source_files_respects_file_count_cap() -> None:
    from interview_coach.ingestion import github_repo as gr

    entries = [_blob(f"src/mod{i}.py", 100) for i in range(gr.MAX_CODE_FILES + 5)]
    picked = gr.select_source_files(entries)
    assert len(picked) == gr.MAX_CODE_FILES


async def test_ingest_repo_grounds_code_but_extracts_without_it(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored/embedded blob carries the code file; the LLM extraction input
    does not (decoupled — the 'no code in profile' rule holds where it matters)."""
    from interview_coach.db import models, repos
    from interview_coach.ingestion import github_repo

    user = await repos.create_user(db_session, "ground@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_repo, "AsyncSessionLocal", factory)
    monkeypatch.setattr(
        github_repo.gh,
        "get_tree",
        lambda *a, **k: _async([_blob("README.md", 50), _blob("src/app.py", 80)]),
    )

    async def fake_blob(owner, repo, path, branch, token):  # noqa: ANN001, ANN202
        return {"README.md": "readme prose", "src/app.py": "SECRET_CODE_TOKEN = 1"}[path]

    monkeypatch.setattr(github_repo.gh, "fetch_blob", fake_blob)
    monkeypatch.setattr(github_repo, "embed_and_store_document", lambda _d: _async(1))

    seen: dict[str, str] = {}

    async def fake_extract(_schema, messages, **k):  # noqa: ANN001, ANN202
        seen["input"] = messages[1].content
        return GithubProjectExtract(description="d", tech=["fastapi"])

    monkeypatch.setattr(github_repo, "chat_model_structured", fake_extract)

    doc_id = await github_repo.ingest_repo(
        user_id=user.id,
        full_name="u/r",
        html_url="https://github.com/u/r",
        description=None,
        default_branch="main",
        token="tok",
    )
    # Extraction input never saw the source file.
    assert "SECRET_CODE_TOKEN" not in seen["input"]
    # But the stored (grounding) raw_text did.
    doc = await db_session.get(models.Document, doc_id)
    assert "SECRET_CODE_TOKEN" in doc.raw_text


async def test_ingest_repo_skips_code_without_token(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.db import models, repos
    from interview_coach.ingestion import github_repo

    user = await repos.create_user(db_session, "notok@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_repo, "AsyncSessionLocal", factory)
    monkeypatch.setattr(
        github_repo.gh,
        "get_tree",
        lambda *a, **k: _async([_blob("src/app.py", 80)]),
    )

    fetched: list[str] = []

    async def fake_blob(owner, repo, path, branch, token):  # noqa: ANN001, ANN202
        fetched.append(path)
        return "code"

    monkeypatch.setattr(github_repo.gh, "fetch_blob", fake_blob)
    monkeypatch.setattr(github_repo, "embed_and_store_document", lambda _d: _async(1))
    monkeypatch.setattr(
        github_repo,
        "chat_model_structured",
        lambda *a, **k: _async(GithubProjectExtract(description="d", tech=[])),
    )

    doc_id = await github_repo.ingest_repo(
        user_id=user.id,
        full_name="u/r",
        html_url="https://github.com/u/r",
        description=None,
        default_branch="main",
        token=None,  # no token → no code fetch
    )
    assert "src/app.py" not in fetched
    doc = await db_session.get(models.Document, doc_id)
    assert "src/app.py" not in doc.raw_text


async def test_ingest_repo_caps_tech_to_10(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.db import repos
    from interview_coach.ingestion import github_repo

    user = await repos.create_user(db_session, "tech10@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_repo, "AsyncSessionLocal", factory)
    monkeypatch.setattr(github_repo.gh, "get_tree", lambda *a, **k: _async([_blob("a.py", 10)]))
    monkeypatch.setattr(github_repo.gh, "fetch_blob", lambda *a, **k: _async("x"))
    monkeypatch.setattr(github_repo, "embed_and_store_document", lambda _d: _async(1))
    monkeypatch.setattr(
        github_repo,
        "chat_model_structured",
        lambda *a, **k: _async(
            GithubProjectExtract(description="d", tech=[f"lib{i}" for i in range(15)])
        ),
    )

    doc_id = await github_repo.ingest_repo(
        user_id=user.id,
        full_name="u/r",
        html_url="https://github.com/u/r",
        description=None,
        default_branch="main",
        token="tok",
    )
    docs = await repos.list_github_repo_docs_for_user(db_session, user.id)
    proj = next(d for d in docs if d.id == doc_id).parsed_json
    assert len(proj["tech"]) == 10
    # github projects carry no role key.
    assert "role" not in proj


# --- follow-up: richer extract (key_features + architecture) ---------------


async def test_ingest_repo_roundtrips_key_features_and_architecture(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.db import repos
    from interview_coach.ingestion import github_repo

    user = await repos.create_user(db_session, "rich@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_repo, "AsyncSessionLocal", factory)
    monkeypatch.setattr(github_repo.gh, "get_tree", lambda *a, **k: _async([_blob("a.py", 10)]))
    monkeypatch.setattr(github_repo.gh, "fetch_blob", lambda *a, **k: _async("x"))
    monkeypatch.setattr(github_repo, "embed_and_store_document", lambda _d: _async(1))
    monkeypatch.setattr(
        github_repo,
        "chat_model_structured",
        lambda *a, **k: _async(
            GithubProjectExtract(
                description="d",
                tech=["fastapi"],
                key_features=["jwt auth", "sse streaming"],
                architecture="fastapi backend + react frontend",
            )
        ),
    )

    doc_id = await github_repo.ingest_repo(
        user_id=user.id,
        full_name="u/r",
        html_url="https://github.com/u/r",
        description=None,
        default_branch="main",
        token="tok",
    )
    docs = await repos.list_github_repo_docs_for_user(db_session, user.id)
    proj = next(d for d in docs if d.id == doc_id).parsed_json
    assert proj["key_features"] == ["jwt auth", "sse streaming"]
    assert proj["architecture"] == "fastapi backend + react frontend"


def test_github_intake_prompt_keys_match_schema() -> None:
    """The prompt's documented JSON shape and the extract schema can't drift —
    every key the LLM is told to emit is a real field, and vice versa."""
    import re

    from interview_coach.agents.prompts import GITHUB_INTAKE_SYSTEM

    documented = set(re.findall(r'"(\w+)":', GITHUB_INTAKE_SYSTEM))
    assert documented == set(GithubProjectExtract.model_fields)


# --- API endpoints ---------------------------------------------------------


async def test_verify_endpoint_persists_handle(
    client: AsyncClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.api.github import routes

    async def fake_verify(handle: str, _token: str | None) -> gh.GithubUser:
        return gh.GithubUser(login=handle, name="Octo Cat", avatar_url="a", public_repos=12)

    monkeypatch.setattr(routes.gh, "verify_user", fake_verify)
    r = await client.get("/github/verify", params={"handle": "octocat"}, headers=_auth(auth_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "exists": True,
        "handle": "octocat",
        "name": "Octo Cat",
        "avatar_url": "a",
        "public_repos": 12,
    }
    # Persisted: a follow-up suggest reflects the stored handle.
    s = await client.get("/github/suggest", headers=_auth(auth_token))
    assert s.json()["current"] == "octocat"


async def test_verify_endpoint_not_found(
    client: AsyncClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.api.github import routes

    async def fake_verify(_handle: str, _token: str | None) -> None:
        return None

    monkeypatch.setattr(routes.gh, "verify_user", fake_verify)
    r = await client.get("/github/verify", params={"handle": "ghost"}, headers=_auth(auth_token))
    assert r.status_code == 200
    assert r.json() == {
        "exists": False,
        "handle": None,
        "name": None,
        "avatar_url": None,
        "public_repos": None,
    }


async def test_suggest_endpoint_reads_cv_handle(client: AsyncClient, auth_token: str) -> None:
    from tests.conftest import make_pdf

    pdf = make_pdf("Contact: github.com/octocat for code")
    files = {"file": ("cv.pdf", pdf, "application/pdf")}
    up = await client.post(
        "/documents", data={"kind": "cv"}, files=files, headers=_auth(auth_token)
    )
    assert up.status_code in (200, 201), up.text
    r = await client.get("/github/suggest", headers=_auth(auth_token))
    assert r.status_code == 200
    assert r.json()["cv_suggested"] == "octocat"


# --- follow-up: post-setup repo management endpoints -----------------------


def _listing(name: str, *, fork: bool = False) -> gh.RepoListing:
    return gh.RepoListing(
        full_name=f"octocat/{name}",
        name=name,
        description=f"{name} repo",
        language="Python",
        stars=1,
        pushed_at="2026-01-01T00:00:00Z",
        html_url=f"https://github.com/octocat/{name}",
        default_branch="main",
        fork=fork,
        archived=False,
    )


async def test_repos_endpoint_lists_with_ingested_flag(
    client: AsyncClient, auth_token: str, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.api.github import routes
    from interview_coach.db import repos

    user = await repos.get_user_by_email(db_session, "alice@example.com")
    assert user is not None
    await repos.set_user_github_handle(db_session, user.id, "octocat")
    # widget is already ingested → already_ingested flips true; a fork is hidden.
    await repos.upsert_github_repo_document(
        db_session,
        user_id=user.id,
        source_url="https://github.com/octocat/widget",
        project_title="widget",
        raw_text="r",
    )

    async def fake_list(*_a: Any, **_kw: Any) -> list[gh.RepoListing]:
        return [_listing("widget"), _listing("gadget"), _listing("aforkrepo", fork=True)]

    monkeypatch.setattr(routes.gh, "list_public_repos", fake_list)

    r = await client.get("/github/repos", headers=_auth(auth_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handle"] == "octocat"
    by_name = {x["name"]: x for x in body["repos"]}
    assert set(by_name) == {"widget", "gadget"}  # fork filtered
    assert by_name["widget"]["already_ingested"] is True
    assert by_name["gadget"]["already_ingested"] is False


async def test_repos_endpoint_requires_handle(client: AsyncClient, auth_token: str) -> None:
    r = await client.get("/github/repos", headers=_auth(auth_token))
    assert r.status_code == 400
    assert r.json()["detail"] == "no_handle"


async def test_select_repos_ingests_deselects_and_refolds(
    client: AsyncClient, auth_token: str, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out-of-graph select converges on the same profile state as the prep
    graph: a deselected repo's project drops, a newly-checked one is folded in,
    non-github projects survive."""
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.api.github import routes
    from interview_coach.db import repos

    user = await repos.get_user_by_email(db_session, "alice@example.com")
    assert user is not None
    await repos.set_user_github_handle(db_session, user.id, "octocat")

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    # The route's re-fold runs in github_ingest's own session — bind it to the
    # test engine so it sees committed deletes/inserts.
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)

    def _proj(name: str, url: str, doc_id: str) -> dict[str, Any]:
        return {
            "name": name,
            "description": name,
            "tech": ["go"],
            "role": None,
            "urls": [url],
            "key_features": [],
            "architecture": None,
            "source": "github",
            "source_document_ids": [doc_id],
        }

    alpha_url = "https://github.com/octocat/alpha"
    beta_url = "https://github.com/octocat/beta"

    # Start: alpha already ingested + in the profile, alongside a non-github proj.
    alpha = await repos.upsert_github_repo_document(
        db_session, user_id=user.id, source_url=alpha_url, project_title="alpha", raw_text="r"
    )
    await repos.set_document_parsed_json(
        db_session, alpha.id, _proj("alpha", alpha_url, str(alpha.id))
    )
    await repos.upsert_profile(
        db_session,
        user_id=user.id,
        profile_json={
            "summary": "x",
            "skills": [],
            "experiences": [],
            "projects": [
                {
                    "name": "DocProj",
                    "description": "d",
                    "tech": [],
                    "role": None,
                    "urls": [],
                    "source": "project_doc",
                    "source_document_ids": [],
                },
                _proj("alpha", alpha_url, str(alpha.id)),
            ],
            "education": [],
        },
        source_doc_ids=[],
        model_name="m",
    )

    # ingest_repo is the heavy (LLM + embed) call — fake it to just store the
    # beta doc + its ProjectItem so the fold has something to pick up.
    async def fake_ingest(*, user_id: Any, full_name: str, html_url: str, **_kw: Any) -> Any:
        async with factory() as s:
            doc = await repos.upsert_github_repo_document(
                s,
                user_id=user_id,
                source_url=html_url,
                project_title=full_name.split("/")[-1],
                raw_text="r",
            )
            await repos.set_document_parsed_json(s, doc.id, _proj("beta", html_url, str(doc.id)))
        return doc.id

    monkeypatch.setattr(routes, "ingest_repo", fake_ingest)
    monkeypatch.setattr(
        routes.gh,
        "list_public_repos",
        lambda *a, **k: _async([_listing("alpha"), _listing("beta")]),
    )

    # Select only beta → deselect alpha, ingest beta, re-fold.
    r = await client.post(
        "/github/repos/select", json={"selected_urls": [beta_url]}, headers=_auth(auth_token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 1
    assert body["removed"] == 1

    profile = await repos.get_profile(db_session, user.id)
    await db_session.refresh(profile)
    names = {p["name"] for p in profile.profile_json["projects"]}
    assert names == {"DocProj", "beta"}
    # alpha's doc is gone (FK cascade wiped its chunks).
    remaining = {
        d.source_url for d in await repos.list_github_repo_docs_for_user(db_session, user.id)
    }
    assert remaining == {beta_url}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- follow-up 3: prep⊥interview barrier + repo-ingest retry ----------------


def _project_json(name: str, url: str, doc_id: uuid.UUID) -> dict[str, Any]:
    """A minimal valid ProjectItem dict for a github repo (no role)."""
    return {
        "name": name,
        "description": f"{name} project",
        "tech": ["python"],
        "role": None,
        "urls": [url],
        "source": "github",
        "source_document_ids": [str(doc_id)],
    }


async def _seed_github_doc(
    factory: async_sessionmaker, user_id: uuid.UUID, url: str, name: str, *, fully: bool
) -> None:
    """Seed a github_repo doc. ``fully`` → also stash parsed_json + a chunk so
    ``list_fully_ingested_github_urls`` counts it; otherwise leave it an orphan."""
    from interview_coach.db import models, repos

    async with factory() as s:
        doc = await repos.upsert_github_repo_document(
            s, user_id=user_id, source_url=url, project_title=name, raw_text="r"
        )
        if fully:
            await repos.set_document_parsed_json(s, doc.id, _project_json(name, url, doc.id))
            s.add(
                models.GroundingChunk(
                    user_id=user_id,
                    document_id=doc.id,
                    source_doc_kind="github_repo",
                    chunk_index=0,
                    text="c",
                    n_tokens=1,
                    embedding=[0.0] * 1024,
                    model_name="m",
                )
            )
            await s.commit()


async def _seed_empty_profile(factory: async_sessionmaker, user_id: uuid.UUID) -> None:
    from interview_coach.db import repos

    async with factory() as s:
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json={
                "summary": "",
                "skills": [],
                "experiences": [],
                "projects": [],
                "education": [],
            },
            source_doc_ids=[],
            model_name="m",
        )


def _meta(name: str, url: str) -> dict[str, Any]:
    return {
        "full_name": f"u/{name}",
        "name": name,
        "html_url": url,
        "default_branch": "main",
        "description": None,
    }


async def test_ingest_node_blocks_on_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo that fails to ingest does NOT finalize prep: the node routes back
    to await_repo_selection and re-emits the picker with a step-tagged error."""
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.db import repos
    from interview_coach.ingestion.github_repo import RepoIngestError

    user = await repos.create_user(db_session, "barrier@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(github_ingest, "get_stream_writer", lambda: events.append)
    await _seed_empty_profile(factory, user.id)

    a = "https://github.com/u/alpha"
    b = "https://github.com/u/beta"

    async def fake_ingest(*, user_id: uuid.UUID, html_url: str, **_kw: Any) -> uuid.UUID:
        if html_url == b:
            raise RepoIngestError(step="embed", code="EmbedderUnavailable", reason="read timeout")
        await _seed_github_doc(factory, user_id, html_url, "alpha", fully=True)
        return uuid.uuid4()

    monkeypatch.setattr(github_ingest, "ingest_repo", fake_ingest)

    out = await github_ingest.node_github_ingest_and_fold(
        {
            "user_id": str(user.id),
            "github_resume": {"selected_urls": [a, b]},
            "github_repos": [_meta("alpha", a), _meta("beta", b)],
        }
    )

    assert out["next_step"] == "await_repo_selection"
    assert [f["html_url"] for f in out["github_failures"]] == [b]
    assert out["github_failures"][0]["step"] == "embed"
    # Barrier: prep did NOT finalize — no fold event.
    assert not any(e.get("event") == "github_folded" for e in events)
    # The re-opened picker annotates the failed repo and keeps the selection checked.
    ev = next(e for e in events if e.get("event") == "repos_available")
    by_url = {r["html_url"]: r for r in ev["payload"]["repos"]}
    assert by_url[b]["ingest_error"]["step"] == "embed"
    assert by_url[b]["ingest_error"]["reason"] == "read timeout"
    assert by_url[a].get("already_ingested") is True


async def test_ingest_node_retry_skips_fully_ingested(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry re-ingests only the previously-broken repo; the one that already
    fully ingested is skipped (not re-embedded), then both fold in."""
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.db import repos

    user = await repos.create_user(db_session, "retry@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(github_ingest, "get_stream_writer", lambda: events.append)
    await _seed_empty_profile(factory, user.id)

    a = "https://github.com/u/alpha"
    b = "https://github.com/u/beta"
    await _seed_github_doc(factory, user.id, a, "alpha", fully=True)  # already succeeded

    called: list[str] = []

    async def fake_ingest(*, user_id: uuid.UUID, html_url: str, **_kw: Any) -> uuid.UUID:
        called.append(html_url)
        await _seed_github_doc(factory, user_id, html_url, "beta", fully=True)
        return uuid.uuid4()

    monkeypatch.setattr(github_ingest, "ingest_repo", fake_ingest)

    out = await github_ingest.node_github_ingest_and_fold(
        {
            "user_id": str(user.id),
            "github_resume": {"selected_urls": [a, b]},
            "github_repos": [_meta("alpha", a), _meta("beta", b)],
        }
    )

    assert called == [b]  # alpha skipped by the fully-ingested guard
    assert out["github_failures"] == []
    assert out["next_step"] == "prepare_mapping_suggestion"
    assert any(e.get("event") == "github_folded" for e in events)
    profile = await repos.get_profile(db_session, user.id)
    await db_session.refresh(profile)
    assert {p["name"] for p in profile.profile_json["projects"]} == {"alpha", "beta"}


async def test_ingest_node_proceed_deletes_orphan(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proceed-without: deselecting the failed (orphan) repo deletes its doc and
    folds the survivors — no ingest attempt, no failures, prep finalizes."""
    from interview_coach.agents.nodes import github_ingest
    from interview_coach.db import repos

    user = await repos.create_user(db_session, "proceed@example.com", "x")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(github_ingest, "get_stream_writer", lambda: events.append)
    await _seed_empty_profile(factory, user.id)

    a = "https://github.com/u/alpha"
    b = "https://github.com/u/beta"
    await _seed_github_doc(factory, user.id, a, "alpha", fully=True)
    await _seed_github_doc(factory, user.id, b, "beta", fully=False)  # orphan from prior fail

    async def fake_ingest(**_kw: Any) -> uuid.UUID:  # pragma: no cover
        raise AssertionError("ingest should not run on the proceed path")

    monkeypatch.setattr(github_ingest, "ingest_repo", fake_ingest)

    out = await github_ingest.node_github_ingest_and_fold(
        {
            "user_id": str(user.id),
            "github_resume": {"selected_urls": [a]},  # beta unchecked
            "github_repos": [_meta("alpha", a), _meta("beta", b)],
        }
    )

    assert out["github_failures"] == []
    assert out["next_step"] == "prepare_mapping_suggestion"
    assert any(e.get("event") == "github_folded" for e in events)
    async with factory() as s:
        remaining = {d.source_url for d in await repos.list_github_repo_docs_for_user(s, user.id)}
    assert remaining == {a}  # the orphan was deleted
    profile = await repos.get_profile(db_session, user.id)
    await db_session.refresh(profile)
    assert {p["name"] for p in profile.profile_json["projects"]} == {"alpha"}
