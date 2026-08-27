"""
`statind_data_url` — register id -> the indicator's SDMX data file URL.

siat.stat.uz publishes each indicator's time series as a JSON file named after
the register id, so the URL is a pure function of the id:

    https://siat.stat.uz/media/uploads/sdmx/sdmx_data_<id>.json

The tool builds those URLs and does not fetch them: what to do with the data is
the caller's decision, and a tool that silently downloads several megabytes of
time series into a conversation is worse than one that hands over links.

Each id is looked up in the register first. A URL is a plausible-looking string
whether or not the id exists, so an id the model invented would otherwise turn
into a dead link that reads like a citation -- the same failure `statind_code`
is built to prevent. Verification returns the code and name too, so the caller
can see it is pointing at the indicator it meant. Ids come from `statind_code`;
that chain is the intended way in.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
from typing import Any

from ._neo4j import session as _session

logger = logging.getLogger("hermes.plugin.pressreliz.sdmx")

TOOL_NAME = "statind_data_url"
TOOLSET_NAME = "pressreliz"

_URL_TEMPLATE = "https://siat.stat.uz/media/uploads/sdmx/sdmx_data_{id}.json"

# Each URL is one line of output, so the cap is about keeping a reply readable
# rather than about cost.
_MAX_IDS = 25

_BY_ID_CYPHER = """
UNWIND $ids AS wanted
MATCH (n:StatisticalIndicators {id: wanted})
RETURN n.id AS id, n.code AS code, n.name_uz AS name, n.period AS period,
       n.level AS level
"""

# Only leaf rows (level 4) are measurable indicators with a published series.
# The 96 classifier headings above them exist in the register and so pass the
# id check, but `sdmx_data_<id>.json` 404s for every one -- verified against
# the live host. Saying so beats handing back a link that looks like a
# citation and resolves to nothing.
_LEAF_LEVEL = 4


def _coerce_ids(raw: Any) -> tuple[list[int], list[Any]]:
    """Split the input into usable ids and whatever could not be read as one."""
    if isinstance(raw, (str, int)):
        # A single id passed unwrapped is the likeliest caller mistake and
        # costs nothing to accept.
        raw = [raw]
    if not isinstance(raw, list):
        return [], []

    ids: list[int] = []
    bad: list[Any] = []
    for item in raw:
        try:
            # `"1142"` and `1142.0` both mean the same row; anything else is a
            # caller error worth naming rather than silently dropping.
            value = int(str(item).strip())
        except (TypeError, ValueError):
            bad.append(item)
            continue
        if value not in ids:
            ids.append(value)
    return ids, bad


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Build the siat.stat.uz SDMX data-file URL for one or more statistical "
        "indicators, given their register ids. Each URL points at a JSON file "
        "holding that indicator's full published time series -- a `metadata` "
        "block (first publication date, last modified date, dataset id, unit, "
        "source, all in uz/ru/en/uzc) and a `data` block of rows keyed by "
        "classifier value with one column per year.\n"
        "IDS COME FROM `statind_code`. Call it first and take the `id` field of "
        "the row you chose; do not guess an id. Every id is checked against the "
        "register and the reply repeats the code and official name, so confirm "
        "those match the indicator you meant before quoting the link. An id that names a "
        "classifier heading rather than an indicator is rejected: headings "
        "have no published data file and the URL would 404.\n"
        "The tool returns links only -- it does not download them. Say the URL "
        "to the user, or fetch it yourself if you have a tool that can."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Register ids, as returned in the `id` field by "
                    f"`statind_code`. At most {_MAX_IDS} per call."
                ),
            },
        },
        "required": ["ids"],
    },
}


def statind_data_url_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Build SDMX URLs for register ids. Returns a dict; the plugin serializes."""
    ids, unreadable = _coerce_ids((params or {}).get("ids"))
    if not ids and not unreadable:
        return {"success": False, "error": "ids must be a non-empty list"}
    if not ids:
        return {
            "success": False,
            "error": f"none of the ids could be read as a number: {unreadable}",
        }
    if len(ids) > _MAX_IDS:
        return {
            "success": False,
            "error": (
                f"{len(ids)} ids requested; at most {_MAX_IDS} per call -- "
                "split the list"
            ),
        }

    known: dict[int, dict[str, Any]] = {}
    verified = True
    try:
        with _session() as session:
            for record in session.run(_BY_ID_CYPHER, ids=ids):
                known[record["id"]] = {
                    "kod": record["code"],
                    "nomi": record["name"],
                    "davriylik": record["period"],
                    "daraja": record["level"],
                }
    except Exception as exc:  # noqa: BLE001
        # The URL is still correct for a correct id, so a register that is down
        # degrades the answer rather than blocking it -- but the caller has to
        # be told the ids went unchecked.
        logger.warning("statind_data_url: register lookup failed: %s", exc)
        verified = False

    results: list[dict[str, Any]] = []
    for value in ids:
        entry: dict[str, Any] = {
            "id": value,
            "url": _URL_TEMPLATE.format(id=value),
        }
        if not verified:
            entry["holat"] = "tekshirilmadi: reyestrga ulanib bo'lmadi"
        elif value in known:
            entry.update(known[value])
            if entry.get("daraja") != _LEAF_LEVEL:
                entry["holat"] = (
                    "XATO: bu klassifikator sarlavhasi, ko'rsatkich emas -- "
                    "ma'lumot fayli yo'q (havola 404 qaytaradi)"
                )
            else:
                entry["holat"] = "ok"
        else:
            entry["holat"] = "XATO: bunday id reyestrda yo'q"
        results.append(entry)

    # Two different failures, kept apart because the fix differs: an unknown id
    # was made up, while a heading is a real row the caller picked by mistake
    # and should replace with one of its child indicators.
    unknown = [r["id"] for r in results if r["holat"].startswith("XATO: bunday")]
    headings = [r["id"] for r in results if r["holat"].startswith("XATO: bu klass")]
    logger.info(
        "statind_data_url: %d ids, %d unknown, %d headings, verified=%s",
        len(results),
        len(unknown),
        len(headings),
        verified,
    )
    out: dict[str, Any] = {
        "success": True,
        "natijalar": results,
        "jami": len(results),
        "tekshirildi": verified,
    }
    if unknown:
        out["reyestrda_yoq"] = unknown
    if headings:
        out["malumot_fayli_yoq"] = headings
    if unreadable:
        out["oqib_bolmadi"] = unreadable
    return out
