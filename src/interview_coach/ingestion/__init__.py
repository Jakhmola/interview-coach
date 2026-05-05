from interview_coach.ingestion.docx import extract_docx_text
from interview_coach.ingestion.errors import ExtractionFailed, UnsupportedFormat
from interview_coach.ingestion.pdf import extract_pdf_text

PDF_TYPES = {"application/pdf"}
DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def extract_text(filename: str, content_type: str, data: bytes) -> str:
    """Dispatch by content type (with filename suffix as fallback)."""
    ct = (content_type or "").lower()
    name = (filename or "").lower()

    if ct in PDF_TYPES or name.endswith(".pdf"):
        return extract_pdf_text(data)
    if ct in DOCX_TYPES or name.endswith(".docx"):
        return extract_docx_text(data)

    raise UnsupportedFormat(f"Unsupported document type: {content_type or '(unknown)'}")


__all__ = [
    "extract_text",
    "extract_pdf_text",
    "extract_docx_text",
    "ExtractionFailed",
    "UnsupportedFormat",
    "PDF_TYPES",
    "DOCX_TYPES",
]
