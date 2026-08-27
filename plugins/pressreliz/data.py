"""
`statind_data` — the published values behind an indicator, from the graph.

The register in Neo4j carries 2.2M `Observation` nodes: one measured value per
indicator × period × area × classifier category, with the unit attached. So a
figure quoted in a press release can be checked against the official series
without leaving the graph -- which is the whole point of this toolset. A claim
of "6,5 foizga o'sdi" is either confirmed or contradicted by a number here.

Two things shape the interface:

  * **Volume.** Observations per indicator run from 4 to 19,890 (median 180).
    Handing an agent every value would bury the turn, so the default is the
    five most recent periods and there is a hard row cap on top of that.
  * **Shape.** Different indicators are cut differently -- some by area
    (regions, countries), some by classifier category (continents, product
    groups), some by neither. Rather than guess, every row reports whichever
    of `hudud` and `kategoriya` applies and leaves the other null.

Coverage is not total: 3179 of the register's 3326 rows have observations, so
an indicator with no published series is a real answer rather than an error.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
from typing import Any

from ._neo4j import CODE_RE, session as _session

logger = logging.getLogger("hermes.plugin.pressreliz.data")

TOOL_NAME = "statind_data"
TOOLSET_NAME = "pressreliz"

# Each indicator can bring back a full cross-section per period, so a handful
# per call is already a large reply.
_MAX_INDICATORS = 5
_DEFAULT_PERIODS = 5
_MAX_PERIODS = 20
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 500

# `collect(DISTINCT p)` keeps the incoming order, so the ORDER BY above it is
# what makes the slice "the most recent periods". Annual rows have no `sub`,
# hence the coalesce.
_RECENT_CYPHER = """
MATCH (i:StatisticalIndicators) WHERE %s
OPTIONAL MATCH (i)-[:MEASURED_IN]->(u:Unit)
WITH i, u.name AS unit
MATCH (i)<-[:OF_INDICATOR]-(:Observation)-[:AT_PERIOD]->(p:Period)
WITH i, unit, p ORDER BY p.year DESC, coalesce(p.sub,'') DESC
WITH i, unit, collect(DISTINCT p)[0..$nper] AS periods
UNWIND periods AS p
MATCH (i)<-[:OF_INDICATOR]-(o:Observation)-[:AT_PERIOD]->(p)
OPTIONAL MATCH (o)-[:FOR_AREA]->(a:Area)
OPTIONAL MATCH (o)-[:FOR_CATEGORY]->(c:Category)
RETURN i.id AS id, i.code AS code, i.name_uz AS name, unit,
       p.id AS period, a.name_uz AS area, c.name_uz AS category, o.value AS value
ORDER BY p.year DESC, coalesce(p.sub,'') DESC, area, category
LIMIT $limit
"""

_EXPLICIT_CYPHER = """
MATCH (i:StatisticalIndicators) WHERE %s
OPTIONAL MATCH (i)-[:MEASURED_IN]->(u:Unit)
WITH i, u.name AS unit
MATCH (i)<-[:OF_INDICATOR]-(o:Observation)-[:AT_PERIOD]->(p:Period)
WHERE p.id IN $periods
OPTIONAL MATCH (o)-[:FOR_AREA]->(a:Area)
OPTIONAL MATCH (o)-[:FOR_CATEGORY]->(c:Category)
RETURN i.id AS id, i.code AS code, i.name_uz AS name, unit,
       p.id AS period, a.name_uz AS area, c.name_uz AS category, o.value AS value
