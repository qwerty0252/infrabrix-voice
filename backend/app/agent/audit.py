"""AuditLogger: records every tool call to agent_actions + audit_events."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.policies import PolicyDecision
from app.models import ActorKind, AgentActionStatus, ToolRisk
from app.repositories import AgentRunRepository, AuditRepository

_SENSITIVE_KEYS = ("password", "secret", "token", "key", "credential")


def redact(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        if any(s in k.lower() for s in _SENSITIVE_KEYS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out


class AuditLogger:
    def __init__(self, session: AsyncSession) -> None:
        self._agents = AgentRunRepository(session)
        self._audit = AuditRepository(session)

    async def record_pending_approval(
        self,
        *,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        tool: str,
        risk: ToolRisk,
        args: dict[str, Any],
        policy: PolicyDecision,
    ) -> uuid.UUID:
        """Park a tool call that needs a human decision; it is not executed."""
        redacted = redact(args)
        action = await self._agents.record_action(
            agent_run_id=run_id,
            tool=tool,
            risk=risk,
            args_redacted=redacted,
            result_status=AgentActionStatus.PENDING_APPROVAL,
            result_summary={"summary": "awaiting human approval"},
            policy_decision={"allowed": policy.allowed, "reason": policy.reason},
            duration_ms=0,
        )
        await self._audit.append(
            project_id=project_id,
            action=f"agent.tool.{tool}.parked",
            actor=ActorKind.AGENT,
            actor_id=str(run_id),
            subject_type="agent_action",
            subject_id=str(action.id),
            payload={"tool": tool, "risk": risk.value, "args": redacted},
        )
        return action.id

    async def record_tool_call(
        self,
        *,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        tool: str,
        risk: ToolRisk,
        args: dict[str, Any],
        status: AgentActionStatus,
        summary: dict[str, Any],
        policy: PolicyDecision,
        duration_ms: int,
        decides_action_id: uuid.UUID | None = None,
    ) -> None:
        redacted = redact(args)
        await self._agents.record_action(
            agent_run_id=run_id,
            tool=tool,
            risk=risk,
            args_redacted=redacted,
            result_status=status,
            result_summary=summary,
            policy_decision={"allowed": policy.allowed, "reason": policy.reason},
            duration_ms=duration_ms,
            decides_action_id=decides_action_id,
        )
        await self._audit.append(
            project_id=project_id,
            action=f"agent.tool.{tool}",
            actor=ActorKind.AGENT,
            actor_id=str(run_id),
            subject_type="agent_run",
            subject_id=str(run_id),
            payload={"tool": tool, "risk": risk.value, "status": status.value, "args": redacted},
        )
