"""Step-2 PDF opener: open the source PDF at the cited page (TDD red phase).

These tests are written BEFORE the implementation and pin the public
contract the general agent must implement.

Module ``pdfgraph/gui/pdf_opener.py`` (pure logic, no Qt imports):

- ``PAGE_OFFSET = 1`` — graph page indices are 0-based; the physical page
  passed to a viewer is ``page + PAGE_OFFSET`` (index 587 -> page 588).
- ``DEFAULT_PDF_DIR`` — default candidate directory of prepared PDFs
  (``str`` or ``Path``); tests always monkeypatch it.
- ``resolve_pdf_dir(cli_dir=None) -> Path | None`` — candidate priority:
  1. ``cli_dir`` (the ``--pdf-dir`` CLI/GUI argument, str or Path),
  2. the ``PDFGRAPH_PDF_DIR`` environment variable,
  3. ``DEFAULT_PDF_DIR``.
  Every candidate must exist as a directory; missing candidates are
  skipped (unlike ``paths.resolve_store_path`` there is no raise); the
  result is ``None`` when nothing exists, otherwise a ``pathlib.Path``
  (even when every candidate was given as ``str``).
- ``open_pdf_at_page(pdf_dir, document, page) -> bool`` — locate the PDF
  inside ``pdf_dir`` (``str`` or ``Path``) and try launch options in
  order, first success wins. File resolution is internal (NOT public
  API): the exact document name first, then ``document + ".pdf"``, then
  a case-insensitive NFC-normalized scan of the directory; the real
  on-disk spelling is what reaches the viewer (a re-spelled name could
  not be opened). Launch options:
  1. the exe reported by ``_find_acrobat()`` via
     ``subprocess.Popen([exe, "/A", f"page={physical}", pdf_path])``;
  2. the exe reported by ``_find_edge()`` via
     ``subprocess.Popen([edge, uri])`` where ``uri`` is a ``file:///``
     URI of the PDF ending with ``#page={physical}`` and percent-encoded
     (no raw Cyrillic/space);
  3. ``os.startfile(pdf_path)`` with the exact path (no page fragment).
  ``_find_acrobat()``/``_find_edge()`` (-> ``str | None``) are the
  mockable discovery interfaces — tests patch them directly instead of
  any candidate-list constants; ``None`` means "not installed", the
  option is skipped.
  Returns ``True`` when any option launched, ``False`` when ``pdf_dir``
  is ``None``, the file is not found, or every attempt raised. A failing
  ``Popen`` must proceed to the next option / the startfile fallback,
  not abort. The module may bind ``Popen`` either as ``from subprocess
  import Popen`` or call ``subprocess.Popen`` — tests patch both
  spellings, the same way for ``os.startfile``.

GUI integration (``pdfgraph/gui/app.py``):

- ``MainWindow`` accepts an explicit ``pdf_dir=`` keyword argument (str
  or Path); after resolution the window passes a ``pathlib.Path`` as the
  first argument of ``open_pdf_at_page`` — even when the constructor
  received a string.
- ``app.py`` imports ``open_pdf_at_page`` at module level, so tests patch
  ``pdfgraph.gui.app.open_pdf_at_page``.
- ``open_sheet(document, page)`` first tries
  ``open_pdf_at_page(self.pdf_dir, document, page)``: on ``True`` the
  viewer opens and the step-1 text-sheet dialog is NOT shown; on ``False``
  ``QMessageBox.warning`` is shown and the step-1 text-sheet dialog is
  preserved unchanged.

Until the module/kwargs exist every test below fails — that is the
expected RED state of the red-green cycle.
"""

import os
import subprocess
import unicodedata
from pathlib import Path

import pytest

from conftest import FX_DOCUMENT, FX_TEXT_47

_PDF_BYTES = b"%PDF-1.4\n"


def _opener():
    """Import the module under test (ModuleNotFoundError = expected RED)."""
    import pdfgraph.gui.pdf_opener as opener
    return opener


def _pdf_dir_with(tmp_path, name, content=_PDF_BYTES):
    """Directory holding one PDF named ``name`` (unicode spelling kept)."""
    d = tmp_path / "pdf"
    d.mkdir()
    (d / name).write_bytes(content)
    return d


def _fake_exe(tmp_path, name):
    return str(tmp_path / name)


class _FakeProc:
    """Whatever the opener may want from a Popen result."""

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


