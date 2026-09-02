"""
`telegram_post` — a draft turned into something ready to paste into Telegram.

The model writes the words. This tool does the three things the model does
badly and a rule does exactly, which is the same division every tool in this
project already follows.

**Markdown does not survive the paste.** Telegram renders no tables and no
headings, and a client pasting text sends it literally -- so `## Xulosa`
arrives as "## Xulosa" and a `|---|---|` table arrives as a wall of pipes.
Everything the model reaches for when it writes a report is exactly what
breaks here, so the draft is flattened into plain lines before it goes out.

**Numbers have one correct shape.** Uzbek statistics writes `1 849 650,0`:
space between thousands, comma before the decimal. Models produce
`1,849,650.0`, `1849650.0` and `1 849 650.0` interchangeably, sometimes three
ways in one post. Rewriting them is mechanical.

**A published figure has to have been checked.** This whole service exists to
stop an unverified number reaching print, and a Telegram post *is* print --
the last place to catch one. Pass the figures that came back from
`statind_data` as `tasdiqlangan`, and every number in the draft that is not
among them comes back flagged, with the words around it, before the user
copies anything.

The tool never invents text and never removes a claim. A flagged number is
reported, not deleted: deciding what to do about it belongs to whoever is
publishing.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger("hermes.plugin.telegram.post")

TOOL_NAME = "telegram_post"
TOOLSET_NAME = "telegram"

# Telegram counts a message in UTF-16 code units, not characters: an emoji
# costs two. Measuring in `len()` would let a post full of emoji sail past the
# limit and be rejected at send time.
_TELEGRAM_LIMIT = 4096
# Leaves room for the " (1/3)" part marker appended when a post is split.
_SPLIT_MARGIN = 24


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------
# Classifier codes (`1.01.01.0001`) are digits joined by dots and must survive
# untouched -- reformatting one would turn a source reference into nonsense.
_DOTTED = re.compile(r"\d+(?:\.\d+){2,}")
# `1,849,650.0` -- the English shape a model falls into.
_ENGLISH = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
# `1849650` or `1849650.5` -- unseparated, four digits or more.
_BARE = re.compile(r"\b\d{4,}(?:[.,]\d+)?\b")
# Already correct: `1 849 650,0`.
_UZBEK = re.compile(r"\b\d{1,3}(?: \d{3})+(?:,\d+)?\b")
# `8.5`, `8,5`, `106,7` -- a growth rate or a percentage, and the commonest
# figure in a post of this kind. It needs its own pattern because `_BARE`
# starts at four digits, so without this a percentage was neither reformatted
# nor put through the verified-figure check: it simply passed unseen.
_SHORT_DECIMAL = re.compile(r"\d{1,3}[.,]\d{1,3}")


def _uz_number(value: float, decimals: Optional[int] = None) -> str:
    """A number in the shape Uzbek statistics prints: `1 849 650,0`."""
    if decimals is None:
        decimals = 0 if float(value).is_integer() else 1
    text = f"{value:,.{decimals}f}"
    whole, _, frac = text.partition(".")
    whole = whole.replace(",", " ")
    return f"{whole},{frac}" if frac else whole


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _DOTTED.finditer(text)]


def _grouped_spans(text: str) -> list[tuple[int, int]]:
    """
    Where whole grouped numbers sit, so their tail is not read as one.

    `1 849 650,0` ends in `650,0`, which looks exactly like a standalone
    decimal. Without knowing the span of the number it belongs to, the short
    decimal pattern would rewrite the inside of a figure already formatted
    correctly, and count it a second time as its own claim.
    """
    spans = _protected_spans(text)
    spans.extend((m.start(), m.end()) for m in _UZBEK.finditer(text))
    spans.extend((m.start(), m.end()) for m in _BARE.finditer(text))
    return spans


def _inside(pos: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start <= pos < stop for start, stop in spans)


def _looks_like_year(raw: str) -> bool:
    digits = raw.replace(" ", "").replace(",", "").replace(".", "")
    return len(digits) == 4 and digits.isdigit() and 1900 <= int(digits) <= 2100


def _normalise_numbers(text: str) -> str:
    """Rewrite every figure into the Uzbek shape, leaving codes and years."""
    spans = _protected_spans(text)

    def english(match: re.Match) -> str:
        if _inside(match.start(), spans):
            return match.group(0)
        raw = match.group(0)
        value = float(raw.replace(",", ""))
        decimals = len(raw.partition(".")[2]) or None
        return _uz_number(value, decimals)

    text = _ENGLISH.sub(english, text)
    spans = _protected_spans(text)

    def bare(match: re.Match) -> str:
        raw = match.group(0)
        if _inside(match.start(), spans) or _looks_like_year(raw):
            return raw
        normalised = raw.replace(",", ".")
        try:
            value = float(normalised)
        except ValueError:
            return raw
        decimals = len(normalised.partition(".")[2]) or None
        return _uz_number(value, decimals)

    text = _BARE.sub(bare, text)

    # Short decimals last: `8.5` -> `8,5`. Run after the grouped forms so
    # their spans are known and their tails left alone.
    spans = _grouped_spans(text)

    def short(match):
        raw = match.group(0)
        if _inside(match.start(), spans) or "," in raw:
            return raw
        return raw.replace(".", ",")

    return _SHORT_DECIMAL.sub(short, text)


def _figures_in(text: str) -> list[tuple[float, str]]:
    """
    Every number in the post worth having checked, with its context.

    Deliberately not every number. A year, a list ordinal and a small count
    are not claims about the register, and flagging them would bury the one
    figure that actually matters under noise nobody reads.
    """
    spans = _protected_spans(text)
    grouped = _grouped_spans(text)
    found: list[tuple[float, str]] = []
    for pattern in (_UZBEK, _BARE, _SHORT_DECIMAL):
        for match in pattern.finditer(text):
            raw = match.group(0)
            if _inside(match.start(), spans) or _looks_like_year(raw):
                continue
            if pattern is _SHORT_DECIMAL and _inside(match.start(), grouped):
                # The tail of a grouped number, already counted with it.
                continue
            try:
                value = float(raw.replace(" ", "").replace(",", "."))
            except ValueError:
                continue
            has_decimal = "," in raw or "." in raw
            if not has_decimal and abs(value) < 100:
                continue
            start = max(0, match.start() - 45)
            context = " ".join(text[start : match.end() + 25].split())
            found.append((value, context))
    # Deduplicate by value, keeping the first context each was seen in.
    seen: dict[float, str] = {}
    for value, context in found:
        seen.setdefault(round(value, 4), context)
    return sorted(seen.items())


# --------------------------------------------------------------------------
# Markdown -> plain text
# --------------------------------------------------------------------------
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
_BOLD_ITALIC = re.compile(r"(\*{1,3}|__|_(?=\S))(.+?)\1", re.DOTALL)
_CODE = re.compile(r"`{1,3}([^`]*)`{1,3}", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RULE = re.compile(r"^\s*([-*_=])\1{2,}\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
# `7,7%%` -- a real post came back with this twice. Nothing here produces it,
# so it arrives in the draft: models reach for the printf escape when they
# mean a literal percent. It has no meaning in Uzbek statistical prose, and a
# doubled sign is exactly the kind of visible blemish this tool exists to
# take out before anyone copies the text.
_DOUBLE_PERCENT = re.compile(r"%{2,}")


def _cells(line: str) -> list[str]:
    return [
        _DOUBLE_PERCENT.sub("%", c.strip())
        for c in line.strip().strip("|").split("|")
    ]


def _table_to_lines(header: list[str], rows: list[list[str]]) -> list[str]:
    """
    A markdown table as lines a phone can read.

    The first column carries the row's subject and the header carries what
    each other column means, so `Eksport | 2025 | 2024` becomes
    `• Eksport — 2025: …, 2024: …`. Nothing is dropped; the grid is just
    unrolled, because Telegram has no grid to render it into.
    """
    out: list[str] = []
    for row in rows:
        if not row or not any(row):
            continue
        label = row[0]
        parts = []
        for index, cell in enumerate(row[1:], start=1):
            if not cell:
                continue
            name = header[index] if index < len(header) else ""
            parts.append(f"{name}: {cell}" if name else cell)
        out.append(f"• {label} — {', '.join(parts)}" if parts else f"• {label}")
    return out


def _flatten(text: str) -> str:
    """Strip every construct Telegram will not render, keeping the content."""
    lines_out: list[str] = []
    table: list[list[str]] = []
    header: list[str] = []

    def flush_table() -> None:
        nonlocal table, header
        if header and table:
            lines_out.extend(_table_to_lines(header, table))
        elif header:
            lines_out.append("• " + " — ".join(c for c in header if c))
        table, header = [], []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        if line.lstrip().startswith("|"):
            if _TABLE_SEP.match(line):
                continue
            cells = _cells(line)
            if not header:
                header = cells
            else:
                table.append(cells)
            continue
        flush_table()

        if _RULE.match(line):
            # A horizontal rule is a visual device with nothing behind it.
            continue

        line = _HEADING.sub("", line)
        line = _LINK.sub(r"\1 (\2)", line)
        line = _CODE.sub(r"\1", line)
        line = _BOLD_ITALIC.sub(r"\2", line)
        line = _BULLET.sub("• ", line)
        line = _DOUBLE_PERCENT.sub("%", line)
        lines_out.append(line)

    flush_table()

    # Collapse runs of blank lines: the flattening above leaves gaps where
    # rules and headings were, and Telegram shows every one of them.
    cleaned: list[str] = []
    for line in lines_out:
        if not line.strip() and (not cleaned or not cleaned[-1].strip()):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# --------------------------------------------------------------------------
# Splitting
# --------------------------------------------------------------------------


def _split(text: str, limit: int) -> list[str]:
    """
    Break a long post at paragraph boundaries, then at lines.

    Telegram refuses a message over the limit outright, so a post that would
    be silently lost is cut into parts the user pastes one after another.
    """
    if _utf16_len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            parts.append("\n".join(current).strip())
            current.clear()

    for block in text.split("\n\n"):
        candidate = "\n\n".join([*current, block]) if current else block
        if _utf16_len(candidate) <= limit:
            current.append(block)
            continue
        flush()
        if _utf16_len(block) <= limit:
            current.append(block)
            continue
        # One paragraph alone is over the limit: fall back to line-by-line.
        for line in block.splitlines():
            candidate = "\n".join([*current, line]) if current else line
            if _utf16_len(candidate) <= limit:
                current.append(line)
            else:
                flush()
                current.append(line)
    flush()

    total = len(parts)
    return [f"{part}\n\n({index}/{total})" for index, part in enumerate(parts, 1)]


# --------------------------------------------------------------------------
# Verified-figure cross-check
# --------------------------------------------------------------------------


def _verified_values(raw: Any) -> tuple[set[float], list[str]]:
    """Read the caller's verified figures into a set of values."""
    values: set[float] = set()
    problems: list[str] = []
    if raw is None:
        return values, problems
    if isinstance(raw, (int, float, str)):
        raw = [raw]
    if not isinstance(raw, list):
        return values, ["`tasdiqlangan` ro'yxat bo'lishi kerak"]

    for item in raw:
        candidate: Any = item
        if isinstance(item, dict):
            candidate = item.get("qiymat", item.get("raqam"))
        try:
            values.add(round(float(str(candidate).replace(" ", "").replace(",", ".")), 4))
        except (TypeError, ValueError):
            problems.append(str(item)[:60])
    return values, problems


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Turn a draft you have written into a post ready to paste into "
        "Telegram, and check its figures before it goes out. YOU write the "
        "text; this tool does not write, shorten or rephrase anything.\n"
        "It flattens what Telegram cannot render -- tables become lines, "
        "headings and `**bold**` lose their markers, links become 'text "
        "(url)' -- because a pasted message is sent literally, so Markdown "
        "arrives as visible punctuation. It rewrites every figure into Uzbek "
        "form (1 849 650,0), and splits a post past Telegram's 4096-character "
        "limit into numbered parts.\n"
        "PASS `tasdiqlangan` -- the values you actually read from "
        "`statind_data`. Every figure in the draft that is not among them "
        "comes back in `tasdiqlanmagan` with the words around it. A Telegram "
        "post is published output: a number that was never checked against "
        "the register must not leave in one. Nothing is deleted -- what to do "
        "about a flagged figure is your decision, but do not hand the user a "
        "post with one still in it without saying so.\n"
        "Years, ordinals and small counts are not checked; classifier codes "
        "are left untouched.\n"
        "REPRODUCE `post` IN FULL IN YOUR REPLY, exactly as it comes back. The "
        "user copies that text into Telegram, so it is the whole point of "
        "the call -- an answer that only says a post was prepared, or "
        "summarises it, leaves them with nothing to copy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "matn": {
                "type": "string",
                "description": (
                    "The post you wrote, in whatever formatting is natural. "
                    "Tables and Markdown are fine here -- they get flattened."
                ),
            },
            "sarlavha": {
                "type": "string",
                "description": (
                    "Optional first line, e.g. '2025-yil yakunlari'. Omit if "
                    "`matn` already opens with one."
                ),
            },
            "manba": {
                "type": "string",
                "description": (
                    "Optional closing source line, e.g. 'Manba: stat.uz "
                    "(1.01.01.0001)'. Cite what you actually verified against."
                ),
            },
            "tasdiqlangan": {
                "type": "array",
                "items": {"type": "number"},
                "description": (
                    "The figures you read from `statind_data` and are "
                    "publishing. Anything numeric in the draft that is not "
                    "here is flagged as unverified."
                ),
            },
        },
        "required": ["matn"],
    },
}


