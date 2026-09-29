"""Authenticated AssemblyAI browser bridge for canonical Brix turns.

The browser holds the AssemblyAI WebSocket. When the voice agent calls its one
tool, ``brix_execute_turn``, the browser forwards that call here with the
signed-in user's bearer token. Identity, project, conversation, tool choice,
policy and approvals are all resolved server-side from the owned voice session.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.agent import voice_approvals
from app.agent.registry import ToolContext
from app.agent.runtime import AgentRuntime
from app.agent.tools import build_default_registry
from app.api.schemas import (
    VoiceApprovalDecision,
    VoiceClientEvent,
    VoiceSessionConnected,
    VoiceSessionCreate,
    VoiceTurnRequest,
)
from app.auth import CurrentProject, CurrentUser
from app.db import SessionDep
from app.errors import ConflictError, NotFoundError
from app.models import ActorKind, MessageChannel, VoiceSessionStatus, VoiceTurnStatus
from app.providers.llm import LLMProvider, get_llm_provider
from app.providers.voice import VoiceProvider, get_voice_provider
from app.repositories import (
    AgentRunRepository,
    AuditRepository,
    ConversationRepository,
    VoiceSessionRepository,
    VoiceTurnRepository,
)
from app.settings import get_settings

router = APIRouter(prefix="/projects/{project_id}/voice-sessions", tags=["voice"])
logger = logging.getLogger(__name__)

LLMDep = Annotated[LLMProvider, Depends(get_llm_provider)]
VoiceDep = Annotated[VoiceProvider, Depends(get_voice_provider)]


def _tool_config() -> dict[str, Any]:
    """The sole provider-visible tool. Cloud operations stay server side."""
    return {
        "type": "function",
        "name": "brix_execute_turn",
        "description": (
            "Ask Brix, the DevOps agent, to handle the user's request about their "
            "infrastructure, deployments, incidents, or cloud costs."
        ),
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "The user's request."}},
            "required": ["message"],
        },
    }


def _session_config() -> dict[str, Any]:
    return {
        "system_prompt": (
            "You are the voice of Brix, an on-call DevOps agent. For every substantive "
            "request about infrastructure, deploys, incidents, or costs, call "
            "brix_execute_turn with the user's request in their own words. Then say the "
            "tool's speech_text naturally and briefly. Never claim an infrastructure action "
            "has happened unless the result says so. Changes are approved on screen, never "
            "by voice: if the user says yes to a change, remind them to press Approve."
        ),
        "tools": [_tool_config()],
    }


def _session_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "status": row.status.value,
        "conversation_id": str(row.conversation_id),
        "correlation_id": str(row.correlation_id),
    }


async def _telemetry(
    session: Any,
    *,
    project: Any,
    user: Any,
    row: Any,
    event: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Structured audit entry for voice lifecycle events. Never contains transcripts."""
    body = {"correlation_id": str(row.correlation_id), **(payload or {})}
    await AuditRepository(session).append(
        project_id=project.id,
        action=f"voice.{event}",
        actor=ActorKind.USER,
        actor_id=str(user.id),
        subject_type="voice_session",
        subject_id=str(row.id),
        payload=body,
    )
    logger.info("voice.%s session=%s %s", event, row.id, body)


