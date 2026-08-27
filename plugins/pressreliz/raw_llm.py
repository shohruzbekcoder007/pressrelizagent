"""
`raw_llm` — a prompt straight to the model, with nothing of the agent attached.

The host agent always answers through Hermes: its system prompt, SOUL.md
identity, session history, memory snapshot and tool schemas all ride along on
every call. This tool is the opposite — one request, one answer, nothing else
in the context window. Useful when the agent wants a clean second opinion,
a mechanical rewrite, or an answer that its own persona would colour.

It talks to the same endpoint the host agent runs on: `resolve_llm_endpoint()`
in `agents/hermes_host.py` is the single resolver, so this tool can never end
up pointed at a different provider than the agent that called it.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.plugin.pressreliz.raw_llm")

TOOL_NAME = "raw_llm"
TOOLSET_NAME = "pressreliz"

# Ollama's OpenAI-compatible endpoint truncates hard when max_tokens is absent,
# so a default is always sent rather than left to the server.
_DEFAULT_MAX_TOKENS = 2048

# A local 27B model answering a long prompt is slow, but not this slow. Without
# a cap a stalled generation hangs the tool, the agent turn and the caller's
# HTTP request all the way up -- nothing else in the chain sets one.
_DEFAULT_TIMEOUT_SECONDS = 120.0

# The OpenAI SDK retries twice by default. Against a local server that is
# usually the wrong trade: a real stall gets multiplied by three before anyone
# is told, and these calls are not idempotent-cheap.
_DEFAULT_MAX_RETRIES = 1

_client_lock = threading.Lock()
_client: Any = None
_client_key: tuple[Any, ...] | None = None


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.getenv(name) or "").strip()
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.getenv(name) or "").strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _reasoning_off_kwargs(endpoint: dict[str, Any]) -> dict[str, Any]:
    """
    The wire flags that stop a thinking model from thinking, or `{}`.

    Hermes applies these through its provider profile, but this tool calls the
    endpoint directly and so gets none of that. Against a reasoning model the
    difference is not cosmetic: the reply lands in a separate `reasoning`
    field and `content` comes back empty once the token budget is spent
    thinking -- which this tool then reports as "model returned an empty
    reply".

    Two flags because no single one covers the local servers: Ollama's
    /v1 route ignores `think` but honours `reasoning_effort`, and vLLM is the
    other way round on some builds. Endpoints that recognize neither ignore
    both.

    Sent only on a local profile. `hermes_provider` is exactly that marker --
    it is None for OpenAI, whose gpt-4.1 rejects `reasoning_effort` outright.
    """
    if not endpoint.get("hermes_provider"):
        return {}
    if _env_bool("HERMES_REASONING_ENABLED", False):
        # Thinking is server-default-on for these backends. Forcing it back on
        # explicitly risks a 400, so leave the endpoint to its own default.
        return {}
    return {
        "reasoning_effort": "none",
        "extra_body": {"think": False},
    }


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Send a prompt to a clean LLM and return its reply verbatim. The call "
        "carries no system prompt, no conversation history, no memory and no "
        "tools -- only what you pass here. Use it for an independent second "
        "opinion, a mechanical rewrite or a self-contained sub-question. Do "
        "NOT use it for anything that needs the conversation so far: the model "
        "cannot see it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The full prompt. It must stand on its own -- include every "
                    "fact needed, because nothing else is sent."
                ),
            },
            "system": {
                "type": "string",
                "description": (
                    "Optional system message for this one call, e.g. a role or "
                    "an output format. Omit for a completely bare call."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional model override. Defaults to the model the agent "
                    "itself runs on."
                ),
            },
            "temperature": {
                "type": "number",
                "description": "Sampling temperature. Defaults to 0.",
            },
            "max_tokens": {
                "type": "integer",
                "description": (
                    f"Reply length cap. Defaults to {_DEFAULT_MAX_TOKENS}."
                ),
            },
        },
        "required": ["prompt"],
    },
}


def _ensure_app_on_path() -> None:
    """Make the app's `agents` package importable from inside the plugin."""
    candidates = [
        Path(os.getenv("APP_HOME", "/app")),
        Path(__file__).resolve().parents[2],
    ]
    for root in candidates:
        if (root / "agents" / "hermes_host.py").is_file():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return


