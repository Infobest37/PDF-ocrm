"""Повороты страниц: OSD-детектор ориентации и обратный маппинг word-боксов.

``detect_orientation`` возвращает угол по часовой стрелке (0/90/180/270),
который нужно применить к изображению, чтобы текст встал ровно. Контракт
маппинга — «по образцу deskew»: бокс из подготовленного изображения
возвращается в систему координат исходного (отрендеренного) листа.

Тесты детектора требуют локальный Tesseract с ``osd.traineddata``
(``pdfprepare/tesseract/`` или ``PDFPREPARE_TESSERACT``); без него они
пропускаются. Геометрические тесты маппинга чистые и идут всегда.
"""

import math
import os

import pytest

from pdfprepare.bundle import tessdata_dir, tesseract_cmd

_OSD_READY = os.path.isfile(tesseract_cmd()) and os.path.isfile(
    os.path.join(tessdata_dir(), "osd.traineddata"))

_osd = pytest.mark.skipif(not _OSD_READY,
                          reason="Tesseract/osd.traineddata недоступны")


def _text_page():
    """Синтетический «лист»: блок кириллического текста 900x1200."""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for name in ("arial.ttf", "segoeui.ttf", "times.ttf", "calibri.ttf"):
        path = os.path.join("C:/Windows/Fonts", name)
        if os.path.isfile(path):
            font = ImageFont.truetype(path, 28)
            break
    assert font is not None, "не найден системный шрифт с кириллицей"
    img = Image.new("L", (900, 1200), 255)
    draw = ImageDraw.Draw(img)
    lines = [
        "ТЕХНОЛОГИЧЕСКАЯ КАРТА № 703",
        "Восстановление лакокрасочных покрытий",
        "Двигатель ТВ3-117 078.00.5700",
        "Давление масла на входе в двигатель",
        "не менее 2,0 кгс/см2 при номинальном режиме",
        "Операция выполняется при снятом кожухе",
        "Втулка несущего винта, конус болтов",
        "Момент затяжки 12 даНм, шпильки М10",
    ]
    y = 80
    for i in range(12):
        draw.text((80, y), lines[i % len(lines)], font=font, fill=0)
        y += 85
    return img


# --- геометрия маппинга (без Tesseract) -----------------------------------


def test_rotate_expand_sizes():
    from PIL import Image

    from pdfprepare.preprocess import rotate_expand

    img = Image.new("L", (200, 100), 255)
    assert rotate_expand(img, 0).size == (200, 100)
    assert rotate_expand(img, 90).size == (100, 200)
    assert rotate_expand(img, 180).size == (200, 100)
    assert rotate_expand(img, 270).size == (100, 200)


def test_invert_orientation_identity_for_zero():
    from pdfprepare.preprocess import invert_orientation

    assert invert_orientation((10, 20, 30, 40), 0, (200, 100), (200, 100)) == (
        10, 20, 30, 40)


def test_invert_orientation_90cw_known_bbox():
    """Поворот 90° по часовой: исходный (200x100) -> повёрнутый (100x200).

    Бокс (10, 20, 30, 40) в повёрнутом кадре обязан вернуться в
    (20, 70, 40, 90) исходного кадра (проверено аналитически).
    """
    from pdfprepare.preprocess import invert_orientation

    x0, y0, x1, y1 = invert_orientation((10, 20, 30, 40), 90, (100, 200),
                                        (200, 100))
    assert math.isclose(x0, 20, abs_tol=1e-9)
    assert math.isclose(y0, 70, abs_tol=1e-9)
    assert math.isclose(x1, 40, abs_tol=1e-9)
    assert math.isclose(y1, 90, abs_tol=1e-9)


def test_invert_orientation_180_known_bbox():
    from pdfprepare.preprocess import invert_orientation

    x0, y0, x1, y1 = invert_orientation((10, 20, 30, 40), 180, (200, 100),
                                        (200, 100))
    assert math.isclose(x0, 170, abs_tol=1e-9)
    assert math.isclose(y0, 60, abs_tol=1e-9)
    assert math.isclose(x1, 190, abs_tol=1e-9)
    assert math.isclose(y1, 80, abs_tol=1e-9)


