"""Demo identity: HMAC-signed bearer tokens, one private project per visitor.

This stands in for a real identity provider. What matters for the voice
security model is that every request resolves to exactly one authenticated
user and project on the server, never from anything the browser, speech, or
model output claims.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from typing import Annotated

from fastapi import Depends, Header

from app.db import SessionDep
from app.errors import NotFoundError, UnauthorizedError
from app.models import Project, User
from app.settings import get_settings


def _sign(value: str) -> str:
    key = get_settings().auth_secret.get_secret_value().encode()
    digest = hmac.new(key, value.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_token(user_id: uuid.UUID) -> str:
    return f"{user_id}.{_sign(str(user_id))}"


def verify_token(token: str) -> uuid.UUID:
    user_part, _, signature = token.partition(".")
    if not signature or not hmac.compare_digest(signature, _sign(user_part)):
        raise UnauthorizedError()
    try:
        return uuid.UUID(user_part)
    except ValueError as exc:
        raise UnauthorizedError() from exc


async def current_user(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError()
    user = await session.get(User, verify_token(authorization.removeprefix("Bearer ")))
    if user is None:
        raise UnauthorizedError()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def current_project(project_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Project not found")
    return project


CurrentProject = Annotated[Project, Depends(current_project)]
