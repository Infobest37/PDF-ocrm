"""Per-file orchestration: route native vs raster pages, OCR, structure, output."""

import os
from collections.abc import Callable

import fitz

from pdfprepare.codes import extract_codes, system_of
from pdfprepare.figures import extract_figures
from pdfprepare.models import DocResult, PageInfo, QualityFlag, Table, Word
from pdfprepare.ocr import ocr_image
from pdfprepare.output import write_json, write_searchable_pdf
from pdfprepare.page import has_native_text, is_blank, render
from pdfprepare.preprocess import invert_deskew, prepare_with_angle
from pdfprepare.quality import flag_pages
from pdfprepare.refs import extract_references
from pdfprepare.tables import extract_tables

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


def _write_report_txt(
    result: DocResult, flags: list[QualityFlag], out_report: str
) -> None:
    """Write ``<stem>.report.txt``: a short header plus the quality flags.

    The header (``source``/``pages``/``ocr_pages``/``native_pages``/
    ``blank_pages``/``schema_version``/``flags``) guarantees the file is
    never empty (backlog P2 — seven zero-byte reports in the 2026-10-18
    runs). The flag lines keep the exact ``page N: <reason> (conf=…,
    coverage=…)`` format of :func:`pdfprepare.quality.write_report`, which
    :func:`pdfgraph.build.mark_defects` parses (``^page \\d+: \\S``).
    Header lines deliberately begin with words other than ``page`` so they
    can never be misparsed as defect entries.
    """
    native_pages = sum(1 for page in result.pages if page.is_native_text)
    blank_pages = sum(1 for page in result.pages if page.is_blank)
    lines = [
        f"source: {os.path.basename(result.source_path)}",
        f"pages: {result.page_count}",
        f"ocr_pages: {result.page_count - native_pages}",
        f"native_pages: {native_pages}",
        f"blank_pages: {blank_pages}",
        f"schema_version: {result.schema_version}",
        f"flags: {len(flags)}",
    ]
    for flag in flags:
        lines.append(
            f"page {flag.page_index}: {flag.reason} "
            f"(conf={flag.avg_conf:.2f}, coverage={flag.text_coverage:.4f})"
        )
    with open(out_report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def process_file(
    src: str,
    dst_dir: str,
    registry: dict[str, str],
    *,
    force_ocr: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> DocResult:
    """Prepare one PDF: OCR raster pages, keep native text, write outputs.

    Per page: native pages keep their ``get_text`` output and words (already
    in PDF points); raster pages are rendered, preprocessed, and OCR'd, with
    OCR pixel coordinates converted to PDF points. Tables are detected on
    raster pages only (in pixel space, then scaled to points like the words);
    figures and cross-references are extracted from every page's text. Writes
    ``<stem>.pdf``, ``<stem>.json`` and ``<stem>.report.txt`` into ``dst_dir``
    and returns the :class:`DocResult`.

    ``force_ocr`` bypasses native-text detection and OCRs every page, so an
    operator can replace a poor existing text layer (e.g. a scan with an old
    OCR layer) for that run.

    ``on_progress``, when given, fires once per processed page with
    ``(pages_done, total_pages)`` — ``pages_done`` is 1-based, so the final
    tick is ``(total_pages, total_pages)``. It is a pure display hook:
    ``None`` (the default) keeps the behaviour identical for every existing
    caller (tests, CLI).
    """
    doc = fitz.open(src)
    try:
        total_pages = doc.page_count
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
                # Grid detection is an image-analysis step and this iteration
                # builds table geometry for raster pages only (project
                # decision); native pages carry no ``tables``.
                tables: list[Table] = []
            else:
                rendered = render(page)
                prepared, angle = prepare_with_angle(rendered)
                words = ocr_image(prepared)

                # Table detection works in *prepared*-image pixels (the
                # coordinate contract of pdfprepare.tables): it must see the
                # raw OCR words before the conversion below, and its pixel
                # bboxes are then mapped to PDF points with the same
                # transform as the words.
                raw_tables = extract_tables(prepared, words, page_index)

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
                tables = []
                for t in raw_tables:
                    x0, y0, x1, y1 = t.bbox
                    if angle:
                        x0, y0, x1, y1 = invert_deskew(
                            x0, y0, x1, y1, angle, prepared.size
                        )
                    tables.append(
                        Table(
                            page_index=t.page_index,
                            bbox=(
                                x0 * factor_x,
                                y0 * factor_y,
                                x1 * factor_x,
                                y1 * factor_y,
                            ),
                            rows=t.rows,
                            ambiguous=t.ambiguous,
                        )
                    )
                full_text = " ".join(w.text for w in words)
                blank = is_blank(
                    words, (rect.x0, rect.y0, rect.x1, rect.y1)
                )

            system_code, subsystem_code = _page_codes(full_text)
            # Figures and cross-references are text-level extraction, so they
            # run on every page — native and blank ones included (a reference
            # on a boundary sheet must not be lost).
            figures = extract_figures(full_text, page_index)
            references = extract_references(full_text, page_index)
            pages.append(
                PageInfo(
                    page_index=page_index,
                    is_native_text=native,
                    is_blank=blank,
                    system_code=system_code,
                    subsystem_code=subsystem_code,
                    full_text=full_text,
                    words=words,
                    tables=tables,
                    figures=figures,
                    references=references,
                )
            )
            if on_progress is not None:
                # Emit AFTER the page is fully processed (render/preprocess/
                # OCR/structure included), so a tick always marks a completed
                # page; done is 1-based and the last tick is
                # (total_pages, total_pages).
                on_progress(page_index + 1, total_pages)
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
    _write_report_txt(result, flag_pages(result), out_report)

    return result
