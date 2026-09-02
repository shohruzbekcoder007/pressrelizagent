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
    (Uzbekistan's own regions, cities and districts), some by classifier
    category (trading-partner countries, product groups), some by neither.
    Rather than guess, every row reports whichever of `hudud` and
    `kategoriya` applies and leaves the other null. Passing `hudud` (or
    `kategoriya`) filters to that one instead of returning the whole
    cross-section -- the counterpart to the `manzil` a `pdf_extract` claim
    carries when a release states a per-region figure.

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
#
# The area/category filter sits after both OPTIONAL MATCHes rather than
# folded into them, so a row with no area at all still shows up when neither
# filter was asked for (`$hudud IS NULL` short-circuits the comparison) --
# turning it into a plain MATCH would silently drop every indicator that has
# no area dimension the moment `hudud` is passed.
_AREA_FILTER = (
    "($hudud IS NULL OR apoc.text.replace(toLower(replace(coalesce"
    "(a.name_uz,''),'  ',' ')), '[‘’ʻʼ`´′]', \"'\") CONTAINS $hudud)\n"
    "  AND ($kategoriya IS NULL OR toLower(coalesce(c.name_uz,'')) "
    "CONTAINS $kategoriya)"
)

_RECENT_CYPHER = f"""
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
// A WHERE directly after an OPTIONAL MATCH is that match's own predicate --
// a row failing it still comes back, just with the optional variable forced
// to null, instead of being dropped. The WITH below closes that clause off
// so this WHERE is an ordinary row filter over everything bound so far.
WITH i, unit, p, o, a, c
WHERE {_AREA_FILTER}
RETURN i.id AS id, i.code AS code, i.name_uz AS name, unit,
       p.id AS period, a.name_uz AS area, c.name_uz AS category, o.value AS value
ORDER BY p.year DESC, coalesce(p.sub,'') DESC, area, category
LIMIT $limit
"""

_EXPLICIT_CYPHER = f"""
MATCH (i:StatisticalIndicators) WHERE %s
OPTIONAL MATCH (i)-[:MEASURED_IN]->(u:Unit)
WITH i, u.name AS unit
MATCH (i)<-[:OF_INDICATOR]-(o:Observation)-[:AT_PERIOD]->(p:Period)
WHERE p.id IN $periods
OPTIONAL MATCH (o)-[:FOR_AREA]->(a:Area)
OPTIONAL MATCH (o)-[:FOR_CATEGORY]->(c:Category)
// A WHERE directly after an OPTIONAL MATCH is that match's own predicate --
// a row failing it still comes back, just with the optional variable forced
// to null, instead of being dropped. The WITH below closes that clause off
// so this WHERE is an ordinary row filter over everything bound so far.
WITH i, unit, p, o, a, c
WHERE {_AREA_FILTER}
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

# Run only when a hudud/kategoriya filter matched nothing, to tell "this
# indicator has no such area" apart from "this indicator has no data at all"
# -- and to hand back real spellings from the register instead of a caller
# having to guess a second time.
_AREA_HINT_CYPHER = """
MATCH (i:StatisticalIndicators) WHERE %s
MATCH (i)<-[:OF_INDICATOR]-(:Observation)-[:FOR_AREA]->(a:Area)
RETURN DISTINCT a.name_uz AS v ORDER BY v LIMIT 30
"""
_CATEGORY_HINT_CYPHER = """
MATCH (i:StatisticalIndicators) WHERE %s
MATCH (i)<-[:OF_INDICATOR]-(:Observation)-[:FOR_CATEGORY]->(c:Category)
RETURN DISTINCT c.name_uz AS v ORDER BY v LIMIT 30
"""
# The period ids are the commonest miss of all: a half-year cumulative is
# indexed as `2026-Q2` while a caller reasonably asks for `2026-M06`, and
# without seeing the real ids it retries variations of the wrong guess.
_PERIOD_HINT_CYPHER = """
MATCH (i:StatisticalIndicators) WHERE %s
MATCH (i)<-[:OF_INDICATOR]-(:Observation)-[:AT_PERIOD]->(p:Period)
RETURN DISTINCT p.id AS v, p.year AS y, coalesce(p.sub,'') AS s
ORDER BY y DESC, s DESC LIMIT 12
"""

# The register spells the same apostrophe four different ways and a release a
# fifth; folded to one before the Cypher `CONTAINS` above, or a caller typing
# the plain ASCII apostrophe would match nothing.
_APOSTROPHES = "‘’ʻʼ`´′"


def _norm_area(text: str) -> str:
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    return " ".join(text.split()).strip().lower()


def _key(raw: str) -> tuple[str, Any]:
    """Read one identifier as either a classifier code or a register id."""
    text = str(raw).strip()
    if CODE_RE.match(text):
        return _BY_CODE, text
    try:
        return _BY_ID, int(text)
    except (TypeError, ValueError):
        return "", None


def _fetch(
    session: Any,
    raw: str,
    periods: list[str],
    nper: int,
    limit: int,
    hudud: str,
    kategoriya: str,
):
    where, key = _key(raw)
    if not where:
        return {
            "soralgan": raw,
            "xato": "kod ham, id ham emas (masalan '1.01.01.0009' yoki 582)",
        }

    cypher = (_EXPLICIT_CYPHER if periods else _RECENT_CYPHER) % where
    params: dict[str, Any] = {
        "key": key,
        "limit": limit,
        "hudud": _norm_area(hudud) if hudud else None,
        "kategoriya": kategoriya.strip().lower() if kategoriya else None,
    }
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

        result: dict[str, Any] = {
            "soralgan": raw,
            "id": found["id"],
            "kod": found["code"],
            "nomi": found["name"],
            "qatorlar": [],
        }
        # The indicator exists, so the question is which filter missed. Every
        # requested axis gets its real values from the register handed back --
        # blaming one axis on a guess sent a caller chasing apostrophe
        # variants when the actual miss was a period id, and the misdiagnosis
        # cost a whole turn.
        hinted = []
        if periods:
            davrlar = [
                r["v"] for r in session.run(_PERIOD_HINT_CYPHER % where, key=key)
            ]
            if davrlar:
                result["mavjud_davrlar"] = davrlar
                hinted.append("davr")
        if hudud:
            areas = [
                r["v"] for r in session.run(_AREA_HINT_CYPHER % where, key=key)
            ]
            if areas:
                result["mavjud_hududlar"] = areas
                hinted.append("hudud")
        if kategoriya:
            cats = [
                r["v"]
                for r in session.run(_CATEGORY_HINT_CYPHER % where, key=key)
            ]
            if cats:
                result["mavjud_kategoriyalar"] = cats
                hinted.append("kategoriya")

        if hinted:
            result["izoh"] = (
                f"so'ralgan {'/'.join(hinted)} bo'yicha mos qator topilmadi -- "
                "yuqoridagi mavjud qiymatlardan aynan mosini tanlab qayta "
                "chaqiring. Davr id reyestrdagi bilan bir xil bo'lishi shart: "
                "yarim yillik jami ko'pincha Q2 sifatida indekslanadi, M06 "
                "emas."
            )
        else:
            result["izoh"] = "reyestrda bor, lekin chop etilgan ma'lumoti yo'q"
        return result

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
        "separate indicators. A cumulative span is indexed by the period that "
        "ends it -- a January-June figure usually sits at Q2 of a quarterly "
        "series. When a filtered call matches nothing, the reply lists the "
        "indicator's real period ids (and area/category names): retry with "
        "one of those exactly, do not keep guessing formats. Growth-rate "
        "indicators are published as an index -- 106.7 means 6.7% growth, "
        "not 106.7%.\n"
        "CHECK THE UNIT BEFORE COMPARING. `birlik` is authoritative and units differ "
        "sharply between indicators -- GDP is in mlrd so'm, foreign trade in mln "
        "AQSH dollari, growth in an index, shares in percent. If the figure in the "
        "text only makes sense in a different unit, you have the wrong indicator, "
        "not a wrong figure: go back to `statind_code` instead of reporting a "
        "mismatch.\n"
        "Indicators are cut differently: some by area (Uzbekistan's own "
        "regions, cities and districts), some by classifier category "
        "(trading-partner countries, product groups), some by neither, so "
        "`hudud` and `kategoriya` are filled in only where they apply. A "
        "national total usually appears as its own row rather than being the "
        "sum of the parts -- do not add rows up unless you have checked what "
        "they are.\n"
        "PASS `hudud` FOR A CLAIM ABOUT ONE REGION. Without it you get the "
        "whole cross-section and have to find the right row yourself; a claim "
        "about Andijon compared against the republic total (or a different "
        "region entirely) is a wrong comparison even when the indicator and "
        "period are right. `pdf_extract` supplies this directly as a claim's "
        "`manzil` -- pass it through unchanged. Spelling is forgiving (case, "
        "apostrophe style, extra spaces) but the region word matters -- "
        "'Andijon' alone is ambiguous between the region, its city and its "
        "district, so keep the full phrase ('Andijon viloyati'). A filter "
        "that matches nothing returns the indicator's real area names instead "
        "of an empty list, so a misspelling is fixable without a second "
        "guess. `kategoriya` works the same way for trading partners.\n"
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
            "hudud": {
                "type": "string",
                "description": (
                    "Restrict to one Uzbek region, city or district, e.g. "
                    "'Andijon viloyati', 'Toshkent shahri', 'O'zbekiston "
                    "Respublikasi' for the nationwide row. This is the same "
                    "value `pdf_extract` returns as a claim's `manzil` -- pass "
                    "it straight through. Applies to every indicator in this "
                    "call, so group indicators by the area they share."
                ),
            },
            "kategoriya": {
                "type": "string",
                "description": (
                    "Restrict to one classifier category -- for most "
                    "indicators this is a trading-partner country, e.g. "
                    "'Rossiya'. Applies to every indicator in this call."
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

    hudud = str(params.get("hudud") or "").strip()
    kategoriya = str(params.get("kategoriya") or "").strip()

    try:
        with _session() as session:
            results = [
                _fetch(session, name, periods, nper, limit, hudud, kategoriya)
                for name in wanted
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
