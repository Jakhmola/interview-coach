import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


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