def telegram_post_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Format and check a Telegram post. Returns a dict; the plugin serializes."""
    text = str((params or {}).get("matn") or "").strip()
    if not text:
        return {"success": False, "error": "matn bo'sh -- avval postni yozing"}

    title = str(params.get("sarlavha") or "").strip()
    source = str(params.get("manba") or "").strip()

    body = _normalise_numbers(_flatten(text))
    pieces = [p for p in (title, body, source) if p]
    post = "\n\n".join(pieces)

    verified, unreadable = _verified_values(params.get("tasdiqlangan"))
    flagged: list[dict[str, Any]] = []
    for value, context in _figures_in(post):
        if value not in verified:
            flagged.append({"raqam": _uz_number(value), "matnda": context})

    parts = _split(post, _TELEGRAM_LIMIT - _SPLIT_MARGIN)
    out: dict[str, Any] = {
        "success": True,
        "post": post,
        "belgi": _utf16_len(post),
        "chegara": _TELEGRAM_LIMIT,
    }
    if len(parts) > 1:
        out["qismlar"] = parts
        out["jami_qism"] = len(parts)
        out["izoh_uzunlik"] = (
            f"post {_utf16_len(post)} belgi -- Telegram chegarasi "
            f"{_TELEGRAM_LIMIT}. `qismlar` ni ketma-ket joylashtiring."
        )

    if not verified:
        out["ogohlantirish"] = (
            "`tasdiqlangan` berilmadi -- postdagi birorta raqam reyestr bilan "
            "solishtirilmadi. Chop etishdan oldin tekshiring."
        )
    elif flagged:
        out["tasdiqlanmagan"] = flagged
        out["ogohlantirish"] = (
            f"{len(flagged)} ta raqam tasdiqlangan ro'yxatda yo'q. Ularni "
            "`statind_data` bilan tekshiring yoki postdan olib tashlang -- "
            "tekshirilmagan raqamni e'lon qilmang."
        )
    else:
        out["tekshiruv"] = "postdagi barcha raqamlar tasdiqlangan ro'yxatda bor"
    if unreadable:
        out["oqib_bolmadi"] = unreadable

    logger.info(
        "telegram_post: %d chars, %d parts, %d unverified",
        out["belgi"],
        len(parts),
        len(flagged),
    )
    return out
