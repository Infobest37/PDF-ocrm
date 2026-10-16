"""Command-line entry point for batch PDF preparation."""

import argparse
import os
import sys

from pdfprepare.ocr import ocr_available
from pdfprepare.pipeline import process_file
from pdfprepare.registry import parse_registry

#: Default ATA-100 code -> name registry file (project root).
_DEFAULT_REGISTRY = "Коды систем Вертолета.txt"


def main(argv: list[str]) -> int:
    """Batch-prepare every ``*.pdf`` in ``src`` (non-recursive) into ``dst``."""
    parser = argparse.ArgumentParser(
        prog="pdfprepare",
        description="Prepare scanned PDFs: add an OCR text layer, emit per-page "
                    "JSON structure, and report pages needing a re-scan.",
    )
    parser.add_argument("src", help="Directory of source PDFs (non-recursive).")
    parser.add_argument("dst", help="Directory where outputs are written.")
    parser.add_argument(
        "--registry",
        default=_DEFAULT_REGISTRY,
        help="ATA-100 code -> name registry file (default: %(default)s).",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="OCR every page, bypassing native-text pass-through.",
    )
    args = parser.parse_args(argv)

    if not ocr_available():
        print(
            "error: OCR engine not available: the bundled tesseract.exe and "
            "tessdata/{rus,eng}.traineddata were not found. Set "
            "PDFPREPARE_TESSERACT to a Tesseract installation directory.",
            file=sys.stderr,
        )
        return 2

    if os.path.isfile(args.registry):
        registry = parse_registry(args.registry)
    else:
        print(
            f"warning: registry file not found: {args.registry!r}; "
            "system_names will be empty",
            file=sys.stderr,
        )
        registry = {}

    os.makedirs(args.dst, exist_ok=True)

    pdfs = sorted(
        os.path.join(args.src, name)
        for name in os.listdir(args.src)
        if name.lower().endswith(".pdf")
    )

    failed = False
    for pdf in pdfs:
        try:
            result = process_file(pdf, args.dst, registry, force_ocr=args.force_ocr)
        except Exception as exc:
            print(f"{pdf}: {exc}", file=sys.stderr)
            failed = True
            continue

        stem = os.path.splitext(os.path.basename(pdf))[0]
        report = os.path.join(args.dst, stem + ".report.txt")
        native_pages = sum(1 for p in result.pages if p.is_native_text)
        print(
            f"{stem}: {result.page_count} pages, {native_pages} native "
            f"pass-through, report {report}"
        )

    return 1 if failed else 0
