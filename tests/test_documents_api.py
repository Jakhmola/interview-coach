from httpx import AsyncClient

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
        assert "parsed_json" not in d
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
