# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-folder Windows build of the pdfprepare CLI.

Build from the repository root with:

    <venv>/Scripts/pyinstaller.exe pdfprepare.spec

which produces `dist/pdfprepare/pdfprepare.exe` plus the bundled Tesseract
binary and language data next to it in the same folder.
"""

import os

#: Repository root (directory containing this spec and the pdfprepare package).
_ROOT = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(_ROOT, "pdfprepare", "__main__.py")],
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

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pdfprepare",
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
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pdfprepare",
)
