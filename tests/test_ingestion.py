import pytest

from interview_coach.ingestion import extract_docx_text, extract_pdf_text, extract_text
from interview_coach.ingestion.errors import ExtractionFailed, UnsupportedFormat
from tests.conftest import make_docx, make_pdf


def test_extract_pdf_text_roundtrip() -> None:
    data = make_pdf("Hello from a PDF.")
    text = extract_pdf_text(data)
    assert "Hello from a PDF." in text


def test_extract_docx_text_roundtrip() -> None:
    data = make_docx("Hello from a DOCX.\nSecond paragraph.")
    text = extract_docx_text(data)
    assert "Hello from a DOCX." in text
    assert "Second paragraph." in text


def test_extract_text_dispatch_pdf() -> None:
    data = make_pdf("dispatch test")
    assert "dispatch test" in extract_text("cv.pdf", "application/pdf", data)


def test_extract_text_dispatch_docx() -> None:
    data = make_docx("dispatch test")
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "dispatch test" in extract_text("project.docx", ct, data)


def test_extract_text_unsupported_format() -> None:
    with pytest.raises(UnsupportedFormat):
        extract_text("notes.txt", "text/plain", b"not a document")


def test_extract_pdf_corrupt_bytes() -> None:
    with pytest.raises(ExtractionFailed):
        extract_pdf_text(b"this is not a PDF")


def test_extract_docx_corrupt_bytes() -> None:
    with pytest.raises(ExtractionFailed):
        extract_docx_text(b"this is not a DOCX")
