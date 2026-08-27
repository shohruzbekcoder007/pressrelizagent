"""
`statind_code` — maps a plain-language indicator name to its classifier code.

The classifier is the Uzbek statistical indicator register: 3326 rows of
`code | name | level`. Matching a name to a code by hand is tedious and matching
it by model alone is unsafe -- the codes look regular enough (`1.01.01.0001`)
that a model will happily invent a plausible one. So this tool does three
things in order:

  1. **Retrieve** candidates from Neo4j, which already holds the whole register
     with a fulltext index over Uzbek latin, Uzbek cyrillic and Russian names
     plus the classifier path.
  2. **Choose** among those candidates with the app's own LLM, which handles
     word order, inflection and which periodicity the caller meant.

     It cannot rescue what retrieval missed, though: the model only ever sees
     the shortlist. An abbreviation is the clear case -- "YaIM" matches only
     the rows whose *names* contain "YAIMdagi ulushi", never the GDP rows
     themselves, so the answer is a correct-but-unhelpful TOPILMADI. Expanding
     the abbreviation before calling is the caller's job, and the tool
     description says so.
  3. **Verify** every returned code against the candidates that were actually
     offered. A code the model did not receive is reported as a hallucination
     rather than passed on, and a code whose name does not match its row is
     flagged.

Retrieval rather than a prompt-stuffed register is a measured choice: the flat
`statind_pipe.txt` is 146,710 tokens, which costs ~52s on a cold cache and
occupies over half the model's 262k context on every call. The Neo4j shortlist
carries the same candidates in a few hundred tokens.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from ._llm import complete, endpoint, env_int

logger = logging.getLogger("hermes.plugin.pressreliz.statind")

TOOL_NAME = "statind_code"
TOOLSET_NAME = "pressreliz"

# One Neo4j lookup per requested name, so a long list multiplies both the
# retrieval and the prompt. Twenty is well inside a comfortable prompt and far
# past what a press release cites at once.
_MAX_INDICATORS = 20
_DEFAULT_CANDIDATES = 10
_MAX_CANDIDATES = 25

# The register is a closed set, so a name that matches nothing at all is a real
# answer ("TOPILMADI"), not an error.
_NOT_FOUND = "TOPILMADI"

_driver_lock = threading.Lock()
_driver: Any = None
_driver_key: tuple[str, str, str] | None = None

# Classifier codes are dotted numeric groups: `1.01`, `1.01.01`, `1.01.01.0001`.
# A caller who already has one wants verification, not search.
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
    pulls the entire shortlist onto the 2023-2024 rows and the right row never
    reaches the model.

    Only *leading* digits qualify. Codes like `XSST-2008` and `MIIT-2018` are
    part of an indicator's identity and start with a letter, so they survive;
    `2024-yilda`, `8,5` and `100` do not. If a query is nothing but figures the
    original is kept, since an empty query finds nothing at all.
    """
    kept = [w for w in text.split() if not w[:1].isdigit()]
    return " ".join(kept) if kept else text


# Returned for every hit. `name_uz` is the authoritative label -- `name` on
# these nodes is the English translation, which is not what a press release
# quotes.
_RETURN = """
    node.code   AS code,
    node.name_uz AS name,
    node.path_uz AS path,
    node.level  AS level,
    node.period AS period
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
    return {
        "kod": record["code"],
        "nomi": record["name"],
        "yol": record["path"],
        "daraja": record["level"],
        "davriylik": record["period"],
    }


def _search(session: Any, name: str, limit: int) -> list[dict[str, Any]]:
    """Candidates for one requested name, best first."""
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


_SYSTEM = """Sen O'zbekiston Milliy statistika qo'mitasining statistik
ko'rsatkichlar klassifikatori bo'yicha yordamchisan.

Har bir so'ralgan ko'rsatkich uchun NOMZODLAR ro'yxati berilgan. Ro'yxat
klassifikatordan qidiruv orqali olingan.

QOIDALAR:
1. Kodni FAQAT o'sha ko'rsatkichning nomzodlar ro'yxatidan ko'chirib yoz.
   Hech qachon kod o'ylab topma va yaqin kodni taxmin qilma.
2. Nomzodlar orasida mos ko'rsatkich bo'lmasa, kod o'rniga "TOPILMADI" yoz.
3. Davriylikka alohida e'tibor ber: "(yillik)", "(oylik)" va "(choraklik)" —
   bular BOSHQA-BOSHQA ko'rsatkichlar, kodlari ham har xil. Xuddi shunday
   "(hududlar kesimida)", "(mamlakatlar kesimida)", "(qit'alar kesimida)" ham
   alohida ko'rsatkichlar.
