"""Open a cited source PDF at the sheet the answer points to (step 2 of the GUI plan).

Pure logic, no Qt imports: the GUI (:mod:`pdfgraph.gui.app`) calls
:func:`open_pdf_at_page` on double-click, while this module owns directory
resolution, file lookup and viewer launching.

Directory priority (:func:`resolve_pdf_dir`): the explicit ``--pdf-dir``
value, the ``PDFGRAPH_PDF_DIR`` environment variable, :data:`DEFAULT_PDF_DIR`.
Unlike :func:`pdfgraph.paths.resolve_store_path` a missing candidate is only
skipped — when nothing exists the result is ``None`` and the GUI falls back
to the stored sheet text.  No directory is ever created here.

Page numbers in the graph are 0-based (``pdfprepare.pipeline`` stores
``page.number``); the physical page passed to a viewer is
``max(1, page + PAGE_OFFSET)`` — graph index 0 opens page 1, index 587 page
588.  Launch order: Acrobat/Reader, then Edge with a percent-encoded
``file:///…#page=N`` URI, then plain ``os.startfile`` (no page fragment).
Every launch failure falls through to the next option; no exception escapes,
the return value says whether something launched.
"""

import os
import subprocess
import unicodedata
from pathlib import Path
from urllib.parse import quote

#: Folder of the prepared source PDFs (the 49-document ``PDFprog\\pdfs`` set).
DEFAULT_PDF_DIR = Path(r"C:\Users\user\Desktop\pythonCODE\PDFprog\pdfs")

#: Graph page indices are 0-based; viewer pages are 1-based.
PAGE_OFFSET = 1

#: Registry-free Acrobat layouts under a Program Files root (both 64- and
#: 32-bit roots are probed; the globs cover «Acrobat DC», the 2024 «Acrobat»
#: and «Acrobat Reader DC»/legacy «Reader 9.0» installs).
_ACROBAT_GLOBS = (
    "Adobe/Acrobat*/Acrobat/AcroRd32.exe",
    "Adobe/Acrobat*/Acrobat/Acrobat.exe",
    "Adobe/*Reader*/Reader/AcroRd32.exe",
)

#: The installer registers the viewer under App Paths; that is the fallback
#: when the well-known folder layout changed.
_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def resolve_pdf_dir(cli_value: str | Path | None = None) -> Path | None:
    """Return the first existing PDF directory: ``--pdf-dir`` > env > default.

    All candidates may be given as ``str`` or :class:`~pathlib.Path`; the
    result is always a ``Path`` (or ``None`` when nothing exists).  Missing
    candidates are skipped, never created.
    """
    env_value = os.environ.get("PDFGRAPH_PDF_DIR")
    for candidate in (cli_value, env_value, DEFAULT_PDF_DIR):
        if not candidate:
            continue
        path = Path(str(candidate))
        if path.is_dir():
            return path
    return None


def _find_acrobat() -> str | None:
    """Path of Acrobat/Reader as reported by the registry, or ``None``.

    Discovery order: ``ProgramFiles*\\Adobe\\…`` well-known layouts, then the
    ``App Paths`` registry keys (HKLM before HKCU, 64-bit view first).
    """
    for root in _program_files_roots():
        for pattern in _ACROBAT_GLOBS:
            try:
                for exe in root.glob(pattern):
                    if exe.is_file():
                        return str(exe)
            except OSError:
                continue
    return _find_in_app_paths(("AcroRd32.exe", "Acrobat.exe"))


def _find_edge() -> str | None:
    """Path of ``msedge.exe`` (Program Files (x86), then Program Files)."""
    for root in _program_files_roots(x86_first=True):
        exe = root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if exe.is_file():
            return str(exe)
    return _find_in_app_paths(("msedge.exe",))


def _program_files_roots(*, x86_first: bool = False) -> list[Path]:
    """Existing Program Files roots, deduplicated.

    Edge and 32-bit Readers historically install under ``ProgramFiles(x86)``;
    Acrobat DC prefers the 64-bit root, so the default order favours it.
    """
    names = ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432")
    if not x86_first:
        names = ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
    roots: list[Path] = []
    for name in names:
        value = os.environ.get(name)
        if not value:
            continue
        root = Path(value)
        if root not in roots and root.is_dir():
            roots.append(root)
    return roots


def _find_in_app_paths(exe_names: tuple[str, ...]) -> str | None:
    """Registered viewer executables from the Windows ``App Paths`` keys."""
    try:
        import winreg
    except ImportError:  # not Windows — discovery simply finds nothing
        return None
    for name in exe_names:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for flags in (getattr(winreg, "KEY_WOW64_64KEY", 0), 0):
                try:
                    with winreg.OpenKey(
                        hive, f"{_APP_PATHS_KEY}\\{name}", 0,
                        winreg.KEY_READ | flags) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                except OSError:
                    continue
                if value and Path(str(value)).is_file():
                    return str(value)
    return None


def _find_pdf_file(pdf_dir: Path, document: str) -> Path | None:
    """Locate the PDF inside ``pdf_dir``: exact name, then +``.pdf``, then NFC.

    The scan is case-insensitive on NFC-normalised names, and the REAL
    on-disk spelling is what is returned — a re-spelled name could not be
    opened by the viewer.
    """
    name = str(document)
    if not name:
        return None
    exact = pdf_dir / name
    if exact.is_file():
        return exact
    with_ext = pdf_dir / (name + ".pdf")
    if with_ext.is_file():
        return with_ext
    target = unicodedata.normalize("NFC", name).casefold()
    target_pdf = unicodedata.normalize("NFC", name + ".pdf").casefold()
    try:
        entries = sorted(pdf_dir.glob("*.pdf"))
    except OSError:
        return None
    for entry in entries:
        folded = unicodedata.normalize("NFC", entry.name).casefold()
        if folded in (target, target_pdf):
            return entry
    return None


def _file_uri(pdf_path: Path, physical_page: int) -> str:
    """ASCII ``file:///`` URI with the ``#page=N`` viewer fragment."""
    posix = pdf_path.as_posix().lstrip("/")
    encoded = quote(posix, safe="/:")
    return f"file:///{encoded}#page={physical_page}"


def open_pdf_at_page(pdf_dir: str | Path | None, document: str,
                     page: int) -> bool:
    """Open ``document`` from ``pdf_dir`` at graph ``page`` (0-based).

    Returns ``True`` when any viewer was launched, ``False`` when the
    directory is unknown, the file is missing or every launch failed — a
    calm refusal, never an exception.
    """
    if pdf_dir is None:
        return False
    pdf_path = _find_pdf_file(Path(str(pdf_dir)), document)
    if pdf_path is None:
        return False
    physical_page = max(1, int(page) + PAGE_OFFSET)

    try:
        acrobat = _find_acrobat()
    except Exception:
        acrobat = None
    if acrobat:
        try:
            subprocess.Popen([acrobat, "/A", f"page={physical_page}",
                              str(pdf_path)])
            return True
        except Exception:
            pass  # fall through to the next viewer option

    try:
        edge = _find_edge()
    except Exception:
        edge = None
    if edge:
        try:
            subprocess.Popen([edge, _file_uri(pdf_path, physical_page)])
            return True
        except Exception:
            pass  # the plain-open fallback is still ahead

    try:
        os.startfile(str(pdf_path))
        return True
    except Exception:
        return False
