from pdfprepare.models import Word
from pdfprepare.page import has_native_text, is_blank, render


def test_is_blank_few_words_low_coverage():
    w = [Word("x", 0, 0, 8, 8, 80.0)]  # tiny, single word
    assert is_blank(w, (0, 0, 612, 792)) is True


def test_is_blank_many_words():
    ws = [Word(f"w{i}", 0, 0, 20, 20, 80.0) for i in range(10)]
    assert is_blank(ws, (0, 0, 612, 792)) is False


def test_has_native_text(tmp_path):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ЭТО ПОЛНЫЙ ЦИФРОВОЙ ТЕКСТ СТРАНИЦЫ ДОСТАТОЧНОЙ ДЛИНЫ")
    assert has_native_text(page) is True
    doc.close()
