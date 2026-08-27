"""
Shared LLM plumbing for the `pressreliz` toolset.

Tools here call the model directly rather than through Hermes, so they do not
inherit the provider profile Hermes applies to the host agent. That profile is
not cosmetic -- it is what stops a thinking model from thinking and what keeps
`max_tokens` off Ollama's truncating default. Rebuilding it per tool is how it
ends up wrong in one of them, so it lives here once, ready for the next tool.

`endpoint()` resolves through `resolve_llm_endpoint()` in
`agents/hermes_host.py`, the single resolver for the whole app: a tool can
never end up pointed at a different provider than the agent that called it.

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

logger = logging.getLogger("hermes.plugin.pressreliz.llm")

# A local 27B model answering a long prompt is slow, but not indefinitely so.
# Without a cap a stalled generation hangs the tool, the agent turn and the
# caller's HTTP request all the way up -- nothing else in the chain sets one.
DEFAULT_TIMEOUT_SECONDS = 120.0

# The OpenAI SDK retries twice by default. Against a local server that is
# usually the wrong trade: a real stall gets multiplied by three before anyone
# is told, and these calls are not idempotent-cheap.
DEFAULT_MAX_RETRIES = 1

# Ollama's OpenAI-compatible endpoint truncates hard when max_tokens is absent,
# so a default is always sent rather than left to the server.
DEFAULT_MAX_TOKENS = 2048

_client_lock = threading.Lock()
_client: Any = None
_client_key: tuple[Any, ...] | None = None


def env_int(name: str, default: int) -> int:
    try:
        raw = (os.getenv(name) or "").strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_first(names: tuple[str, ...]) -> str:
    """First non-empty value among `names`."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


# `RAW_LLM_*` named the first tool in this toolset, which no longer exists.
# The neutral names are what the docs use now; the old ones are still read so
# an existing .env keeps working without an edit.
_TIMEOUT_VARS = ("PRESSRELIZ_LLM_TIMEOUT_SECONDS", "RAW_LLM_TIMEOUT_SECONDS")
_RETRY_VARS = ("PRESSRELIZ_LLM_MAX_RETRIES", "RAW_LLM_MAX_RETRIES")


def ensure_app_on_path() -> None:
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


def endpoint() -> dict[str, Any]:
    """Model, base URL and key for whichever provider `LLM_PROVIDER` selects."""
    ensure_app_on_path()
    from agents.hermes_host import resolve_llm_endpoint

    return resolve_llm_endpoint()


def reasoning_off_kwargs(ep: dict[str, Any]) -> dict[str, Any]:
    """
    The wire flags that stop a thinking model from thinking, or `{}`.

    Against a reasoning model the difference is not cosmetic: the reply lands
    in a separate `reasoning` field and `content` stays empty until thinking
    ends, so a budget that runs out mid-thought returns nothing at all.

    Two flags because no single one covers every local server: Ollama's /v1
    route honours `reasoning_effort` but ignores `think`, and some vLLM builds
    are the other way round. Endpoints recognizing neither ignore both.

    Sent only on a local profile. `hermes_provider` is exactly that marker --
    it is None for OpenAI, whose gpt-4.1 rejects `reasoning_effort` outright.
    Enabled reasoning sends nothing either: thinking is server-default-on for
    these backends, so forcing it back on risks a 400 for no gain.
    """
    if not ep.get("hermes_provider"):
        return {}
    if env_bool("HERMES_REASONING_ENABLED", False):
        return {}
    return {
        "reasoning_effort": "none",
        "extra_body": {"think": False},
    }


def get_client(base_url: str | None, api_key: str) -> Any:
    """
    One OpenAI client, reused across calls and across tools.

    Rebuilt only when the endpoint or its limits change. Building one per call
    would open a fresh connection pool every time and leak it.
    """
    global _client, _client_key
    try:
        timeout = float(_env_first(_TIMEOUT_VARS) or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        retries = int(_env_first(_RETRY_VARS) or DEFAULT_MAX_RETRIES)
    except (TypeError, ValueError):
        retries = DEFAULT_MAX_RETRIES
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
            "pressreliz llm client built base_url=%s timeout=%ss max_retries=%s",
            base_url,
            timeout,
            retries,
        )
        return _client


def complete(
    messages: list[dict[str, str]],
    *,
    ep: dict[str, Any] | None = None,
    model: str = "",
    temperature: float = 0.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """
    One chat completion, with the reply normalized to a plain dict.

    Never raises: the caller is a tool handler whose exception would surface to
    the agent as a stack trace rather than something it can act on.
    """
    ep = ep or endpoint()
    if ep.get("error"):
        return {"success": False, "error": str(ep["error"])}

    model = (model or "").strip() or ep["model"]
    if not model:
        return {
            "success": False,
            "error": f"no model configured ({ep['model_var']} is unset)",
        }

    client = get_client(ep["base_url"], ep["api_key"])
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **reasoning_off_kwargs(ep),
        )
    except Exception as exc:  # noqa: BLE001
        # A timeout is worth naming: it means the model is alive but slow, so
        # a shorter prompt or a smaller model is the fix, not a bug report.
        timed_out = "timeout" in type(exc).__name__.lower()
        logger.warning("llm call failed (%s): %s", type(exc).__name__, exc)
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
    finish_reason = getattr(choice, "finish_reason", None)

    if not text:
        # A thinking model that ran out of budget mid-thought answers with an
        # empty `content` and its whole reply sitting in `reasoning`. That is a
        # budget problem with a known fix, not the blank reply it looks like.
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
        "text": text,
        # `length` here means the reply hit max_tokens and is cut off -- the
        # caller should say so rather than treat it as a complete answer.
        "finish_reason": finish_reason,
    }
