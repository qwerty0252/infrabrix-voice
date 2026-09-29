"""LLM provider dependency."""

from __future__ import annotations

from functools import lru_cache

from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compat import (
    OpenAICompatibleProvider,
    ProviderConfig,
    UnconfiguredProvider,
)
from app.providers.llm.scripted import ScriptedLLMProvider
from app.settings import get_settings


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    name, key, base_url, model = settings.active_llm()
    if name == "scripted":
        return ScriptedLLMProvider()
    if key is None or not key.get_secret_value().strip():
        return UnconfiguredProvider(f"LLM_PROVIDER={name} but no API key is set for it.")
    return OpenAICompatibleProvider(
        ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=key,
            default_model=model,
            timeout_seconds=settings.llm_request_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    )


__all__ = ["LLMProvider", "get_llm_provider"]
