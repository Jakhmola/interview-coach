"""Telemetry recording for LLM calls.

The async path needs a real DB session, so each test installs a transient
in-memory SQLite engine and points `AsyncSessionLocal` at it for the test's
duration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.db import models
from interview_coach.db.models import LLMCall
from interview_coach.llm import client as llm_client
from interview_coach.llm import telemetry as telemetry_mod
from interview_coach.llm.client import (
    ainvoke_with_telemetry,
    astream_with_telemetry,
    stream_text,
)
from interview_coach.llm.telemetry import (
    current_node_name,
    extract_token_usage,
    set_node_context,
)


@pytest.fixture
async def telemetry_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(telemetry_mod, "AsyncSessionLocal", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _async_iter(items: list) -> AsyncIterator:
    for it in items:
        yield it


def test_set_node_context_propagates_to_current_node_name() -> None:
    assert current_node_name() is None
    with set_node_context("profile_builder"):
        assert current_node_name() == "profile_builder"
    assert current_node_name() is None


def test_extract_token_usage_handles_langchain_shape() -> None:
    pt, ct = extract_token_usage({"input_tokens": 12, "output_tokens": 7})
    assert pt == 12 and ct == 7


def test_extract_token_usage_handles_openai_shape() -> None:
    pt, ct = extract_token_usage({"prompt_tokens": 3, "completion_tokens": 5})
    assert pt == 3 and ct == 5


def test_extract_token_usage_handles_missing() -> None:
    assert extract_token_usage(None) == (None, None)
    assert extract_token_usage({}) == (None, None)


async def test_stream_text_records_one_call(telemetry_db) -> None:
    chunks = [
        AIMessageChunk(content="hi"),
        AIMessageChunk(
            content=" world",
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        ),
    ]

    fake_llm = AsyncMock()
    fake_llm.astream = lambda _msgs: _async_iter(chunks)

    with patch.object(llm_client, "chat_model", return_value=fake_llm):
        with set_node_context("profile_builder"):
            tokens = [t async for t in stream_text([HumanMessage("hi")], temperature=0.0)]
    assert tokens == ["hi", " world"]

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.node_name == "profile_builder"
    assert row.success is True
    assert row.prompt_tokens == 4
    assert row.completion_tokens == 2
    assert row.retry_count == 0


async def test_ainvoke_with_telemetry_records_one_call(telemetry_db) -> None:
    fake_llm = AsyncMock()

    class _Result:
        usage_metadata = {"input_tokens": 11, "output_tokens": 9}

    fake_llm.ainvoke.return_value = _Result()

    with set_node_context("job_analyzer"):
        result = await ainvoke_with_telemetry(fake_llm, [HumanMessage("hi")])
    assert isinstance(result, _Result)

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].node_name == "job_analyzer"
    assert rows[0].prompt_tokens == 11
    assert rows[0].completion_tokens == 9
    assert rows[0].success is True


async def test_ainvoke_with_telemetry_records_failure(telemetry_db) -> None:
    fake_llm = AsyncMock()
    fake_llm.ainvoke.side_effect = RuntimeError("boom")

    with set_node_context("profile_builder"):
        with pytest.raises(RuntimeError, match="boom"):
            await ainvoke_with_telemetry(fake_llm, [HumanMessage("hi")])

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_class == "RuntimeError"


async def test_astream_with_telemetry_records_one_call(telemetry_db) -> None:
    chunks = [
        AIMessageChunk(content="a"),
        AIMessageChunk(
            content="b", usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
        ),
    ]
    fake_llm = AsyncMock()
    fake_llm.astream = lambda _msgs: _async_iter(chunks)

    with set_node_context("question_generator"):
        out = [c async for c in astream_with_telemetry(fake_llm, [HumanMessage("hi")])]
    assert len(out) == 2

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].node_name == "question_generator"
    assert rows[0].prompt_tokens == 1
    assert rows[0].completion_tokens == 2


async def test_record_call_swallows_db_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry write failures must not block agent calls."""

    class _BrokenFactory:
        def __call__(self):  # noqa: D401, ANN001
            raise RuntimeError("db down")

    monkeypatch.setattr(telemetry_mod, "AsyncSessionLocal", _BrokenFactory())
    # Should not raise.
    await telemetry_mod.record_call(model="qwen3-8b", latency_ms=42, success=True, node_name="x")
