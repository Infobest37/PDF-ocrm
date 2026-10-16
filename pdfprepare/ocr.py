import os

import pytesseract
from PIL import Image
from pytesseract import Output

from pdfprepare.bundle import tesseract_cmd, tessdata_dir
from pdfprepare.models import Word


def ocr_image(img: Image.Image, lang: str = "rus+eng") -> list[Word]:
    """Run Tesseract over a page image and map each detected word to a ``Word``.

    Uses the bundled Tesseract binary and language data discovered by
    :mod:`pdfprepare.bundle`. Rows with empty text or a confidence of ``-1``
    are skipped; confidence is normalized to ``0..1`` (``conf / 100.0``).
    """
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()
    os.environ["TESSDATA_PREFIX"] = tessdata_dir()

    data = pytesseract.image_to_data(
        img, lang=lang, output_type=Output.DICT, config="--psm 6"
    )

    words: list[Word] = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        conf = data["conf"][i]
        if not text or conf == -1:
            continue
        x0 = float(data["left"][i])
        y0 = float(data["top"][i])
        x1 = x0 + float(data["width"][i])
        y1 = y0 + float(data["height"][i])
        words.append(Word(text=text, x0=x0, y0=y0, x1=x1, y1=y1, conf=conf / 100.0))
    return words


def ocr_available() -> bool:
    """``True`` when the bundled Tesseract binary and rus/eng data are present."""
    data_dir = tessdata_dir()
    return (
        os.path.isfile(tesseract_cmd())
        and os.path.isfile(os.path.join(data_dir, "rus.traineddata"))
        and os.path.isfile(os.path.join(data_dir, "eng.traineddata"))
    )
