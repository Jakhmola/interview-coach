"""Unit tests for the deepeval ↔ local-LLM bridge.

Two invariants matter:
1. `get_model_name()` reflects `settings.model_name`.
2. `generate()`/`a_generate()` refuse to run unless `INTEGRATION=1` — so a
   default `make test` cannot accidentally hit a real LLM.
"""

from __future__ import annotations

import os

import pytest

from interview_coach.config import settings
from tests.integration.eval.local_llm import IntegrationOnlyError, LocalChatLLM


def test_model_name_matches_settings() -> None:
    llm = LocalChatLLM()
    assert llm.get_model_name() == settings.model_name


def test_generate_refuses_without_integration_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTEGRATION", raising=False)
    assert os.environ.get("INTEGRATION") != "1"
    llm = LocalChatLLM()
    with pytest.raises(IntegrationOnlyError):
        llm.generate("hi")


async def test_a_generate_refuses_without_integration_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTEGRATION", raising=False)
    llm = LocalChatLLM()
    with pytest.raises(IntegrationOnlyError):
        await llm.a_generate("hi")