def _patch_launchers(monkeypatch, opener, acrobat=None, edge=None,
                     fail_popen=False, fail_first_popen=0,
                     fail_startfile=False):
    """Intercept every launch path and pin the discovery results.

    ``acrobat``/``edge`` are the exe paths ``_find_acrobat()``/``_find_edge()``
    report (``None`` = discovery finds nothing, the option is skipped).
    Every launch call is recorded and no real process is ever started.
    ``fail_first_popen=N`` makes the first N Popen calls raise
    (FileNotFoundError), ``fail_popen=True`` makes all of them raise.
    """
    popen_calls = []
    startfile_calls = []
    state = {"popen": 0}

    def fake_popen(args, **kwargs):
        popen_calls.append(args)
        state["popen"] += 1
        if fail_popen or state["popen"] <= fail_first_popen:
            raise FileNotFoundError(args[0] if args else "no exe")
        return _FakeProc()

    def fake_startfile(path, *args, **kwargs):
        startfile_calls.append(path)
        if fail_startfile:
            raise OSError("startfile failed")
        return None

    # Cover both import spellings: `import subprocess` and
    # `from subprocess import Popen` (raising=False creates a spare
    # attribute when the module did not bind the short name).
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    if hasattr(opener, "Popen"):
        monkeypatch.setattr(opener, "Popen", fake_popen)
    monkeypatch.setattr(os, "startfile", fake_startfile)
    if hasattr(opener, "startfile"):
        monkeypatch.setattr(opener, "startfile", fake_startfile)
    monkeypatch.setattr(opener, "_find_acrobat", lambda: acrobat)
    monkeypatch.setattr(opener, "_find_edge", lambda: edge)
    return popen_calls, startfile_calls


# ---------------------------------------------------------------------------
# resolve_pdf_dir: CLI > PDFGRAPH_PDF_DIR env > DEFAULT_PDF_DIR, missing
# candidates are skipped, None when nothing exists. Result is a Path.
# ---------------------------------------------------------------------------


def test_resolve_pdf_dir_cli_beats_env_and_default(tmp_path, monkeypatch):
    opener = _opener()
    cli, env, default = (tmp_path / name for name in ("cli", "env", "default"))
    for d in (cli, env, default):
        d.mkdir()
    monkeypatch.setattr(opener, "DEFAULT_PDF_DIR", str(default))
    monkeypatch.setenv("PDFGRAPH_PDF_DIR", str(env))
    got = opener.resolve_pdf_dir(str(cli))
    assert isinstance(got, Path)
    assert got == cli


def test_resolve_pdf_dir_env_beats_default(tmp_path, monkeypatch):
    opener = _opener()
    env, default = (tmp_path / name for name in ("env", "default"))
    for d in (env, default):
        d.mkdir()
    monkeypatch.setattr(opener, "DEFAULT_PDF_DIR", str(default))
    monkeypatch.setenv("PDFGRAPH_PDF_DIR", str(env))
    got = opener.resolve_pdf_dir(None)
    assert isinstance(got, Path)
    assert got == env
    assert opener.resolve_pdf_dir() == env


def test_resolve_pdf_dir_default_without_cli_and_env(tmp_path, monkeypatch):
    opener = _opener()
    default = tmp_path / "default"
    default.mkdir()
    monkeypatch.setattr(opener, "DEFAULT_PDF_DIR", str(default))
    monkeypatch.delenv("PDFGRAPH_PDF_DIR", raising=False)
    got = opener.resolve_pdf_dir()
    assert isinstance(got, Path)
    assert got == default


def test_resolve_pdf_dir_skips_missing_cli_and_uses_env(tmp_path, monkeypatch):
    opener = _opener()
    env, default = (tmp_path / name for name in ("env", "default"))
    for d in (env, default):
        d.mkdir()
    monkeypatch.setattr(opener, "DEFAULT_PDF_DIR", str(default))
    monkeypatch.setenv("PDFGRAPH_PDF_DIR", str(env))
    missing = str(tmp_path / "no-such-dir")
    assert opener.resolve_pdf_dir(missing) == env


def test_resolve_pdf_dir_returns_none_when_all_candidates_missing(
        tmp_path, monkeypatch):
    opener = _opener()
    monkeypatch.setattr(opener, "DEFAULT_PDF_DIR", str(tmp_path / "no-default"))
    monkeypatch.delenv("PDFGRAPH_PDF_DIR", raising=False)
    missing_cli = str(tmp_path / "no-cli")
    assert opener.resolve_pdf_dir(missing_cli) is None
    assert opener.resolve_pdf_dir() is None


# ---------------------------------------------------------------------------
# open_pdf_at_page: calm refusals — no directory, no match -> False, no launch.
# ---------------------------------------------------------------------------


def test_no_directory_returns_false_without_launch(tmp_path, monkeypatch):
    opener = _opener()
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    assert opener.open_pdf_at_page(None, FX_DOCUMENT, 47) is False
    assert popen_calls == []
    assert startfile_calls == []


