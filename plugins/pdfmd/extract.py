"""
`pdf_extract` — the checkable claims inside a converted release.

The unit this tool deals in is a **claim**: one statement in the release that
can be true or false against the register.

    {"jumla":  "2025-yilda yalpi ichki mahsulot 1 849 650,0 mlrd so'mni tashkil etdi",
     "raqam":  1849650.0,
     "birlik": "mlrd so'mni",
     "davr":   "2025"}

That shape exists because of what comes after it: `davr` becomes `periods` for
`statind_data`, and `raqam` with `birlik` is what the published value gets
compared against. The indicator name is read off `jumla` by the caller and
taken to `statind_code`.

**The split of work is deliberate.** Everything here is mechanical -- regular
expressions over the whole document, no model. That is not a shortcut; it is
the half a model does badly. Models drop digits out of `1 849 650,0`, and they
skip rows when a release runs to forty tables. A regular expression reads every
figure in the file, exactly, every time. Deciding *which indicator a sentence
is about* is the other half, needs judgment, and is left to the caller -- the
same division `statind_code` already uses, where the tool returns candidates
and the model chooses.

So `korsatkich` is filled in only where it can be read off structure rather
than guessed: a table row's own label. In prose it is omitted, because a
plausible-looking guess would be carried straight into a register search and
answered about confidently.

`jumla` is verbatim for prose, for the same reason the conversion has no LLM
in it: when the agent reports a mismatch it has to quote the sentence the
release actually printed. A table has no sentence, so one is assembled from
the row label and the column header and marked `manba: "jadval"` -- the
figure is still exact, but the wording is ours and the caller has to know that.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterator

from ._pdf import PdfError, data_root, md_dir, md_path_for, relative

logger = logging.getLogger("hermes.plugin.pdfmd.extract")

TOOL_NAME = "pdf_extract"
TOOLSET_NAME = "pdfmd"

_MAX_CLAIMS = 80
_DEFAULT_CLAIMS = 25

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
# Every replacement is one character for one character, so an offset into the
# normalised text is also an offset into the original. That is what lets the
# patterns below run on tidy text while `jumla` is still sliced verbatim out of
# what the release printed.
_TRANSLATE = {
    # The five ways this corpus writes the Uzbek apostrophe.
    0x2018: "'", 0x2019: "'", 0x02BB: "'", 0x02BC: "'", 0x0060: "'", 0x00B4: "'",
    # Thousands separators that are not a plain space.
    0x00A0: " ", 0x202F: " ", 0x2009: " ", 0x2007: " ",
    # Dashes, so `2024-2025` and `2024–2025` read alike.
    0x2013: "-", 0x2014: "-", 0x2212: "-",
}


def _norm(text: str) -> str:
    return text.translate(_TRANSLATE)


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------
# Uzbek statistical style: space groups the thousands, comma is the decimal
# point. The grouped form is tried first so `1 849 650,0` is one figure and not
# three. Only a literal space groups -- `\s` would join across a line break.
_NUM_RE = re.compile(r"\d{1,3}(?: \d{3})+(?:,\d+)?|\d+(?:,\d+)?")

# Digits joined by dots are never a measurement in this corpus, and reading
# them as one is not an option either -- `1.01.01.0009` is a classifier code and
# `8.5` is a decimal written the English way. Both are skipped whole. Missing an
# unusual figure costs less than the alternative: without this, `8.5` came back
# as two claims, "8" and "5", each of which reads like something the release
# said.
_DOTTED = re.compile(r"\d+(?:\.\d+)+")

# A number carrying one of these right after it is a date or an ordinal, not a
# measurement: `2025-yilda`, `1-yanvar`, `II chorak`, `86-sonli`.
#
# No `^` anchor on this or `_UNIT_RE`: both are used as `.match(text, pos)`,
# which already anchors at `pos`, while `^` would still mean start-of-string
# and never match at all.
_NOT_A_FIGURE = re.compile(
    r" ?-? ?(?:yil|yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|"
    r"sentyabr|oktabr|oktyabr|noyabr|dekabr|chorak|son|sonli|raqamli|modda|"
    r"bandi|o'rin"
    # Uzbek Cyrillic. The register indexes it and government releases are
    # published in it, so a release in Cyrillic has to parse as well as one in
    # Latin rather than coming back empty.
    r"|йил|январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|"
    r"декабр|чорак|сон)",
    re.IGNORECASE,
)


def _to_number(raw: str) -> float:
    return float(raw.replace(" ", "").replace(",", "."))


# --------------------------------------------------------------------------
# Units, as a release prints them
# --------------------------------------------------------------------------
# Not the register's unit strings. Those are long and formal ("o'tgan yilning
# mos davriga nisbatan foizda") and never appear inline in prose. What a
# release writes next to a figure is a short magnitude-plus-measure phrase, and
# that is what this matches. The register's own unit is what `statind_data`
# returns, and comparing the two is the caller's job.
_MAG = (
    r"(?:ming|mln\.?|million|mlrd\.?|milliard|trln\.?|trillion"
    r"|минг|млн\.?|миллион|млрд\.?|миллиард|трлн\.?)"
)
_MEASURE = (
    r"(?:so'm\w*|sum\w*|aqsh\s+dollar\w*|dollar\w*|yevro\w*|evro\w*"
    r"|tonna\w*|kilogramm\w*|kg\b|litr\w*|gektar\w*|kilometr\w*|km\b"
    r"|kv\.?\s*metr\w*|metr\s+kvadrat\w*|metr\w*|kvt\s*soat\w*"
    r"|kishi\w*|nafar\w*|dona\w*|birlik\w*|xo'jalik\w*|o'rin\w*|bosh\b"
    r"|promille\w*|promil\w*"
    r"|сўм\w*|сум\w*|ақш\s+доллар\w*|доллар\w*|тонна\w*|киши\w*|нафар\w*"
    r"|дона\w*|бирлик\w*|гектар\w*|литр\w*|промилле\w*)"
)
_PERCENT = r"(?:%|foiz\w*|protsent\w*|punkt\w*|фоиз\w*|процент\w*|пункт\w*)"

# Ordered: a magnitude with a measure beats a bare measure, which beats a bare
# magnitude -- otherwise "mlrd so'm" would come back as "mlrd".
_UNIT_RE = re.compile(
    rf" ?(?:{_MAG}[ .]*{_MEASURE}|{_MEASURE}|{_PERCENT}|{_MAG}\b)",
    re.IGNORECASE,
)

# A column label in a statistical table carries its unit after the last comma:
# "Yalpi ichki mahsulot, mlrd so'm". Splitting it matters -- the name goes to
# `statind_code`, and leaving the unit attached would put "mlrd so'm" into a
# keyword search that ORs its terms.
_LABEL_UNIT_RE = re.compile(
    rf"^(.+?),\s*({_MAG}[ .]*{_MEASURE}|{_MEASURE}|{_PERCENT}|{_MAG})\.?\s*$",
    re.IGNORECASE,
)


def _unit_after(norm: str, end: int) -> tuple[str, int]:
    """The unit printed right after a figure, as (text, end offset)."""
    match = _UNIT_RE.match(norm, end)
    if not match:
        return "", end
    return " ".join(match.group(0).split()), match.end()


def _split_label(label: str) -> tuple[str, str]:
    """A table row label as (indicator name, unit); unit is '' if none."""
    match = _LABEL_UNIT_RE.match(_norm(label))
    if not match:
        return label, ""
    # Sliced out of the original, so the apostrophes are the ones printed.
    return label[: match.end(1)].strip(), label[match.start(2) :].strip(" .")


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------
# Resolved to the ids the register uses, because that is what `statind_data`
# takes: '2025', '2025-Q2', '2024-M03'.
_MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9, "oktabr": 10,
    "oktyabr": 10, "noyabr": 11, "dekabr": 12,
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11,
    "декабр": 12,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

_YEAR_RE = re.compile(
    r"\b(19\d{2}|20\d{2})(?: ?-? ?(?:yil|йил)\w*)?", re.IGNORECASE
)
_QUARTER_RE = re.compile(
    r"\b(IV|III|II|I|[1-4])[- ]?(?:chorak|чорак)\w*", re.IGNORECASE
)
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# `yanvar-dekabr` and friends: a cumulative range, which the register publishes
# as the period ending it rather than as a month of its own.
_RANGE_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")"
    r"\s?-\s?(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _period(norm: str, fallback_year: str | None) -> tuple[str | None, str | None]:
    """
    Read a period out of one segment.

    Returns the register id and the phrase it came from, so a caller can see
    what was interpreted. A year on its own is the commonest case by far.
    """
    year_match = _YEAR_RE.search(norm)
    year = year_match.group(1) if year_match else fallback_year
    if not year:
        return None, None
    phrase = year_match.group(0).strip() if year_match else None

    quarter = _QUARTER_RE.search(norm)
    if quarter:
        token = quarter.group(1).lower()
        number = _ROMAN.get(token) or int(token)
        return f"{year}-Q{number}", (phrase or "") + " " + quarter.group(0).strip()

    span = _RANGE_RE.search(norm)
    if span:
        # `yanvar-dekabr` is the whole year; any other range is cumulative to
        # its last month, which is how the register indexes it.
        last = _MONTHS[span.group(2).lower()]
        if _MONTHS[span.group(1).lower()] == 1 and last == 12:
            return year, (phrase or "") + " " + span.group(0).strip()
        return f"{year}-M{last:02d}", (phrase or "") + " " + span.group(0).strip()

    month = _MONTH_RE.search(norm)
    if month:
        value = _MONTHS[month.group(1).lower()]
        return f"{year}-M{value:02d}", (phrase or "") + " " + month.group(0).strip()

    return (year, phrase) if year_match else (year, None)


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------
# Splitting on a full stop is wrong here: the corpus is full of `mln.`,
# `mlrd.` and `y.`, and a naive split cuts a figure away from its unit. So a
# sentence ends at punctuation only when what follows starts a new one, and
# never right after a known abbreviation.
_ABBREV = re.compile(r"(?:mln|mlrd|trln|ming|y|yy|t|k|kv|ml|st|proc|foiz)\.$",
                     re.IGNORECASE)
_SENT_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЎҚҒҲ0-9\"'(])")


def _sentences(block: str) -> Iterator[str]:
    for piece in _SENT_END.split(block):
        piece = piece.strip()
        if piece:
            yield piece


_ENDS_SENTENCE = re.compile(r"[.!?:;…]\s*$")


def _continues(previous: str, nxt: str) -> bool:
    """
    Whether `nxt` is the rest of a line wrapped at the column edge.

    A blank line cannot answer this. The converter emits one for every printed
    line, so a sentence broken across the column arrives as two "paragraphs"
    and a figure can end up separated from its unit. The test that works is
    the pair: the previous line stopped without terminal punctuation, and this
    one starts lower-case.

    A continuation starting with a digit is missed by that rule, deliberately.
    Accepting digits would swallow the heading in

        O'zbekiston Respublikasi Statistika agentligi
        2025-yil yakunlari

    and a heading glued into `jumla` misquotes the release, while a missed join
    only shortens a sentence whose figure is still read correctly.
    """
    return not _ENDS_SENTENCE.search(previous) and nxt[:1].islower()


def _prose_segments(text: str) -> Iterator[str]:
    """Sentences of the non-table parts, in document order."""
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        # Blank lines carry no information here -- see `_continues`.
        if not stripped:
            continue
        if stripped.startswith("|") or set(stripped) <= {"-", "=", "*", "_"}:
            yield from _merge(buffer)
            buffer = []
            continue
        heading = stripped.startswith("#")
        stripped = stripped.lstrip("#").strip()
        if not stripped:
            continue
        if heading:
            # Its own segment: a heading is never the tail of a sentence, and
            # it usually carries the period the figures below it inherit.
            yield from _merge(buffer)
            buffer = []
            yield stripped
            continue
        if buffer and _continues(buffer[-1], stripped):
            buffer.append(stripped)
        else:
            yield from _merge(buffer)
            buffer = [stripped]
    yield from _merge(buffer)


def _merge(lines: list[str]) -> Iterator[str]:
    """Rejoin a wrapped paragraph, then split it into sentences."""
    if not lines:
        return
    parts = _SENT_END.split(" ".join(lines))
    out: list[str] = []
    for part in parts:
        # `mln.` and `mlrd.` end in a full stop without ending a sentence, and
        # splitting there would cut a figure away from its unit.
        if out and _ABBREV.search(out[-1]):
            out[-1] = out[-1] + " " + part
        else:
            out.append(part)
    for piece in out:
        piece = piece.strip()
        if piece:
            yield piece


_ROW_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _tables(text: str) -> Iterator[tuple[list[str], list[str]]]:
    """Yield (header, row) for every data row of every markdown table."""
    header: list[str] = []
    in_table = False
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            header, in_table = [], False
            continue
        if _ROW_SEP.match(line):
            in_table = True
            continue
        cells = _cells(line)
        if not header:
            # The first row of a block is the header, whether or not a
            # separator line follows -- not every converter emits one.
            header = cells
            continue
        if not in_table and len(cells) != len(header):
            header = cells
            continue
        yield header, cells


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _figures(segment: str) -> Iterator[tuple[float, str]]:
    """Every real measurement in one segment, as (value, unit)."""
    norm = _norm(segment)
    dotted = [(m.start(), m.end()) for m in _DOTTED.finditer(norm)]
    for match in _NUM_RE.finditer(norm):
        raw, end = match.group(0), match.end()
        if any(start <= match.start() < stop for start, stop in dotted):
            continue
        if _NOT_A_FIGURE.match(norm, end):
            continue
        unit, _ = _unit_after(norm, end)
        value = _to_number(raw)
        if not unit and raw.isdigit() and 1900 <= value <= 2100:
            # A bare four-digit number with nothing after it is a year far more
            # often than it is a quantity, and a wrong claim costs more than a
            # missed one.
            continue
        yield value, unit


def _claims(text: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    """
    Pull the checkable claims out of a converted release.

    Returns the claims and how many were found in total, so a truncated list
    can say so instead of looking complete.
    """
    claims: list[dict[str, Any]] = []
    total = 0
    year: str | None = None

    # Prose first, then tables, so the reply reads in the order a person would.
    for segment in _prose_segments(text):
        norm = _norm(segment)
        period, phrase = _period(norm, year)
        if period:
            # Releases name the period in a heading and then list figures
            # without repeating it, so it carries forward until another is
            # stated.
            year = period[:4]
        for value, unit in _figures(segment):
            total += 1
            if len(claims) >= limit:
                continue
            claim: dict[str, Any] = {"jumla": segment, "raqam": value}
            if unit:
                claim["birlik"] = unit
            if period:
                claim["davr"] = period
                if phrase:
                    claim["davr_matni"] = phrase.strip()
            claims.append(claim)

    for header, row in _tables(text):
        label = row[0] if row else ""
        if not label:
            continue
        name, label_unit = _split_label(label)
        for index, cell in enumerate(row[1:], start=1):
            column = header[index] if index < len(header) else ""
            for value, unit in _figures(cell):
                total += 1
                if len(claims) >= limit:
                    continue
                period, _ = _period(_norm(f"{column} {label}"), year)
                claim = {
                    "jumla": f"{label} | {column}: {cell}".strip(" |:"),
                    "raqam": value,
                    # A row label is the indicator's own name as the release
                    # printed it -- structure, not a guess, so it is safe to
                    # hand to `statind_code`.
                    "korsatkich": name,
                    "manba": "jadval",
                }
                # The cell's own unit wins: a column can restate it ("2025, %")
                # while the row label carries the general one.
                if unit or label_unit:
                    claim["birlik"] = unit or label_unit
                if period:
                    claim["davr"] = period
                claims.append(claim)

    return claims, total


def _resolve_md(name: str) -> Path:
    """
    Find the Markdown for whatever the caller named.

    A caller may hold any of three things after `pdf_to_md`: the md path it
    returned, the original PDF name, or a bare stem. All three point at one
    file, so all three are accepted rather than requiring the caller to
    remember which one it has.
    """
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        raise PdfError("fayl nomi bo'sh")
    if ".." in raw or "\x00" in raw:
        raise PdfError(f"fayl nomi ruxsat etilmagan: {name!r}")

    stem = Path(raw).stem
    root = data_root()
    for candidate in (root / raw, md_dir() / raw, md_path_for(Path(stem))):
        path = candidate.resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file() and path.suffix.lower() == ".md":
            return path

    known = sorted(p.name for p in md_dir().glob("*.md"))[:20]
    hint = f" (mavjud: {', '.join(known)})" if known else ""
    raise PdfError(
        f"markdown topilmadi: {name!r}{hint} -- avval `pdf_to_md` ni chaqiring"
    )


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Pull the checkable statistical claims out of a press release that "
        "`pdf_to_md` has already converted. Call that first; this tool reads "
        "the stored Markdown and never touches the PDF.\n"
        "Each claim is one figure the release states, with the sentence it "
        "came from (`jumla`), the number (`raqam`), the unit as printed "
        "(`birlik`) and the period resolved to a register id (`davr`: '2025', "
        "'2025-Q2', '2024-M03'). A sentence stating two figures yields two "
        "claims, because they are usually two different indicators -- a volume "
        "and a growth rate.\n"
        "READ THE INDICATOR NAME OFF `jumla` YOURSELF. It is supplied as "
        "`korsatkich` only for claims that came from a table row, where it is "
        "the row's own label; in prose it is deliberately absent rather than "
        "guessed. Take it to `statind_code`, then `davr` to `statind_data`, "
        "then compare `raqam` -- checking the unit first, since a release and "
        "the register do not always print the same one.\n"
        "`manba: \"jadval\"` marks a claim assembled from a table row and "
        "column header. Its figure is exact but its wording is reconstructed, "
        "so quote it as a table entry, not as a sentence the release wrote.\n"
        "THE CLAIMS ARE WHAT THE RELEASE STATES, NOT WHAT IS TRUE. Nothing "
        "here is verified against anything; verifying is the next call.\n"
        "Extraction is mechanical, so it finds every figure in the file but "
        "understands none of them. Years standing alone are skipped as dates. "
        "If `jami_topildi` is larger than the list, raise `limit` rather than "
        "assuming the rest do not exist."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": (
                    "The Markdown from `pdf_to_md` ('md/yanvar_reliz.md'), or "
                    "the original PDF name -- both resolve to the same file."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum claims to return. Defaults to {_DEFAULT_CLAIMS}, "
                    f"maximum {_MAX_CLAIMS}. A release full of tables reaches "
                    "it quickly."
                ),
            },
        },
        "required": ["file"],
    },
}


def pdf_extract_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Extract claims from converted Markdown. Returns a dict."""
    name = str((params or {}).get("file") or "").strip()
    if not name:
        return {"success": False, "error": "file must be a markdown or pdf name"}

    try:
        limit = int(params.get("limit") or _DEFAULT_CLAIMS)
    except (TypeError, ValueError):
        limit = _DEFAULT_CLAIMS
    limit = max(1, min(limit, _MAX_CLAIMS))

    try:
        path = _resolve_md(name)
        text = path.read_text(encoding="utf-8")
    except PdfError as exc:
        return {"success": False, "error": str(exc)}
    except OSError as exc:
        logger.warning("pdf_extract: cannot read %s: %s", name, exc)
        return {"success": False, "error": f"faylni o'qib bo'lmadi: {exc}"}

    claims, total = _claims(text, limit)
    logger.info(
        "pdf_extract: %s, %d chars, %d/%d claims",
        relative(path),
        len(text),
        len(claims),
        total,
    )
    out: dict[str, Any] = {
        "success": True,
        "fayl": relative(path),
        "belgi": len(text),
        "dalillar": claims,
        "jami": len(claims),
        "jami_topildi": total,
    }
    if total > len(claims):
        out["qisqartirildi"] = (
            f"{total} ta raqamdan {len(claims)} tasi qaytarildi -- qolganini "
            "ko'rish uchun `limit` ni oshiring"
        )
    if not total:
        # An empty result is a statement about the document, and it has two
        # very different causes worth telling apart.
        out["izoh"] = (
            "hujjatda tekshiriladigan raqam topilmadi -- matn bo'sh yoki "
            "raqamlar rasm ichida bo'lishi mumkin"
        )
    return out
