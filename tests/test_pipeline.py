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
