"""Voice provider dependency."""

from __future__ import annotations

from app.providers.voice.assemblyai import AssemblyAIVoiceProvider
from app.providers.voice.base import VoiceProvider
from app.settings import get_settings


def get_voice_provider() -> VoiceProvider:
    return AssemblyAIVoiceProvider(get_settings())


__all__ = ["VoiceProvider", "get_voice_provider"]