def test_no_file_match_returns_false_without_launch(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    assert opener.open_pdf_at_page(str(d), "НЕТ ТАКОГО.pdf", 47) is False
    assert opener.open_pdf_at_page(str(tmp_path / "nope"), FX_DOCUMENT,
                                   47) is False
    assert popen_calls == []
    assert startfile_calls == []


# ---------------------------------------------------------------------------
# File resolution is internal: exact / ".pdf"-appended / case-insensitive NFC
# lookups are asserted through the path that reaches the launch arguments.
# ---------------------------------------------------------------------------


def test_open_pdf_at_page_exact_document_name(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    pdf = d / FX_DOCUMENT
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 587) is True
    args = [str(a) for a in popen_calls[0]]
    assert str(pdf) in args
    assert startfile_calls == []


def test_open_pdf_at_page_appends_missing_pdf_extension(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    pdf = d / FX_DOCUMENT
    popen_calls, _ = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    assert opener.open_pdf_at_page(str(d), "РТЭ кн.1", 587) is True
    args = [str(a) for a in popen_calls[0]]
    assert str(pdf) in args


def test_open_pdf_at_page_case_insensitive_and_nfc(tmp_path, monkeypatch):
    """NFD/decomposed + lower-case file on disk, precomposed + upper query.

    «Й» is the one Cyrillic letter with a canonical decomposition (И + breve),
    so exact-name lookup misses and the NFC/casefold scan must find it — and
    the on-disk spelling is what reaches the viewer, which could not open a
    re-spelled name.
    """
    opener = _opener()
    on_disk = unicodedata.normalize("NFD", "район-1.pdf")
    assert on_disk != "район-1.pdf"
    d = _pdf_dir_with(tmp_path, on_disk)
    pdf = d / on_disk
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    assert opener.open_pdf_at_page(str(d), "РАЙОН-1.PDF", 587) is True
    args = [str(a) for a in popen_calls[0]]
    assert str(pdf) in args
    assert startfile_calls == []


# ---------------------------------------------------------------------------
# Page offset: graph indices are 0-based, viewers get index + PAGE_OFFSET.
# ---------------------------------------------------------------------------


def test_page_offset_is_one():
    assert _opener().PAGE_OFFSET == 1


def test_page_zero_opens_physical_page_one(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 0) is True
    assert len(popen_calls) == 1
    args = [str(a) for a in popen_calls[0]]
    assert "/A" in args
    assert any("page=1" in a for a in args)
    assert startfile_calls == []


# ---------------------------------------------------------------------------
# Acrobat: Popen args carry /A and the physical page; Edge: file URI with
# #page fragment; startfile: exact path fallback; failures fall through.
# ---------------------------------------------------------------------------


def test_acrobat_launch_uses_popen_with_page_588(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    pdf = d / FX_DOCUMENT
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, acrobat=_fake_exe(tmp_path, "AcroRd32.exe"))
    # Graph index 587 (0-based) is physical page 588 in the viewer.
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 587) is True
    assert len(popen_calls) == 1
    args = [str(a) for a in popen_calls[0]]
    assert "/A" in args
    assert any("page=588" in a for a in args)
    assert str(pdf) in args
    assert startfile_calls == []


def test_edge_launch_uses_percent_encoded_uri_with_page_fragment(
        tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener, edge=_fake_exe(tmp_path, "msedge.exe"))
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 587) is True
    assert len(popen_calls) == 1
    args = [str(a) for a in popen_calls[0]]
    uri = next(a for a in args if a.startswith("file:///"))
    assert uri.endswith("#page=588")
    assert uri.isascii(), uri  # Cyrillic and the space are percent-encoded
    assert startfile_calls == []


def test_startfile_fallback_gets_exact_path(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    pdf = d / FX_DOCUMENT
    popen_calls, startfile_calls = _patch_launchers(monkeypatch, opener)
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 587) is True
    assert popen_calls == []
    assert [os.fspath(p) for p in startfile_calls] == [str(pdf)]


def test_popen_failure_proceeds_to_next_option(tmp_path, monkeypatch):
    """Acrobat's Popen raises -> Edge is still tried; startfile untouched."""
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener,
        acrobat=_fake_exe(tmp_path, "AcroRd32.exe"),
        edge=_fake_exe(tmp_path, "msedge.exe"),
        fail_first_popen=1)
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 587) is True
    assert len(popen_calls) == 2
    args = [str(a) for a in popen_calls[1]]
    assert any(a.endswith("#page=588") for a in args)
    assert startfile_calls == []


