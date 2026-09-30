"""In-process sliding-window limits for a publicly hosted demo.

Every voice session spends provider credits, so a public deployment caps demo
sign-ins per client, voice sessions per user, agent turns per user, and voice
sessions per day overall. Single-process by design; a multi-replica deployment
would move these counters to Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from app.errors import AppError
from app.settings import get_settings

HOUR = 3600.0
DAY = 86400.0


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    message = "The demo is busy. Please try again in a little while."


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, *, limit: int, window: float, message: str | None = None) -> None:
        if limit <= 0:
            return
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            raise RateLimitedError(message)
        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()


limiter = SlidingWindow()


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def check_signin(request: Request) -> None:
    limiter.hit(
        f"signin:{client_ip(request)}",
        limit=get_settings().limit_signins_per_ip_per_hour,
        window=HOUR,
    )


def check_voice_session(user_id: object) -> None:
    settings = get_settings()
    limiter.hit(
        f"voice:{user_id}",
        limit=settings.limit_voice_sessions_per_user_per_hour,
        window=HOUR,
        message="You've started a lot of voice sessions. Try again in a bit, or type instead.",
    )
    limiter.hit(
        "voice:all",
        limit=settings.limit_voice_sessions_per_day,
        window=DAY,
        message="Voice Mode has reached today's demo limit. Typed chat still works.",
    )


def check_turn(user_id: object) -> None:
    limiter.hit(
        f"turn:{user_id}",
        limit=get_settings().limit_turns_per_user_per_hour,
        window=HOUR,
    )
