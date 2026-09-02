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

**Which backend does the conversion.** Normally the separate PP-StructureV3
service at `PDF_CONVERT_URL`, which is a layout model rather than a text
extractor: it reconstructs tables and reads scanned pages, and a press release
is mostly tables of figures. It needs no Python dependency here either -- an
HTTP POST over httpx, which is already a requirement -- so the whole toolset
works without touching the image.

The in-process libraries stay behind it as a fallback for when that service is
down, and they are not equivalent: `pypdf` flattens tables and reads nothing
from a scan, `PyMuPDF` keeps more layout but is AGPL. Neither is installed.
`PDF_BACKEND=auto` takes the first that is usable and names it in every reply,
so output is never silently attributed to the wrong converter.

Adding an in-process backend is a *dependency* change: requirements.txt plus
`docker compose build`. Pointing at the service is not -- this file is
bind-mounted (`./plugins:/app/plugins:ro`) and needs only a restart.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

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


def ensure_dirs() -> None:
    """
    Create the drop and output folders if they are missing.

    `data/` is a bind mount whose contents are gitignored, so a fresh clone or
    a new deployment has the mount but not the two folders inside it. Creating
    them here means the first thing a user is told is "put the PDF in
    data/pdf/" rather than a path that does not exist yet.
    """
    for path in (pdf_dir(), md_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("pdfmd: cannot create %s: %s", path, exc)


def closest(wanted: str, candidates: list[str]) -> Optional[str]:
    """
    The one existing name a near-miss obviously meant, or None.

    Uploaded reports carry long machine-made names, and a model retyping one
    quietly normalises it -- a real turn asked six times for
    `talil-2026-j_-janvar-iyun-...` when the file on disk said `-yanvar-`,
    the Russian spelling of the month against the Uzbek one. One letter in
    forty-five. The listing of real names was in every error reply and the
    difference was still missed, so the tool has to close the gap itself
    rather than expecting the caller to spot it.

    Two guards keep this from opening the wrong document, which for a
    verification tool would be worse than failing: the match must be very
    close, and it must be clearly closer than the runner-up. Two genuinely
    similar names -- last month's report and this month's -- resolve to
    nothing and the caller is told to choose.
    """
    from difflib import SequenceMatcher

    target = wanted.strip().lower()
    if not target or not candidates:
        return None

    scored = sorted(
        ((SequenceMatcher(None, target, c.lower()).ratio(), c) for c in candidates),
        reverse=True,
    )
    best_score, best = scored[0]
    if best_score < 0.82:
        return None
    if len(scored) > 1 and scored[1][0] > best_score - 0.06:
        # Two names about equally close: guessing between them is exactly the
        # case where being wrong is silent and expensive.
        return None
    return best


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
        # Before giving up, check whether one existing file is unmistakably
        # what was meant -- see `closest`. The reply still reports the name
        # actually opened, so a substitution is never invisible.
        try:
            available = sorted(p.name for p in pdf_dir().glob("*.pdf"))
        except OSError:
            available = []
        near = closest(Path(raw).name, available)
        if near:
            logger.info("pdfmd: %r resolved to nearest match %r", name, near)
            path = (pdf_dir() / near).resolve()
        else:
            hint = f" (mavjud: {', '.join(available)})" if available else ""
            raise PdfError(
                f"fayl topilmadi: {name!r}{hint} -- data/pdf/ ichiga qo'ying"
            )
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
# Each takes a path and returns (markdown, page_count, extra), where `extra` is
# whatever that backend can say about the run and every reader treats as
# optional.


def convert_url() -> str:
    """
    The PP-StructureV3 service endpoint, or '' if none is configured.

    Note this is read from the environment on every call rather than captured
    at import: the tools are bind-mounted and reload on restart, and an
    operator moving the converter should not need to remember which.
    """
    return (os.getenv("PDF_CONVERT_URL") or "").strip()


def _convert_timeout() -> float:
    """
    Seconds to wait on the converter.

    Deliberately long. PP-StructureV3 is a layout model, and on CPU a
    multi-page release takes minutes -- httpx's 5-second default would fail
    every real document while looking like the service was down.
    """
    try:
        return float(os.getenv("PDF_CONVERT_TIMEOUT") or 600)
    except ValueError:
        return 600.0


def _via_service(path: Path) -> tuple[str, int, dict[str, Any]]:
    import httpx

    url = convert_url()
    try:
        with path.open("rb") as fh:
            response = httpx.post(
                url,
                files={"file": (path.name, fh, "application/pdf")},
                timeout=_convert_timeout(),
            )
    except httpx.TimeoutException as exc:
        raise PdfError(
            f"konvertor {int(_convert_timeout())} soniyada javob bermadi "
            f"({url}) -- PDF katta bo'lsa PDF_CONVERT_TIMEOUT ni oshiring"
        ) from exc
    except httpx.RequestError as exc:
        # By far the likeliest failure, and the likeliest cause is one specific
        # mistake, so name it instead of printing the connection error alone:
        # `127.0.0.1` inside this container is this container.
        raise PdfError(
            f"konvertorga ulanib bo'lmadi ({url}): {exc}. Konteyner ichidan "
            "xost mashinasi `host.docker.internal` nomi bilan ko'rinadi -- "
            "PDF_CONVERT_URL da `127.0.0.1` bo'lmasin."
        ) from exc

    if response.status_code >= 400:
        raise PdfError(
            f"konvertor {response.status_code} qaytardi: {response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError:
        # `?raw=true` answers text/markdown. Not what we ask for, but a
        # perfectly good answer to fall back on if the service is reconfigured.
        return response.text, 0, {}

    if not isinstance(payload, dict) or "markdown" not in payload:
        raise PdfError(f"konvertor javobida `markdown` yo'q: {str(payload)[:300]}")

    extra = {
        key: payload[key]
        for key in ("seconds", "device", "markdown_file")
        if payload.get(key) is not None
    }
    try:
        pages = int(payload.get("pages") or 0)
    except (TypeError, ValueError):
        pages = 0
    return str(payload["markdown"]), pages, extra


def _via_pypdf(path: Path) -> tuple[str, int, dict[str, Any]]:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    # Page breaks are kept deliberately: a figure and the heading that gives it
    # meaning often sit on opposite sides of one, and the extractor downstream
    # has to be able to tell.
    return "\n\n---\n\n".join(t for t in pages if t), len(reader.pages), {}


def _via_pymupdf(path: Path) -> tuple[str, int, dict[str, Any]]:
    import fitz  # type: ignore[import-not-found]

    with fitz.open(str(path)) as doc:
        pages = [page.get_text("text").strip() for page in doc]
        body = "\n\n---\n\n".join(t for t in pages if t)
        return body, doc.page_count, {}


_BACKENDS: dict[str, Callable[[Path], tuple[str, int, dict[str, Any]]]] = {
    "service": _via_service,
    "pypdf": _via_pypdf,
    "pymupdf": _via_pymupdf,
}

# `pypdf` is the import name; PyMuPDF installs as `fitz`.
_MODULES = {"pypdf": "pypdf", "pymupdf": "fitz"}

# Order matters only for `auto`: best output first, so a host that happens to
# have a local library installed does not quietly fall back to the weaker one.
_AUTO_ORDER = ("service", "pymupdf", "pypdf")


def _usable(name: str) -> bool:
    if name == "service":
        return bool(convert_url())

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
        if _usable(name):
            return name
    return ""


def convert(path: Path) -> tuple[str, int, str, dict[str, Any]]:
    """PDF -> (markdown, page_count, backend_used, extra)."""
    name = backend_name()
    if not name:
        raise PdfError(
            "PDF konvertori sozlanmagan: .env da PDF_CONVERT_URL yo'q va "
            "mahalliy kutubxona ham o'rnatilmagan."
        )
    try:
        body, pages, extra = _BACKENDS[name](path)
    except PdfError:
        # Already carries a message written for the user; re-wrapping would
        # bury it behind a second, vaguer one.
        raise
    except ImportError as exc:
        raise PdfError(f"{name} import bo'lmadi: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"{name} faylni o'qiy olmadi: {exc}") from exc

    if not body.strip():
        # An empty result means different things per backend, and the fix
        # differs with it: the service reads scans, so nothing coming back from
        # it says the pages really are blank, while the local libraries have no
        # OCR at all and go quiet on any scan.
        raise PdfError(
            "konvertor bo'sh matn qaytardi -- PDF bo'sh yoki o'qib bo'lmadi"
            if name == "service"
            else "PDF da matn qatlami yo'q (skan qilingan bo'lishi mumkin) -- "
            "PDF_CONVERT_URL orqali OCR konvertoridan foydalaning"
        )
    return body, pages, name, extra


def stats() -> dict[str, Any]:
    """What the tools report about their own environment."""
    return {
        "backend": backend_name() or None,
        "convert_url": convert_url() or None,
        "pdf_dir": relative(pdf_dir()),
        "md_dir": relative(md_dir()),
    }
