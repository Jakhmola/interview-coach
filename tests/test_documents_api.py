import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from interview_coach.db import repos

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_upload_requires_auth(client: AsyncClient, sample_pdf: bytes) -> None:
    r = await client.post(
        "/documents",
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    assert r.status_code == 401


async def test_upload_cv_pdf_happy_path(
    client: AsyncClient, auth_token: str, sample_pdf: bytes
) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("alice_cv.pdf", sample_pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "cv"
    assert body["filename"] == "alice_cv.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["byte_size"] == len(sample_pdf)
    assert "Alice Engineer" in body["raw_text"]
    assert body["char_count"] == len(body["raw_text"])
    assert body["parsed_json"] is None


async def test_upload_cv_replaces_existing(
    client: AsyncClient, auth_token: str, sample_pdf: bytes
) -> None:
    """Phase 22 contract: same-bytes re-uploads dedup to HTTP 200 with the
    same row; different-bytes uploads replace (still one CV row total)."""
    from tests.conftest import make_pdf

    r1 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("v1.pdf", sample_pdf, "application/pdf")},
    )
    assert r1.status_code == 201, r1.text
    cv1_id = r1.json()["id"]

    # Same bytes → dedup → 200, same id, filename NOT updated.
    r2 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("v2.pdf", sample_pdf, "application/pdf")},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == cv1_id

    # Different bytes → replace → 201, new id, old row gone.
    r3 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("v3.pdf", make_pdf("entirely different body"), "application/pdf")},
    )
    assert r3.status_code == 201, r3.text
    assert r3.json()["id"] != cv1_id

    r = await client.get("/documents", headers=_auth(auth_token))
    assert r.status_code == 200
    cv_docs = [d for d in r.json() if d["kind"] == "cv"]
    assert len(cv_docs) == 1
    assert cv_docs[0]["filename"] == "v3.pdf"


async def test_upload_multiple_project_docs_allowed(client: AsyncClient, auth_token: str) -> None:
    """Two project_docs with distinct content land as two rows. Phase 22
    dedups by ``sha256(text)`` so the bodies must differ — that's the
    intended ergonomics: ten identical re-uploads stay one row."""
    from tests.conftest import make_docx

    for name, body in [
        ("proj_a.docx", make_docx("Project A: built an indexer.")),
        ("proj_b.docx", make_docx("Project B: shipped a CRDT sync engine.")),
    ]:
        r = await client.post(
            "/documents",
            headers=_auth(auth_token),
            data={"kind": "project_doc"},
            files={"file": (name, body, DOCX_CT)},
        )
        assert r.status_code == 201, r.text

    r = await client.get("/documents", headers=_auth(auth_token))
    assert r.status_code == 200
    project_docs = [d for d in r.json() if d["kind"] == "project_doc"]
    assert len(project_docs) == 2


