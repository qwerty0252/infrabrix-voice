"""Typed application errors rendered as ``{"error": {"code", "message"}}``."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "error"
    status_code = 500
    message = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404


class ConflictError(AppError):
    code = "conflict"
    status_code = 409


class ValidationError(AppError):
    code = "validation_error"
    status_code = 422


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = 401
    message = "Sign in to continue."


class AgentProviderError(AppError):
    code = "agent_provider_error"
    status_code = 502
