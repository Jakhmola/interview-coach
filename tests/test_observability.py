"""Unit tests for the Langfuse observability shim.

These tests never make network calls. They verify the on/off behavior
of the env-driven gate and that the public surface is safe to call
when disabled.
"""

from __future__ import annotations

import pytest

from interview_coach.observability import langfuse as obs


def test_disabled_when_env_unset() -> None:
    assert obs.langfuse_enabled() is False
    assert obs.langfuse_callback() is None


def test_disabled_when_only_one_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    # Secret intentionally missing.
    assert obs.langfuse_enabled() is False
    assert obs.langfuse_callback() is None


def test_enabled_when_both_keys_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both keys are set, the gate flips and the SDK is constructed.

    We don't assert the client *succeeded* (it might still fail the
    real auth handshake), only that the public function returns a
    handler instance instead of None.
    """
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    assert obs.langfuse_enabled() is True
    cb = obs.langfuse_callback()
    assert cb is not None
    # The handler is a LangChain BaseCallbackHandler subclass.
    from langchain_core.callbacks import BaseCallbackHandler

    assert isinstance(cb, BaseCallbackHandler)


def test_trace_attributes_is_noop_when_disabled() -> None:
    """The context manager must never raise when Langfuse is off."""
    with obs.trace_attributes(
        user_id="u1",
        session_id="s1",
        metadata={"k": "v"},
        tags=["t"],
    ):
        # Body runs unchanged.
        assert True


async def test_flush_is_safe_when_disabled() -> None:
    # No client was initialized; flush should return cleanly.
    await obs.flush_langfuse()
