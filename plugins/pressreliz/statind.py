"""
`statind_code` — the nearest rows of the Uzbek statistical indicator register.

The register is 3326 rows of `id | code | name | level | period`, held in Neo4j
with a fulltext index over Uzbek latin, Uzbek cyrillic and Russian names plus
the classifier path. This tool searches it and hands back the closest matches.
It does **not** decide which one is right: the caller sees the candidates and
picks, because the caller has the surrounding conversation and this tool does
not.

That division matters for correctness, not just tidiness. Classifier codes are
regular enough (`1.01.01.0001`) that a language model will invent a plausible
one; every code leaving this tool was read out of the register a moment
earlier, so there is nothing here to invent. Passing a code back in (see
`indicators`) looks it up exactly, which is how a code quoted from elsewhere
gets checked.

Retrieval rather than a prompt-stuffed register is a measured choice: the flat
classifier file is 146,710 tokens, ~52s on a cold cache and over half the
model's 262k context on every call. A five-row shortlist is a few hundred.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

logger = logging.getLogger("hermes.plugin.pressreliz.statind")

TOOL_NAME = "statind_code"
TOOLSET_NAME = "pressreliz"

# One Neo4j lookup per requested name, so a long list multiplies the work.
# Twenty is far past what a press release cites at once.
_MAX_INDICATORS = 20
_DEFAULT_CANDIDATES = 5
_MAX_CANDIDATES = 25

_driver_lock = threading.Lock()
_driver: Any = None
_driver_key: tuple[str, str, str] | None = None

# Classifier codes are dotted numeric groups: `1.01`, `1.01.01`, `1.01.01.0001`.
# A caller who already has one wants that exact row, not a search.
_CODE_RE = re.compile(r"^\d{1,2}(?:\.\d{2}){1,2}(?:\.\d{4})?$")

# Lucene reserved characters. Indicator names are full of parentheses and
# hyphens, so an unescaped name is a query syntax error rather than a search.
_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def _neo4j_driver() -> Any:
    """One driver for the process, rebuilt only if the target moves."""
    global _driver, _driver_key
    import os

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
        logger.info("statind: neo4j driver built uri=%s", uri)
        return _driver


def _database() -> str:
    import os

    return os.getenv("NEO4J_DATABASE") or "neo4j"


def _lucene_escape(text: str) -> str:
    """Escape a free-text name so Lucene reads it as words, not syntax."""
    return "".join("\\" + ch if ch in _LUCENE_SPECIAL else ch for ch in text)


def _strip_figures(text: str) -> str:
    """
    Drop tokens that start with a digit.

    A name quoted from a press release drags its numbers along -- "2024-yilda
    ... 8,5 foizga oshdi". Those score against the register just like the real
    words do, and the register contains rows whose *names* carry years
    ("Chakana savdo hajmi (2023-2024yy, oylik)"), so the year in the sentence
    pulls the whole shortlist onto the 2023-2024 rows and the right row never
    surfaces.

    Only *leading* digits qualify. Codes like `XSST-2008` and `MIIT-2018` are
    part of an indicator's identity and start with a letter, so they survive;
    `2024-yilda`, `8,5` and `100` do not. If a query is nothing but figures the
    original is kept, since an empty query finds nothing at all.
    """
    kept = [w for w in text.split() if not w[:1].isdigit()]
    return " ".join(kept) if kept else text


# `id` is the register's own key, wanted by callers that go on to query the
# graph directly. `name_uz` is the authoritative label -- `name` on these nodes
# is the English translation, which is not what a press release quotes.
_RETURN = """
    node.id      AS id,
    node.code    AS code,
    node.name_uz AS name,
    node.path_uz AS path,
    node.level   AS level,
    node.period  AS period
