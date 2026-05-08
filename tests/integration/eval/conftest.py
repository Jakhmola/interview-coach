"""Shared fixtures for the question-quality eval harness."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _discover_fixture_slugs() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(
        p.name for p in FIXTURES_DIR.iterdir() if p.is_dir() and (p / "profile.json").exists()
    )


@pytest.fixture(scope="session")
def fixture_slugs() -> list[str]:
    return _discover_fixture_slugs()


def load_fixture(slug: str) -> dict[str, Any]:
    """Read a fixture's four files into a dict the harness can hand to the node."""
    base = FIXTURES_DIR / slug
    return {
        "slug": slug,
        "profile": json.loads((base / "profile.json").read_text()),
        "job": json.loads((base / "job.json").read_text()),
        "company": json.loads((base / "company.json").read_text()),
        "cv_text": (base / "cv.txt").read_text(),
    }


@pytest.fixture
def fixture_data(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Indirect fixture: tests parametrize on fixture slug, this loads it."""
    slug = request.param
    return load_fixture(slug)


@pytest.fixture(autouse=True)
def _guard_no_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt-and-braces: scrub OPENAI_API_KEY for the eval harness so a leaked
    deepeval default cannot reach OpenAI. The local LLM endpoint is configured
    via `settings.llm_base_url` separately.
    """
    if os.environ.get("INTEGRATION") == "1":
        # Even under INTEGRATION, we don't want a stray OpenAI call.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