def test_invert_orientation_round_trip_all_corners():
    """Обратный маппинг согласован с прямым поворотом rotate_expand.

    Угол прямой проверяется через геометрию: центр бокса в исходном кадре,
    переведённый в повёрнутый кадр известной формулой (поворот по часовой
    на 90° в координатах y-вниз: (x, y) -> (H1 - y, x)), обязан вернуться
    обратным маппингом на место.
    """
    from pdfprepare.preprocess import invert_orientation

    w1, h1 = 200, 100
    box = (40, 30, 120, 70)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    # Прямой ход: поворот 90° CW, новый кадр (h1, w1).
    rx, ry = h1 - cy, cx
    x0, y0, x1, y1 = invert_orientation(
        (rx - 10, ry - 5, rx + 10, ry + 5), 90, (h1, w1), (w1, h1))
    assert math.isclose((x0 + x1) / 2, cx, abs_tol=1e-9)
    assert math.isclose((y0 + y1) / 2, cy, abs_tol=1e-9)


def test_invert_rotation_deskew_composes_orientation_and_deskew():
    """Комбинированный маппинг: deskew-угол 0 и поворот 90 — чистый поворот."""
    from pdfprepare.preprocess import invert_rotation_deskew

    # prepared == oriented (deskew 0, без апскейла), повёрнутый кадр 100x200.
    x0, y0, x1, y1 = invert_rotation_deskew(
        (10, 20, 30, 40), angle=0.0, prepared_size=(100, 200), rot=90,
        oriented_size=(100, 200), rendered_size=(200, 100))
    assert math.isclose(x0, 20, abs_tol=1e-9)
    assert math.isclose(y0, 70, abs_tol=1e-9)
    assert math.isclose(x1, 40, abs_tol=1e-9)
    assert math.isclose(y1, 90, abs_tol=1e-9)


# --- OSD-детектор (нужен Tesseract с osd.traineddata) ----------------------


@_osd
def test_detect_orientation_upright_is_zero():
    from pdfprepare.ocr import detect_orientation

    assert detect_orientation(_text_page()) == 0


@_osd
def test_detect_orientation_rotated_90cw_returns_270():
    from PIL import Image

    from pdfprepare.ocr import detect_orientation

    rotated = _text_page().transpose(Image.Transpose.ROTATE_270)  # 90° CW
    assert detect_orientation(rotated) == 270


@_osd
def test_detect_orientation_rotated_180_returns_180():
    from PIL import Image

    from pdfprepare.ocr import detect_orientation

    rotated = _text_page().transpose(Image.Transpose.ROTATE_180)
    assert detect_orientation(rotated) == 180


@_osd
def test_detect_orientation_blank_page_is_zero():
    from PIL import Image

    from pdfprepare.ocr import detect_orientation

    assert detect_orientation(Image.new("L", (900, 1200), 255)) == 0


# --- конвейер (мок OCR, без Tesseract) -------------------------------------


def test_pipeline_rotated_word_boxes_map_to_page_points(monkeypatch, tmp_path):
    """Поворот 90° CW на конвейере: бокс OCR из подготовленного кадра
    возвращается в точки PDF-страницы (верх-лево повёрнутого -> низ-лево
    исходного)."""
    import fitz

    import pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    from pdfprepare.page import render
    from pdfprepare.preprocess import prepare_with_angle, rotate_expand

    src = tmp_path / "p.pdf"
    d = fitz.open()
    page = d.new_page(width=400, height=600)
    page.draw_rect(fitz.Rect(0, 0, 400, 600), color=None, fill=(1, 1, 1))
    d.save(str(src))
    d.close()

    doc = fitz.open(str(src))
    rendered = render(doc[0])
    oriented = rotate_expand(rendered, 90)
    prepared, angle = prepare_with_angle(oriented)
    assert angle == 0.0
    # Бокс в подготовленном (повёрнутом) кадре: верх-лево.
    box = (10.0, 10.0, 60.0, 30.0)

    monkeypatch.setattr(pl, "detect_orientation", lambda img: 90)
    monkeypatch.setattr(pl, "ocr_image",
                        lambda img, lang="rus+eng": [
                            Word("ТЕКСТ", box[0], box[1], box[2], box[3], 0.9)])
    monkeypatch.setattr(pl, "extract_tables", lambda img, words, idx: [])
    doc.close()
    (tmp_path / "out").mkdir()

    result = pl.process_file(str(src), str(tmp_path / "out"), {})
    w = result.pages[0].words[0]
    # Точки страницы: повёрнутый верх-лево -> исходный низ-лево.
    assert w.x0 < 400 / 2, "x обязан быть в левой половине исходного листа"
    assert w.y0 > 600 / 2, "y обязан быть в нижней половине исходного листа"
    assert 0 <= w.x0 < w.x1 <= 400
    assert 0 <= w.y0 < w.y1 <= 600
