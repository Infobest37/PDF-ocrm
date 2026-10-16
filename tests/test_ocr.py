def test_ocr_image_maps_data_to_words(monkeypatch):
    import pdfprepare.ocr as ocr
    monkeypatch.setattr(ocr.pytesseract, "image_to_data",
        lambda *a, **k: {"level": [5], "text": ["ПРИМЕР"], "conf": [90],
                         "left": [0], "top": [0], "width": [100], "height": [20]})
    words = ocr.ocr_image(None)
    assert len(words) == 1
    w = words[0]
    assert w.text == "ПРИМЕР" and w.conf == 0.9 and w.x1 == 100.0 and w.y1 == 20.0
