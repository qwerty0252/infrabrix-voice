"""Demo sign-in, simulated-cloud state, conversation history, and text chat."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app import democloud
from app.agent import voice_approvals
from app.agent.registry import ToolContext
from app.agent.runtime import AgentRuntime
from app.agent.tools import build_default_registry
from app.api.schemas import DemoSignIn, SendMessageRequest, VoiceApprovalDecision
from app.auth import CurrentProject, CurrentUser, issue_token
from app.db import SessionDep
from app.errors import NotFoundError
from app.models import Conversation, MessageChannel, Project, User
from app.providers.llm import LLMProvider, get_llm_provider
from app.repositories import (
    AgentRunRepository,
    AuditRepository,
    ConversationRepository,
    DeploymentRepository,
)
from app.settings import get_settings

router = APIRouter()
LLMDep = Annotated[LLMProvider, Depends(get_llm_provider)]


@router.post("/auth/demo")
async def demo_sign_in(body: DemoSignIn, session: SessionDep) -> dict[str, Any]:
    """Create a visitor with a private project and simulated production cloud."""
    user = User(display_name=body.display_name)
    session.add(user)
    await session.flush()
    project = Project(owner_id=user.id, name="acme-shop", cloud_state=democloud.seed_state())
    session.add(project)
    await session.flush()
    conversation = Conversation(project_id=project.id)
    session.add(conversation)
    await session.flush()
    return {
        "token": issue_token(user.id),
        "user": {"id": str(user.id), "display_name": user.display_name},
        "project_id": str(project.id),
        "conversation_id": str(conversation.id),
    }


@router.get("/me")
async def me(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    project = (
        await session.execute(select(Project).where(Project.owner_id == user.id).limit(1))
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    conversation = await ConversationRepository(session).for_project(project.id)
    return {
        "user": {"id": str(user.id), "display_name": user.display_name},
        "project_id": str(project.id),
        "conversation_id": str(conversation.id) if conversation else None,
    }


@router.get("/projects/{project_id}/cloud")
async def cloud_state(project: CurrentProject, session: SessionDep) -> dict[str, Any]:
    await democloud.advance(session, project)
    state = project.cloud_state
    deployments = await DeploymentRepository(session).for_project(project.id, limit=5)
    audit = await AuditRepository(session).recent(project.id, limit=25)
    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "environment": project.environment,
        },
        "overview": democloud.overview(state),
        "releases": {name: democloud.releases(state, name) for name in state["releases"]},
        "incident": state.get("incident"),
        "deployments": [
            {
                "id": str(d.id),
                "service": d.service,
                "from_version": d.from_version,
                "to_version": d.to_version,
                "status": d.status.value,
                "created_at": d.created_at.isoformat(),
            }
            for d in deployments
        ],
        "audit": [
            {"action": a.action, "actor": a.actor.value, "at": a.created_at.isoformat()}
            for a in audit
        ],
    }


@router.post("/projects/{project_id}/cloud/reset")
async def reset_cloud(project: CurrentProject, session: SessionDep) -> dict[str, Any]:
    """Put the simulated incident back so the demo can be replayed."""
    project.cloud_state = democloud.seed_state()
    await session.flush()
    return {"ok": True}


@router.get("/projects/{project_id}/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID, project: CurrentProject, session: SessionDep
) -> list[dict[str, Any]]:
    repo = ConversationRepository(session)
    conversation = await repo.get(conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise NotFoundError("Conversation not found")
    return [
        {
            "id": str(m.id),
            "role": m.role.value,
            "channel": m.channel.value,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in await repo.messages(conversation.id, limit=100)
    ]


@router.post("/projects/{project_id}/conversations/{conversation_id}/messages")
async def send_text_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
    llm: LLMDep,
) -> dict[str, Any]:
    """Typed chat: the same runtime and result contract as a voice turn."""
    conversation = await ConversationRepository(session).get(conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise NotFoundError("Conversation not found")
    runtime = AgentRuntime(
        session=session,
        llm=llm,
        registry=build_default_registry(),
        model=get_settings().active_llm()[3],
    )
    return await runtime.run_result(
        conversation=conversation,
        project=project,
        user=user,
        user_message=body.content,
        message_channel=MessageChannel.TEXT,
    )


async def _owned_conversation(
    session: Any, project: Project, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await ConversationRepository(session).get(conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise NotFoundError("Conversation not found")
    return conversation


@router.get("/projects/{project_id}/conversations/{conversation_id}/approvals/{action_id}")
async def get_chat_approval(
    conversation_id: uuid.UUID,
    action_id: uuid.UUID,
    project: CurrentProject,
    session: SessionDep,
) -> dict[str, Any]:
    conversation = await _owned_conversation(session, project, conversation_id)
    return await voice_approvals.status(
        session,
        project=project,
        action_id=action_id,
        conversation_id=conversation.id,
        ttl_seconds=get_settings().voice_approval_ttl_seconds,
    )


@router.post(
    "/projects/{project_id}/conversations/{conversation_id}/approvals/{action_id}/decision"
)
async def decide_chat_approval(
    conversation_id: uuid.UUID,
    action_id: uuid.UUID,
    body: VoiceApprovalDecision,
    project: CurrentProject,
    user: CurrentUser,
    session: SessionDep,
    llm: LLMDep,
) -> dict[str, Any]:
    """The typed-chat twin of the Voice Mode approval card: an explicit click."""
    conversation = await _owned_conversation(session, project, conversation_id)
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
        conversation_id=conversation.id,
        voice_session_id=None,
        ttl_seconds=get_settings().voice_approval_ttl_seconds,
    )
