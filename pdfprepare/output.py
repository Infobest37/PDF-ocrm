import json
from dataclasses import asdict

import fitz

from pdfprepare.models import DocResult, PageInfo

# System TTF with Cyrillic glyph support. PyMuPDF's built-in Base-14 fonts
# ("helv" etc.) lack Cyrillic, so inserting Cyrillic text with the default
# font would drop it. Bundling a font for portability is deferred to Task 10.
_FONT_PATH = r"C:\Windows\Fonts\arial.ttf"


def write_searchable_pdf(src_pdf: str, pages: list[PageInfo], out_path: str) -> None:
    """Add an invisible (searchable) text layer over non-native pages.

    Opens ``src_pdf`` and, for each non-native :class:`PageInfo`, inserts every
    word as invisible text (``render_mode=3``) at its bounding box, using a
    font size derived from the box height. Native pages already carry their
    text in the source PDF and are left untouched. Words with an empty box
    are skipped.
    """
    doc = fitz.open(src_pdf)
    try:
        for p in pages:
            if p.is_native_text:
                continue
            if not (0 <= p.page_index < doc.page_count):
                continue
            page = doc[p.page_index]
            page.insert_font(fontname="f0", fontfile=_FONT_PATH)
            for w in p.words:
                if not w.text:
                    continue
                height = w.y1 - w.y0
                width = w.x1 - w.x0
                if height <= 0 or width <= 0:
                    continue
                page.insert_text(
                    (w.x0, w.y1),
                    w.text,
                    fontname="f0",
                    fontsize=height,
                    render_mode=3,
                )
        doc.save(out_path)
    finally:
        doc.close()


def write_json(result: DocResult, out_path: str) -> None:
    """Write a :class:`DocResult` as indented UTF-8 JSON.

    Output carries the keys ``source_path``, ``page_count``, ``system_names``
    and ``pages`` (each page and word serialized as a plain dict).
    """
    data = asdict(result)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
