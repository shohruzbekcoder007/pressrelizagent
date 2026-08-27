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
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("hermes.plugin.pressreliz")


def register(ctx: Any) -> None:
    """Called once by the Hermes plugin loader."""
    # Relative, not absolute: the loader execs this file under a generated
    # module name with `submodule_search_locations` pointing at the plugin
    # directory, so the package is never importable as `plugins.pressreliz`
    # and the directory is not on sys.path either.
    try:
        from .raw_llm import (
            TOOL_NAME,
            TOOL_SCHEMA,
            TOOLSET_NAME,
            raw_llm_handler,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("pressreliz: cannot import raw_llm: %s", exc)
        return

    def _handle(params: dict, **kwargs: Any) -> str:
        del kwargs
        try:
            result = raw_llm_handler(params or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("raw_llm failed")
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(result, ensure_ascii=False)

    try:
        ctx.register_tool(
            name=TOOL_NAME,
            toolset=TOOLSET_NAME,
            schema=TOOL_SCHEMA,
            handler=_handle,
            description=TOOL_SCHEMA.get("description", TOOL_NAME),
            emoji="🧠",
        )
        logger.info("pressreliz: registered %s (toolset=%s)", TOOL_NAME, TOOLSET_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.error("pressreliz: register_tool failed: %s", exc)