async def _issue_session(
    *,
    session: Any,
    provider: VoiceProvider,
    project: Any,
    user: Any,
    conversation_id: uuid.UUID,
    event: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = await VoiceSessionRepository(session).create(
        project_id=project.id, conversation_id=conversation_id, user_id=user.id
    )
    token = await provider.mint_token()
    await _telemetry(session, project=project, user=user, row=row, event=event, payload=extra)
    return {
        "session": _session_out(row),
        "token": token.token,
        "websocket_url": token.websocket_url,
        "expires_in_seconds": token.expires_in_seconds,
        "max_session_duration_seconds": token.max_session_duration_seconds,
        "session_config": _session_config(),
    }


async def _owned(session: Any, voice_session_id: uuid.UUID, project: Any, user: Any) -> Any:
    row = await VoiceSessionRepository(session).get_owned(
        session_id=voice_session_id, project_id=project.id, user_id=user.id
    )
    if row is None:
        raise NotFoundError("Voice session not found")
    return row


@router.post("")
async def create_voice_session(
    body: VoiceSessionCreate,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
    provider: VoiceDep,
) -> dict[str, Any]:
    conversation = await ConversationRepository(session).get(body.conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise NotFoundError("Conversation not found")
    return await _issue_session(
        session=session,
        provider=provider,
        project=project,
        user=user,
        conversation_id=conversation.id,
        event="session.started",
    )


@router.post("/{voice_session_id}/reconnect")
async def reconnect_voice_session(
    voice_session_id: uuid.UUID,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
    provider: VoiceDep,
) -> dict[str, Any]:
    """Redeem a fresh provider token after a dropped connection.

    Provider tokens are short lived and single use, so a reconnect is a new
    voice session on the same conversation. The old one is marked disconnected;
    a session the user deliberately ended cannot be resumed.
    """
    repo = VoiceSessionRepository(session)
    old = await _owned(session, voice_session_id, project, user)
    if old.status is VoiceSessionStatus.ENDED:
        raise ConflictError("Voice session was ended; start a new one")
    if old.status in {
        VoiceSessionStatus.CREATED,
        VoiceSessionStatus.CONNECTING,
        VoiceSessionStatus.ACTIVE,
    }:
        await repo.end(old, disconnected=True)
    return await _issue_session(
        session=session,
        provider=provider,
        project=project,
        user=user,
        conversation_id=old.conversation_id,
        event="session.reconnected",
        extra={"previous_session_id": str(old.id)},
    )


@router.post("/{voice_session_id}/connected")
async def mark_voice_connected(
    voice_session_id: uuid.UUID,
    body: VoiceSessionConnected,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    row = await _owned(session, voice_session_id, project, user)
    if row.status not in {VoiceSessionStatus.CREATED, VoiceSessionStatus.CONNECTING}:
        raise ConflictError("Voice session is no longer available")
    await VoiceSessionRepository(session).connect(row, body.provider_session_id)
    await _telemetry(session, project=project, user=user, row=row, event="session.connected")
    return {"session": _session_out(row)}


@router.post("/{voice_session_id}/turns")
async def execute_voice_turn(
    voice_session_id: uuid.UUID,
    body: VoiceTurnRequest,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
    llm: LLMDep,
) -> dict[str, Any]:
    voice_session = await _owned(session, voice_session_id, project, user)
    if voice_session.status is not VoiceSessionStatus.ACTIVE:
        raise ConflictError("Voice session has ended or is not connected")

    # Provider tool calls can be retried; the call ID makes a turn idempotent.
    turns = VoiceTurnRepository(session)
    existing = await turns.by_provider_call(
        voice_session_id=voice_session.id, provider_call_id=body.call_id
    )
    if existing is not None:
        if existing.result is not None:
            return existing.result
        raise ConflictError("Voice turn is already in progress")

    conversation = await ConversationRepository(session).get(voice_session.conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise NotFoundError("Conversation not found")
    turn = await turns.create(voice_session_id=voice_session.id, provider_call_id=body.call_id)
    runtime = AgentRuntime(
        session=session,
        llm=llm,
        registry=build_default_registry(),
        model=get_settings().active_llm()[3],
    )
    result = await runtime.run_result(
        conversation=conversation,
        project=project,
        user=user,
        user_message=body.message,
        message_channel=MessageChannel.VOICE,
    )
    state = {
        "completed": VoiceTurnStatus.COMPLETED,
        "pending": VoiceTurnStatus.PENDING,
    }.get(result["status"], VoiceTurnStatus.FAILED)
    run_id = uuid.UUID(result["agent_run_id"]) if result["agent_run_id"] else None
    await turns.finish(
        turn,
        status=state,
        result=result,
        agent_run_id=run_id,
        error=None if state is not VoiceTurnStatus.FAILED else result["speech_text"],
    )
    approval = result.get("approval") or {}
    await _telemetry(
        session,
        project=project,
        user=user,
        row=voice_session,
        event="turn",
        payload={
            "turn_id": str(turn.id),
            "status": result["status"],
            "agent_run_id": result["agent_run_id"],
            "approval_requested": bool(approval.get("required")),
            "approval_tool": approval.get("tool"),
        },
    )
    return result


@router.post("/{voice_session_id}/end")
async def end_voice_session(
    voice_session_id: uuid.UUID,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    row = await _owned(session, voice_session_id, project, user)
    if row.status not in {VoiceSessionStatus.ENDED, VoiceSessionStatus.DISCONNECTED}:
        await VoiceSessionRepository(session).end(row)
        await _telemetry(session, project=project, user=user, row=row, event="session.ended")
    return {"session": _session_out(row)}


@router.post("/{voice_session_id}/events", status_code=204)
async def report_client_event(
    voice_session_id: uuid.UUID,
    body: VoiceClientEvent,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    row = await _owned(session, voice_session_id, project, user)
    await _telemetry(
        session,
        project=project,
        user=user,
        row=row,
        event=f"client.{body.type}",
        payload={"code": body.code, "attempt": body.attempt},
    )


@router.get("/{voice_session_id}/approvals/{action_id}")
async def get_voice_approval(
    voice_session_id: uuid.UUID,
    action_id: uuid.UUID,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    """Approval state plus, once approved, the recovery (deployment) progress."""
    row = await _owned(session, voice_session_id, project, user)
    return await voice_approvals.status(
        session,
        project=project,
        action_id=action_id,
        conversation_id=row.conversation_id,
        ttl_seconds=get_settings().voice_approval_ttl_seconds,
    )


@router.post("/{voice_session_id}/approvals/{action_id}/decision")
async def decide_voice_approval(
    voice_session_id: uuid.UUID,
    action_id: uuid.UUID,
    body: VoiceApprovalDecision,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
    llm: LLMDep,
) -> dict[str, Any]:
    """The only way a parked risky call proceeds: an explicit click by the signed-in user.

    This is a separate authenticated request from the browser. It is not part of
    the provider tool bridge, so speech or model output cannot trigger it.
    """
    row = await _owned(session, voice_session_id, project, user)
    if row.status is not VoiceSessionStatus.ACTIVE:
        raise ConflictError("Voice session has ended or is not connected")
    action = await AgentRunRepository(session).get_action(action_id)
    if action is None:
        raise NotFoundError("Approval not found")
    ctx = ToolContext(
        session=session, project=project, user=user, llm=llm, run_id=action.agent_run_id
    )
    return await voice_approvals.decide(
        session,
        action_id=action_id,
        approved=body.approved,
        ctx=ctx,
        registry=build_default_registry(),
        conversation_id=row.conversation_id,
        voice_session_id=row.id,
        ttl_seconds=get_settings().voice_approval_ttl_seconds,
    )
