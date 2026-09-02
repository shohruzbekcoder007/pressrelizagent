"""
Hermes plugin: telegram

Registers the `telegram` toolset -- one tool that turns a finished draft into
something ready to paste into Telegram, and checks its figures on the way out.

Its own plugin rather than a tool inside `pressreliz`, for the same reason
`pdfmd` is: it shares no dependency with the others. `pressreliz` needs Neo4j,
`pdfmd` needs the converter service and the data mount; this one needs
nothing, and so keeps working when either of those is down -- which is
exactly when someone still wants to publish what has already been verified.

Loading requires three things to line up:
  * this directory copied to $HERMES_HOME/plugins/ (scripts/start.sh)
  * `telegram` listed under plugins.enabled in config.yaml -- standalone
    plugins are opt-in
  * `telegram` listed in HERMES_ENABLED_TOOLSETS

Tools:
  * `telegram_post` — a draft -> paste-ready text, plus an unverified-figure check

It comes last in the chain: `pdf_to_md` -> `pdf_extract` -> `statind_code` ->
`statind_data` -> `telegram_post`. The figures it is handed to check against
are the ones `statind_data` returned.
"""


from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("hermes.plugin.telegram")

TOOLSET_NAME = "telegram"

# (module, schema attribute, handler attribute, emoji)
_TOOLS = [
    ("post", "TOOL_SCHEMA", "telegram_post_handler", "✈️"),
]


# --------------------------------------------------------------------------
# Identical-call guard
# --------------------------------------------------------------------------
# Scoped to one turn. A model working through a long document re-issues
# calls it has already
# made: one measured turn spent 8 of its 25 tool calls on `pdf_extract` and
# 5 on `pdf_to_md`, all with arguments seen before. Every repeat costs a full
# model round trip, and the iteration budget runs out before the checking
# does.
#
# The repeat is answered from cache -- same arguments, same answer, so this
# cannot change what the model sees -- with one field added saying it has
# been here before. That field is the point: the tool result is the only
# channel back to the model mid-turn, and without it nothing tells it that
# the call it just made changed nothing.
_REPEAT_TTL_SECONDS = 600.0
_REPEAT_MAX = 64
_repeat_lock = threading.Lock()
_repeat_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()


def _repeat_key(task_id: str, name: str, params: dict) -> str:
    """
    Identify one exact call within one turn.

    Keyed by `task_id` -- Hermes mints a fresh one per turn -- so the guard
    only ever sees repetition inside a single turn's tool loop, which is the
    loop worth breaking. A later turn asking the same question legitimately
    re-does the work and must not be told it already has the answer. Without
    a task_id there is no turn to scope to, so the guard stands aside.
    """
    if not task_id:
        return ""
    try:
        canonical = json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        # Unserialisable arguments cannot be compared, so they are never
        # treated as repeats rather than being wrongly matched.
        return ""
    return f"{task_id}\x00{name}\x00{canonical}"


def _repeat_lookup(key: str) -> "str | None":
    if not key:
        return None
    now = time.time()
    with _repeat_lock:
        hit = _repeat_cache.get(key)
        if hit is None:
            return None
        stored_at, payload = hit
        if now - stored_at > _REPEAT_TTL_SECONDS:
            _repeat_cache.pop(key, None)
            return None
        _repeat_cache.move_to_end(key)
        return payload


def _repeat_store(key: str, payload: str) -> None:
    if not key:
        return
    with _repeat_lock:
        _repeat_cache[key] = (time.time(), payload)
        _repeat_cache.move_to_end(key)
        while len(_repeat_cache) > _REPEAT_MAX:
            _repeat_cache.popitem(last=False)


def _mark_repeat(payload: str, name: str) -> str:
    """Re-serialise a cached reply with a note that it is a repeat."""
    try:
        data = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(data, dict):
        return payload
    data["takroriy"] = (
        f"DIQQAT: `{name}` ayni shu argumentlar bilan allaqachon chaqirilgan "
        "va natija o'zgarmadi. Uni yana takrorlamang -- argumentlarni "
        "o'zgartiring (masalan `offset`), boshqa toolga o'ting yoki "
        "hozirgacha to'plangan ma'lumot asosida javob yozing."
    )
    return json.dumps(data, ensure_ascii=False)


def _make_handler(name: str, fn: Any) -> Any:
    """Wrap a tool function so it always answers with a JSON string."""

    def _handle(params: dict, **kwargs: Any) -> str:
        key = _repeat_key(str(kwargs.get("task_id") or ""), name, params)
        cached = _repeat_lookup(key)
        if cached is not None:
            logger.info("%s: identical call repeated, answered from cache", name)
            return _mark_repeat(cached, name)
        try:
            result = fn(params or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s failed", name)
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        payload = json.dumps(result, ensure_ascii=False)
        # Only successful calls are cached: a failure often reflects something
        # transient (the register down, the converter restarting), and
        # replaying it would keep the caller from ever seeing it recover.
        if isinstance(result, dict) and result.get("success"):
            _repeat_store(key, payload)
        return payload

    return _handle


def register(ctx: Any) -> None:
    """Called once by the Hermes plugin loader."""
    from importlib import import_module

    registered = 0
    for module_name, schema_attr, handler_attr, emoji in _TOOLS:
        try:
            # Relative, not absolute: the loader execs this file under a
            # generated module name with `submodule_search_locations` pointing
            # at the plugin directory, so the package is never importable as
            # `plugins.telegram` and the directory is not on sys.path either.
            module = import_module(f".{module_name}", __package__)
            schema = getattr(module, schema_attr)
            fn = getattr(module, handler_attr)
        except Exception as exc:  # noqa: BLE001
            logger.error("telegram: cannot import %s: %s", module_name, exc)
            continue

        name = schema["name"]
        try:
            ctx.register_tool(
                name=name,
                toolset=TOOLSET_NAME,
                schema=schema,
                handler=_make_handler(name, fn),
                description=schema.get("description", name),
                emoji=emoji,
            )
            registered += 1
            logger.info("telegram: registered %s", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("pdfmd: register_tool(%s) failed: %s", name, exc)

    if not registered:
        logger.error("telegram: no tools registered")
