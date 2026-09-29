"""Request bodies. Voice-facing bodies forbid unknown fields."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DemoSignIn(BaseModel):
    display_name: str = Field(default="Demo engineer", min_length=1, max_length=80)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class VoiceSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID


class VoiceSessionConnected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_session_id: str = Field(min_length=1, max_length=255)


class VoiceTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=8000)


class VoiceApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool


class VoiceClientEvent(BaseModel):
    """Client-reported connection telemetry. Codes only: never transcripts or free text."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["reconnect_attempt", "disconnected", "error", "approval_shown"]
    code: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    attempt: int | None = Field(default=None, ge=0, le=50)
