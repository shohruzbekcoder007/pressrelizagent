"""
FastAPI — Hermes host starter.

  Open WebUI → Gateway → POST /v1/chat
       → Hermes host (context/memory)
            → tools registered in agents.hermes_host
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import __version__

logger = logging.getLogger("app")


class ChatRequest(BaseModel):
    """Hermes-compatible chat body (gateway Open WebUI platform)."""

    message: str = Field(..., min_length=1, description="User question")
    session_id: Optional[str] = Field(
        default=None,
        description="Multi-turn session id (Hermes host memory)",
    )
    reset_session: bool = Field(
        default=False,
        description="Clear Hermes host session history",
    )


class ChatResponse(BaseModel):
    success: bool
    response: Optional[str] = None
    session_id: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    retryable: Optional[bool] = None
    tools_called: Optional[list[dict[str, Any]]] = None
    tool_call_count: Optional[int] = None
    agents_used: Optional[list[str]] = None
    mode: Optional[str] = None
    backend: Optional[str] = None


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _check_bearer(
    authorization: Optional[str] = Header(default=None),
) -> None:
    expected = os.getenv("API_BEARER_TOKEN", "").strip()
    if not expected:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title=os.getenv("APP_NAME", "ai-agents"),
        version=__version__,
        description=(
            "Hermes host agent with session memory and a tool-calling loop. "
            "Open WebUI gateway compatible (POST /v1/chat)."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        logger.info("Starting Hermes host service v%s", __version__)
        try:
            from agents.hermes_host import get_hermes_host

            host = get_hermes_host()
            logger.info("Hermes host readiness: %s", host.readiness())
        except Exception:
            logger.exception("Hermes host init failed — /ready may be 503")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": os.getenv("APP_NAME", "ai-agents")}

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        from agents.hermes_host import get_hermes_host

        host = get_hermes_host()
        if not host.ready:
            host.initialize()
        rd = host.readiness()
        if not rd.get("ready"):
            raise HTTPException(
                status_code=503,
                detail={"status": "not_ready", "host": rd},
            )
        return {"status": "ready", "host": rd}

    @app.get("/v1/info")
    def info() -> dict[str, Any]:
        from agents.hermes_host import get_hermes_host

        host = get_hermes_host()
        rd = host.readiness()
        return {
            "service": os.getenv("APP_NAME", "ai-agents"),
            "version": __version__,
            "design": "hermes-host",
            "architecture": rd.get("architecture"),
            "backend": rd.get("backend"),
            "gateway_compatible": True,
            "hermes_chat_path": "/v1/chat",
            "tools": rd.get("tools"),
            "toolsets": rd.get("toolsets"),
            "ready": host.ready,
            "provider": rd.get("provider"),
            "model": rd.get("model"),
            "task_model": rd.get("task_model"),
            "base_url": rd.get("base_url"),
        }

    @app.post("/v1/chat", response_model=ChatResponse)
    def chat(
        body: ChatRequest,
        _: None = Depends(_check_bearer),
    ) -> ChatResponse:
        """Gateway entry: Hermes host keeps context and calls its tools."""
        from agents.hermes_host import get_hermes_host

        try:
            host = get_hermes_host()
            logger.info(
                "POST /v1/chat session_id=%r reset=%s msg_len=%d msg_preview=%r",
                body.session_id,
                body.reset_session,
                len(body.message or ""),
                (body.message or "")[:80],
            )
            result = host.chat(
                body.message,
                session_id=body.session_id,
                reset_session=body.reset_session,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("chat endpoint failed: %s", exc, exc_info=True)
            return ChatResponse(
                success=False,
                response=None,
                session_id=body.session_id,
                error="Ichki server xatosi. Iltimos keyinroq urinib ko'ring.",
                error_code="internal",
                error_detail=str(exc)[:500],
                retryable=True,
            )
        return ChatResponse(
            success=bool(result.get("success")),
            response=result.get("response"),
            session_id=result.get("session_id") or body.session_id,
            error=result.get("error"),
            error_code=result.get("error_code"),
            error_detail=result.get("error_detail"),
            retryable=result.get("retryable"),
            tools_called=result.get("tools_called"),
            tool_call_count=result.get("tool_call_count"),
            agents_used=result.get("agents_used"),
            mode=result.get("mode"),
            backend=result.get("backend"),
        )

    return app


app = create_app()
