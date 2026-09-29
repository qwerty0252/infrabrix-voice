"""Provider-neutral contract for browser voice credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VoiceToken:
    token: str
    websocket_url: str
    expires_in_seconds: int
    max_session_duration_seconds: int


class VoiceProvider(Protocol):
    async def mint_token(self) -> VoiceToken: ...
