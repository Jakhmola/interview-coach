import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class DocumentKind(StrEnum):
    cv = "cv"
    project_doc = "project_doc"


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


class DocumentOut(_DocumentBase):
    raw_text: str
    parsed_json: dict[str, Any] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        return len(self.raw_text)
