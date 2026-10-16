"""Page-level quality flags and the re-scan report.

After OCR, pages are inspected for two defects that justify a manual re-scan:

* **low confidence** — the mean per-word OCR confidence is below ``MIN_CONF``;
* **low text coverage** — the area covered by recognized words is a tiny
  fraction of the page (below ``MIN_COVERAGE``), usually meaning the page is
  mostly whitespace or the OCR found almost nothing.

Only non-blank, non-native pages are considered: native pages already carry
their text, and blank pages are expected to have none. At most one flag is
emitted per page, with confidence checked first (see the binding ruling).
"""

from pdfprepare.models import DocResult, QualityFlag

#: Mean word confidence below which a page is flagged for re-scan (0..1).
MIN_CONF = 0.60
#: Minimum word-area ÷ page-area ratio before a page is flagged (0..1).
MIN_COVERAGE = 0.02

#: Fallback page area (points) used when a page has no words to bound it.
_DEFAULT_PAGE_AREA = 612.0 * 792.0


def flag_pages(result: DocResult) -> list[QualityFlag]:
    """Return one :class:`QualityFlag` per suspicious page in ``result``.

    For each non-blank, non-native page, ``avg_conf`` is the mean word
    confidence and ``text_coverage`` is the summed word bounding-box area
    divided by the area of the words' union bounding box (or ``612*792`` when
    the page has no words). A page with no words gets ``avg_conf = 0.0`` and
    ``text_coverage = 0.0``.

    Confidence is checked first: a page with both low confidence and low
    coverage emits a single flag with reason ``"low_confidence"``; otherwise a
    low-coverage page emits ``"low_text_coverage"``.
    """
    flags: list[QualityFlag] = []
    for page in result.pages:
        if page.is_blank or page.is_native_text:
            continue

        words = page.words
        if words:
            avg_conf = sum(w.conf for w in words) / len(words)

            min_x = min(w.x0 for w in words)
            min_y = min(w.y0 for w in words)
            max_x = max(w.x1 for w in words)
            max_y = max(w.y1 for w in words)
            page_area = (max_x - min_x) * (max_y - min_y)
            if page_area <= 0:
                page_area = _DEFAULT_PAGE_AREA

            word_area = sum((w.x1 - w.x0) * (w.y1 - w.y0) for w in words)
            text_coverage = word_area / page_area
        else:
            avg_conf = 0.0
            text_coverage = 0.0

        if avg_conf < MIN_CONF:
            flags.append(
                QualityFlag(page.page_index, "low_confidence", avg_conf, text_coverage)
            )
        elif text_coverage < MIN_COVERAGE:
            flags.append(
                QualityFlag(page.page_index, "low_text_coverage", avg_conf, text_coverage)
            )

    return flags


def write_report(flags: list[QualityFlag], out_path: str) -> None:
    """Write ``flags`` to ``out_path`` as UTF-8 text, one line per flag.

    Each line has the form ``page {page_index}: {reason} (conf={avg_conf:.2f},
    coverage={text_coverage:.4f})``.
    """
    with open(out_path, "w", encoding="utf-8") as f:
        for flag in flags:
            f.write(
                f"page {flag.page_index}: {flag.reason} "
                f"(conf={flag.avg_conf:.2f}, coverage={flag.text_coverage:.4f})\n"
            )
