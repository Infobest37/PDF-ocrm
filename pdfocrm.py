from pathlib import Path
import os, io, sys, tempfile, traceback
from pdf2image import convert_from_path
import pymupdf as fitz
from PyPDF2 import PdfReader, PdfWriter
from PIL import Image
import pytesseract
import ocrmypdf

# optional: pip install pikepdf
try:
    import pikepdf

    HAVE_PIKEPDF = True
except Exception:
    HAVE_PIKEPDF = False

# --- Настройки путей (Windows) ---
tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
os.environ["TESSDATA_PREFIX"] = r"C:\Program Files\Tesseract-OCR\tessdata"
pytesseract.pytesseract.tesseract_cmd = tesseract_path

# Если poppler не в PATH, укажите здесь:
POPPLER_BIN = None
# POPPLER_BIN = r"C:\tools\poppler-24.02.0\Library\bin"

# Путь к исходному файлу (убедитесь, что файл существует)
INPUT_PDF = Path(r"C:\Users\user\Desktop\pythonCODE\OCR pdf\PDFn8nlibrali\PDF_OCR_APP\OCRfile\Инструкция.pdf")

# Путь к выходному файлу
OUTPUT_PDF = Path(r"C:\Users\user\Desktop\pythonCODE\OCR pdf\PDFn8nlibrali\PDF_OCR_APP\OCRoutput\Инструкция_OCR.pdf")

# Создаем папку для выходного файла, если её нет
OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)

Image.MAX_IMAGE_PIXELS = None  # для очень больших изображений


def osd_rotation(image: Image.Image) -> int:
    try:
        osd = pytesseract.image_to_osd(image)
        for line in osd.splitlines():
            if "Rotate:" in line:
                return int(line.split(":")[1].strip())
    except Exception as e:
        print(f"⚠️ OSD ошибка: {e}", file=sys.stderr)
    return 0


def page_has_digital_text(fitz_page: fitz.Page) -> bool:
    text = fitz_page.get_text("text")
    if text and text.strip():
        return True
    # если есть поля/виджеты — считаем как цифровой
    try:
        widgets = fitz_page.widgets()
        if widgets:
            return True
    except Exception:
        pass
    return False


def page_is_only_images(fitz_page: fitz.Page) -> bool:
    """
    Проверяет, состоит ли страница только из изображений (без текста).
    """
    if fitz_page.get_text("text").strip():
        return False
    images = fitz_page.get_images(full=True)
    return len(images) > 0


def ocr_single_scanned_page(pdf_path: Path, page_index: int, fitz_page: fitz.Page,
                            lang: str = "rus+eng") -> "PyPDF2.PageObject":
    """
    OCR одной страницы (с учётом размера страницы → DPI).
    """
    # Адаптивный DPI
    dpi = 300
    if fitz_page.rect.width > 800 and fitz_page.rect.height > 1200:  # больше A4
        dpi = 200

    images = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=page_index + 1,
        last_page=page_index + 1,
        fmt="jpeg",
        poppler_path=POPPLER_BIN if POPPLER_BIN else None,
    )
    img = images[0]

    # Автоповорот (OSD)
    rot = osd_rotation(img)
    if rot:
        img = img.rotate(-rot, expand=True, resample=Image.BICUBIC)
        print(f"   🔄 Повернули страницу {page_index + 1} на {rot}° (OSD)")

    # OCR в PDF
    pdf_bytes = pytesseract.image_to_pdf_or_hocr(img, extension="pdf", lang=lang)
    page_reader = PdfReader(io.BytesIO(pdf_bytes))
    return page_reader.pages[0]


def page_needs_ocr(fitz_page: fitz.Page) -> bool:
    """
    Проверяет, стоит ли делать OCR:
      - текста нет
      - или текст подозрительно короткий
    """
    text = fitz_page.get_text("text").strip()
    if not text:
        return True
    # если текст очень маленький (например, меньше 20 символов) – считаем, что OCR нужен
    if len(text) < 20:
        return True
    return False