4. Foydalanuvchi davriylikni aytmagan bo'lsa, o'zing tanlama: "kod" ga eng
   umumiy variantni qo'y, ishonchni "past" deb belgila va barcha mos
   variantlarni "variantlar" ro'yxatiga chiqar.
5. Bir nechta mos variant bo'lsa, ishonch "past" yoki "o'rta" bo'ladi.

JAVOB FORMATI — faqat JSON massiv, boshqa hech narsa yozma:
[
  {"soralgan": "<so'ralgan matn>",
   "kod": "<nomzodlar ro'yxatidagi kod yoki TOPILMADI>",
   "nomi": "<tanlangan nomzodning to'liq nomi>",
   "ishonch": "yuqori|o'rta|past",
   "variantlar": [{"kod": "...", "nomi": "..."}],
   "izoh": "<past yoki o'rta bo'lsa sabab, aks holda bo'sh>"}
]"""


def _build_user_message(batch: list[tuple[str, list[dict[str, Any]]]]) -> str:
    parts: list[str] = []
    for i, (name, candidates) in enumerate(batch, 1):
        parts.append(f'### {i}. So\'ralgan: "{name}"')
        if not candidates:
            parts.append("Nomzodlar: (qidiruv hech nima topmadi)")
        else:
            parts.append("Nomzodlar:")
            for c in candidates:
                extra = f" — yo'l: {c['yol']}" if c.get("yol") else ""
                parts.append(f"- {c['kod']} | {c['nomi']}{extra}")
        parts.append("")
    return "\n".join(parts).strip()


def _parse_reply(text: str) -> Any:
    """The model is asked for bare JSON; fenced JSON is tolerated anyway."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        # A stray sentence before or after the array is common enough to be
        # worth one salvage attempt before giving up.
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end > start:
            return json.loads(cleaned[start : start + (end - start + 1)])
        raise


def _norm(text: Any) -> str:
    """Compare names ignoring case, spacing and the register's four apostrophes."""
    s = str(text or "")
    for ch in "‘’ʻʼ`´′":
        s = s.replace(ch, "'")
    return " ".join(s.split()).lower()


def _verify(
    entry: dict[str, Any], offered: dict[str, str]
) -> tuple[str, dict[str, Any]]:
    """
    Classify one answer against the codes actually offered for it.

    `offered` maps code -> authoritative name. Anything outside it was not in
    front of the model, so it was invented.
    """
    code = str(entry.get("kod") or "").strip()
    if code == _NOT_FOUND or not code:
        return "topilmadi", entry
    if code not in offered:
        return "XATO: bu kod nomzodlar ro'yxatida yo'q (model o'ylab topgan)", entry
    real = offered[code]
    if _norm(entry.get("nomi")) != _norm(real):
        # The code is real but the model retyped the name wrong. The code wins;
        # the name is corrected from the register so the caller never quotes a
        # label that does not exist.
        entry["nomi"] = real
        return f"OGOHLANTIRISH: nom to'g'rilandi -> {real}", entry
    return "ok", entry


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Find the official Uzbek statistical classifier code for one or more "
        "indicators. Searches the indicator register (Uzbek latin, Uzbek "
        "cyrillic and Russian names are all indexed), picks the best match and "
        "reports how confident it is. Every returned code is verified against "
        "the register, so an invented code is reported as an error instead of "
        "being passed on. Use it whenever a press release or a question "
        "mentions a statistical indicator and the code is needed.\n"
        "PASS THE INDICATOR NAME, NOT THE SENTENCE IT CAME FROM. The search is "
        "keyword-based, so surrounding prose dilutes it: from '2024-yilda "
        "chakana savdo hajmi 8,5 foizga oshdi' pass 'Chakana savdo hajmining "
        "o'sish sur'ati (yillik)'. Expand abbreviations yourself -- 'YaIM' "
        "finds nothing, 'Yalpi ichki mahsulot' finds the right rows. If the "
        "text says something grew by a percentage, the indicator is usually "
        "the growth rate (o'sish sur'ati), not the volume (hajmi).\n"
        "Use dictionary forms. Uzbek case endings are not stemmed, so "
        "'mahsulotning' does not match the register's 'mahsulot' and drops the "
        "right row out of the shortlist -- strip the suffix before passing "
        "the name.\n"
        "Periodicity (yillik / oylik / choraklik) and cross-sections are "
        "separate indicators with separate codes: if you do not know which was "
        "meant, say so rather than guessing -- the reply lists the variants."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "indicators": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Indicator names, each one a name and nothing else -- not "
                    "the sentence it was quoted from, and with abbreviations "
                    "spelled out. A classifier code (e.g. '1.01.01.0001') may "
                    "be passed instead to verify it. At most "
                    f"{_MAX_INDICATORS} per call."
                ),
            },
            "candidates_per_indicator": {
                "type": "integer",
                "description": (
                    "How many register rows to put in front of the model for "
                    f"each name. Defaults to {_DEFAULT_CANDIDATES}; raise it "
                    "for a vague name, lower it for a precise one."
                ),
            },
        },
        "required": ["indicators"],
    },
}


