import httpx
import pytest

from interview_coach import __version__
from interview_coach.api.main import app


@pytest.mark.asyncio
async def test_healthz() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}
