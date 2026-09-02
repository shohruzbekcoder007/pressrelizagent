"""
`text_check` — the spelling, consistency and logic faults in a release.

A figure can be right and the sentence around it still wrong: an index
published as if it were a growth rate, a share over 100 per cent, the same
number written two ways on one page, a word run into the next by a missing
space. Those are not caught by comparing a value against the register,
because the value matches. They are caught here.

**Precision over recall, deliberately.** These findings go in front of an
editor next to the figure check, and a checker that cries wolf gets switched
off. Every rule below either describes a certain fault (`xato`) or an
inconsistency worth a human glance (`shubha`), and anything that could not be
told apart from normal writing was left out rather than guessed at.

That line was drawn against the real corpus, not in the abstract. The
converted analytical report was measured before any of this was written: one
apostrophe style throughout, no mixed-script words, no doubled words, no
mixed number styles. What it *does* contain is `I.Yalpi`, `II.Sanoat`,
`IV.Inflyatsiya` -- a roman numeral, a full stop and a capital, which is a
section heading and not a missing space. A naive rule would have opened this
tool's account with six false positives on a clean document, so roman
numerals and the corpus's own abbreviations (`mln.`, `mlrd.`) are excluded by
name.

Nothing here rewrites the text. Each finding names the fragment and says what
looks wrong; the wording is the editor's to fix.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("hermes.plugin.tahrir.check")

TOOL_NAME = "text_check"
TOOLSET_NAME = "tahrir"

_MAX_FINDINGS = 40
# Enough of the sentence to see the fault without pasting the paragraph.
_CONTEXT = 70


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - _CONTEXT // 2)
    return " ".join(text[left : end + _CONTEXT // 2].split())


# --------------------------------------------------------------------------
# Orthography
# --------------------------------------------------------------------------
_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_WORD = re.compile(r"[^\s|]{2,}")

# The five characters this corpus uses for the Uzbek apostrophe. A document
# should pick one; several means the text was assembled from sources that
# disagreed, and search and sorting both suffer for it.
_APOSTROPHES = {
    "‘": "'",
    "’": "'",
    "ʻ": "ʻ",
    "ʼ": "ʼ",
    "`": "`",
    "'": "'",
}

_DOUBLE_WORD = re.compile(r"\b(\w{3,})(\s+)\1\b", re.IGNORECASE)
# `..` but not `...`, plus the doubled comma, semicolon and percent sign. The
# doubled percent is here because a real post came back carrying it twice.
_DOUBLE_PUNCT = re.compile(r"(?<!\.)\.\.(?!\.)|,,|;;|%%")

# A full stop between a lowercase letter and a capital is usually a lost
# space -- but not after a numbered-section roman numeral, and not after the
# abbreviations this corpus writes without one.
_RUN_TOGETHER = re.compile(r"\b([\w'ʻʼ`]+)\.([A-ZЀ-ӿ])")
_ROMAN = re.compile(r"^[IVXLCDM]+$")
_ABBREVIATIONS = {
    "mln", "mlrd", "trln", "ming", "y", "yy", "t", "kv", "ta", "dol",
    "doll", "foiz", "km", "kg", "soat", "dona", "nafar",
}

# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------
_GROUPED = re.compile(r"\b\d{1,3}(?: \d{3})+(?:,\d+)?\b")
_ENGLISH_GROUPED = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
_DOT_DECIMAL = re.compile(r"(?<![\d.])\d+\.\d+(?![\d.])")
_ANY_NUMBER = re.compile(r"\b\d[\d ]*(?:[.,]\d+)?\b")
_CODE = re.compile(r"\d+(?:\.\d+){2,}")


def _value_of(raw: str) -> Optional[float]:
    try:
        return round(float(raw.replace(" ", "").replace(",", ".")), 4)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Domain logic
# --------------------------------------------------------------------------
# The signature error of this subject. Growth is published as an index --
# 107,7 means growth of 7,7 per cent -- and writing "107,7 foizga o'sdi" says
# the economy grew by 107,7 per cent, which is a different and untrue claim.
# The whole verification chain misses it, because 107,7 *is* the register's
# number.
_INDEX_AS_GROWTH = re.compile(
    r"(\d{2,4}(?:,\d+)?)\s*(?:foiz|%)\w*\s*(?:ga|iga)?\s*"
    r"(o'sdi|oshdi|ko'paydi|o'sgan|oshgan|o'sish\w*|ortdi)",
    re.IGNORECASE,
)
# A share of a whole cannot exceed the whole.
_SHARE_OVER_100 = re.compile(
    r"ulush\w*[^.\n]{0,60}?(\d{2,4}(?:,\d+)?)\s*(?:foiz|%)",
    re.IGNORECASE,
)
# A rule that looked for a rise and a fall together was written here and
# then removed. On the real report it produced this service's only false
# positive -- a paragraph reading "turnover rose ... exports fell ... imports
# rose", three correct sentences about three indicators. Even confined to one
# sentence it cannot stand: "Eksport kamaydi, import esa oshdi" is ordinary,
# correct writing. Telling contrast from contradiction needs to know what
# each verb belongs to, which is judgement, and judgement is the model's --
# it is reading the text anyway.


def _finding(kind: str, severity: str, fragment: str, note: str,
             suggestion: str = "") -> dict[str, Any]:
    out = {"tur": kind, "ogirlik": severity, "parcha": fragment, "izoh": note}
    if suggestion:
        out["taklif"] = suggestion
    return out


def _orthography(text: str) -> Iterator[dict[str, Any]]:
    # Mixed scripts inside one word: a keyboard or OCR slip every time. Uzbek
    # is written in either alphabet but never both inside a single word.
    for match in _WORD.finditer(text):
        word = match.group(0)
        if _LATIN.search(word) and _CYRILLIC.search(word):
            yield _finding(
                "imlo", "xato",
                _context(text, match.start(), match.end()),
                f"{word!r} ichida lotin va kirill harflari aralashgan",
                "so'zni bitta alifboda qayta yozing",
            )

    used = {ch for ch in _APOSTROPHES if ch in text}
    if len(used) > 1:
        counts = ", ".join(f"{ch!r}: {text.count(ch)}" for ch in sorted(used))
        yield _finding(
            "izchillik", "shubha", counts,
            f"hujjatda {len(used)} xil apostrof ishlatilgan",
            "bittasini tanlab, hamma joyda shuni qo'llang",
        )

    for match in _DOUBLE_WORD.finditer(text):
        yield _finding(
            "imlo", "xato",
            _context(text, match.start(), match.end()),
            f"{match.group(1)!r} so'zi ketma-ket takrorlangan",
            "ortiqchasini o'chiring",
        )

    for match in _DOUBLE_PUNCT.finditer(text):
        yield _finding(
            "imlo", "xato",
            _context(text, match.start(), match.end()),
            f"{match.group(0)!r} -- tinish belgisi ikki marta",
        )

    for match in _RUN_TOGETHER.finditer(text):
        word = match.group(1)
        if _ROMAN.match(word) or word.lower() in _ABBREVIATIONS:
            # A numbered section heading or a known abbreviation, not a fault.
            continue
        if not word[-1:].islower():
            continue
        yield _finding(
            "imlo", "xato",
            _context(text, match.start(), match.end()),
            f"{word!r} dan keyin nuqtadan so'ng bo'shliq yo'q",
            "nuqtadan keyin bo'shliq qo'ying",
        )


def _consistency(text: str) -> Iterator[dict[str, Any]]:
    styles: dict[str, int] = {}
    if _GROUPED.search(text):
        styles["1 849 650,0 (bo'shliqli)"] = len(_GROUPED.findall(text))
    if _ENGLISH_GROUPED.search(text):
        styles["1,849,650.0 (inglizcha)"] = len(_ENGLISH_GROUPED.findall(text))
    dots = [m for m in _DOT_DECIMAL.finditer(text) if not _inside_code(text, m)]
    if dots:
        styles["8.5 (nuqtali o'nlik)"] = len(dots)
    if len(styles) > 1:
        yield _finding(
            "izchillik", "shubha",
            ", ".join(f"{k}: {v}" for k, v in styles.items()),
            "bitta hujjatda bir nechta son yozuv uslubi ishlatilgan",
            "o'zbek statistikasi uslubi: 1 849 650,0",
        )

    # The same value spelled two ways -- `1 849 650,0` in one paragraph and
    # `1849650,0` in the next reads as two different figures to anyone
    # skimming, and to any search.
    spellings: dict[float, set[str]] = {}
    for match in _ANY_NUMBER.finditer(text):
        raw = match.group(0).strip()
        if len(raw.replace(" ", "")) < 4 or _inside_code(text, match):
            continue
        value = _value_of(raw)
        if value is None:
            continue
        spellings.setdefault(value, set()).add(raw)
    for value, forms in sorted(spellings.items()):
        if len(forms) > 1:
            yield _finding(
                "izchillik", "shubha", " / ".join(sorted(forms)),
                "bir xil qiymat turlicha yozilgan",
                "hammasini bitta ko'rinishga keltiring",
            )


def _inside_code(text: str, match: re.Match) -> bool:
    return any(
        m.start() <= match.start() < m.end() for m in _CODE.finditer(text)
    )


def _logic(text: str) -> Iterator[dict[str, Any]]:
    for match in _INDEX_AS_GROWTH.finditer(text):
        value = _value_of(match.group(1))
        if value is None or value <= 100:
            continue
        yield _finding(
            "mantiq", "xato",
            _context(text, match.start(), match.end()),
            f"{match.group(1)} foizga o'sish deyilgan -- bu indeks bo'lsa, "
            f"o'sish {_format(value - 100)} foiz",
            f"'{_format(value - 100)} foizga o'sdi' yoki "
            f"'{match.group(1)} foizni tashkil etdi'",
        )

    for match in _SHARE_OVER_100.finditer(text):
        value = _value_of(match.group(1))
        if value is None or value <= 100:
            continue
        yield _finding(
            "mantiq", "xato",
            _context(text, match.start(), match.end()),
            f"ulush {match.group(1)} foiz -- ulush 100 foizdan oshmaydi",
        )


def _format(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------


def _read_file(name: str) -> str:
    """Read a converted release from the data mount, safely."""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw or ".." in raw or "\x00" in raw:
        raise ValueError(f"fayl nomi ruxsat etilmagan: {name!r}")

    root = Path(os.getenv("PDF_DATA_DIR") or "/app/data").resolve()
    md_dir = root / "md"
    stem = Path(raw).stem
    for candidate in (root / raw, md_dir / raw, md_dir / f"{stem}.md"):
        path = candidate.resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            return path.read_text(encoding="utf-8")

    known = sorted(p.name for p in md_dir.glob("*.md"))[:20]
    hint = f" (mavjud: {', '.join(known)})" if known else ""
    raise ValueError(f"fayl topilmadi: {name!r}{hint}")


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Find the spelling, consistency and logic faults in a press release: "
        "the problems that survive a figure check because the figure itself "
        "is right.\n"
        "Pass `matn` for text you have in hand, or `fayl` for a release "
        "`pdf_to_md` already converted.\n"
        "RUN THIS EARLY, alongside the figure check -- not after. A release "
        "is reviewed once, and an editor wants the wording problems and the "
        "wrong numbers in the same reply.\n"
        "What it catches: Latin and Cyrillic mixed inside one word; several "
        "apostrophe styles in one document; a value written two different "
        "ways; repeated words; doubled punctuation; a missing space after a "
        "full stop; a share above 100 per cent; and the error this subject "
        "produces most -- an index published as a growth rate ('107,7 foizga "
        "o'sdi' when the growth was 7,7 per cent). That last one is invisible "
        "to `statind_data`, because 107,7 is exactly what the register says.\n"
        "It does not judge whether a sentence is confusing or whether a claim "
        "contradicts itself -- that needs to know what each statement is "
        "about, and you are reading the text anyway. Raise those yourself.\n"
        "Each finding is `xato` (a definite fault) or `shubha` (worth a look, "
        "may be intentional). Report them as they come; the tool changes no "
        "wording, and rewriting is the editor's call. An empty list is a real "
        "answer -- say the text is clean rather than inventing something."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "matn": {
                "type": "string",
                "description": "The text to check. Use this or `fayl`.",
            },
            "fayl": {
                "type": "string",
                "description": (
                    "A converted release to check instead, by the name "
                    "`pdf_to_md` returned ('md/yanvar_reliz.md')."
                ),
            },
        },
    },
}


def text_check_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Review text for language and logic faults. Returns a dict."""
    params = params or {}
    text = str(params.get("matn") or "")
    source = "matn"

    if not text.strip():
        name = str(params.get("fayl") or "").strip()
        if not name:
            return {
                "success": False,
                "error": "matn ham, fayl ham berilmadi",
            }
        try:
            text = _read_file(name)
        except (ValueError, OSError) as exc:
            return {"success": False, "error": str(exc)}
        source = name

    findings: list[dict[str, Any]] = []
    for producer in (_orthography, _consistency, _logic):
        try:
            findings.extend(producer(text))
        except Exception as exc:  # noqa: BLE001
            # One failing rule must not cost the caller every other finding.
            logger.exception("text_check: rule %s failed", producer.__name__)
            findings.append(
                _finding("xato", "shubha", "", f"tekshiruv qismi ishlamadi: {exc}")
            )

    total = len(findings)
    findings = findings[:_MAX_FINDINGS]

    kinds: dict[str, int] = {}
    for item in findings:
        kinds[item["tur"]] = kinds.get(item["tur"], 0) + 1

    out: dict[str, Any] = {
        "success": True,
        "manba": source,
        "belgi": len(text),
        "muammolar": findings,
        "jami": total,
        "turlari": kinds,
    }
    if total > len(findings):
        out["qisqartirildi"] = (
            f"{total} muammodan {len(findings)} tasi ko'rsatildi"
        )
    if not total:
        out["xulosa"] = "imlo, izchillik va mantiq bo'yicha muammo topilmadi"

    logger.info("text_check: %s, %d chars, %d findings", source, len(text), total)
    return out
