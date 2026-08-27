"""
Shared Neo4j access for the `pressreliz` toolset.

One driver per process, reused by every tool. A driver owns a connection pool,
so building one per call leaks pools; building one per tool multiplies them for
no reason, since every tool here talks to the same database.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger("hermes.plugin.pressreliz.neo4j")

# Classifier codes are dotted numeric groups: `1.01`, `1.01.01`,
# `1.01.01.0001`. Tools that accept "a code or an id" tell them apart with
# this, so it lives here rather than in whichever tool needed it first.
CODE_RE = re.compile(r"^\d{1,2}(?:\.\d{2}){1,2}(?:\.\d{4})?$")

_driver_lock = threading.Lock()
_driver: Any = None
_driver_key: tuple[str, str, str] | None = None


def driver() -> Any:
    """The shared driver, rebuilt only if the target moves."""
    global _driver, _driver_key

    uri = os.getenv("NEO4J_URI") or "bolt://neo4j:7687"
    user = os.getenv("NEO4J_USER") or "neo4j"
    password = os.getenv("NEO4J_PASSWORD") or ""
    key = (uri, user, password)
    with _driver_lock:
        if _driver is not None and _driver_key == key:
            return _driver
        from neo4j import GraphDatabase

        if _driver is not None:
            try:
                _driver.close()
            except Exception:  # noqa: BLE001
                pass
        _driver = GraphDatabase.driver(uri, auth=(user, password))
        _driver_key = key
        logger.info("pressreliz: neo4j driver built uri=%s", uri)
        return _driver


def database() -> str:
    return os.getenv("NEO4J_DATABASE") or "neo4j"


def session() -> Any:
    """A session on the configured database, for use as a context manager."""
    return driver().session(database=database())
