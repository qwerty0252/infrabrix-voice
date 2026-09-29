"""FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import demo, voice
from app.db import create_schema
from app.errors import AppError
from app.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await create_schema()
    yield


app = FastAPI(title="InfraBrix Voice", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    key = settings.assemblyai_api_key
    return {
        "ok": True,
        "llm_provider": settings.llm_provider,
        "voice_configured": bool(key and key.get_secret_value().strip()),
    }


app.include_router(demo.router)
app.include_router(voice.router)
