import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl, computed_field, model_validator


class JobSource(StrEnum):
    pasted = "pasted"
    url = "url"


class JobCreateRequest(BaseModel):
    """Exactly one of `text` or `url` must be set."""

    text: str | None = None
    url: HttpUrl | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "JobCreateRequest":
        if (self.text is None) == (self.url is None):
            raise ValueError("Provide exactly one of `text` or `url`")
        return self


class _JobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    source: JobSource
    source_url: str | None = None
    created_at: datetime


class JobListItem(_JobBase):
    char_count: int
    preview: str


class JobOut(_JobBase):
    raw_text: str
    parsed_json: dict[str, Any] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        return len(self.raw_text)
