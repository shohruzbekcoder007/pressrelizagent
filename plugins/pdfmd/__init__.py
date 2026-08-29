"""
Hermes plugin: pdfmd

Registers the `pdfmd` toolset -- a press release arriving as a PDF, turned
into Markdown and then into claims the `pressreliz` tools can check against
the register. The two toolsets are deliberately separate plugins: one talks to
Neo4j and the other to the filesystem, they fail for unrelated reasons, and
either can be switched off without the other.

Loading requires three things to line up:
  * this directory copied to $HERMES_HOME/plugins/ (scripts/start.sh)
  * `pdfmd` listed under plugins.enabled in config.yaml -- standalone plugins
    are opt-in
  * `pdfmd` listed in HERMES_ENABLED_TOOLSETS

Tools:
  * `pdf_to_md`   — PDF in data/pdf/ -> Markdown in data/md/, plus a preview
  * `pdf_extract` — that Markdown -> the checkable claims inside it

The intended chain is `pdf_to_md` -> `pdf_extract` -> `statind_code` ->
`statind_data`: convert, find what the release asserts, find the indicator,
compare the figure.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("hermes.plugin.pdfmd")

TOOLSET_NAME = "pdfmd"

# (module, schema attribute, handler attribute, emoji)
_TOOLS = [
    ("convert", "TOOL_SCHEMA", "pdf_to_md_handler", "📄"),
    ("extract", "TOOL_SCHEMA", "pdf_extract_handler", "🔍"),
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
            # `plugins.pdfmd` and the directory is not on sys.path either.
            module = import_module(f".{module_name}", __package__)
            schema = getattr(module, schema_attr)
            fn = getattr(module, handler_attr)
        except Exception as exc:  # noqa: BLE001
            logger.error("pdfmd: cannot import %s: %s", module_name, exc)
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
            logger.info("pdfmd: registered %s", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("pdfmd: register_tool(%s) failed: %s", name, exc)

    if not registered:
        logger.error("pdfmd: no tools registered")
