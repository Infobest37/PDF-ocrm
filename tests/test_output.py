import pytest

from pdfprepare.output import write_json, write_searchable_pdf


def test_write_searchable_pdf_contains_words(tmp_path):
    import fitz
    from pdfprepare.models import Word, PageInfo
    src = tmp_path / "s.pdf"; out = tmp_path / "o.pdf"
    d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    p = PageInfo(page_index=0, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="",
                 full_text="ПРИМЕР", words=[Word("ПРИМЕР", 72, 72, 130, 82, 0.9)])
    write_searchable_pdf(str(src), [p], str(out))
    d2 = fitz.open(str(out))
    assert "ПРИМЕР" in d2[0].get_text()
    d2.close()


def test_write_json_shape(tmp_path):
    from pdfprepare.models import PageInfo, DocResult
    r = DocResult(source_path="a.pdf", page_count=1,
                  pages=[PageInfo(page_index=0, is_native_text=True, is_blank=False,
                                  system_code="023.00.00", subsystem_code="023.11.30",
                                  full_text="", words=[])],
                  system_names={"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    out = tmp_path / "r.json"
    write_json(r, str(out))
    import json
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["pages"][0]["system_code"] == "023.00.00"


def test_write_searchable_pdf_missing_font_raises(tmp_path):
    import fitz
    from pdfprepare.models import Word, PageInfo
    src = tmp_path / "s.pdf"
    d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    p = PageInfo(page_index=0, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="",
                 full_text="ПРИМЕР", words=[Word("ПРИМЕР", 72, 72, 130, 82, 0.9)])
    missing = tmp_path / "missing.ttf"
    with pytest.raises(FileNotFoundError, match="font"):
        write_searchable_pdf(str(src), [p], str(tmp_path / "o.pdf"),
                             font_path=str(missing))
