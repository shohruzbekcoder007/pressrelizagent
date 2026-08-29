"""
`pdf_to_md` — a press release PDF into Markdown on disk.

The tool returns a **path and a preview, not the document**. That is the whole
shape of it. A converted release runs to tens of thousands of tokens, and
handing all of it back would repeat the mistake this project already measured
and rejected once: the flat classifier file was 146,710 tokens and over half
the model's context on every call. Markdown goes to `data/md/`, the reply
carries the first page or so, and `pdf_extract` reads the rest back only as
the fields that matter.

Conversion is deterministic -- no LLM. Whatever the backend produces is what
gets stored, so the same PDF converts to the same Markdown every time and a
figure read out of it can be traced to a page rather than to a paraphrase.

This file is bind-mounted into the container (`./plugins:/app/plugins:ro`), so
editing it needs a container restart, not a rebuild.
"""

from __future__ import annotations

import logging
from typing import Any

from ._pdf import (
    PdfError,
    convert as _convert,
    md_dir,
    md_path_for,
    pdf_dir,
    relative,
    resolve_pdf,
    stats,
)

logger = logging.getLogger("hermes.plugin.pdfmd.convert")

TOOL_NAME = "pdf_to_md"
TOOLSET_NAME = "pdfmd"

# Conversion is I/O and CPU, not a query; a handful per call is plenty and a
# long list is more likely a mistake than an intention.
_MAX_FILES = 5
_DEFAULT_PREVIEW = 1200
_MAX_PREVIEW = 8000


def _available() -> list[str]:
    """PDFs sitting in the drop folder, for a 'file not found' that helps."""
    try:
        return sorted(p.name for p in pdf_dir().glob("*.pdf"))[:20]
    except Exception:  # noqa: BLE001
        return []


def _one(name: str, force: bool, preview: int) -> dict[str, Any]:
    try:
        pdf = resolve_pdf(name)
    except PdfError as exc:
        entry: dict[str, Any] = {"soralgan": name, "xato": str(exc)}
        found = _available()
        if found:
            entry["mavjud_fayllar"] = found
        return entry

    out = md_path_for(pdf)
    reused = False
    if out.is_file() and not force:
        # Converting again costs seconds and produces the same bytes, so a
        # second question about the same release should not pay for it. `force`
        # is there for the case the file on disk was replaced.
        body = out.read_text(encoding="utf-8")
        pages, backend = 0, "cache"
        reused = True
    else:
        try:
            body, pages, backend = _convert(pdf)
        except PdfError as exc:
            return {"soralgan": name, "fayl": relative(pdf), "xato": str(exc)}
        md_dir().mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")

    result: dict[str, Any] = {
        "soralgan": name,
        "fayl": relative(pdf),
        "md_fayl": relative(out),
        "belgi": len(body),
        "backend": backend,
        "holat": "avvaldan mavjud" if reused else "aylantirildi",
        # Named `boshlanishi`, not `matn`, so nothing downstream mistakes a
        # preview for the document.
        "boshlanishi": body[:preview],
    }
    if pages:
        result["sahifa"] = pages
    if len(body) > preview:
        result["izoh"] = (
            f"faqat birinchi {preview} belgi ko'rsatildi -- to'liq matn uchun "
            f"`pdf_extract` ni `{relative(out)}` bilan chaqiring"
        )
    return result


TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Convert a press release PDF to Markdown. The file must already be in "
        "the shared folder `data/pdf/`; the user puts it there, this tool does "
        "not download anything.\n"
        "IT RETURNS A PATH AND A PREVIEW, NOT THE DOCUMENT. The full Markdown "
        "is written to `data/md/` and is meant to be read by `pdf_extract`, "
        "which pulls out the checkable claims. Do not ask for a larger preview "
        "in order to read the whole release -- call `pdf_extract` instead.\n"
        "Conversion is mechanical: no model rewrites the text, so a figure in "
        "the Markdown is the figure printed in the PDF. `backend` names the "
        "converter that produced it, and `sahifa` the page count. Pages are "
        "separated by a `---` rule.\n"
        "A scanned PDF with no text layer cannot be converted and comes back "
        "as an error asking for OCR; that is an answer, not a fault.\n"
        "Converting the same file twice reuses the stored Markdown (`holat` "
        "says which happened). Pass `force` only if the PDF itself was "
        "replaced."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "PDF file names in `data/pdf/`, e.g. 'yanvar_reliz.pdf'. "
                    f"At most {_MAX_FILES} per call."
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Re-convert even if Markdown already exists. Only needed "
                    "when the PDF was replaced with a new version."
                ),
            },
            "preview": {
                "type": "integer",
                "description": (
                    f"Characters of the beginning to return. Defaults to "
                    f"{_DEFAULT_PREVIEW}, maximum {_MAX_PREVIEW}. This is for "
                    "recognising the document, not for reading it."
                ),
            },
        },
        "required": ["files"],
    },
}


def pdf_to_md_handler(params: dict[str, Any]) -> dict[str, Any]:
    """Convert PDFs to Markdown. Returns a dict; the plugin serializes it."""
    raw = (params or {}).get("files")
    if isinstance(raw, str):
        # A single name passed unwrapped is the likeliest caller mistake and
        # costs nothing to accept.
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return {"success": False, "error": "files must be a non-empty list"}

    names = [str(n).strip() for n in raw if str(n or "").strip()]
    if not names:
        return {"success": False, "error": "files must contain a file name"}
    if len(names) > _MAX_FILES:
        return {
            "success": False,
            "error": (
                f"{len(names)} files requested; at most {_MAX_FILES} per call "
                "-- split the list"
            ),
        }

    try:
        preview = int(params.get("preview") or _DEFAULT_PREVIEW)
    except (TypeError, ValueError):
        preview = _DEFAULT_PREVIEW
    preview = max(0, min(preview, _MAX_PREVIEW))
    force = bool(params.get("force"))

    results = [_one(name, force, preview) for name in names]
    failed = [r["soralgan"] for r in results if r.get("xato")]
    logger.info(
        "pdf_to_md: %d files, %d failed, backend=%s",
        len(results),
        len(failed),
        stats().get("backend"),
    )
    out: dict[str, Any] = {
        "success": True,
        "natijalar": results,
        "jami": len(results),
    }
    if failed:
        # Named explicitly so the caller does not have to scan the list to
        # notice a file that never converted.
        out["aylantirilmadi"] = failed
    return out
