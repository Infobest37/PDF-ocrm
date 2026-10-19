"""Schema v2 integration: tables/figures/references in the pipeline and JSON.

Covers Task 1.6: ``process_file`` fills ``PageInfo.tables`` (raster pages
only, in PDF points), ``.figures`` and ``.references`` (all pages), the JSON
written by ``write_json`` round-trips through ``read_result``, and old v1
JSON without the new keys still loads (empty lists, ``schema_version`` 1).
"""

import json

import pytest


def test_json_v2_round_trip_includes_structure(tmp_path, monkeypatch):
    import fitz, pdfprepare.pipeline as pl
    from pdfprepare.models import Word, Table, Figure, Reference
    from pdfprepare.output import read_result, write_json
    src = tmp_path / "in.pdf"; d = fitz.open(); d.new_page(); d.new_page(); d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [Word("023.11.30", 72, 700, 140, 710, 0.95)])
    monkeypatch.setattr(pl, "extract_tables", lambda img, words, page_index: [] )
    monkeypatch.setattr(pl, "extract_figures", lambda text, page_index: [Figure(page_index, "12", "Рис. 12")])
    monkeypatch.setattr(pl, "extract_references", lambda text, page_index: [Reference(page_index, "system", "028.10.00", "см. 028.10.00")])
    dst = tmp_path / "out"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})
    assert res.pages[0].figures[0].number == "12"
    assert res.pages[0].references[0].target == "028.10.00"
    back = read_result(str(dst / "in.json"))
    assert back.schema_version == 2 and back.pages[0].references[0].target == "028.10.00"
    assert back.pages[0].subsystem_code == "023.11.30"


def test_blank_page_with_code_not_reported(tmp_path, monkeypatch):
    # пустой лист с кодом: код сохранён, в флаги отчёта не попал, таблиц нет.
    # P2: report больше не пустой (шапка), но blank-лист — не дефект:
    # ни одной строки "page N:" в отчёте быть не должно.
    import fitz, pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    src = tmp_path / "b.pdf"; d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [Word("023.11.30", 72, 700, 140, 710, 0.95)])
    dst = tmp_path / "o"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})
    assert res.pages[0].subsystem_code == "023.11.30"
    text = (dst / "b.report.txt").read_text(encoding="utf-8")
    assert "source: b.pdf" in text and "flags: 0" in text
    assert "page 0:" not in text


def test_sheet_without_any_code_is_still_processed(tmp_path, monkeypatch):
    # лист вообще без кода: не падаем, код пустой, лист в результате есть
    import fitz, pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    src = tmp_path / "n.pdf"; d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [Word("просто", 72, 700, 120, 710, 0.9)])
    dst = tmp_path / "o"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})
    assert res.pages[0].system_code == "" and res.pages[0].subsystem_code == ""
    assert (dst / "n.json").exists()


def test_extract_tables_gets_pixels_and_bbox_scaled_to_points(tmp_path, monkeypatch):
    """Tables must be extracted on *pixel* words before the point conversion,
    then their bbox scaled to PDF points with the same factor as the words
    (see the coordinate contract in pdfprepare/tables.py)."""
    import fitz
    import pdfprepare.pipeline as pl
    from pdfprepare.models import Table, Word
    from pdfprepare.output import read_result
    from pdfprepare.page import render
    from pdfprepare.preprocess import prepare

    # Tiny page: ``prepare`` upscales it, so prepared size != rendered size
    # and a wrong scale factor would be visible in the numbers.
    src = tmp_path / "t.pdf"
    d = fitz.open(); d.new_page(width=50, height=50); d.save(str(src)); d.close()
    with fitz.open(str(src)) as doc:
        rendered = render(doc[0])
        rect = doc[0].rect
    prepared = prepare(rendered)

    pixel_words = [Word("X", 30, 40, 60, 80, 0.9)]
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": list(pixel_words))

    seen = {}

    def fake_extract_tables(img, words, page_index):
        seen["img_size"] = img.size
        seen["words"] = words
        return [Table(page_index=page_index, bbox=(10.0, 20.0, 300.0, 400.0),
                      rows=[["N", "ОБОЗНАЧЕНИЕ"]])]

    monkeypatch.setattr(pl, "extract_tables", fake_extract_tables)
    dst = tmp_path / "o"; dst.mkdir()
    result = pl.process_file(str(src), str(dst), {})

    # Called with the prepared image and the raw pixel words (not converted).
    assert seen["img_size"] == (prepared.width, prepared.height)
    assert seen["words"] == pixel_words

    # Resulting bbox is in PDF points: pixels * rect / prepared.size.
    fx = rect.width / prepared.width
    fy = rect.height / prepared.height
    bbox = result.pages[0].tables[0].bbox
    assert bbox[0] == pytest.approx(10.0 * fx)
    assert bbox[1] == pytest.approx(20.0 * fy)
    assert bbox[2] == pytest.approx(300.0 * fx)
    assert bbox[3] == pytest.approx(400.0 * fy)
    assert result.pages[0].tables[0].rows == [["N", "ОБОЗНАЧЕНИЕ"]]

    # Tables carry the page index they were extracted on.
    assert result.pages[0].tables[0].page_index == 0

    # The bbox survives the JSON round-trip as a tuple of floats.
    back = read_result(str(dst / "t.json"))
    assert isinstance(back.pages[0].tables[0].bbox, tuple)
    assert back.pages[0].tables[0].bbox == bbox
    assert back.pages[0].tables[0].rows == [["N", "ОБОЗНАЧЕНИЕ"]]