def _endpoint() -> dict[str, Any]:
    _ensure_app_on_path()
    from agents.hermes_host import resolve_llm_endpoint

    return resolve_llm_endpoint()


def _get_client(base_url: str | None, api_key: str) -> Any:
    """
    One OpenAI client, reused across calls.

    Rebuilt only when the endpoint or its limits change. Building one per call
    would open a fresh connection pool every time and leak it.
    """
    global _client, _client_key
    timeout = _env_float("RAW_LLM_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
    retries = _env_int("RAW_LLM_MAX_RETRIES", _DEFAULT_MAX_RETRIES)
    key = (base_url or "", api_key, timeout, retries)
    with _client_lock:
        if _client is not None and _client_key == key:
            return _client
        from openai import OpenAI

        kwargs: dict[str, Any] = {
            "api_key": api_key or "no-key-required",
            "timeout": timeout,
            "max_retries": retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        _client = OpenAI(**kwargs)
        _client_key = key
        logger.info(
            "raw_llm client built base_url=%s timeout=%ss max_retries=%s",
            base_url,
            timeout,
            retries,
        )
        return _client


def raw_llm_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Run one bare completion. Returns a dict; the plugin serializes it."""
    prompt = str((params or {}).get("prompt") or "").strip()
    if not prompt:
        return {"success": False, "error": "prompt must not be empty"}

    endpoint = _endpoint()
    if endpoint.get("error"):
        return {"success": False, "error": str(endpoint["error"])}

    model = str(params.get("model") or "").strip() or endpoint["model"]
    if not model:
        return {
            "success": False,
            "error": f"no model configured ({endpoint['model_var']} is unset)",
        }

    temperature = params.get("temperature")
    try:
        temperature = 0.0 if temperature is None else float(temperature)
    except (TypeError, ValueError):
        temperature = 0.0

    max_tokens = params.get("max_tokens")
    try:
        max_tokens = (
            _DEFAULT_MAX_TOKENS if max_tokens is None else int(max_tokens)
        )
    except (TypeError, ValueError):
        max_tokens = _DEFAULT_MAX_TOKENS

    messages: list[dict[str, str]] = []
    system = str(params.get("system") or "").strip()
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    client = _get_client(endpoint["base_url"], endpoint["api_key"])

    logger.info(
        "raw_llm model=%s temp=%s max_tokens=%s system=%s prompt_len=%d",
        model,
        temperature,
        max_tokens,
        bool(system),
        len(prompt),
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **_reasoning_off_kwargs(endpoint),
        )
    except Exception as exc:  # noqa: BLE001
        # A timeout is worth naming: it means the model is alive but slow, so
        # a shorter prompt or a smaller model is the fix, not a bug report.
        timed_out = "timeout" in type(exc).__name__.lower()
        logger.warning(
            "raw_llm call failed (%s): %s", type(exc).__name__, exc
        )
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "model": model,
            "timed_out": timed_out,
            "retryable": timed_out,
        }

    choice = response.choices[0] if response.choices else None
    message = getattr(choice, "message", None)
    text = (getattr(message, "content", None) or "").strip()

    if not text:
        # A thinking model that ran out of budget mid-thought answers with an
        # empty `content` and its whole reply sitting in `reasoning`. That is
        # a budget problem with a known fix, not the blank reply it looks
        # like, so it is worth telling apart.
        finish_reason = getattr(choice, "finish_reason", None)
        reasoning = getattr(message, "reasoning", None) or getattr(
            message, "reasoning_content", None
        )
        if reasoning and finish_reason == "length":
            error = (
                f"model spent the whole {max_tokens}-token budget reasoning "
                "and never started its answer -- raise max_tokens, or set "
                "HERMES_REASONING_ENABLED=false to turn thinking off"
            )
        else:
            error = "model returned an empty reply"
        return {
            "success": False,
            "error": error,
            "model": model,
            "finish_reason": finish_reason,
            "reasoning_chars": len(reasoning) if reasoning else 0,
        }

    return {
        "success": True,
        "model": model,
        "response": text,
        # `length` here means the reply hit max_tokens and is cut off -- the
        # caller should say so rather than treat it as a complete answer.
        "finish_reason": getattr(choice, "finish_reason", None),
    }
