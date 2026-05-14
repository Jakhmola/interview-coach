"""Self-correction retry around `with_structured_output` call sites.

On a first-attempt `ValidationError` we resend the messages with a
follow-up `HumanMessage` that explains the failure and ask the model to
return valid JSON. Telemetry records two rows; the second carries
`retry_count=1`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.db import models
from interview_coach.db.models import LLMCall
from interview_coach.llm import client as llm_client
from interview_coach.llm import telemetry as telemetry_mod
from interview_coach.llm.client import chat_model_structured


class Toy(BaseModel):
    name: str
    n: int


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


class _FakeStructuredLLM:
    """Sequenced fake: first call returns/raises whatever's in `responses[0]`,
    second call returns `responses[1]`, etc. A `BaseException` instance is
    raised; anything else is returned.
    """

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list = []

    async def ainvoke(self, messages):  # noqa: ANN001
        self.calls.append(list(messages))
        head = self.responses.pop(0)
        if isinstance(head, BaseException):
            raise head
        return head


async def test_structured_retry_fires_once_on_validation_error(telemetry_db) -> None:
    from pydantic import ValidationError

    try:
        Toy.model_validate({"name": "x"})  # missing `n`
    except ValidationError as ve:
        validation_err: ValidationError = ve
    valid = Toy(name="x", n=5)

    fake = _FakeStructuredLLM([validation_err, valid])

    class _Base:
        def with_structured_output(self, schema, method=None):  # noqa: ANN001, ARG002
            return fake

    with patch.object(llm_client, "chat_model", return_value=_Base()):
        result = await chat_model_structured(
            Toy,
            [SystemMessage(content="sys"), HumanMessage(content="user")],
            temperature=0.0,
        )

    assert result == valid
    # Retry message appended on second call.
    assert len(fake.calls) == 2
    assert len(fake.calls[1]) == 3
    retry_msg = fake.calls[1][-1]
    assert isinstance(retry_msg, HumanMessage)
    assert "failed JSON schema validation" in retry_msg.content

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall).order_by(LLMCall.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].success is False
    assert rows[0].retry_count == 0
    assert rows[0].error_class == "ValidationError"
    assert rows[1].success is True
    assert rows[1].retry_count == 1


async def test_structured_no_retry_on_success(telemetry_db) -> None:
    valid = Toy(name="x", n=1)
    fake = _FakeStructuredLLM([valid])

    class _Base:
        def with_structured_output(self, schema, method=None):  # noqa: ANN001, ARG002
            return fake

    with patch.object(llm_client, "chat_model", return_value=_Base()):
        result = await chat_model_structured(
            Toy,
            [SystemMessage(content="sys"), HumanMessage(content="user")],
            temperature=0.0,
        )
    assert result == valid
    assert len(fake.calls) == 1

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].success is True
    assert rows[0].retry_count == 0


async def test_structured_retry_propagates_second_failure(telemetry_db) -> None:
    from pydantic import ValidationError

    try:
        Toy.model_validate({})
    except ValidationError as ve:
        err1: ValidationError = ve
    try:
        Toy.model_validate({"name": "x"})
    except ValidationError as ve:
        err2: ValidationError = ve

    fake = _FakeStructuredLLM([err1, err2])

    class _Base:
        def with_structured_output(self, schema, method=None):  # noqa: ANN001, ARG002
            return fake

    with patch.object(llm_client, "chat_model", return_value=_Base()):
        with pytest.raises(ValidationError):
            await chat_model_structured(
                Toy,
                [SystemMessage(content="sys"), HumanMessage(content="user")],
                temperature=0.0,
            )

    async with telemetry_db() as s:
        rows = (await s.execute(select(LLMCall).order_by(LLMCall.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].retry_count == 0 and rows[0].success is False
    assert rows[1].retry_count == 1 and rows[1].success is False
