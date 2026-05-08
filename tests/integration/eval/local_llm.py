"""Bridge `deepeval` to the project's local LLM (llama.cpp via OpenAI-compat).

deepeval's `GEval` defaults to OpenAI's `gpt-4o-mini`, which would break two
project invariants: cloud dependency and reading `OPENAI_API_KEY`. Subclassing
`DeepEvalBaseLLM` and reusing the same `chat_model()` factory the agents use
keeps every LLM call on the local server.

Phase 12a uses this for `profile_groundedness` and `jd_relevance`. Future eval
phases (12b) reuse the same bridge.
"""

from __future__ import annotations

import os

from deepeval.models import DeepEvalBaseLLM
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from interview_coach.config import settings
from interview_coach.llm.client import chat_model


class IntegrationOnlyError(RuntimeError):
    """Raised when the local LLM bridge is used without `INTEGRATION=1`."""


def _require_integration() -> None:
    if os.environ.get("INTEGRATION") != "1":
        raise IntegrationOnlyError(
            "LocalChatLLM requires INTEGRATION=1 — refusing to hit the LLM "
            "during a default `make test` run."
        )


class LocalChatLLM(DeepEvalBaseLLM):
    """`DeepEvalBaseLLM` impl that delegates to `chat_model()`.

    Temperature is pinned to 0.0 — G-Eval scoring should be as deterministic
    as the local model can make it; we are NOT measuring the evaluator's
    creativity.
    """

    def __init__(self, temperature: float = 0.0) -> None:
        # Set _temperature BEFORE super().__init__(), because
        # DeepEvalBaseLLM.__init__ immediately calls self.load_model().
        self._temperature = temperature
        super().__init__()

    def get_model_name(self) -> str:
        return settings.model_name

    def load_model(self) -> BaseChatModel:
        return chat_model(temperature=self._temperature)

    def generate(self, prompt: str) -> str:
        _require_integration()
        model = self.load_model()
        result = model.invoke([HumanMessage(content=prompt)])
        return _content_as_str(result.content)

    async def a_generate(self, prompt: str) -> str:
        _require_integration()
        model = self.load_model()
        result = await model.ainvoke([HumanMessage(content=prompt)])
        return _content_as_str(result.content)


def _content_as_str(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and "text" in p:
                parts.append(str(p["text"]))
        return "".join(parts)
    return str(content)
