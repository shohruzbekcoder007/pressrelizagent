"""
Shared file handling and PDF backends for the `pdfmd` toolset.

Two things are settled here rather than in each tool.

**Where files live.** Everything happens under `/app/data`, which
docker-compose bind-mounts from `./data` read-write. A PDF dropped into
`data/pdf/` on the host is visible to the container immediately, and the
Markdown written back to `data/md/` is visible on the host -- no upload
endpoint, no volume of its own. Names are resolved against that root and
checked to still be inside it, so a caller cannot walk out of the mount with
`../../etc/passwd`; the model composes these arguments, so the check is not
theoretical.

**Which backend does the conversion.** Kept behind a registry because the
choice is not obvious and is expensive to reverse: `pypdf` is pure Python and
tiny but flattens tables, `PyMuPDF` keeps layout but is AGPL, `docling` and
`marker` produce the best Markdown and pull in torch. A press release is
mostly tables of figures, so the answer depends on how the real files look --
which we will know after the first conversions. Until then `PDF_BACKEND=auto`
takes whichever is installed and names it in every reply, so output is never
silently attributed to the wrong converter.

A backend is a *dependency*, not code: adding one means editing
requirements.txt and running `docker compose build`. This file itself is
bind-mounted (`./plugins:/app/plugins:ro`) and needs only a restart.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("hermes.plugin.pdfmd.pdf")

# A press release is a handful of pages. The cap is here so a mistaken path to
# some large report fails with a sentence instead of an out-of-memory kill.
MAX_PDF_BYTES = 64 * 1024 * 1024

# Slashes are allowed so `2025/yanvar.pdf` works; everything else that could
# change the meaning of a path is not. `..` is rejected outright rather than
# normalised, because a caller writing it meant something we do not want.
_UNSAFE = re.compile(r"(^|/)\.\.(/|$)|[\x00\\]")


class PdfError(Exception):
    """A failure whose message is meant to be shown to the user as-is."""


def data_root() -> Path:
    return Path(os.getenv("PDF_DATA_DIR") or "/app/data").resolve()


def pdf_dir() -> Path:
    return data_root() / "pdf"


def md_dir() -> Path:
    return data_root() / "md"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_pdf(name: str) -> Path:
    """
    Read a caller-supplied name as a path to a PDF under the data mount.

    Accepts `hisobot.pdf`, `pdf/hisobot.pdf` and `/app/data/pdf/hisobot.pdf`
    alike -- all three spellings turn up in a conversation and none of them is
    wrong.
    """
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        raise PdfError("fayl nomi bo'sh")
    if _UNSAFE.search(raw):
        raise PdfError(f"fayl nomi ruxsat etilmagan: {name!r}")

    root = data_root()
    candidate = Path(raw)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        # Bare name first, since that is what a user says out loud; then the
        # same name read as relative to the data root, which is what a caller
        # repeating an earlier reply sends back.
        path = (pdf_dir() / raw).resolve()
        if not path.is_file():
            path = (root / raw).resolve()

    if not _inside(path, root):
        raise PdfError(f"fayl ma'lumotlar papkasidan tashqarida: {name!r}")
    if not path.is_file():
        raise PdfError(f"fayl topilmadi: {name!r} (data/pdf/ ichiga qo'ying)")
    if path.suffix.lower() != ".pdf":
        raise PdfError(f"bu PDF emas: {path.name}")

    size = path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise PdfError(
            f"fayl juda katta: {size // 1024 // 1024} MB "
            f"(chegara {MAX_PDF_BYTES // 1024 // 1024} MB)"
        )
    return path


def md_path_for(pdf: Path) -> Path:
    """
    Where the Markdown for this PDF is kept.

    Deterministic, so converting the same file twice overwrites its own output
    instead of piling up copies -- and so `pdf_extract` can find the Markdown
    from the PDF name alone, without the caller carrying a handle between two
    tool calls.
    """
    return md_dir() / f"{pdf.stem}.md"


def relative(path: Path) -> str:
    """A path as the caller should see it: short, and rooted at the mount."""
    root = data_root()
    return str(path.relative_to(root)) if _inside(path, root) else str(path)


# ---------------------------------------------------------------------------
# Conversion backends
# ---------------------------------------------------------------------------
# Each takes a path and returns (markdown, page_count). None is installed yet.


def _via_pypdf(path: Path) -> tuple[str, int]:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    # Page breaks are kept deliberately: a figure and the heading that gives it
    # meaning often sit on opposite sides of one, and the extractor downstream
    # has to be able to tell.
    return "\n\n---\n\n".join(t for t in pages if t), len(reader.pages)


def _via_pymupdf(path: Path) -> tuple[str, int]:
    import fitz  # type: ignore[import-not-found]

    with fitz.open(str(path)) as doc:
        pages = [page.get_text("text").strip() for page in doc]
        return "\n\n---\n\n".join(t for t in pages if t), doc.page_count


_BACKENDS: dict[str, Callable[[Path], tuple[str, int]]] = {
    "pypdf": _via_pypdf,
    "pymupdf": _via_pymupdf,
}

# `pypdf` is the import name; PyMuPDF installs as `fitz`.
_MODULES = {"pypdf": "pypdf", "pymupdf": "fitz"}

# Order matters only for `auto`: best output first, so a machine with both
# installed does not quietly fall back to the weaker one.
_AUTO_ORDER = ("pymupdf", "pypdf")


def _installed(name: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(_MODULES[name]) is not None
    except Exception:  # noqa: BLE001
        return False


def backend_name() -> str:
    """The backend that would run, or '' if none is installed."""
    wanted = (os.getenv("PDF_BACKEND") or "auto").strip().lower()
    if wanted and wanted != "auto":
        if wanted not in _BACKENDS:
            raise PdfError(
                f"PDF_BACKEND={wanted!r} noma'lum "
                f"(mavjud: {', '.join(sorted(_BACKENDS))})"
            )
        return wanted
    for name in _AUTO_ORDER:
        if _installed(name):
            return name
    return ""


def convert(path: Path) -> tuple[str, int, str]:
    """PDF -> (markdown, page_count, backend_used)."""
    name = backend_name()
    if not name:
        raise PdfError(
            "PDF kutubxonasi o'rnatilmagan. requirements.txt ga `pypdf` yoki "
            "`PyMuPDF` qo'shib, `docker compose build app` bajaring."
        )
    try:
        body, pages = _BACKENDS[name](path)
    except ImportError as exc:
        raise PdfError(f"{name} import bo'lmadi: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"{name} faylni o'qiy olmadi: {exc}") from exc

    if not body.strip():
        # A scanned release is a real case and deserves a real answer: there is
        # no text layer to extract, and OCR is a different tool than this one.
        raise PdfError(
            "PDF da matn qatlami yo'q (skan qilingan bo'lishi mumkin) -- "
            "OCR kerak"
        )
    return body, pages, name


def stats() -> dict[str, Any]:
    """What the tools report about their own environment."""
    return {
        "backend": backend_name() or None,
        "pdf_dir": relative(pdf_dir()),
        "md_dir": relative(md_dir()),
    }
