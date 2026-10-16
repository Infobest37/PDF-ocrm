"""Command-line entry point for batch PDF preparation."""

import argparse
import os

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
    args = parser.parse_args(argv)

    registry = parse_registry(args.registry) if os.path.isfile(args.registry) else {}
    os.makedirs(args.dst, exist_ok=True)

    pdfs = sorted(
        os.path.join(args.src, name)
        for name in os.listdir(args.src)
        if name.lower().endswith(".pdf")
    )

    for pdf in pdfs:
        result = process_file(pdf, args.dst, registry)
        stem = os.path.splitext(os.path.basename(pdf))[0]
        report = os.path.join(args.dst, stem + ".report.txt")
        print(f"{stem}: {result.page_count} pages, report {report}")

    return 0