async def test_list_documents_survives_github_repo_doc(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    """Phase 32 regression: a github_repo doc must not 500 GET /documents.

    github_repo is a valid DB DocumentKind but was missing from the API
    StrEnum, so ``DocumentKind(d.kind)`` raised once a repo was ingested —
    crashing the Manage page and bouncing the user back to setup.
    """
    user = await repos.get_user_by_email(db_session, "alice@example.com")
    assert user is not None
    await repos.upsert_github_repo_document(
        db_session,
        user_id=user.id,
        source_url="https://github.com/alice/widget",
        project_title="widget",
        raw_text="# Repository: alice/widget\n\n# README\nA thing.",
    )

    r = await client.get("/documents", headers=_auth(auth_token))
    assert r.status_code == 200, r.text
    kinds = {d["kind"] for d in r.json()}
    assert "github_repo" in kinds


def _github_project(name: str, url: str, doc_id: str) -> dict:
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


async def test_delete_github_repo_refolds_profile_no_orphan(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 32 follow-up (C1): deleting a github_repo doc on Manage must drop
    its folded ``source='github'`` project from the Profile, while sibling
    github + non-github projects survive — no orphaned enrichment."""
    from interview_coach.agents.nodes import github_ingest

    user = await repos.get_user_by_email(db_session, "alice@example.com")
    assert user is not None
    # The route's re-fold opens its own AsyncSessionLocal session; bind it to
    # the test engine so it sees the committed delete (same pattern as the
    # phase32 fold test).
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)

    doc_ids: dict[str, str] = {}
    for name, url in [
        ("alpha", "https://github.com/u/alpha"),
        ("beta", "https://github.com/u/beta"),
    ]:
        doc = await repos.upsert_github_repo_document(
            db_session, user_id=user.id, source_url=url, project_title=name, raw_text="r"
        )
        doc_ids[name] = str(doc.id)
        await repos.set_document_parsed_json(
            db_session, doc.id, _github_project(name, url, str(doc.id))
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
                _github_project("alpha", "https://github.com/u/alpha", doc_ids["alpha"]),
                _github_project("beta", "https://github.com/u/beta", doc_ids["beta"]),
            ],
            "education": [],
        },
        source_doc_ids=[],
        model_name="m",
    )

    r = await client.delete(f"/documents/{doc_ids['beta']}", headers=_auth(auth_token))
    assert r.status_code == 204, r.text

    profile = await repos.get_profile(db_session, user.id)
    await db_session.refresh(profile)
    names = {p["name"] for p in profile.profile_json["projects"]}
    assert names == {"DocProj", "alpha"}


async def test_list_documents_returns_parsed_json_for_github(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    """Phase 32 follow-up (C2): the Manage GitHub-repo section reads tech +
    key_features off ``parsed_json`` in the list payload (no per-row fetch)."""
    user = await repos.get_user_by_email(db_session, "alice@example.com")
    assert user is not None
    doc = await repos.upsert_github_repo_document(
        db_session,
        user_id=user.id,
        source_url="https://github.com/u/widget",
        project_title="widget",
        raw_text="r",
    )
    await repos.set_document_parsed_json(
        db_session,
        doc.id,
        {
            "name": "widget",
            "description": "d",
            "tech": ["fastapi"],
            "role": None,
            "urls": ["https://github.com/u/widget"],
            "key_features": ["auth"],
            "architecture": "fastapi",
            "source": "github",
            "source_document_ids": [str(doc.id)],
        },
    )

    r = await client.get("/documents", headers=_auth(auth_token))
    assert r.status_code == 200, r.text
    gh_row = next(d for d in r.json() if d["kind"] == "github_repo")
    assert gh_row["parsed_json"]["tech"] == ["fastapi"]
    assert gh_row["parsed_json"]["key_features"] == ["auth"]


async def test_list_payload_excludes_raw_text(
    client: AsyncClient, auth_token: str, sample_pdf: bytes
) -> None:
    await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    r = await client.get("/documents", headers=_auth(auth_token))
    assert r.status_code == 200
    for d in r.json():
        assert "raw_text" not in d
        # Phase 32 follow-up: parsed_json IS now in the list payload (the Manage
        # GitHub-repo section reads tech/features off it); null for a cv row.
        assert "parsed_json" in d
        assert d["parsed_json"] is None
        assert "char_count" in d


async def test_get_one_includes_raw_text(
    client: AsyncClient, auth_token: str, sample_pdf: bytes
) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    doc_id = r.json()["id"]
    r = await client.get(f"/documents/{doc_id}", headers=_auth(auth_token))
    assert r.status_code == 200
    assert "Alice Engineer" in r.json()["raw_text"]


async def test_delete_document(client: AsyncClient, auth_token: str, sample_pdf: bytes) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    doc_id = r.json()["id"]
    r = await client.delete(f"/documents/{doc_id}", headers=_auth(auth_token))
    assert r.status_code == 204
    r = await client.get(f"/documents/{doc_id}", headers=_auth(auth_token))
    assert r.status_code == 404


async def test_isolation_between_users(
    client: AsyncClient,
    auth_token: str,
    second_user_token: str,
    sample_pdf: bytes,
) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    a_doc_id = r.json()["id"]

    r = await client.get("/documents", headers=_auth(second_user_token))
    assert r.status_code == 200
    assert r.json() == []

    r = await client.get(f"/documents/{a_doc_id}", headers=_auth(second_user_token))
    assert r.status_code == 404

    r = await client.delete(f"/documents/{a_doc_id}", headers=_auth(second_user_token))
    assert r.status_code == 404


async def test_upload_unsupported_format(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


async def test_upload_corrupt_pdf(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", b"not a pdf", "application/pdf")},
    )
    assert r.status_code == 400


async def test_upload_empty_file(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 400


async def test_upload_too_large(client: AsyncClient, auth_token: str) -> None:
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", big, "application/pdf")},
    )
    assert r.status_code == 413


async def test_upload_invalid_kind(client: AsyncClient, auth_token: str, sample_pdf: bytes) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cover_letter"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    assert r.status_code == 422
