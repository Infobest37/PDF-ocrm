"""Accept re-scanned sheets: plan from the quality report, rebuild the PDF.

Design (section 8) closes the loop: the program reports which sheets it
needs (:mod:`pdfprepare.quality`), a human re-scans them at better quality,
and this module swaps the new scans into the document. Untouched sheets are
copied byte-for-page unchanged; only the named sheets are replaced.
"""

import os
import re
from dataclasses import dataclass

import fitz

#: File extensions treated as single-sheet images (inserted as a new page of
#: the same size as the sheet being replaced). Anything else must be a PDF.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif",
               ".webp", ".jp2", ".jpx"}

#: One flagged page in the quality report: ``page 2: low_confidence (...)``.
_REPORT_PAGE_RE = re.compile(r"^page (\d+):", re.MULTILINE)


@dataclass
class Replacement:
    """A named sheet to swap: ``page_index`` in the source PDF and ``source`` —
    a path to a one-page PDF or to an image of that sheet."""

    page_index: int
    source: str


def plan_replacements(report_path: str) -> list[int]:
    """Return the sheet numbers a re-scan is requested for, in report order.

    Reads the ``<name>.report.txt`` written by :func:`pdfprepare.quality.write_report`
    and extracts every ``page N:`` line verbatim — those are exactly the
    sheets the quality check asked to re-scan.
    """
    with open(report_path, "r", encoding="utf-8") as f:
        text = f.read()
    return [int(m.group(1)) for m in _REPORT_PAGE_RE.finditer(text)]


def _validate_replacements(replacements: list[Replacement], page_count: int) -> None:
    """Raise ``ValueError`` on an out-of-range or duplicated sheet index."""
    seen: set[int] = set()
    for r in replacements:
        if not 0 <= r.page_index < page_count:
            raise ValueError(
                f"лист {r.page_index} вне диапазона документа "
                f"(0..{page_count - 1}); замена невозможна"
            )
        if r.page_index in seen:
            raise ValueError(f"дублирующая замена листа {r.page_index}")
        seen.add(r.page_index)


def _insert_replacement(out_doc: fitz.Document, sheet_rect: fitz.Rect,
                        source: str) -> None:
    """Append the replacement sheet to ``out_doc``.

    A PDF source contributes its first page as-is; an image is placed on a
    new page of the same size as the sheet it replaces.
    """
    if os.path.splitext(source)[1].lower() in _IMAGE_EXTS:
        page = out_doc.new_page(width=sheet_rect.width, height=sheet_rect.height)
        page.insert_image(sheet_rect, filename=source)
        return
    src_doc = fitz.open(source)
    try:
        if src_doc.page_count < 1:
            raise ValueError(f"в файле «{source}» нет страниц — нечего подставить")
        out_doc.insert_pdf(src_doc, from_page=0, to_page=0)
    finally:
        src_doc.close()


def apply_replacements(src_pdf: str, replacements: list[Replacement],
                       out_pdf: str) -> int:
    """Rebuild ``src_pdf`` into ``out_pdf`` with the named sheets replaced.

    Sheets not named in ``replacements`` are copied unchanged. Returns the
    number of replaced sheets. Raises :class:`ValueError` (before anything is
    written) when a replacement names a sheet outside the document or names
    the same sheet twice.
    """
    doc = fitz.open(src_pdf)
    try:
        _validate_replacements(replacements, doc.page_count)
        by_index = {r.page_index: r for r in replacements}

        out_doc = fitz.open()
        try:
            for i in range(doc.page_count):
                replacement = by_index.get(i)
                if replacement is None:
                    out_doc.insert_pdf(doc, from_page=i, to_page=i)
                else:
                    _insert_replacement(out_doc, doc[i].rect, replacement.source)
            out_doc.save(out_pdf)
        finally:
            out_doc.close()
        return len(replacements)
    finally:
        doc.close()
