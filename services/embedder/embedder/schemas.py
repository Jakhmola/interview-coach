"""Wire schemas for the embedder service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Task = Literal["retrieval.passage", "retrieval.query"]


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    task: Task


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    dim: int


class ModelInfo(BaseModel):
    name: str
    dim: int


class HealthResponse(BaseModel):
    ok: bool
    model_loaded: bool