def test_native_pages_get_figures_refs_but_no_tables(tmp_path, monkeypatch):
    """Figures/references run on every page, including native and blank;
    extract_tables is called for raster pages only."""
    import fitz
    import pdfprepare.pipeline as pl
    from pdfprepare.output import resolve_font

    src = tmp_path / "d.pdf"; d = fitz.open()
    page = d.new_page()
    # Base-14 fonts drop Cyrillic on extraction (see tests/test_replace.py),
    # so the native text layer is written with the project's TTF.
    page.insert_font(fontname="f0", fontfile=resolve_font(None))
    page.insert_text(
        (72, 72),
        "ПРОЧИТАЙТЕ ИНСТРУКЦИЮ СМ. 028.10.00 И РИСУНОК 7 НИЖЕ",
        fontname="f0",
    )
    d.new_page()  # blank raster page
    d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [])

    table_pages = []
    monkeypatch.setattr(
        pl, "extract_tables",
        lambda img, words, page_index: table_pages.append(page_index) or [],
    )
    dst = tmp_path / "o"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})

    page = res.pages[0]
    assert page.is_native_text
    assert page.tables == []
    assert [f.number for f in page.figures] == ["7"]
    assert [(r.kind, r.target) for r in page.references] == [("system", "028.10.00")]
    # Only the raster page reached table detection.
    assert table_pages == [1]
    # Blank raster page went through the same extraction (no data, no crash).
    assert res.pages[1].is_blank
    assert res.pages[1].figures == [] and res.pages[1].references == []


def test_read_result_old_json_without_new_keys(tmp_path):
    """v1 JSON (no schema_version, no tables/figures/references) still loads:
    missing keys become empty lists, schema_version falls back to 1."""
    from pdfprepare.output import read_result

    data = {
        "source_path": "a.pdf",
        "page_count": 1,
        "system_names": {"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"},
        "pages": [
            {
                "page_index": 0,
                "is_native_text": True,
                "is_blank": False,
                "system_code": "023.00.00",
                "subsystem_code": "023.11.30",
                "full_text": "ТЕКСТ ЛИСТА",
                "words": [
                    {"text": "023.11.30", "x0": 72.0, "y0": 700.0,
                     "x1": 140.0, "y1": 710.0, "conf": 1.0}
                ],
            }
        ],
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    res = read_result(str(path))
    assert res.schema_version == 1
    assert res.source_path == "a.pdf" and res.page_count == 1
    assert res.system_names == {"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"}
    page = res.pages[0]
    assert page.tables == [] and page.figures == [] and page.references == []
    assert page.words[0].text == "023.11.30"
    assert page.words[0].x0 == pytest.approx(72.0)


def test_table_ambiguity_is_optional_in_v2_json(tmp_path):
    import json

    from pdfprepare.models import DocResult, PageInfo, Table
    from pdfprepare.output import read_result, write_json

    page = PageInfo(
        page_index=0, is_native_text=False, is_blank=False,
        system_code="", subsystem_code="", full_text="", words=[],
        tables=[Table(0, (0, 0, 10, 10), [["", "8МТ.6101.100"]],
                      ambiguous=[[True, False]])],
    )
    result = DocResult("a.pdf", 1, [page], {})
    path = tmp_path / "ambiguous.json"
    write_json(result, str(path))
    assert read_result(str(path)).pages[0].tables[0].ambiguous == [[True, False]]

    legacy = json.loads(path.read_text(encoding="utf-8"))
    del legacy["pages"][0]["tables"][0]["ambiguous"]
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    assert read_result(str(path)).pages[0].tables[0].ambiguous == []
