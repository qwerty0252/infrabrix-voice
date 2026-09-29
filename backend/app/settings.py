"""Runtime configuration. Provider keys are read without a prefix."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_prefix="INFRABRIX_", extra="ignore"
    )

    database_url: str = "sqlite+aiosqlite:///./infrabrix-voice.db"
    # Signs demo bearer tokens. Override in any shared deployment.
    auth_secret: SecretStr = SecretStr("dev-only-change-me")
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- LLM -------------------------------------------------------------
    # "scripted" is a deterministic offline planner so the demo runs with only
    # an AssemblyAI key. Any OpenAI-compatible provider gives real reasoning.
    llm_provider: Literal["scripted", "openai", "openrouter", "gemini", "groq"] = Field(
        default="scripted", alias="LLM_PROVIDER"
    )
    llm_request_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")

    openrouter_api_key: SecretStr | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="deepseek/deepseek-v3.2", alias="OPENROUTER_MODEL")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )

    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai",
        alias="GEMINI_BASE_URL",
    )

    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")

    # --- Voice (AssemblyAI Voice Agent API) ------------------------------
    # The provider key is server-only. Browsers receive a short-lived,
    # single-use token, never this credential.
    assemblyai_api_key: SecretStr | None = Field(default=None, alias="ASSEMBLYAI_API_KEY")
    voice_token_ttl_seconds: int = 300
    voice_session_max_duration_seconds: int = 1800
    # A parked voice approval can only be confirmed for this long.
    voice_approval_ttl_seconds: int = 600

    # How long a simulated rollback deployment takes to finish.
    demo_rollback_seconds: float = 8.0

    def active_llm(self) -> tuple[str, SecretStr | None, str, str]:
        """(name, api_key, base_url, default_model) for the selected provider."""
        table = {
            "scripted": (None, "", "scripted"),
            "openai": (self.openai_api_key, self.openai_base_url, self.openai_model),
            "openrouter": (
                self.openrouter_api_key,
                self.openrouter_base_url,
                self.openrouter_model,
            ),
            "gemini": (self.gemini_api_key, self.gemini_base_url, self.gemini_model),
            "groq": (self.groq_api_key, self.groq_base_url, self.groq_model),
        }
        key, base_url, model = table[self.llm_provider]
        return self.llm_provider, key, base_url, model


@lru_cache
def get_settings() -> Settings:
    return Settings()
