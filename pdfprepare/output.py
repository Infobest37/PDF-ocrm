import json
import os
from dataclasses import asdict

import fitz

from pdfprepare.models import DocResult, PageInfo

# Environment variable and candidate paths for the Cyrillic-capable TTF used to
# build the searchable text layer. PyMuPDF's built-in Base-14 fonts ("helv"
# etc.) lack Cyrillic, so inserting Cyrillic text with the default font would
# drop it. Candidates are tried in order (explicit override first, then the
# bundled font shipped next to this module, then the Windows system font).
# Bundling a font for portability is deferred to Task 10.
_FONT_ENV_VAR = "PDFPREPARE_FONT"
_BUNDLED_FONT = os.path.join(os.path.dirname(__file__), "fonts", "arial.ttf")
_SYSTEM_FONT = r"C:\Windows\Fonts\arial.ttf"


def _resolve_font(font_path: str | None) -> str:
    """Return the first existing candidate TTF, or raise a clear error."""
    if font_path is not None:
        candidates = [font_path]
    else:
        env_override = os.environ.get(_FONT_ENV_VAR)
        if env_override:
            candidates = [env_override]
        else:
            candidates = [_BUNDLED_FONT, _SYSTEM_FONT]

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    tried = ", ".join(c for c in candidates if c) or "<none>"
    raise FileNotFoundError(
        "No Cyrillic-capable TTF font found for the PDF text layer. "
        f"Tried: {tried}. Set {_FONT_ENV_VAR} or pass font_path to "
        "write_searchable_pdf()."
    )


def write_searchable_pdf(
    src_pdf: str,
    pages: list[PageInfo],
    out_path: str,
    font_path: str | None = None,
) -> None:
    """Add an invisible (searchable) text layer over non-native pages.

    Opens ``src_pdf`` and, for each non-native :class:`PageInfo`, inserts every
    word as invisible text (``render_mode=3``) at its bounding box, using a
    font size derived from the box height. Native pages already carry their
    text in the source PDF and are left untouched. Words with an empty box
    are skipped.

    ``font_path`` optionally overrides the Cyrillic-capable TTF used for the
    text layer; otherwise a candidate is resolved via the ``PDFPREPARE_FONT``
    environment variable, a bundled font next to this module, or the Windows
    system font. Raises :class:`FileNotFoundError` if no candidate exists.
    """
    font_path = _resolve_font(font_path)
    doc = fitz.open(src_pdf)
    try:
        for p in pages:
            if p.is_native_text:
                continue
            if not (0 <= p.page_index < doc.page_count):
                continue
            page = doc[p.page_index]
            page.insert_font(fontname="f0", fontfile=font_path)
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