ORDER BY p.year DESC, coalesce(p.sub,'') DESC, area, category
LIMIT $limit
"""

# The register is reachable by either key, and the caller has whichever
# `statind_code` gave them.
_BY_CODE = "i.code = $key"
_BY_ID = "i.id = $key"

_NAME_ONLY_CYPHER = """
MATCH (i:StatisticalIndicators) WHERE %s
RETURN i.id AS id, i.code AS code, i.name_uz AS name
LIMIT 1
"""


def _key(raw: str) -> tuple[str, Any]:
    """Read one identifier as either a classifier code or a register id."""
    text = str(raw).strip()
    if CODE_RE.match(text):
        return _BY_CODE, text
    try:
        return _BY_ID, int(text)
    except (TypeError, ValueError):
        return "", None


def _fetch(session: Any, raw: str, periods: list[str], nper: int, limit: int):
    where, key = _key(raw)
    if not where:
        return {
            "soralgan": raw,
            "xato": "kod ham, id ham emas (masalan '1.01.01.0009' yoki 582)",
        }

    cypher = (_EXPLICIT_CYPHER if periods else _RECENT_CYPHER) % where
    params: dict[str, Any] = {"key": key, "limit": limit}
    if periods:
        params["periods"] = periods
    else:
        params["nper"] = nper

    rows: list[dict[str, Any]] = []
    head: dict[str, Any] = {}
    for record in session.run(cypher, **params):
        if not head:
            head = {
                "id": record["id"],
                "kod": record["code"],
                "nomi": record["name"],
                "birlik": record["unit"],
            }
        rows.append(
            {
                "davr": record["period"],
                "hudud": record["area"],
                "kategoriya": record["category"],
                "qiymat": record["value"],
            }
        )

    if not head:
        # No observations came back. Separate "indicator does not exist" from
        # "indicator exists but has no published series" -- 147 rows of the
        # register are in the second case, and that is an answer, not a fault.
        found = session.run(_NAME_ONLY_CYPHER % where, key=key).single()
        if found is None:
            return {"soralgan": raw, "xato": "reyestrda bunday kod/id yo'q"}
        return {
            "soralgan": raw,
            "id": found["id"],
            "kod": found["code"],
            "nomi": found["name"],
            "qatorlar": [],
            "izoh": "reyestrda bor, lekin chop etilgan ma'lumoti yo'q",
        }

    out: dict[str, Any] = {"soralgan": raw, **head}
    out["davrlar"] = sorted({r["davr"] for r in rows}, reverse=True)
    out["qatorlar"] = rows
    out["jami_qator"] = len(rows)
    if len(rows) == limit:
        # Hit the cap, so the series is cut off rather than complete. Saying so
        # stops the caller reading "no 2021 row" as "no 2021 data".
        out["qisqartirildi"] = (
            f"{limit} qator chegarasiga yetildi -- davrni toraytiring yoki "
            "limitni oshiring"
        )
    return out


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Read the officially published values for one or more statistical "
        "indicators straight from the register. Use this to CHECK A NUMBER a "
        "press release states, or to get the figure when the text does not "
        "give one. Every row carries its period, its area and/or classifier "
        "category, and the indicator's unit.\n"
        "Identify an indicator by its classifier code ('1.01.01.0009') or its "
        "register id (582), both of which `statind_code` returns.\n"
        f"By default the {_DEFAULT_PERIODS} most recent periods come back, "
        "newest first. Pass `periods` for specific ones ('2024', '2025-Q2', "
        "'2024-M03'); annual, quarterly and monthly series all exist and are "
        "separate indicators. Growth-rate indicators are published as an index "
        "-- 106.7 means 6.7% growth, not 106.7%.\n"
        "CHECK THE UNIT BEFORE COMPARING. `birlik` is authoritative and units differ "
        "sharply between indicators -- GDP is in mlrd so'm, foreign trade in mln "
        "AQSH dollari, growth in an index, shares in percent. If the figure in the "
        "text only makes sense in a different unit, you have the wrong indicator, "
        "not a wrong figure: go back to `statind_code` instead of reporting a "
        "mismatch.\n"
        "Indicators are cut differently: some by area (regions, countries), "
        "some by classifier category (continents, product groups), some by "
        "neither, so `hudud` and `kategoriya` are filled in only where they "
        "apply. A national total usually appears as its own row rather than "
        "being the sum of the parts -- do not add rows up unless you have "
        "checked what they are.\n"
        "147 of the register's rows have no published series; that comes back "
        "as an empty `qatorlar` with a note, which is an answer and not a "
        "failure."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "indicators": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Classifier codes or register ids, as returned by "
                    f"`statind_code`. At most {_MAX_INDICATORS} per call."
                ),
            },
            "periods": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific periods, e.g. ['2024','2025'] or ['2025-Q1'] or "
                    "['2024-M03']. Omit to get the most recent ones."
                ),
            },
            "recent": {
                "type": "integer",
                "description": (
                    f"How many recent periods when `periods` is omitted. "
                    f"Defaults to {_DEFAULT_PERIODS}, maximum {_MAX_PERIODS}."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Row cap per indicator. Defaults to {_DEFAULT_LIMIT}, "
                    f"maximum {_MAX_LIMIT}. A wide cross-section over several "
                    "periods reaches it quickly."
                ),
            },
        },
        "required": ["indicators"],
    },
}


def statind_data_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Read published values. Returns a dict; the plugin serializes it."""
    raw = (params or {}).get("indicators")
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return {"success": False, "error": "indicators must be a non-empty list"}

    wanted = [str(n).strip() for n in raw if str(n or "").strip()]
    if not wanted:
        return {"success": False, "error": "indicators must contain a code or id"}
    if len(wanted) > _MAX_INDICATORS:
        return {
            "success": False,
            "error": (
                f"{len(wanted)} indicators requested; at most "
                f"{_MAX_INDICATORS} per call -- split the list"
            ),
        }

    periods_raw = params.get("periods")
    if isinstance(periods_raw, str):
        periods_raw = [periods_raw]
    periods = (
        [str(p).strip() for p in periods_raw if str(p or "").strip()]
        if isinstance(periods_raw, list)
        else []
    )

    try:
        nper = int(params.get("recent") or _DEFAULT_PERIODS)
    except (TypeError, ValueError):
        nper = _DEFAULT_PERIODS
    nper = max(1, min(nper, _MAX_PERIODS))

    try:
        limit = int(params.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    try:
        with _session() as session:
            results = [
                _fetch(session, name, periods, nper, limit) for name in wanted
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("statind_data: query failed: %s", exc)
        return {
            "success": False,
            "error": f"register unreachable ({type(exc).__name__}: {exc})",
            "retryable": True,
        }

    total = sum(len(r.get("qatorlar") or []) for r in results)
    logger.info(
        "statind_data: %d indicators, %d rows, periods=%s",
        len(results),
        total,
        periods or f"recent {nper}",
    )
    return {
        "success": True,
        "natijalar": results,
        "jami_qator": total,
    }