"""

_SEARCH_CYPHER = f"""
CALL db.index.fulltext.queryNodes('indicator_fulltext', $q) YIELD node, score
RETURN {_RETURN}, score
ORDER BY score DESC
LIMIT $limit
"""

_BY_CODE_CYPHER = f"""
MATCH (node:StatisticalIndicators {{code: $code}})
RETURN {_RETURN}, 1.0 AS score
LIMIT 1
"""


def _row(record: Any) -> dict[str, Any]:
    score = record["score"]
    return {
        "id": record["id"],
        "kod": record["code"],
        "nomi": record["name"],
        "yol": record["path"],
        "daraja": record["level"],
        "davriylik": record["period"],
        # Lucene's raw score. Meaningless on its own, useful only for comparing
        # the rows of one search against each other.
        "moslik": round(float(score), 2) if score is not None else None,
    }


def _search(session: Any, name: str, limit: int) -> list[dict[str, Any]]:
    """The closest register rows for one requested name, best first."""
    if _CODE_RE.match(name.strip()):
        result = session.run(_BY_CODE_CYPHER, code=name.strip())
        return [_row(r) for r in result]

    # No explicit operator: Lucene's default OR ranks by how many words of the
    # name a row matches, which is exactly the behaviour wanted here. The
    # standard analyzer splits on the apostrophe too, so the four spellings of
    # o'/o‘/oʻ/oʼ in this register all reduce to the same tokens.
    query = _lucene_escape(_strip_figures(name.strip()))
    if not query:
        return []
    result = session.run(_SEARCH_CYPHER, q=query, limit=limit)
    return [_row(r) for r in result]


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Search the Uzbek statistical indicator register and return the "
        f"closest {_DEFAULT_CANDIDATES} rows for each name given, with their "
        "id, classifier code, official name, classifier path, level and "
        "periodicity. Uzbek latin, Uzbek cyrillic and Russian names are all "
        "indexed, so a Russian query finds the Uzbek row.\n"
        "THIS TOOL DOES NOT CHOOSE. It returns candidates read straight out of "
        "the register; you decide which one the text meant, and you may decide "
        "none of them fit. Never quote a code that is not among the rows "
        "returned -- if none match, say so or search again with different "
        "wording.\n"
        "PASS THE INDICATOR NAME, NOT THE SENTENCE IT CAME FROM. The search is "
        "keyword-based, so surrounding prose dilutes it: from '2024-yilda "
        "chakana savdo hajmi 8,5 foizga oshdi' search 'Chakana savdo hajmi "
        "o'sish sur'ati'. Expand abbreviations yourself -- 'YaIM' finds "
        "nothing, 'Yalpi ichki mahsulot' finds the right rows. Use dictionary "
        "forms: Uzbek case endings are not stemmed, so 'mahsulotning' does not "
        "match the register's 'mahsulot'. If the text says something grew by a "
        "percentage, the indicator is usually the growth rate (o'sish sur'ati) "
        "rather than the volume (hajmi).\n"
        "Periodicity (yillik / oylik / choraklik) and cross-sections "
        "(hududlar / mamlakatlar / qit'alar kesimida) are SEPARATE indicators "
        "with separate codes. The same indicator can also appear in more than "
        "one section of the register, so compare the `yol` (path) field before "
        "choosing; if the text does not say which was meant, present the "
        "alternatives rather than picking one.\n"
        "Passing a classifier code instead of a name returns that exact row, "
        "which is how a code quoted from elsewhere gets verified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "indicators": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Indicator names to search for, each one a name and "
                    "nothing else -- not the sentence it was quoted from, and "
                    "with abbreviations spelled out. A classifier code (e.g. "
                    "'1.01.01.0001') may be passed instead to look up that "
                    f"exact row. At most {_MAX_INDICATORS} per call."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Rows to return per name. Defaults to "
                    f"{_DEFAULT_CANDIDATES}, maximum {_MAX_CANDIDATES}. Raise "
                    "it when the wording is vague or the first search missed."
                ),
            },
        },
        "required": ["indicators"],
    },
}


def statind_code_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Search the register. Returns a dict; the plugin serializes it."""
    raw = (params or {}).get("indicators")
    if isinstance(raw, str):
        # A single name passed unwrapped is the likeliest caller mistake and
        # costs nothing to accept.
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return {"success": False, "error": "indicators must be a non-empty list"}

    names = [str(n).strip() for n in raw if str(n or "").strip()]
    if not names:
        return {"success": False, "error": "indicators must contain a name"}
    if len(names) > _MAX_INDICATORS:
        return {
            "success": False,
            "error": (
                f"{len(names)} indicators requested; at most {_MAX_INDICATORS} "
                "per call -- split the list"
            ),
        }

    try:
        limit = int(params.get("limit") or _DEFAULT_CANDIDATES)
    except (TypeError, ValueError):
        limit = _DEFAULT_CANDIDATES
    limit = max(1, min(limit, _MAX_CANDIDATES))

    try:
        driver = _neo4j_driver()
        with driver.session(database=_database()) as session:
            results = [
                {"soralgan": name, "nomzodlar": _search(session, name, limit)}
                for name in names
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("statind: neo4j search failed: %s", exc)
        return {
            "success": False,
            "error": f"indicator register unreachable ({type(exc).__name__}: {exc})",
            "retryable": True,
        }

    for entry in results:
        entry["topildi"] = len(entry["nomzodlar"])

    empty = [e["soralgan"] for e in results if not e["nomzodlar"]]
    logger.info(
        "statind_code: %d searched, %d with no match", len(results), len(empty)
    )
    out: dict[str, Any] = {
        "success": True,
        "natijalar": results,
        "jami": len(results),
    }
    if empty:
        # Named explicitly so the caller does not have to scan the list to
        # notice a search that came back empty.
        out["topilmadi"] = empty
    return out
