import math
import warnings

import fitz
import pytest
from PIL import Image

from pdfprepare.models import Word
from pdfprepare.page import has_native_text, is_blank, render


def test_is_blank_few_words_low_coverage():
    w = [Word("x", 0, 0, 8, 8, 80.0)]  # tiny, single word
    assert is_blank(w, (0, 0, 612, 792)) is True


def test_is_blank_many_words():
    ws = [Word(f"w{i}", 0, 0, 20, 20, 80.0) for i in range(10)]
    assert is_blank(ws, (0, 0, 612, 792)) is False

#: Constants of the installed Pillow for bomb-protection expectations:
#: warning at size > MAX_IMAGE_PIXELS, error at size > 2 * MAX_IMAGE_PIXELS.
PIL_ERROR = Image.MAX_IMAGE_PIXELS * 2  # 178 956 970
PIL_WARN = Image.MAX_IMAGE_PIXELS       # 89 478 485
BUDGET = 80_000_000


def _square_page(megapixels: float):
    """Empty fitz page whose area at 300 DPI is ~``megapixels`` Mpx."""
    side_pt = math.sqrt(megapixels * 1_000_000.0) * 72 / 300
    doc = fitz.open()
    page = doc.new_page(width=side_pt, height=side_pt)
    return doc, page, side_pt


def _render_strict(page):
    """render() with Pillow bomb warnings escalated to errors."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return render(page)


def test_render_small_page_300dpi():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 portrait
    img = _render_strict(page)
    w, h = img.size
    assert 72 * w / 595.0 == pytest.approx(300, abs=0.5)
    assert 72 * h / 842.0 == pytest.approx(300, abs=0.5)
    assert w * h <= BUDGET
    doc.close()


def test_render_bomb_page_reduced_dpi():
    doc, page, side_pt = _square_page(319.6)
    img = _render_strict(page)  # must NOT raise DecompressionBombError
    w, h = img.size
    assert w * h <= BUDGET
    eff_dpi = 72 * w / side_pt
    assert eff_dpi == pytest.approx(150, abs=0.5)  # giant page: floor 150
    doc.close()


def test_render_warning_page_below_budget():
    doc, page, side_pt = _square_page(160.0)  # former warning-zone page
    img = _render_strict(page)  # must NOT raise DecompressionBombWarning
    w, h = img.size
    assert w * h <= BUDGET <= PIL_WARN
    eff_dpi = 72 * w / side_pt
    assert eff_dpi == pytest.approx(212, abs=0.5)  # floor 200; budget math
    doc.close()


def test_render_never_below_150dpi():
    doc, page, side_pt = _square_page(319.6)
    img = render(page)
    w, h = img.size
    eff_dpi = 72 * w / side_pt
    assert 149.9 <= eff_dpi <= 150.5  # the 150-DPI floor holds
    doc.close()


def test_has_native_text(tmp_path):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ЭТО ПОЛНЫЙ ЦИФРОВОЙ ТЕКСТ СТРАНИЦЫ ДОСТАТОЧНОЙ ДЛИНЫ")
    assert has_native_text(page) is True
    doc.close()
