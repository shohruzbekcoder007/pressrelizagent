"""
`pdf_extract` — the checkable claims inside a converted release.

STATUS: SCAFFOLD. The plumbing below is real -- resolving the Markdown,
reading it, capping the reply, reporting honestly. `_claims()` is the one part
still to be written, and it returns nothing yet rather than guessing, so a
caller is never handed invented claims that look finished. Filling it in is
the next step.

The unit this tool deals in is a **claim**: one statement in the release that
can be true or false against the register.

    {"jumla":  "2025-yilda yalpi ichki mahsulot 1 850 650,0 mlrd so'mni tashkil etdi",
     "raqam":  1850650.0,
     "birlik": "mlrd so'm",
     "davr":   "2025",
     "korsatkich": "Yalpi ichki mahsulot"}

That shape exists because of what comes after it. `korsatkich` is the search
string for `statind_code`, `davr` becomes `periods` for `statind_data`, and
`raqam` with `birlik` is what the published value gets compared against. A
claim that carries all four can be checked without another look at the
document; one that carries only a number cannot be checked at all. So the
extraction is judged by how often it fills those fields, not by how much text
it returns.

`jumla` is kept verbatim for the same reason the conversion has no LLM in it:
when the agent reports a mismatch it has to quote the sentence the release
actually printed, and a paraphrase would put words in the publisher's mouth.

Two ways to fill `_claims()`, still open:
  * **Deterministic** -- regex over figures, units and dates. Cheap, repeatable,
    and it will miss claims whose indicator is named a paragraph away.
  * **The host LLM** -- pass the Markdown up and let the coordinator read it.
    This is how `statind_code` ended up working: the tool returns candidates
    and the model decides, because the model has the conversation and the tool
    does not. It costs a turn and context.
Note that the second needs no LLM client in here at all, which is why nothing
in this file imports one.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ._pdf import PdfError, data_root, md_dir, md_path_for, relative

logger = logging.getLogger("hermes.plugin.pdfmd.extract")

TOOL_NAME = "pdf_extract"
TOOLSET_NAME = "pdfmd"

_MAX_CLAIMS = 50
_DEFAULT_CLAIMS = 20


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


def _claims(text: str, limit: int) -> list[dict[str, Any]]:
    """
    Pull the checkable claims out of a converted release.

    NOT IMPLEMENTED. Returns an empty list on purpose: an empty result is
    visibly unfinished, whereas a plausible-looking guess would be reported to
    the user as if the document had been read.

    Contract, once written -- each claim carries as many of these as the text
    supports, and omits the rest rather than filling them in:
        jumla       the sentence, verbatim
        raqam       the figure, as a number
        birlik      its unit, as printed
        davr        the period ('2025', '2025-Q2', '2024-M03')
        korsatkich  the indicator name, ready for `statind_code`
    """
    del text, limit
    return []


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Pull the checkable statistical claims out of a press release that "
        "`pdf_to_md` has already converted. Call that first; this tool reads "
        "the stored Markdown and never touches the PDF.\n"
        "Each claim is one statement that can be verified against the "
        "register: the sentence verbatim (`jumla`), the figure (`raqam`), its "
        "unit (`birlik`), the period (`davr`) and the indicator name "
        "(`korsatkich`). Fields the text does not support are left out rather "
        "than guessed.\n"
        "THIS IS THE INPUT TO THE REGISTER TOOLS. Take `korsatkich` to "
        "`statind_code`, then `davr` to `statind_data`, then compare `raqam` "
        "against the published value -- checking the unit first, since a "
        "release and the register do not always print the same one.\n"
        "The claims are what the release states, not what is true. Nothing "
        "here is verified; verifying is the next call.\n"
        "SCAFFOLD: extraction is not implemented yet and the list comes back "
        "empty. Say so plainly rather than reading the preview and answering "
        "as though the document had been analysed."
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
                    f"maximum {_MAX_CLAIMS}."
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

    claims = _claims(text, limit)
    logger.info(
        "pdf_extract: %s, %d chars, %d claims", relative(path), len(text), len(claims)
    )
    out: dict[str, Any] = {
        "success": True,
        "fayl": relative(path),
        "belgi": len(text),
        "dalillar": claims,
        "jami": len(claims),
    }
    if not claims:
        # Distinguish "nothing to find" from "the finder is not written yet" --
        # they read the same in an empty list, and only one of them is a fact
        # about the document.
        out["holat"] = (
            "bajarilmagan: dalil ajratish hali yozilmagan -- bo'sh ro'yxat "
            "hujjat haqida xulosa emas"
        )
    return out