def smart_ocr_pdf(input_pdf: Path, output_pdf: Path, lang: str = "rus+eng"):
    """
    Постраничная обработка PDF:
      - если страница реально с текстом → копируем
      - иначе → OCR
    """
    print(f"\n🔹 Постраничная обработка: {input_pdf.name}")
    writer = PdfWriter()

    try:
        with fitz.open(str(input_pdf)) as doc:
            src_reader = PdfReader(str(input_pdf))
            total = len(src_reader.pages)

            for i in range(total):
                try:
                    page = doc[i]

                    if not page_needs_ocr(page):
                        writer.add_page(src_reader.pages[i])
                        print(f"   📄 Стр. {i + 1}: цифровой текст → копия")
                    else:
                        ocr_page = ocr_single_scanned_page(input_pdf, i, page, lang=lang)
                        writer.add_page(ocr_page)
                        print(f"   🔠 Стр. {i + 1}: OCR выполнен")

                except Exception as e:
                    print(f"⚠️ Стр. {i + 1}: ошибка {e}. Копирую оригинал.")
                    traceback.print_exc()
                    writer.add_page(src_reader.pages[i])

        with open(output_pdf, "wb") as f:
            writer.write(f)
        print(f"✅ Готово: {output_pdf}")
        return True
    except Exception as e:
        print(f"❌ Ошибка в smart_ocr_pdf для {input_pdf.name}: {e}")
        traceback.print_exc()
        return False


def sanitize_pdf_with_pikepdf(src: Path, dst: Path):
    if not HAVE_PIKEPDF:
        raise RuntimeError("pikepdf не установлен")
    with pikepdf.open(str(src)) as pdf:
        pdf.save(str(dst))


def main():
    print(f"📂 Исходный файл: {INPUT_PDF}")
    print(f"📂 Выходной файл: {OUTPUT_PDF}")

    # Проверяем, существует ли исходный файл
    if not INPUT_PDF.exists():
        print(f"❌ Исходный файл не найден: {INPUT_PDF}")
        return

    # Проверяем, существует ли уже выходной файл
    if OUTPUT_PDF.exists():
        print(f"⚠️ Выходной файл уже существует. Удалите его или выберите другое имя.")
        return

    # Пробуем ocrmypdf (самый быстрый способ, если работает)
    try:
        print(f"\n🚀 Пробую ocrmypdf (самый быстрый способ)...")
        ocrmypdf.ocr(
            input_file=str(INPUT_PDF),
            output_file=str(OUTPUT_PDF),
            language='rus+eng',
            rotate_pages=True,
            output_type='pdf',
            optimize=3,
            jpeg_quality=50,
            progress_bar=True
        )
        print(f"✅ Файл успешно обработан через ocrmypdf!")

    except Exception as e:
        print(f"⚠️ ocrmypdf не сработал: {e}")

        # Пробуем "лечить" через pikepdf (если есть) и повторить
        if HAVE_PIKEPDF:
            try:
                tmp_sanitized = INPUT_PDF.with_suffix(".sanitized.pdf")
                print(f"🔧 Пробую санитаризацию через pikepdf...")
                sanitize_pdf_with_pikepdf(INPUT_PDF, tmp_sanitized)

                try:
                    ocrmypdf.ocr(
                        input_file=str(tmp_sanitized),
                        output_file=str(OUTPUT_PDF),
                        language='rus+eng',
                        rotate_pages=True,
                        output_type='pdf',
                        optimize=1,
                        progress_bar=True
                    )
                    print(f"✅ После санитаризации ocrmypdf сработал.")
                    tmp_sanitized.unlink(missing_ok=True)
                except Exception as e2:
                    print(f"⚠️ После санитаризации ocrmypdf всё ещё падает: {e2}")
                    print(f"🔄 Использую постраничный способ (smart_ocr_pdf)...")
                    success = smart_ocr_pdf(INPUT_PDF, OUTPUT_PDF, lang="rus+eng")
                    if not success:
                        print(f"❌ Не удалось обработать файл")
            except Exception as e3:
                print(f"⚠️ Ошибка при санитаризации: {e3}")
                print(f"🔄 Использую постраничный способ (smart_ocr_pdf)...")
                success = smart_ocr_pdf(INPUT_PDF, OUTPUT_PDF, lang="rus+eng")
                if not success:
                    print(f"❌ Не удалось обработать файл")
        else:
            print(f"🔄 Использую постраничный способ (smart_ocr_pdf)...")
            success = smart_ocr_pdf(INPUT_PDF, OUTPUT_PDF, lang="rus+eng")
            if not success:
                print(f"❌ Не удалось обработать файл")

    print(f"\n🎉 Обработка завершена!")


if __name__ == "__main__":
    main()