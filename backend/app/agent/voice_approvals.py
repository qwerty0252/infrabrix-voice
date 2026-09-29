"""Explicit human confirmation for risky tool calls parked during a voice turn.

The runtime never executes a WRITE_INFRASTRUCTURE / DESTRUCTIVE tool from a
spoken request: it parks the call as an ``agent_actions`` row in
``pending_approval``. The authenticated browser UI later posts an explicit
approve/deny for that row. Speech, the AssemblyAI tool bridge and model output
cannot reach that endpoint, so only a person clicking the card can proceed.

Only tools listed in :data:`INLINE_CONFIRMABLE_TOOLS` run from the card.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import democloud
from app.agent.audit import AuditLogger
from app.agent.policies import PolicyDecision
from app.agent.registry import Tool, ToolContext, ToolRegistry
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    ActorKind,
    AgentAction,
    AgentActionStatus,
    DeploymentStatus,
    MessageChannel,
    MessageRole,
    Project,
)
from app.repositories import (
    AgentRunRepository,
    AuditRepository,
    ConversationRepository,
    DeploymentRepository,
)

logger = logging.getLogger(__name__)

INLINE_CONFIRMABLE_TOOLS = frozenset({"rollback_release"})


def _expired(action: AgentAction, ttl_seconds: int) -> bool:
    return (datetime.now(tz=UTC) - action.created_at).total_seconds() > ttl_seconds


async def _load(
    session: AsyncSession, *, action_id: uuid.UUID, conversation_id: uuid.UUID
) -> AgentAction:
    action = await AgentRunRepository(session).get_action(action_id)
    if action is None or action.run.conversation_id != conversation_id:
        raise NotFoundError("Approval not found")
    return action


async def status(
    session: AsyncSession,
    *,
    project: Project,
    action_id: uuid.UUID,
    conversation_id: uuid.UUID,
    ttl_seconds: int,
) -> dict[str, Any]:
    action = await _load(session, action_id=action_id, conversation_id=conversation_id)
    decision = await AgentRunRepository(session).decision_for(action.id)
    out: dict[str, Any] = {
        "action_id": str(action.id),
        "tool": action.tool,
        "confirmable": action.tool in INLINE_CONFIRMABLE_TOOLS,
    }
    if decision is not None:
        state = {
            AgentActionStatus.OK: "approved",
            AgentActionStatus.DENIED: "denied",
        }.get(decision.result_status, "failed")
        out["status"] = state
        data = (decision.result_summary or {}).get("data") or {}
        ids = [uuid.UUID(d) for d in data.get("deployments", [])]
        if ids:
            out["recovery"] = await _recovery(session, project, ids)
        return out
    out["status"] = "expired" if _expired(action, ttl_seconds) else "pending"
    return out


async def _recovery(
    session: AsyncSession, project: Project, ids: list[uuid.UUID]
) -> dict[str, Any]:
    await democloud.advance(session, project)
    repo = DeploymentRepository(session)
    rows = [d for d in [await repo.get(i) for i in ids] if d is not None]
    states = [d.status for d in rows]
    if states and all(s is DeploymentStatus.SUCCEEDED for s in states):
        state = "recovered"
    elif any(s is DeploymentStatus.FAILED for s in states):
        state = "failed"
    else:
        state = "in_progress"
    return {
        "state": state,
        "deployments": [{"id": str(d.id), "status": d.status.value} for d in rows],
    }


async def decide(
    session: AsyncSession,
    *,
    action_id: uuid.UUID,
    approved: bool,
    ctx: ToolContext,
    registry: ToolRegistry,
    conversation_id: uuid.UUID,
    voice_session_id: uuid.UUID | None,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Record a person's decision on a parked call and, if approved, run it.

    ``voice_session_id`` is set when the decision came from the Voice Mode card
    and ``None`` when it came from the typed-chat card.
    """
    action = await _load(session, action_id=action_id, conversation_id=conversation_id)
    runs = AgentRunRepository(session)
    if action.result_status is not AgentActionStatus.PENDING_APPROVAL:
        raise ConflictError("This request is not awaiting approval")
    if await runs.decision_for(action.id) is not None:
        raise ConflictError("This request has already been decided")
    if _expired(action, ttl_seconds):
        raise ConflictError("This approval request expired; ask again to get a fresh one")
    if action.tool not in INLINE_CONFIRMABLE_TOOLS:
        raise ValidationError(f"{action.tool} cannot be approved from Voice Mode")
    tool: Tool | None = registry.get(action.tool)
    if tool is None:
        raise NotFoundError("Tool no longer available")

    audit = AuditLogger(session)
    project = ctx.project
    started = time.perf_counter()
    summary: dict[str, Any]
    if not approved:
        status_value = AgentActionStatus.DENIED
        summary = {"summary": "denied by user"}
        spoken = "Okay, I cancelled that. Nothing was changed."
        outcome = "denied"
        ok = False
    else:
        args = dict(action.args_redacted)
        if any(v == "***" for v in args.values()):
            raise ValidationError("Redacted arguments cannot be executed; ask again")
        # Re-validate against the tool schema: parked arguments are data, not trusted input.
        result = await tool.run(tool.Args.model_validate(args), ctx)
        ok = result.ok
        status_value = AgentActionStatus.OK if ok else AgentActionStatus.ERROR
        summary = {"summary": result.summary, "data": result.data if ok else None}
        outcome = "approved" if ok else "failed"
        spoken = (
            f"Approved. {result.summary}. I'll tell you when it finishes."
            if ok
            else f"The approved action failed: {result.error or result.summary}."
        )
    try:
        await audit.record_tool_call(
            run_id=action.agent_run_id,
            project_id=project.id,
            tool=action.tool,
            risk=action.risk,
            args=dict(action.args_redacted),
            status=status_value,
            summary=summary,
            policy=PolicyDecision(allowed=True, reason="decided by the user in the UI"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            decides_action_id=action.id,
        )
    except IntegrityError as exc:
        # A concurrent click recorded a decision first.
        await session.rollback()
        raise ConflictError("This request has already been decided") from exc
    await AuditRepository(session).append(
        project_id=project.id,
        action=f"{'voice.' if voice_session_id else ''}approval.{outcome}",
        actor=ActorKind.USER,
        actor_id=str(ctx.user.id),
        subject_type="agent_action",
        subject_id=str(action.id),
        payload={
            "tool": action.tool,
            "voice_session_id": str(voice_session_id) if voice_session_id else None,
        },
    )
    await ConversationRepository(session).add_message(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        channel=MessageChannel.VOICE if voice_session_id else MessageChannel.TEXT,
        content=spoken,
        agent_run_id=action.agent_run_id,
    )
    logger.info(
        "voice approval %s tool=%s action=%s session=%s",
        outcome,
        action.tool,
        action.id,
        voice_session_id,
    )
    data = summary.get("data") or {}
    return {
        "status": outcome,
        "ok": ok,
        "action_id": str(action.id),
        "tool": action.tool,
        "speech_text": spoken,
        "deployments": data.get("deployments", []),
    }
