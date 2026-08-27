"""
Hermes plugin: pressreliz

Registers the `pressreliz` toolset. Hermes' own `AIAgent` has no parameter for
injecting Python tools, so a plugin calling `ctx.register_tool()` is the only
way a custom tool reaches the real backend -- LangChain `@tool` functions only
ever reach the `hermes_lite` fallback.

Loading requires three things to line up:
  * this directory copied to $HERMES_HOME/plugins/ (scripts/start.sh)
  * `pressreliz` listed under plugins.enabled in config.yaml -- standalone
    plugins are opt-in
  * `pressreliz` listed in HERMES_ENABLED_TOOLSETS

Tools:
  * `statind_code` — plain-language indicator name -> statistical classifier code

The list is deliberately a list: adding a tool is one entry in `_TOOLS`, and a
tool that fails to import is logged and skipped rather than taking the whole
toolset down with it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("hermes.plugin.pressreliz")

TOOLSET_NAME = "pressreliz"

# (module, schema attribute, handler attribute, emoji)
_TOOLS = [
    ("statind", "TOOL_SCHEMA", "statind_code_handler", "🔢"),
]


def _make_handler(name: str, fn: Any) -> Any:
    """Wrap a tool function so it always answers with a JSON string."""

    def _handle(params: dict, **kwargs: Any) -> str:
        del kwargs
        try:
            result = fn(params or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s failed", name)
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(result, ensure_ascii=False)

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
            # `plugins.pressreliz` and the directory is not on sys.path either.
            module = import_module(f".{module_name}", __package__)
            schema = getattr(module, schema_attr)
            fn = getattr(module, handler_attr)
        except Exception as exc:  # noqa: BLE001
            logger.error("pressreliz: cannot import %s: %s", module_name, exc)
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
            logger.info("pressreliz: registered %s", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("pressreliz: register_tool(%s) failed: %s", name, exc)

    if not registered:
        logger.error("pressreliz: no tools registered")
