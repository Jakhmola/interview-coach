import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PyPdfError

from interview_coach.ingestion.errors import ExtractionFailed

_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n\s*\n+")


def _normalize(text: str) -> str:
    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        chunks: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                chunks.append(page_text)
    except (PdfReadError, PyPdfError, ValueError, KeyError) as e:
        raise ExtractionFailed(f"Failed to read PDF: {e}") from e

    return _normalize("\n\n".join(chunks))
