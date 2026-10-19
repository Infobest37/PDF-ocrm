import io

import fitz
from PIL import Image

from pdfprepare.models import Word


def has_native_text(page: fitz.Page) -> bool:
    text = page.get_text("text").strip()
    return bool(text) and len(text) >= 20


#: Pixel budget for a single rendered page. Chosen below Pillow's
#: DecompressionBombWarning threshold (MAX_IMAGE_PIXELS = 89 478 485 px;
#: DecompressionBombError fires at 2 * MAX_IMAGE_PIXELS ~ 179 Mpx at 300 DPI)
#: so neither the warning nor the error ever fires.
_RENDER_BUDGET_PX = 80_000_000


def render(page: fitz.Page, dpi: int = 300) -> Image.Image:
    """Render the page to a grayscale PIL image (default target: 300 DPI).

    Oversized pages (giant scans / развороты) are rendered with a
    proportionally reduced DPI so the pixel count stays inside
    ``_RENDER_BUDGET_PX``: for pages above the budget the DPI is scaled down
    by ``sqrt(budget / px_at_target)``, floored at 200 DPI for moderately
    oversized pages (budget < px <= 200 Mpx) and at 150 DPI for giant ones
    (px > 200 Mpx). Pages within the budget keep the requested DPI.
    """
    target_px = (page.rect.width / 72 * dpi) * (page.rect.height / 72 * dpi)
    if target_px > _RENDER_BUDGET_PX:
        min_dpi = 150 if target_px > 200_000_000 else 200
        dpi = max(min_dpi, int(dpi * (_RENDER_BUDGET_PX / target_px) ** 0.5))
    pix = page.get_pixmap(dpi=dpi)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")


def is_blank(words: list[Word], page_rect: tuple[float, float, float, float]) -> bool:
    page_area = (page_rect[2] - page_rect[0]) * (page_rect[3] - page_rect[1])
    if page_area <= 0:
        return True
    text_area = sum((w.x1 - w.x0) * (w.y1 - w.y0) for w in words)
    coverage = text_area / page_area
    return len(words) < 5 and coverage < 0.005
