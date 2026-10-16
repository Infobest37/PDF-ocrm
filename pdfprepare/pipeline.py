"""Per-file orchestration: route native vs raster pages, OCR, structure, output."""

import os

import fitz

from pdfprepare.codes import extract_codes, system_of
from pdfprepare.models import DocResult, PageInfo, Word
from pdfprepare.ocr import ocr_image
from pdfprepare.output import write_json, write_searchable_pdf
from pdfprepare.page import has_native_text, is_blank, render
from pdfprepare.preprocess import invert_deskew, prepare_with_angle
from pdfprepare.quality import flag_pages, write_report

#: Confidence assigned to words taken from a native digital text layer
#: (no OCR involved, so they are treated as fully trusted).
_NATIVE_CONF = 1.0


def _footer_text(full_text: str) -> str:
    """Return the last three non-empty lines of ``full_text``."""
    lines = [line.strip() for line in full_text.splitlines()]
    non_empty = [line for line in lines if line]
    return "\n".join(non_empty[-3:])


def _first_subsystem(codes: list[str]) -> str:
    """Return the first code that is not a system-level ``XX.00.00`` code."""
    for code in codes:
        if code[3:] != ".00.00":
            return code
    return ""


def _first_system(codes: list[str]) -> str:
    """Return the first system-level ``XX.00.00`` code."""
    for code in codes:
        if code[3:] == ".00.00":
            return code
    return ""


def _page_codes(full_text: str) -> tuple[str, str]:
    """Resolve ``(system_code, subsystem_code)`` for a page's text.

    The subsystem code is the first subsystem-level code (anything not
    ``XX.00.00``) found in the footer (last three non-empty lines), falling
    back to the whole page. The system code is ``system_of(subsystem_code)``.
    When only a system-level code is present, it becomes ``system_code`` and
    ``subsystem_code`` stays ``""``.
    """
    footer_codes = extract_codes(_footer_text(full_text))
    page_codes = extract_codes(full_text)

    subsystem_code = _first_subsystem(footer_codes) or _first_subsystem(page_codes)
    if subsystem_code:
        return system_of(subsystem_code), subsystem_code

    system_code = _first_system(footer_codes) or _first_system(page_codes)
    return system_code, ""


def process_file(
    src: str, dst_dir: str, registry: dict[str, str], *, force_ocr: bool = False
) -> DocResult:
    """Prepare one PDF: OCR raster pages, keep native text, write outputs.

    Per page: native pages keep their ``get_text`` output and words (already
    in PDF points); raster pages are rendered, preprocessed, and OCR'd, with
    OCR pixel coordinates converted to PDF points. Writes ``<stem>.pdf``,
    ``<stem>.json`` and ``<stem>.report.txt`` into ``dst_dir`` and returns the
    :class:`DocResult`.

    ``force_ocr`` bypasses native-text detection and OCRs every page, so an
    operator can replace a poor existing text layer (e.g. a scan with an old
    OCR layer) for that run.
    """
    doc = fitz.open(src)
    try:
        pages: list[PageInfo] = []
        for page in doc:
            page_index = page.number
            native = has_native_text(page) and not force_ocr
            rect = page.rect  # fitz.Rect, PDF points

            if native:
                full_text = page.get_text("text")
                raw_words = page.get_text("words")
                words = [
                    Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3],
                         conf=_NATIVE_CONF)
                    for w in raw_words
                ]
                blank = False
            else:
                rendered = render(page)
                prepared, angle = prepare_with_angle(rendered)
                words = ocr_image(prepared)

                # OCR returns boxes in *prepared*-image pixels. Invert
                # prepare's transform (deskew rotation, then upscale) to map
                # them to PDF points: un-rotate about the deskew center, then
                # scale by page_rect / prepared_image_size (not the rendered
                # image size, which may differ after upscaling).
                factor_x = rect.width / prepared.width
                factor_y = rect.height / prepared.height
                converted = []
                for w in words:
                    if angle:
                        x0, y0, x1, y1 = invert_deskew(
                            w.x0, w.y0, w.x1, w.y1, angle, prepared.size
                        )
                    else:
                        x0, y0, x1, y1 = w.x0, w.y0, w.x1, w.y1
                    converted.append(
                        Word(
                            text=w.text,
                            x0=x0 * factor_x,
                            y0=y0 * factor_y,
                            x1=x1 * factor_x,
                            y1=y1 * factor_y,
                            conf=w.conf,
                        )
                    )
                words = converted
                full_text = " ".join(w.text for w in words)
                blank = is_blank(
                    words, (rect.x0, rect.y0, rect.x1, rect.y1)
                )

            system_code, subsystem_code = _page_codes(full_text)
            pages.append(
                PageInfo(
                    page_index=page_index,
                    is_native_text=native,
                    is_blank=blank,
                    system_code=system_code,
                    subsystem_code=subsystem_code,
                    full_text=full_text,
                    words=words,
                )
            )
    finally:
        doc.close()

    stem = os.path.splitext(os.path.basename(src))[0]
    out_pdf = os.path.join(dst_dir, stem + ".pdf")
    out_json = os.path.join(dst_dir, stem + ".json")
    out_report = os.path.join(dst_dir, stem + ".report.txt")

    system_names: dict[str, str] = {}
    for page in pages:
        for code in (page.system_code, page.subsystem_code):
            if code and code in registry and code not in system_names:
                system_names[code] = registry[code]

    result = DocResult(
        source_path=src,
        page_count=len(pages),
        pages=pages,
        system_names=system_names,
    )

    write_searchable_pdf(src, pages, out_pdf)
    write_json(result, out_json)
    write_report(flag_pages(result), out_report)

    return result
