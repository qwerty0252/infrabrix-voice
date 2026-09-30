"""Public-demo abuse limits."""

from __future__ import annotations

import httpx
import pytest

from app.settings import get_settings
from tests.conftest import sign_in


@pytest.fixture
def tight_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "limit_signins_per_ip_per_hour", 2)
    monkeypatch.setattr(settings, "limit_voice_sessions_per_user_per_hour", 2)
    monkeypatch.setattr(settings, "limit_voice_sessions_per_day", 3)


async def test_signins_are_limited_per_client(
    client: httpx.AsyncClient, tight_limits: None
) -> None:
    await sign_in(client)
    await sign_in(client)
    r = await client.post("/auth/demo", json={})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


async def test_voice_sessions_are_limited_per_user_and_per_day(
    client: httpx.AsyncClient, tight_limits: None
) -> None:
    alice = await sign_in(client, "Alice")
    bob = await sign_in(client, "Bob")
    await alice.start_voice(connect=False)
    await alice.start_voice(connect=False)
    r = await alice.post(alice.voice, {"conversation_id": alice.conversation_id})
    assert r.status_code == 429
    # Bob gets one before the global daily cap (3) is reached.
    await bob.start_voice(connect=False)
    r = await bob.post(bob.voice, {"conversation_id": bob.conversation_id})
    assert r.status_code == 429
    assert "today" in r.json()["error"]["message"]
