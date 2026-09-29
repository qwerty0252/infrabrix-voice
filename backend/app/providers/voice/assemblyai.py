"""AssemblyAI Voice Agent temporary-token provider."""

from __future__ import annotations

import httpx

from app.errors import AppError
from app.providers.voice.base import VoiceToken
from app.settings import Settings

_TOKEN_URL = "https://agents.assemblyai.com/v1/token"
WEBSOCKET_URL = "wss://agents.assemblyai.com/v1/ws"


class VoiceNotConfiguredError(AppError):
    code = "voice_not_configured"
    message = "Voice Mode is not configured. Set ASSEMBLYAI_API_KEY on the backend."
    status_code = 503


class AssemblyAIVoiceProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def mint_token(self) -> VoiceToken:
        key = self._settings.assemblyai_api_key
        if key is None or not key.get_secret_value().strip():
            raise VoiceNotConfiguredError()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    _TOKEN_URL,
                    params={
                        "expires_in_seconds": self._settings.voice_token_ttl_seconds,
                        "max_session_duration_seconds": (
                            self._settings.voice_session_max_duration_seconds
                        ),
                    },
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                )
                response.raise_for_status()
                token = response.json().get("token")
        except httpx.HTTPError as exc:
            raise AppError(
                "Voice Mode is temporarily unavailable.",
                code="voice_provider_unavailable",
                status_code=502,
            ) from exc
        if not isinstance(token, str) or not token:
            raise AppError(
                "Voice Mode is temporarily unavailable.",
                code="voice_provider_unavailable",
                status_code=502,
            )
        return VoiceToken(
            token=token,
            websocket_url=WEBSOCKET_URL,
            expires_in_seconds=self._settings.voice_token_ttl_seconds,
            max_session_duration_seconds=self._settings.voice_session_max_duration_seconds,
        )
