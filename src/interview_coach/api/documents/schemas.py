import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class DocumentKind(StrEnum):
    cv = "cv"
    project_doc = "project_doc"


# Derived from (chunk count, doc age):
#   ready   → at least one chunk present
#   pending → zero chunks, doc younger than EMBED_PENDING_GRACE_S
#   failed  → zero chunks, doc older than EMBED_PENDING_GRACE_S
#   n_a     → kind never embeds inline (project_doc waits for mapping confirm)
EmbeddingStatus = Literal["ready", "pending", "failed", "n_a"]


class _DocumentBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    kind: DocumentKind
    filename: str
    content_type: str
    byte_size: int
    created_at: datetime


class DocumentListItem(_DocumentBase):
    char_count: int = Field(description="Length of extracted text")
    project_title: str | None = None
    embedding_status: EmbeddingStatus = "pending"


class DocumentOut(_DocumentBase):
    raw_text: str
    parsed_json: dict[str, Any] | None = None
    project_title: str | None = None
    embedding_status: EmbeddingStatus = "pending"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        return len(self.raw_text)


# --- Phase 22: remap-confirm request body --------------------------------


class RemapMappingRow(BaseModel):
    """One mapping decision row submitted on remap-confirm. Mirrors the
    persisted ``document_mappings`` schema minus ``extracted_json``,
    which the route stamps with the doc-level extracted payload."""

    mapping_kind: Literal["highlight", "experience", "project"]
    experience_idx: int | None = None
    highlight_idx: int | None = None
    project_idx: int | None = None


class RemapConfirmRequest(BaseModel):
    """Body for ``POST /documents/{id}/remap/confirm``.

    On ``action='apply'`` the route runs ``apply_mapping`` with ``rows``,
    ``title``, and ``extracted``. On ``action='skip'`` the doc stays
    unmapped (no DB writes) — same effect as the user clicking Skip in
    the prep-graph mapping panel.
    """

    action: Literal["apply", "skip"]
    rows: list[RemapMappingRow] = Field(default_factory=list)
    title: str | None = None
    extracted: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _apply_requires_payload(self) -> "RemapConfirmRequest":
        if self.action == "apply":
            if not self.rows:
                raise ValueError("action='apply' requires non-empty rows")
            if not self.title:
                raise ValueError("action='apply' requires title")
            if self.extracted is None:
                raise ValueError("action='apply' requires extracted")
        return self
