"""Persistence model: identity, conversations, agent runs, voice sessions, audit."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow


class ToolRisk(enum.StrEnum):
    READ = "read"
    PLAN = "plan"
    WRITE_LOW_RISK = "write_low_risk"
    WRITE_INFRASTRUCTURE = "write_infrastructure"
    DESTRUCTIVE = "destructive"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageChannel(enum.StrEnum):
    TEXT = "text"
    VOICE = "voice"


class AgentRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


class AgentActionStatus(enum.StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"


class ActorKind(enum.StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class VoiceSessionStatus(enum.StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    ACTIVE = "active"
    ENDED = "ended"
    DISCONNECTED = "disconnected"


class VoiceTurnStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PENDING = "pending"
    FAILED = "failed"


class DeploymentStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, values_callable=lambda x: [m.value for m in x], native_enum=False)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _id()
    display_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _created()


class Project(Base):
    """A demo project. ``cloud_state`` holds the simulated cloud for this project."""

    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _id()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    environment: Mapped[str] = mapped_column(String(40), default="production")
    cloud_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="Brix")
    created_at: Mapped[datetime] = _created()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = _id()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole))
    channel: Mapped[MessageChannel] = mapped_column(
        _enum(MessageChannel), default=MessageChannel.TEXT
    )
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = _created()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _id()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    status: Mapped[AgentRunStatus] = mapped_column(
        _enum(AgentRunStatus), default=AgentRunStatus.RUNNING
    )
    model: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40))
    stop_reason: Mapped[str | None] = mapped_column(String(120), default=None)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class AgentAction(Base):
    """One tool call, a parked call awaiting approval, or a human decision on one."""

    __tablename__ = "agent_actions"
    id: Mapped[uuid.UUID] = _id()
    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    risk: Mapped[ToolRisk] = mapped_column(_enum(ToolRisk))
    args_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_status: Mapped[AgentActionStatus] = mapped_column(_enum(AgentActionStatus))
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Set on a decision row: the parked action it decides. Unique, so a parked
    # action can be decided exactly once even under concurrent clicks.
    decides_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_actions.id"), unique=True, default=None
    )
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()

    run: Mapped[AgentRun] = relationship(lazy="joined")


class VoiceSession(Base):
    __tablename__ = "voice_sessions"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[VoiceSessionStatus] = mapped_column(
        _enum(VoiceSessionStatus), default=VoiceSessionStatus.CREATED
    )
    provider_session_id: Mapped[str | None] = mapped_column(String(255), default=None)
    correlation_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    created_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class VoiceTurn(Base):
    __tablename__ = "voice_turns"
    __table_args__ = (UniqueConstraint("voice_session_id", "provider_call_id"),)
    id: Mapped[uuid.UUID] = _id()
    voice_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("voice_sessions.id"))
    provider_call_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[VoiceTurnStatus] = mapped_column(
        _enum(VoiceTurnStatus), default=VoiceTurnStatus.RUNNING
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = _created()


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    service: Mapped[str] = mapped_column(String(80))
    from_version: Mapped[str] = mapped_column(String(40))
    to_version: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20), default="rollback")
    status: Mapped[DeploymentStatus] = mapped_column(
        _enum(DeploymentStatus), default=DeploymentStatus.IN_PROGRESS
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class AuditEvent(Base):
    """Append-only audit trail. Never stores transcripts or secret values."""

    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    action: Mapped[str] = mapped_column(String(120))
    actor: Mapped[ActorKind] = mapped_column(_enum(ActorKind))
    actor_id: Mapped[str] = mapped_column(String(80))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _created()
