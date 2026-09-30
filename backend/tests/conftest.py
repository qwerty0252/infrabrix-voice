from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from typing import Any

# Configure before the app (and its engine) is imported.
_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["INFRABRIX_DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB}"
os.environ["INFRABRIX_DEMO_ROLLBACK_SECONDS"] = "0"
os.environ["LLM_PROVIDER"] = "scripted"
os.environ["ASSEMBLYAI_API_KEY"] = "server-only-test-key"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.providers.voice import get_voice_provider  # noqa: E402
from app.providers.voice.base import VoiceToken  # noqa: E402
from app.ratelimit import limiter  # noqa: E402


class FakeVoiceProvider:
    def __init__(self) -> None:
        self.minted = 0

    async def mint_token(self) -> VoiceToken:
        self.minted += 1
        return VoiceToken(
            token=f"temp-token-{self.minted}",
            websocket_url="wss://voice.example/ws",
            expires_in_seconds=300,
            max_session_duration_seconds=1800,
        )


@pytest.fixture
def voice_provider() -> FakeVoiceProvider:
    return FakeVoiceProvider()


@pytest.fixture
async def client(voice_provider: FakeVoiceProvider) -> AsyncIterator[httpx.AsyncClient]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    limiter.reset()
    app.dependency_overrides[get_voice_provider] = lambda: voice_provider
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class Visitor:
    """A signed-in demo user with their own project and conversation."""

    def __init__(self, client: httpx.AsyncClient, data: dict[str, Any]) -> None:
        self.client = client
        self.token = data["token"]
        self.project_id = data["project_id"]
        self.conversation_id = data["conversation_id"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def post(self, path: str, json: Any = None) -> httpx.Response:
        return await self.client.post(path, json=json, headers=self.headers)

    async def get(self, path: str) -> httpx.Response:
        return await self.client.get(path, headers=self.headers)

    @property
    def voice(self) -> str:
        return f"/projects/{self.project_id}/voice-sessions"

    async def start_voice(self, *, connect: bool = True) -> str:
        r = await self.post(self.voice, {"conversation_id": self.conversation_id})
        assert r.status_code == 200, r.text
        sid = r.json()["session"]["id"]
        if connect:
            r = await self.post(f"{self.voice}/{sid}/connected", {"provider_session_id": "p-1"})
            assert r.status_code == 200, r.text
        return sid

    async def say(self, sid: str, message: str, call_id: str) -> httpx.Response:
        return await self.post(
            f"{self.voice}/{sid}/turns", {"call_id": call_id, "message": message}
        )

    async def cloud(self) -> dict[str, Any]:
        r = await self.get(f"/projects/{self.project_id}/cloud")
        assert r.status_code == 200, r.text
        return r.json()


async def sign_in(client: httpx.AsyncClient, name: str = "Demo") -> Visitor:
    r = await client.post("/auth/demo", json={"display_name": name})
    assert r.status_code == 200, r.text
    return Visitor(client, r.json())


@pytest.fixture
async def alice(client: httpx.AsyncClient) -> Visitor:
    return await sign_in(client, "Alice")


@pytest.fixture
async def bob(client: httpx.AsyncClient) -> Visitor:
    return await sign_in(client, "Bob")
