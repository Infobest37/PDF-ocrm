# Portable packaging

The `pdfprepare` CLI is distributed as a single self-contained folder that runs
on a clean Windows PC with no Python, Tesseract, or Ghostscript installed.

## How the portable folder is produced

1. **Bundle Tesseract** (dev machine only — needs an installed Tesseract):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\bundle_tesseract.ps1
   ```

   This copies `tesseract.exe`, its sibling `*.dll` files (required — the
   executable is dynamically linked against them), and
   `tessdata/{rus,eng}.traineddata` from `C:\Program Files\Tesseract-OCR\` into
   `pdfprepare\tesseract\`.

2. **Build the one-folder bundle** (from the repository root):

   ```powershell
   <venv>\Scripts\pyinstaller.exe pdfprepare.spec
   ```

   The spec bundles `pdfprepare/tesseract/**` as data and produces:

   ```text
   dist/pdfprepare/
     pdfprepare.exe
     _internal/            # Python runtime + dependencies + bundled tesseract
   ```

3. **Distribute** the whole `dist/pdfprepare/` folder — nothing else is needed.

## How to run it on a clean Windows PC

Copy `dist/pdfprepare/` to the target machine and run:

```powershell
dist\pdfprepare\pdfprepare.exe <src_dir> <dst_dir> [--registry <registry.txt>]
```

- `<src_dir>` — directory of scanned PDFs (non-recursive).
- `<dst_dir>` — directory where outputs are written.
- `--registry` — optional ATA-100 code -> name file
  (default: `Коды систем Вертолета.txt`). The default path is resolved
  relative to the **current working directory**, not the exe folder, so run the
  tool from the directory that contains the registry (or pass an explicit
  path).

For each `<name>.pdf` in `src`, the tool writes `<name>.pdf` (with a searchable
text layer), `<name>.json` (per-page structure), and `<name>.report.txt`
(pages needing a re-scan) into `dst`.

The searchable text layer uses the Windows system font `C:\Windows\Fonts\arial.ttf`
(present on every Windows install), so no font needs to be bundled. Set
`PDFPREPARE_FONT` to override with a different Cyrillic-capable TTF.
