"""
FastAPI — Hermes host starter.

  Open WebUI → Gateway → POST /v1/chat
       → Hermes host (context/memory)
            → tools registered in agents.hermes_host

`/v1/chat` takes JSON as it always has, and `multipart/form-data` when the
user attached a PDF -- the file and the question travel together, so there is
no upload step for the gateway to sequence. `/v1/files` stores a PDF on its
own, for uploading ahead of time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

# Starlette's, not FastAPI's. `fastapi.UploadFile` is a subclass, and
# `request.form()` builds the base class -- so testing against the subclass
# rejects every real upload.
from starlette.datastructures import UploadFile

from agents.user_profiles import InvalidUserId, UserProfile, resolve_profile
from app import __version__, jobs
from app.rate_limit import RateLimiter

logger = logging.getLogger("app")

# Header carrying the end user's identity, set by the gateway *after* it has
# authenticated them. It is trusted only because the gateway bearer token is
# required on the same request -- see `_check_bearer`. Never accept it from an
# unauthenticated caller.
USER_ID_HEADER = "X-User-Id"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _max_message_chars() -> int:
    return max(1, _env_int("MAX_MESSAGE_CHARS", 8000))

# How long a plain JSON `/v1/chat` holds the connection before handing back a
# job id instead. 0 keeps the old behaviour of waiting for as long as it
# takes -- correct when nothing between the caller and here has an idle
# timeout, and the wrong default the moment something does.
_DEFAULT_CHAT_WAIT = 0.0
# Progress line cadence on the SSE path. Comfortably under the 30-60s idle
# timeout typical of a proxy or an HTTP client's read deadline.
_DEFAULT_HEARTBEAT = 10.0


def _chat_wait_seconds() -> Optional[float]:
    try:
        value = float(os.getenv("CHAT_WAIT_SECONDS") or _DEFAULT_CHAT_WAIT)
    except ValueError:
        value = _DEFAULT_CHAT_WAIT
    return None if value <= 0 else value


def _heartbeat_seconds() -> float:
    try:
        value = float(os.getenv("CHAT_HEARTBEAT_SECONDS") or _DEFAULT_HEARTBEAT)
    except ValueError:
        value = _DEFAULT_HEARTBEAT
    return max(1.0, value)

# Uploads land in the folder the `pdfmd` tools read, which is bind-mounted from
# ./data -- one subfolder per user, `users/<slug>/`, so nobody can list, open
# or overwrite another user's release. Nothing is imported from the plugin to
# get here: Hermes loads plugins under a generated module name, so
# `plugins.pdfmd` is not importable from the app at all. The two copies of
# this layout are kept in step by `PDF_DATA_DIR` and the profile slug, which
# the plugin reads back from the Hermes home override.
_MAX_UPLOAD_BYTES = 64 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _data_root(profile: UserProfile) -> Path:
    base = Path(os.getenv("PDF_DATA_DIR") or "/app/data").resolve()
    # The slug is already validated as a single safe path segment
    # (`agents.user_profiles`); the containment check is the second layer.
    root = (base / "users" / profile.slug).resolve()
    if not root.is_relative_to(base / "users"):
        raise HTTPException(status_code=400, detail="invalid user")
    return root


def _safe_pdf_name(raw: str) -> str:
    """
    A caller-supplied filename reduced to something safe to write.

    The name arrives from a browser upload, so it can carry a path, a null
    byte, or anything else the sender's filesystem allowed. Only the basename
    survives, and only after the character filter.
    """
    base = Path(str(raw or "").replace("\\", "/")).name
    base = _SAFE_NAME.sub("_", base).strip("._") or "yuklangan"
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base


def _store_pdf(profile: UserProfile, raw_name: str, data: bytes) -> str:
    """
    Validate an uploaded PDF and put it where the `pdfmd` tools read.

    Shared by `/v1/files` and the multipart form of `/v1/chat`, so a file
    arriving either way lands under the same rules -- in the caller's own
    folder.
    """
    if not data:
        raise HTTPException(status_code=400, detail="fayl bo'sh")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"fayl juda katta: {len(data) // 1024 // 1024} MB "
                f"(chegara {_MAX_UPLOAD_BYTES // 1024 // 1024} MB)"
            ),
        )
    if not data.lstrip()[:5].startswith(b"%PDF"):
        # Cheap, and it catches the common integration bug of a gateway posting
        # JSON or an already-extracted text blob instead of a file.
        raise HTTPException(
            status_code=400, detail="bu PDF emas (%PDF sarlavhasi yo'q)"
        )

    name = _safe_pdf_name(raw_name)
    root = _data_root(profile)
    try:
        (root / "pdf").mkdir(parents=True, exist_ok=True)
        (root / "pdf" / name).write_bytes(data)
        # A re-upload under the same name makes the stored Markdown stale, and
        # `pdf_to_md` reuses that Markdown by design -- so the next question
        # would be answered about the previous document. Dropping it here is
        # what makes re-uploading safe.
        stale = root / "md" / f"{Path(name).stem}.md"
        if stale.is_file():
            stale.unlink()
    except OSError as exc:
        logger.error("upload failed for %s: %s", name, exc)
        raise HTTPException(
            status_code=500, detail=f"faylni saqlab bo'lmadi: {exc}"
        ) from exc

    logger.info("stored upload=%s user=%s bytes=%d", name, profile.slug, len(data))
    return name


def _form_files(form: Any) -> list[UploadFile]:
    """
    Every uploaded file in a form, `file` fields first.

    The field name is not fixed: the gateway sits outside this repo and may
    call it `file`, `files`, `upload` or `document`. Anything that arrived as
    a file is taken, so the integration does not hinge on guessing the name.
    """
    uploads = [
        (key, value)
        for key, value in form.multi_items()
        if isinstance(value, UploadFile)
    ]
    return [v for k, v in uploads if k == "file"] + [
        v for k, v in uploads if k != "file"
    ]


def _form_file(form: Any) -> Optional[UploadFile]:
    found = _form_files(form)
    return found[0] if found else None


def _form_bool(raw: Any) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


class ChatRequest(BaseModel):
    """Hermes-compatible chat body (gateway Open WebUI platform)."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=_max_message_chars(),
        description="User question",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Multi-turn session id (Hermes host memory)",
    )
    reset_session: bool = Field(
        default=False,
        description="Clear Hermes host session history",
    )
    stream: bool = Field(
        default=False,
        description=(
            "Answer as Server-Sent Events: a progress line every few seconds, "
            "then the result. Keeps a long turn's connection alive."
        ),
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
    # Files stored from this request's multipart form, under the names the
    # tools will see. Absent on a plain JSON chat.
    files: Optional[list[str]] = None
    # Set only while a turn is still running: the answer is not here yet, and
    # `job_id` is where to collect it. A finished reply never carries these,
    # so a client that ignores them behaves exactly as it did before.
    status: Optional[str] = None
    job_id: Optional[str] = None
    elapsed: Optional[float] = None


def _chat_response(
    job: "jobs.Job",
    body: Optional[ChatRequest],
    stored: Optional[list[str]],
) -> ChatResponse:
    """The finished turn, in the shape callers have always received."""
    result = job.result or {}
    session_id = result.get("session_id") or (
        body.session_id if body else job.session_id
    )
    return ChatResponse(
        success=bool(result.get("success")),
        response=result.get("response"),
        session_id=session_id,
        error=result.get("error"),
        error_code=result.get("error_code"),
        error_detail=result.get("error_detail"),
        retryable=result.get("retryable"),
        tools_called=result.get("tools_called"),
        tool_call_count=result.get("tool_call_count"),
        agents_used=result.get("agents_used"),
        mode=result.get("mode"),
        backend=result.get("backend"),
        files=stored or None,
        job_id=job.id,
        elapsed=job.elapsed,
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_job(
    job: "jobs.Job",
    body: ChatRequest,
    stored: Optional[list[str]],
) -> AsyncIterator[str]:
    """
    Progress lines until the turn lands, then the answer.

    The first event goes out immediately: a client that sees no bytes at all
    cannot tell a working server from a hung one, and neither can the proxies
    in between.

    Completion is polled far more finely than the heartbeat cadence so a turn
    that finishes just after a progress line is not held back until the next
    one -- the heartbeat governs how often we *speak*, not how long we wait.
    """
    heartbeat = _heartbeat_seconds()
    yield _sse("progress", job.snapshot())

    waited = 0.0
    tick = 0.25
    while not await asyncio.to_thread(job.wait, 0):
        await asyncio.sleep(tick)
        waited += tick
        if waited >= heartbeat:
            waited = 0.0
            yield _sse("progress", job.snapshot())

    yield _sse("done", _chat_response(job, body, stored).model_dump())
    # Mirrors the OpenAI streaming convention, so a gateway written against
    # that shape recognises the end of the stream without special-casing us.
    yield "data: [DONE]\n\n"


def _validated(payload: dict[str, Any]) -> ChatRequest:
    try:
        return ChatRequest(**payload)
    except ValidationError as exc:
        # Same 422 a declared body model would have produced, so the multipart
        # path fails exactly like the JSON one.
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _mention(message: str, files: list[str]) -> str:
    """
    Make sure the question names the files that came with it.

    The agent reaches a PDF by name -- that is what `pdf_to_md` takes -- so a
    file attached without being named in the text would simply be invisible.
    A message that already names it is left alone rather than annotated twice.
    """
    listed = ", ".join(files)
    if not message:
        # A chat UI lets someone attach a file and send with no text at all.
        # Checking it is the only thing this agent does with a press release,
        # so that is the question asked on their behalf.
        return (
            f"{listed} faylini tekshir: undagi raqamlar rasmiy statistikaga "
            "mos keladimi?"
        )
    missing = [name for name in files if name not in message]
    if not missing:
        return message
    return (
        f"{message}\n\n[Biriktirilgan fayl: {', '.join(missing)} "
        "-- data/pdf/ ichida saqlangan]"
    )


async def _read_chat(
    request: Request, caller: UserProfile
) -> tuple[ChatRequest, list[str]]:
    """
    Read a chat request in either wire shape, storing any attached PDFs.

    Returns the request and the names the files were stored under. The
    multipart branch exists so that asking about an attachment is one call:
    a gateway that had to upload and then chat would have to keep the name
    between two requests and undo the first if the second failed.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if not content_type.startswith("multipart/form-data"):
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail="body must be JSON or multipart/form-data",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        return _validated(payload), []

    form = await request.form()
    stored = [
        _store_pdf(caller, item.filename or "", await item.read())
        for item in _form_files(form)
    ]
    # `message` is the field this API documents; the other two are what chat
    # frontends commonly call it, and accepting them costs nothing.
    raw_message = form.get("message") or form.get("prompt") or form.get("text")
    message = str(raw_message or "").strip()
    session = str(form.get("session_id") or "").strip() or None

    return (
        _validated(
            {
                "message": _mention(message, stored) if stored else message,
                "session_id": session,
                "reset_session": _form_bool(form.get("reset_session")),
                "stream": _form_bool(form.get("stream")),
            }
        ),
        stored,
    )


def _cors_origins() -> list[str]:
    """Allowed browser origins.

    `*` together with `allow_credentials=True` is the combination browsers
    refuse anyway, so it is not offered: an unset or wildcard value means no
    cross-origin access at all. The gateway is a server-side caller and needs
    none.
    """
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw or raw == "*":
        if raw == "*":
            logger.warning(
                "CORS_ORIGINS=* is refused with credentialed requests; "
                "cross-origin access disabled. List exact origins to enable it."
            )
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def _gateway_token() -> str:
    """The shared secret the gateway must present.

    `GATEWAY_TOKEN` is the current name; `API_BEARER_TOKEN` stays accepted so
    an existing deployment keeps working.
    """
    return (
        os.getenv("GATEWAY_TOKEN", "").strip()
        or os.getenv("API_BEARER_TOKEN", "").strip()
    )


def _require_auth_configured() -> None:
    """Refuse to serve without a gateway token.

    Previously an unset token disabled authentication entirely, which meant a
    forgotten setting silently published /v1/chat -- and with it, since the
    user id header is only as trustworthy as the caller, every user's memory.
    Failing at startup is the safer direction for that mistake to fall.
    `ALLOW_UNAUTHENTICATED=true` is the explicit opt-out for local development.
    """
    if _gateway_token():
        return
    if _env_bool("ALLOW_UNAUTHENTICATED", False):
        logger.warning(
            "ALLOW_UNAUTHENTICATED=true and no GATEWAY_TOKEN set -- /v1/chat is "
            "open. Never do this on a reachable network."
        )
        return
    raise RuntimeError(
        "GATEWAY_TOKEN is not set. Set it to the shared secret the gateway "
        "sends, or set ALLOW_UNAUTHENTICATED=true for local development only."
    )


def _check_bearer(
    authorization: Optional[str] = Header(default=None),
) -> None:
    expected = _gateway_token()
    if not expected:
        # Only reachable with ALLOW_UNAUTHENTICATED=true; startup refuses
        # otherwise.
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


def _resolve_caller(
    _auth: None = Depends(_check_bearer),
    x_user_id: Optional[str] = Header(default=None),
) -> UserProfile:
    """Map the gateway's `X-User-Id` header to the caller's own Hermes profile.

    Depends on `_check_bearer`, so the header is never read from a caller that
    has not proved it is the gateway. Requiring the header by default is
    deliberate: a missing identity used to mean everyone shared one memory,
    one session store and one upload folder, which is the leak this closes.
    `HERMES_REQUIRE_USER_ID=false` restores a single shared profile for a
    genuinely single-user deployment.
    """
    if x_user_id is None or not x_user_id.strip():
        if _env_bool("HERMES_REQUIRE_USER_ID", True):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{USER_ID_HEADER} header is required. The gateway must "
                    "send the authenticated end user's id, or set "
                    "HERMES_REQUIRE_USER_ID=false to share one profile."
                ),
            )
        return resolve_profile(None)

    try:
        return resolve_profile(x_user_id)
    except InvalidUserId as exc:
        # Logged at warning because the rejected values worth seeing here are
        # traversal attempts, not typos.
        logger.warning("rejected %s=%r: %s", USER_ID_HEADER, x_user_id[:120], exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {USER_ID_HEADER}: {exc}",
        ) from exc


_limiter = RateLimiter(
    rate_per_minute=float(_env_int("RATE_LIMIT_PER_MINUTE", 30)),
    burst=_env_int("RATE_LIMIT_BURST", 10),
)


def _check_rate_limit(profile: UserProfile, request: Request) -> None:
    """One bucket per user, falling back to the peer address for the shared
    profile so it cannot be drained by a single client on everyone's behalf."""
    if not _limiter.enabled:
        return
    key = profile.slug
    if profile.is_shared:
        client = request.client.host if request.client else "unknown"
        key = f"{profile.slug}:{client}"
    allowed, retry_after = _limiter.check(key)
    if not allowed:
        logger.warning("rate limit hit for %s (retry in %ss)", key, retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Juda ko'p so'rov. Birozdan keyin qayta urinib ko'ring.",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


def create_app() -> FastAPI:
    _require_auth_configured()

    app = FastAPI(
        title=os.getenv("APP_NAME", "PressRelizAgent"),
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
        return {"status": "ok", "service": os.getenv("APP_NAME", "PressRelizAgent")}

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        """Readiness probe. Unauthenticated, so the body stays minimal.

        The status code carries the signal an orchestrator needs; the full
        diagnostic view (including the upstream address and the last error)
        is on the authenticated /v1/info, and the detail is logged here.
        """
        from agents.hermes_host import get_hermes_host

        host = get_hermes_host()
        if not host.ready:
            host.initialize()
        rd = host.readiness()
        if not rd.get("ready"):
            logger.warning("readiness probe failed: %s", rd)
            raise HTTPException(status_code=503, detail={"status": "not_ready"})
        return {"status": "ready", "backend": rd.get("backend")}

    @app.get("/v1/info")
    def info(_: None = Depends(_check_bearer)) -> dict[str, Any]:
        from agents.hermes_host import get_hermes_host

        host = get_hermes_host()
        rd = host.readiness()
        return {
            "service": os.getenv("APP_NAME", "PressRelizAgent"),
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
            "error": rd.get("error"),
            # `base_url` is deliberately omitted: it is the internal vLLM /
            # Ollama address and nothing downstream needs it.
        }

    @app.post("/v1/files")
    async def upload_file(
        request: Request,
        caller: UserProfile = Depends(_resolve_caller),
    ) -> dict[str, Any]:
        """
        Store a PDF without asking anything about it.

        Useful for uploading ahead of time, or for a gateway that already has
        an upload step of its own. **A gateway attaching a file to a message
        does not need this** -- `/v1/chat` takes the file and the question in
        one multipart request.

        Two wire shapes are accepted, because the gateway sits outside this
        repo and either is a reasonable thing for it to send: a
        `multipart/form-data` upload under the field `file`, or the raw bytes
        as the body with `?name=`.
        """
        _check_rate_limit(caller, request)
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("multipart/form-data"):
            item = _form_file(await request.form())
            if item is None:
                raise HTTPException(
                    status_code=400,
                    detail="multipart body has no file part (field `file`)",
                )
            name = _store_pdf(caller, item.filename or "", await item.read())
        else:
            name = _store_pdf(
                caller,
                request.query_params.get("name") or "",
                await request.body(),
            )

        return {
            "success": True,
            "fayl": name,
            "keyingi_qadam": (
                f"/v1/chat ga shu nomni yozib so'rang, masalan: "
                f"\"{name} press-relizini tekshir\". Yoki faylni to'g'ridan-"
                f"to'g'ri /v1/chat ga multipart bilan yuboring -- bitta so'rov."
            ),
        }

    @app.get("/v1/files")
    def list_files(
        caller: UserProfile = Depends(_resolve_caller),
    ) -> dict[str, Any]:
        """What the caller has uploaded, and what has already been converted."""
        root = _data_root(caller)

        def names(folder: str, suffix: str) -> list[str]:
            try:
                return sorted(p.name for p in (root / folder).glob(f"*{suffix}"))
            except OSError:
                return []

        return {
            "success": True,
            "pdf": names("pdf", ".pdf"),
            "markdown": names("md", ".md"),
        }

    @app.post("/v1/chat", response_model=ChatResponse)
    async def chat(
        request: Request,
        caller: UserProfile = Depends(_resolve_caller),
    ) -> Any:
        """
        Gateway entry: Hermes host keeps context and calls its tools.

        Takes either wire shape, so the gateway posts here whether or not the
        user attached anything:

          * `application/json` -- `{message, session_id, reset_session}`, the
            shape this endpoint has always taken.
          * `multipart/form-data` -- the same fields as form parts, plus one
            or more PDFs. The files are stored first and their names are added
            to the message, so asking about an attachment is one request rather
            than an upload followed by a chat.

        The turn itself runs on its own thread (see `app.jobs`). A checking
        turn takes minutes, and holding the request open for all of it is what
        put it at the mercy of every idle timer on the path. Three ways out,
        all of them keeping the work alive:

          * `stream: true` (or `Accept: text/event-stream`) -- Server-Sent
            Events, a progress line every few seconds, then the answer. Bytes
            keep flowing, so nothing on the path times the connection out.
            This is the recommended shape for a gateway.
          * plain JSON -- waits like it always has. `CHAT_WAIT_SECONDS` caps
            the wait; past it the reply is `status: "running"` with a
            `job_id` rather than a hung request.
          * a retry -- same session and same message while the first turn is
            still running attaches to it instead of starting a second one.

        In every case a dropped connection leaves the turn running and its
        answer collectable from `GET /v1/jobs/{job_id}`.

        Runs against `caller`'s own Hermes profile, so memory, session
        history, `session_search`, uploads and jobs see only that user's data.
        """
        from agents.hermes_host import get_hermes_host

        # Before reading the body: a rejected request must not get as far as
        # writing its attachments to disk.
        _check_rate_limit(caller, request)
        body, stored = await _read_chat(request, caller)
        wants_stream = bool(body.stream) or "text/event-stream" in (
            request.headers.get("accept") or ""
        )

        existing = jobs.find_running(
            caller.slug, body.session_id, body.message, body.reset_session
        )
        if existing is not None:
            logger.info(
                "POST /v1/chat attaching to running job %s (user=%s session=%r)",
                existing.id,
                caller.slug,
                body.session_id,
            )
            job = existing
        else:
            logger.info(
                "POST /v1/chat user=%s session_id=%r reset=%s files=%s "
                "stream=%s msg_len=%d msg_preview=%r",
                caller.slug,
                body.session_id,
                body.reset_session,
                stored or "-",
                wants_stream,
                len(body.message or ""),
                (body.message or "")[:80],
            )
            host = get_hermes_host()
            job = jobs.start(
                lambda: host.chat(
                    body.message,
                    session_id=body.session_id,
                    reset_session=body.reset_session,
                    profile=caller,
                ),
                owner=caller.slug,
                session_id=body.session_id,
                message=body.message,
                reset=body.reset_session,
                files=stored,
            )

        if wants_stream:
            return StreamingResponse(
                _stream_job(job, body, stored),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    # Tell nginx not to buffer: a buffered SSE stream arrives
                    # in one lump at the end, which defeats the whole point.
                    "X-Accel-Buffering": "no",
                },
            )

        # `asyncio.to_thread` rather than a bare blocking wait: the wait must
        # not occupy the event loop, or one long turn would stall every other
        # request the service is serving.
        wait = _chat_wait_seconds()
        finished = await asyncio.to_thread(job.wait, wait)
        if not finished:
            return ChatResponse(
                success=True,
                response=None,
                session_id=body.session_id,
                status="running",
                job_id=job.id,
                elapsed=job.elapsed,
                files=stored or None,
                error=None,
            )
        return _chat_response(job, body, stored)

    @app.get("/v1/jobs/{job_id}", response_model=ChatResponse)
    def job_status(
        job_id: str,
        caller: UserProfile = Depends(_resolve_caller),
    ) -> Any:
        """
        Collect a turn started earlier, whatever happened to its connection.

        Still running: `status: "running"` with the seconds so far. Finished:
        the full answer, exactly as `/v1/chat` would have returned it. Another
        user's job id answers 404, the same as an id that never existed.
        """
        job = jobs.get(job_id, caller.slug)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=f"job topilmadi: {job_id} (eskirgan yoki noto'g'ri id)",
            )
        if job.status != "done":
            return ChatResponse(
                success=True,
                response=None,
                session_id=job.session_id,
                status="running",
                job_id=job.id,
                elapsed=job.elapsed,
                files=job.files or None,
            )
        return _chat_response(job, None, job.files)

    @app.get("/v1/jobs")
    def job_list(
        session_id: Optional[str] = None,
        caller: UserProfile = Depends(_resolve_caller),
    ) -> dict[str, Any]:
        """The caller's most recent turn for a session -- how a reconnecting
        client finds the job it lost the connection to without holding its
        id."""
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id kerak")
        job = jobs.latest_for_session(caller.slug, session_id)
        if job is None:
            return {"success": True, "job": None}
        return {"success": True, "job": job.snapshot()}

    return app


app = create_app()