def statind_code_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Look up classifier codes. Returns a dict; the plugin serializes it."""
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
        limit = int(params.get("candidates_per_indicator") or _DEFAULT_CANDIDATES)
    except (TypeError, ValueError):
        limit = _DEFAULT_CANDIDATES
    limit = max(1, min(limit, _MAX_CANDIDATES))

    # --- 1. retrieve -----------------------------------------------------
    try:
        driver = _neo4j_driver()
        with driver.session(database=_database()) as session:
            batch = [(name, _search(session, name, limit)) for name in names]
    except Exception as exc:  # noqa: BLE001
        logger.warning("statind: neo4j search failed: %s", exc)
        return {
            "success": False,
            "error": f"indicator register unreachable ({type(exc).__name__}: {exc})",
            "retryable": True,
        }

    offered_by_name = {
        name: {c["kod"]: c["nomi"] for c in cands} for name, cands in batch
    }
    if not any(cands for _, cands in batch):
        return {
            "success": True,
            "natijalar": [
                {
                    "soralgan": name,
                    "kod": _NOT_FOUND,
                    "nomi": "",
                    "ishonch": "past",
                    "izoh": "klassifikatordan hech qanday nomzod topilmadi",
                    "holat": "topilmadi",
                }
                for name in names
            ],
            "jami": len(names),
            "tekshiruvdan_otmadi": 0,
        }

    # --- 2. choose -------------------------------------------------------
    # Room for one object per indicator plus the variant lists, which are the
    # part that grows when a name is ambiguous.
    max_tokens = env_int("STATIND_MAX_TOKENS", 400 * len(names) + 600)
    reply = complete(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_message(batch)},
        ],
        ep=endpoint(),
        max_tokens=max_tokens,
    )
    if not reply.get("success"):
        return {
            "success": False,
            "error": reply.get("error"),
            "timed_out": reply.get("timed_out"),
            "retryable": reply.get("retryable"),
        }

    try:
        parsed = _parse_reply(str(reply.get("text") or ""))
    except ValueError as exc:
        return {
            "success": False,
            "error": f"model did not return usable JSON: {exc}",
            "raw": str(reply.get("text") or "")[:500],
        }
    if not isinstance(parsed, list):
        return {
            "success": False,
            "error": "model returned JSON that is not an array",
            "raw": str(reply.get("text") or "")[:500],
        }

    # --- 3. verify -------------------------------------------------------
    # Keyed by requested name so a reordered or partial reply still lines up
    # with the candidates that were offered for it.
    by_name = {}
    for entry in parsed:
        if isinstance(entry, dict):
            by_name.setdefault(_norm(entry.get("soralgan")), entry)

    results: list[dict[str, Any]] = []
    for name in names:
        entry = by_name.get(_norm(name))
        if entry is None:
            results.append(
                {
                    "soralgan": name,
                    "kod": _NOT_FOUND,
                    "nomi": "",
                    "ishonch": "past",
                    "izoh": "model bu ko'rsatkich uchun javob qaytarmadi",
                    "holat": "XATO: javobda yo'q",
                }
            )
            continue

        offered = offered_by_name.get(name, {})
        holat, entry = _verify(entry, offered)
        entry["soralgan"] = name
        entry["holat"] = holat

        variants = entry.get("variantlar")
        if isinstance(variants, list) and variants:
            # Variants are quoted onward just like the main pick, so they get
            # the same treatment: unknown codes dropped, names corrected.
            kept = []
            for v in variants:
                if not isinstance(v, dict):
                    continue
                vcode = str(v.get("kod") or "").strip()
                if vcode in offered:
                    kept.append({"kod": vcode, "nomi": offered[vcode]})
            entry["variantlar"] = kept
        else:
            entry.pop("variantlar", None)

        results.append(entry)

    failed = sum(1 for r in results if r["holat"] not in ("ok", "topilmadi"))
    logger.info(
        "statind_code: %d requested, %d failed verification (model=%s)",
        len(results),
        failed,
        reply.get("model"),
    )
    return {
        "success": True,
        "natijalar": results,
        "jami": len(results),
        "tekshiruvdan_otmadi": failed,
        "model": reply.get("model"),
    }
