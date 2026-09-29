"""Data access. Every lookup that crosses a trust boundary is scoped by owner."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import (
    ActorKind,
    AgentAction,
    AgentActionStatus,
    AgentRun,
    AgentRunStatus,
    AuditEvent,
    Conversation,
    Deployment,
    Message,
    MessageChannel,
    MessageRole,
    ToolRisk,
    VoiceSession,
    VoiceSessionStatus,
    VoiceTurn,
    VoiceTurnStatus,
)


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self._s.get(Conversation, conversation_id)

    async def for_project(self, project_id: uuid.UUID) -> Conversation | None:
        stmt = select(Conversation).where(Conversation.project_id == project_id).limit(1)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def add_message(
        self,
        *,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content: str,
        channel: MessageChannel = MessageChannel.TEXT,
        author_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> Message:
        row = Message(
            conversation_id=conversation_id,
            role=role,
            channel=channel,
            content=content,
            author_id=author_id,
            agent_run_id=agent_run_id,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def messages(self, conversation_id: uuid.UUID, limit: int = 40) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = list((await self._s.execute(stmt)).scalars())
        return list(reversed(rows))


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, conversation_id: uuid.UUID, model: str, provider: str) -> AgentRun:
        row = AgentRun(conversation_id=conversation_id, model=model, provider=provider)
        self._s.add(row)
        await self._s.flush()
        return row

    async def finish(
        self,
        run: AgentRun,
        *,
        status: AgentRunStatus,
        stop_reason: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        step_count: int,
    ) -> None:
        run.status = status
        run.stop_reason = stop_reason
        run.input_tokens = input_tokens
        run.output_tokens = output_tokens
        run.latency_ms = latency_ms
        run.step_count = step_count
        await self._s.flush()

    async def record_action(
        self,
        *,
        agent_run_id: uuid.UUID,
        tool: str,
        risk: ToolRisk,
        args_redacted: dict[str, Any],
        result_status: AgentActionStatus,
        result_summary: dict[str, Any],
        policy_decision: dict[str, Any],
        duration_ms: int,
        decides_action_id: uuid.UUID | None = None,
    ) -> AgentAction:
        row = AgentAction(
            agent_run_id=agent_run_id,
            tool=tool,
            risk=risk,
            args_redacted=args_redacted,
            result_status=result_status,
            result_summary=result_summary,
            policy_decision=policy_decision,
            duration_ms=duration_ms,
            decides_action_id=decides_action_id,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get_action(self, action_id: uuid.UUID) -> AgentAction | None:
        return await self._s.get(AgentAction, action_id)

    async def decision_for(self, action_id: uuid.UUID) -> AgentAction | None:
        stmt = select(AgentAction).where(AgentAction.decides_action_id == action_id)
        return (await self._s.execute(stmt)).unique().scalar_one_or_none()


class VoiceSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self, *, project_id: uuid.UUID, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> VoiceSession:
        row = VoiceSession(project_id=project_id, conversation_id=conversation_id, user_id=user_id)
        self._s.add(row)
        await self._s.flush()
        return row

    async def get_owned(
        self, *, session_id: uuid.UUID, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> VoiceSession | None:
        """A session is only visible to the user who created it, in its own project."""
        stmt = select(VoiceSession).where(
            VoiceSession.id == session_id,
            VoiceSession.project_id == project_id,
            VoiceSession.user_id == user_id,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def connect(self, row: VoiceSession, provider_session_id: str) -> None:
        row.status = VoiceSessionStatus.ACTIVE
        row.provider_session_id = provider_session_id
        await self._s.flush()

    async def end(self, row: VoiceSession, *, disconnected: bool = False) -> None:
        row.status = VoiceSessionStatus.DISCONNECTED if disconnected else VoiceSessionStatus.ENDED
        row.ended_at = utcnow()
        await self._s.flush()


class VoiceTurnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def by_provider_call(
        self, *, voice_session_id: uuid.UUID, provider_call_id: str
    ) -> VoiceTurn | None:
        stmt = select(VoiceTurn).where(
            VoiceTurn.voice_session_id == voice_session_id,
            VoiceTurn.provider_call_id == provider_call_id,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def create(self, *, voice_session_id: uuid.UUID, provider_call_id: str) -> VoiceTurn:
        # The transcript itself lives in the conversation; turns keep only the result.
        row = VoiceTurn(voice_session_id=voice_session_id, provider_call_id=provider_call_id)
        self._s.add(row)
        await self._s.flush()
        return row

    async def finish(
        self,
        row: VoiceTurn,
        *,
        status: VoiceTurnStatus,
        result: dict[str, Any],
        agent_run_id: uuid.UUID | None,
        error: str | None,
    ) -> None:
        row.status = status
        row.result = result
        row.agent_run_id = agent_run_id
        row.error = error
        await self._s.flush()


class DeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, deployment_id: uuid.UUID) -> Deployment | None:
        return await self._s.get(Deployment, deployment_id)

    async def for_project(self, project_id: uuid.UUID, limit: int = 10) -> list[Deployment]:
        stmt = (
            select(Deployment)
            .where(Deployment.project_id == project_id)
            .order_by(Deployment.created_at.desc())
            .limit(limit)
        )
        return list((await self._s.execute(stmt)).scalars())


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        *,
        project_id: uuid.UUID,
        action: str,
        actor: ActorKind,
        actor_id: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        row = AuditEvent(
            project_id=project_id,
            action=action,
            actor=actor,
            actor_id=actor_id,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload or {},
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def recent(self, project_id: uuid.UUID, limit: int = 30) -> list[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
        return list((await self._s.execute(stmt)).scalars())
