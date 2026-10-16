"""Fixture note: PyMuPDF Base-14 fonts cannot embed extractable Cyrillic
(see pdfprepare/output.py), so the fixtures below write their sheet markers
through the project's resolved Cyrillic TTF. Extraction of TTF text returns
NBSP (\\xa0) where a space was written, so assertions normalize it back.
The test logic itself (which sheets are swapped, returned count, range
validation) is unchanged.
"""

import fitz


def _write_sheet(doc: fitz.Document, text: str) -> None:
    from pdfprepare.output import resolve_font
    page = doc.new_page()
    page.insert_font(fontname="f0", fontfile=resolve_font(None))
    page.insert_text((72, 72), text, fontname="f0")


def _text(page) -> str:
    return page.get_text().replace("\xa0", " ")


def test_plan_replacements_reads_report(tmp_path):
    from pdfprepare.replace import plan_replacements
    rep = tmp_path / "d.report.txt"
    rep.write_text(
        "page 2: low_confidence (conf=0.41, coverage=0.0300)\n"
        "page 5: low_text_coverage (conf=0.90, coverage=0.0010)\n", encoding="utf-8")
    assert plan_replacements(str(rep)) == [2, 5]


def test_apply_replacements_swaps_only_named_sheets(tmp_path):
    from pdfprepare.replace import Replacement, apply_replacements
    src = tmp_path / "d.pdf"; d = fitz.open()
    for i in range(4):
        _write_sheet(d, f"ЛИСТ {i}")
    d.save(str(src)); d.close()
    new = tmp_path / "new.pdf"; nd = fitz.open()
    _write_sheet(nd, "ПЕРЕСКАНИРОВАН"); nd.save(str(new)); nd.close()
    out = tmp_path / "out.pdf"
    assert apply_replacements(str(src), [Replacement(2, str(new))], str(out)) == 1
    doc = fitz.open(str(out))
    assert doc.page_count == 4
    assert "ПЕРЕСКАНИРОВАН" in _text(doc[2])
    assert "ЛИСТ 1" in _text(doc[1]) and "ЛИСТ 3" in _text(doc[3])
    doc.close()


def test_page_index_out_of_range_raises(tmp_path):
    import pytest
    from pdfprepare.replace import Replacement, apply_replacements
    src = tmp_path / "d.pdf"; d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    with pytest.raises(ValueError, match="лист 9"):
        apply_replacements(str(src), [Replacement(9, str(src))], str(tmp_path / "o.pdf"))


def test_apply_replacements_accepts_image_source(tmp_path):
    from pdfprepare.replace import Replacement, apply_replacements
    src = tmp_path / "d.pdf"; d = fitz.open()
    for i in range(2):
        _write_sheet(d, f"ЛИСТ {i}")
    d.save(str(src)); d.close()
    img = tmp_path / "rescan.png"
    tmp = fitz.open(); tp = tmp.new_page(width=100, height=100)
    tp.insert_text((10, 50), "R")
    tp.get_pixmap(dpi=72).save(str(img))
    tmp.close()
    out = tmp_path / "out.pdf"
    assert apply_replacements(str(src), [Replacement(0, str(img))], str(out)) == 1
    doc = fitz.open(str(out))
    assert doc.page_count == 2
    assert doc[0].rect.width == doc[1].rect.width
    assert doc[0].get_images() and not doc[1].get_images()
    doc.close()
