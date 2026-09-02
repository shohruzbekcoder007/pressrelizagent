"""
Hermes plugin: tahrir

Registers the `tahrir` toolset -- the editorial side of a review. Where the
`pressreliz` tools ask whether a figure is true, this one asks whether the
sentence around it is sound: spelling, internal consistency, and the logic
faults that survive a correct number.

Its own plugin for the same reason the others are: it shares no dependency
with them. It needs neither Neo4j nor the converter service, so it keeps
working when either is down -- and a release can be read for language
problems before a single figure has been checked.

Loading requires three things to line up:
  * this directory copied to $HERMES_HOME/plugins/ (scripts/start.sh)
  * `tahrir` listed under plugins.enabled in config.yaml -- standalone
    plugins are opt-in
  * `tahrir` listed in HERMES_ENABLED_TOOLSETS

Tools:
  * `text_check` — spelling, consistency and logic faults in a release

Meant to run early and beside the figure check rather than after it: a
release is reviewed once, and the wording problems and the wrong numbers
belong in the same reply.
"""



from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("hermes.plugin.tahrir")

TOOLSET_NAME = "tahrir"

# (module, schema attribute, handler attribute, emoji)
_TOOLS = [
    ("check", "TOOL_SCHEMA", "text_check_handler", "📝"),
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
            # `plugins.tahrir` and the directory is not on sys.path either.
            module = import_module(f".{module_name}", __package__)
            schema = getattr(module, schema_attr)
            fn = getattr(module, handler_attr)
        except Exception as exc:  # noqa: BLE001
            logger.error("tahrir: cannot import %s: %s", module_name, exc)
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
            logger.info("tahrir: registered %s", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("pdfmd: register_tool(%s) failed: %s", name, exc)

    if not registered:
        logger.error("tahrir: no tools registered")
