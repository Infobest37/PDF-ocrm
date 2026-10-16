# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-folder Windows build of the knowledge graph (task 3.3).

Build from the repository root with:

    <venv>/Scripts/python.exe -m PyInstaller pdfgraph.spec

which produces one portable folder ``dist/pdfgraph/`` carrying two
executables over one shared ``_internal`` payload (interpreter, PyQt5,
PyMuPDF, OpenCV, Tesseract + rus/eng tessdata):

* ``pdfgraph.exe``    — windowed UI (``pdfgraph/gui/app.py``:main, console=False,
  double-click launch);
* ``pdfgraph-cli.exe`` — console build/ask commands (``pdfgraph/__main__.py``,
  console=True, subcommands from task 2.6).

The PyInstaller bootloader executes *every* entry script present in an
executable's archive, so although one ``Analysis`` feeds both ``EXE`` targets,
each EXE is handed only its own program script plus the shared runtime hooks
(see ``_scripts_without``).
"""

import os

#: Repository root (directory containing this spec and the pdfgraph package).
_ROOT = os.path.abspath(SPECPATH)

#: Windowed entry point (pdfgraph.gui.app:main behind ``__main__`` guard).
_GUI_ENTRY = os.path.join(_ROOT, "pdfgraph", "gui", "app.py")

#: Console entry point (pdfgraph.cli:main over sys.argv).
_CLI_ENTRY = os.path.join(_ROOT, "pdfgraph", "__main__.py")

a = Analysis(
    [_GUI_ENTRY, _CLI_ENTRY],
    pathex=[_ROOT],
    binaries=[],
    datas=[(os.path.join(_ROOT, "pdfprepare", "tesseract"), "tesseract")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)


def _scripts_without(*entry_scripts):
    """``a.scripts`` minus the given program scripts.

    ``Analysis`` stores runtime hooks and program scripts in one TOC; a
    frozen bootloader runs all bundled PYSOURCE entries in order. Keep the
    shared hooks (and OPTION rows) for every EXE but drop the entry script
    belonging to the *other* executable.
    """
    drop = {os.path.normcase(os.path.normpath(p)) for p in entry_scripts}
    return [entry for entry in a.scripts
            if not entry[1]
            or os.path.normcase(os.path.normpath(entry[1])) not in drop]


pyz = PYZ(a.pure)

#: Bootloader-parsed interpreter option (PYTHONUTF8 env does not reach a
#: frozen app): force UTF-8 stdio so ``pdfgraph-cli.exe ask --json`` emits
#: byte-exact UTF-8 JSON for machine consumers regardless of the console
#: codepage (cp866 rendering on screen is the project's norm).
_UTF8_OPTION = [("X utf8", None, "OPTION")]

exe_gui = EXE(
    pyz,
    _scripts_without(_CLI_ENTRY),
    _UTF8_OPTION,
    exclude_binaries=True,
    name="pdfgraph",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_cli = EXE(
    pyz,
    _scripts_without(_GUI_ENTRY),
    _UTF8_OPTION,
    exclude_binaries=True,
    name="pdfgraph-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pdfgraph",
)
