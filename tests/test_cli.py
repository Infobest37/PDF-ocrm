import pdfprepare.cli as cli


def test_main_one_corrupt_pdf_does_not_abort_batch(tmp_path, monkeypatch, capsys):
    import fitz

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()

    good = src_dir / "good.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 72), "ЭТО ЦИФРОВОЙ ТЕКСТ ДЛЯ НАТИВНОЙ СТРАНИЦЫ ДОСТАТОЧНОЙ ДЛИНЫ")
    d.save(str(good)); d.close()

    bad = src_dir / "bad.pdf"
    bad.write_bytes(b"this is definitely not a pdf file")

    monkeypatch.setattr(cli, "ocr_available", lambda: True)

    rc = cli.main([str(src_dir), str(dst_dir)])

    assert rc != 0
    assert (dst_dir / "good.pdf").exists()
    assert (dst_dir / "good.json").exists()
    assert "bad.pdf" in capsys.readouterr().err


def test_main_fails_fast_when_ocr_missing(tmp_path, monkeypatch, capsys):
    src_dir = tmp_path / "src"; src_dir.mkdir()
    dst_dir = tmp_path / "dst"

    monkeypatch.setattr(cli, "ocr_available", lambda: False)

    rc = cli.main([str(src_dir), str(dst_dir)])

    assert rc != 0
    err = capsys.readouterr().err
    assert "tesseract" in err.lower()
    assert "PDFPREPARE_TESSERACT" in err
    assert not dst_dir.exists()
