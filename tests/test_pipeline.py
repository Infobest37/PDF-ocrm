import pytest


def test_process_file_builds_structure(tmp_path, monkeypatch):
    import fitz
    import pdfprepare.pipeline as pl
    src = tmp_path / "in.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 72), "ЭТО ЦИФРОВОЙ ТЕКСТ ДЛЯ НАТИВНОЙ СТРАНИЦЫ ДОСТАТОЧНОЙ ДЛИНЫ")
    d.new_page()  # raster (empty) page
    d.save(str(src)); d.close()

    monkeypatch.setattr(pl, "ocr_image",
        lambda img, lang="rus+eng": [__import__("pdfprepare.models", fromlist=["Word"]).Word("023.11.30", 72, 700, 140, 710, 0.95)])
    dst = tmp_path / "out"; dst.mkdir()
    result = pl.process_file(str(src), str(dst), {"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    assert result.pages[0].is_native_text is True
    assert result.pages[1].subsystem_code == "023.11.30"
    assert (dst / "in.json").exists() and (dst / "in.pdf").exists()


def test_raster_page_system_only_code(tmp_path, monkeypatch):
    import fitz
    import pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    src = tmp_path / "in.pdf"
    d = fitz.open(); d.new_page(); d.save(str(src)); d.close()

    monkeypatch.setattr(pl, "ocr_image",
        lambda img, lang="rus+eng": [Word("023.00.00", 10, 10, 60, 40, 0.9)])
    dst = tmp_path / "out"; dst.mkdir()
    result = pl.process_file(str(src), str(dst), {})
    page = result.pages[0]
    assert page.system_code == "023.00.00"
    assert page.subsystem_code == ""


def test_raster_page_blank_boundary(tmp_path, monkeypatch):
    import fitz
    import pdfprepare.pipeline as pl
    src = tmp_path / "in.pdf"
    d = fitz.open(); d.new_page(); d.save(str(src)); d.close()

    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [])
    dst = tmp_path / "out"; dst.mkdir()
    result = pl.process_file(str(src), str(dst), {})
    assert result.pages[0].is_blank is True
    assert result.pages[0].system_code == ""


def test_ocr_boxes_scale_from_prepared_image(tmp_path, monkeypatch):
    import fitz
    import pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    from pdfprepare.page import render
    from pdfprepare.preprocess import prepare

    # A tiny page: rendering at 300 dpi yields < 300 px on the short side, so
    # ``prepare`` upscales and the prepared image is larger than the rendered
    # one. OCR boxes must scale by prepared size, not rendered size.
    src = tmp_path / "in.pdf"
    d = fitz.open(); d.new_page(width=50, height=50); d.save(str(src)); d.close()

    # Recompute the exact prepared size the pipeline will use.
    with fitz.open(str(src)) as doc:
        rendered = render(doc[0])
        rect = doc[0].rect
    prepared = prepare(rendered)
    assert prepared.width != rendered.width

    # A word at a known prepared-image pixel.
    monkeypatch.setattr(pl, "ocr_image",
        lambda img, lang="rus+eng": [Word("X", 30, 40, 60, 80, 0.9)])

    dst = tmp_path / "out"; dst.mkdir()
    result = pl.process_file(str(src), str(dst), {})
    word = result.pages[0].words[0]

    # PDF-point coordinates = prepared-pixel * page_rect / prepared_size.
    assert word.x0 == pytest.approx(30 * rect.width / prepared.width)
    assert word.y0 == pytest.approx(40 * rect.height / prepared.height)
    assert word.x1 == pytest.approx(60 * rect.width / prepared.width)
    assert word.y1 == pytest.approx(80 * rect.height / prepared.height)
