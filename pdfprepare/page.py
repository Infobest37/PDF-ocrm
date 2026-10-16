import io

import fitz
from PIL import Image

from pdfprepare.models import Word


def has_native_text(page: fitz.Page) -> bool:
    text = page.get_text("text").strip()
    return bool(text) and len(text) >= 20


def render(page: fitz.Page, dpi: int = 300) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")


def is_blank(words: list[Word], page_rect: tuple[float, float, float, float]) -> bool:
    page_area = (page_rect[2] - page_rect[0]) * (page_rect[3] - page_rect[1])
    if page_area <= 0:
        return True
    text_area = sum((w.x1 - w.x0) * (w.y1 - w.y0) for w in words)
    coverage = text_area / page_area
    return len(words) < 5 and coverage < 0.005