def test_every_launch_failing_returns_false(tmp_path, monkeypatch):
    opener = _opener()
    d = _pdf_dir_with(tmp_path, FX_DOCUMENT)
    popen_calls, startfile_calls = _patch_launchers(
        monkeypatch, opener,
        acrobat=_fake_exe(tmp_path, "AcroRd32.exe"),
        edge=_fake_exe(tmp_path, "msedge.exe"),
        fail_popen=True, fail_startfile=True)
    assert opener.open_pdf_at_page(str(d), FX_DOCUMENT, 587) is False
    assert len(popen_calls) == 2          # Acrobat, then Edge
    assert len(startfile_calls) == 1      # then the fallback was still tried


# ---------------------------------------------------------------------------
# GUI integration (offscreen Qt): double-click tries the PDF first (the
# window passes a resolved Path as pdf_dir even when the constructor got a
# string); the step-1 text-sheet dialog stays as the fallback behind a calm
# warning.
# ---------------------------------------------------------------------------


def test_double_click_opens_pdf_at_page(tmp_path, fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / FX_DOCUMENT).write_bytes(_PDF_BYTES)
    import pdfgraph.gui.app as gui_app
    from pdfgraph.gui.app import MainWindow
    calls = []

    def fake_open(pdf_dir_arg, document, page):
        calls.append((pdf_dir_arg, document, page))
        return True

    monkeypatch.setattr(gui_app, "open_pdf_at_page", fake_open)
    dialogs = []
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: (dialogs.append(self), 0)[1])
    w = MainWindow(fx_store_path, "Коды систем Вертолета.txt",
                   pdf_dir=str(pdf_dir))
    rows = w.ask("радиомаяк ПАРМ-406А")
    assert rows and rows[0].document == FX_DOCUMENT
    w._on_double_click(0, 0)
    assert len(calls) == 1
    opened_dir, opened_doc, opened_page = calls[0]
    assert isinstance(opened_dir, Path)     # resolved to Path, not the string
    assert opened_dir == pdf_dir
    assert opened_doc == rows[0].document
    assert opened_page == rows[0].page
    assert dialogs == []  # success: no text-sheet fallback dialog
    w.close()


def test_open_sheet_warns_and_keeps_text_sheet_when_pdf_not_opened(
        tmp_path, fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import (QApplication, QDialog, QMessageBox,
                                 QPlainTextEdit)
    app = QApplication.instance() or QApplication([])
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()  # PDF file deliberately absent: the opener has nothing
    import pdfgraph.gui.app as gui_app
    from pdfgraph.gui.app import MainWindow
    calls = []
    monkeypatch.setattr(gui_app, "open_pdf_at_page",
                        lambda *a: calls.append(a) or False)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a))
    dialogs = []
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: (dialogs.append(self), 0)[1])
    w = MainWindow(fx_store_path, "Коды систем Вертолета.txt",
                   pdf_dir=str(pdf_dir))
    w.open_sheet(FX_DOCUMENT, 47)
    assert len(calls) == 1
    opened_dir, opened_doc, opened_page = calls[0]
    assert isinstance(opened_dir, Path)     # resolved to Path, not the string
    assert opened_dir == pdf_dir
    assert opened_doc == FX_DOCUMENT
    assert opened_page == 47
    assert len(warnings) == 1
    assert len(dialogs) == 1  # step-1 text-sheet dialog preserved
    editor = dialogs[0].findChild(QPlainTextEdit)
    assert FX_TEXT_47 in editor.toPlainText()
    w.close()


def test_open_sheet_tries_pdf_even_when_stored_text_is_blank(
        tmp_path, fx_store_path, monkeypatch):
    """Regression: blank stored text must not bypass the PDF opener.

    A blank page in the graph is a section/techcard boundary, not a scan
    defect — the physical PDF page still exists, so a valid double-click
    must still try to open the viewer. Current production code checks
    ``text.strip()`` BEFORE calling the opener and skips it for blank
    sheets (expected RED).
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    import pdfgraph.gui.app as gui_app
    from pdfgraph.gui.app import MainWindow
    calls = []
    monkeypatch.setattr(gui_app, "open_pdf_at_page",
                        lambda *a: calls.append(a) or True)
    dialogs = []
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: (dialogs.append(self), 0)[1])
    w = MainWindow(fx_store_path, "Коды систем Вертолета.txt",
                   pdf_dir=str(tmp_path))
    # The sheet exists in the store, but its stored text is blank.
    monkeypatch.setattr(w._store, "page_text", lambda document, page: "")
    w.open_sheet(FX_DOCUMENT, 47)
    assert len(calls) == 1, \
        "open_sheet skipped the PDF opener for a blank-text sheet"
    opened_dir, opened_doc, opened_page = calls[0]
    assert isinstance(opened_dir, Path)     # resolved to Path, not the string
    assert opened_dir == tmp_path
    assert opened_doc == FX_DOCUMENT
    assert opened_page == 47
    assert dialogs == []  # opener succeeded: no text-sheet dialog
    w.close()
