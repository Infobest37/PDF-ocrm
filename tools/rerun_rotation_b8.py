# -*- coding: utf-8 -*-
"""Б8 (повороты): перепрогон needs_rescan-листов с исправлением ориентации.

Механика конвейера (pdfprepare): OSD определяет угол поворота (кратный 90°),
изображение разворачивается (rotate_expand), затем обычный deskew + OCR.
pages.text обновляется дословным OCR-текстом (слова через пробел — тот же
формат, что при build); needs_rescan снимается через Store.unmark_rescan
(причина «manual: снято: ...», авто-пометка не вернётся).

Два прохода:
  1) 9 целевых листов: «Двигатель ТВ3-117 078.00.5700 кн1 книга_1
     (изм. от 05.03.2026).pdf», страницы 226–234;
  2) остальные: все needs_rescan=1 минус уже перепрогнанные.

По умолчанию dry-run (только печать: угол, длина текста было/стало, превью);
запись в базу — только с явным --write.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from pdfgraph.store import Store  # noqa: E402
from pdfprepare.ocr import detect_orientation, ocr_image  # noqa: E402
from pdfprepare.page import render  # noqa: E402
from pdfprepare.preprocess import prepare_with_angle, rotate_expand  # noqa: E402

DEFAULT_SRC_DIR = "c:/Users/user/Desktop/pythonCODE/PDFprog/pdfs"
#: Шаблон имени документа прохода 1 (резолвится по documents, не строкой).
PASS1_PATTERN = ("%книга_1%05.03.2026%")
PASS1_PAGES = list(range(226, 235))  # 226..234 включительно


def resolve_pass1_document(store: Store) -> str:
    """Точное имя документа прохода 1 по шаблону; ошибка, если не один."""
    rows = store._connection.execute(
        "SELECT document FROM documents WHERE document LIKE ?",
        (PASS1_PATTERN,),
    ).fetchall()
    if len(rows) != 1:
        raise LookupError(
            f"шаблон {PASS1_PATTERN!r} дал {len(rows)} документов, нужен один")
    return rows[0][0]


def src_index(src_dir: str) -> dict[str, str]:
    """Индекс «имя в нижнем регистре -> реальное имя файла» каталога."""
    return {name.lower(): name for name in os.listdir(src_dir)}


def ocr_page(pdf_path: str, page_index: int) -> tuple[int, str]:
    """OCR одной страницы с исправлением ориентации.

    Возвращает ``(угол_поворота, текст)``; текст дословный, слова через
    пробел (тот же формат, что ``pipeline.process_file``).
    """
    doc = fitz.open(pdf_path)
    try:
        rendered = render(doc[page_index])
        rot = detect_orientation(rendered)
        oriented = rotate_expand(rendered, rot) if rot else rendered
        prepared, _angle = prepare_with_angle(oriented)
        words = ocr_image(prepared)
        return rot, " ".join(w.text for w in words)
    finally:
        doc.close()


def worklist(store: Store, pass1: bool, pass2: bool) -> list[tuple[str, int]]:
    """Список (документ, страница): проход 1 + проход 2 без дублей."""
    items: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    if pass1:
        document = resolve_pass1_document(store)
        for page in PASS1_PAGES:
            items.append((document, page))
            seen.add((document, page))
    if pass2:
        for document, page in store.rescan_sheets():
            if (document, page) not in seen:
                items.append((document, page))
                seen.add((document, page))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="graph.db")
    parser.add_argument("--src-dir", default=DEFAULT_SRC_DIR)
    parser.add_argument("--pass1", action="store_true",
                        help="9 целевых листов книга_1 л.226-234")
    parser.add_argument("--pass2", action="store_true",
                        help="остальные needs_rescan=1")
    parser.add_argument("--write", action="store_true",
                        help="записать в базу (по умолчанию dry-run)")
    args = parser.parse_args()

    if not args.pass1 and not args.pass2:
        args.pass1 = True  # без явного выбора — только целевые 9 листов

    store = Store(args.db)
    items = worklist(store, args.pass1, args.pass2)
    print(f"листов в работе: {len(items)} "
          f"(pass1={args.pass1}, pass2={args.pass2}, "
          f"mode={'WRITE' if args.write else 'DRY-RUN'})")

    names = src_index(args.src_dir)
    rotated = 0
    written = 0
    skipped = 0
    try:
        for document, page in items:
            actual = names.get(document.lower())
            if actual is None:
                print(f"НЕТ ИСХОДНИКА: {document} л.{page}")
                skipped += 1
                continue
            pdf_path = os.path.join(args.src_dir, actual)
            old_text = store.page_text(document, page)
            try:
                rot, new_text = ocr_page(pdf_path, page)
            except Exception as exc:  # noqa: BLE001 — лист не блокирует очередь
                print(f"ОШИБКА OCR: {document} л.{page}: {exc}")
                skipped += 1
                continue
            if rot:
                rotated += 1
            preview_new = (new_text[:70] + "…") if len(new_text) > 70 else new_text
            print(f"л.{page} rot={rot:>3} "
                  f"len {len(old_text)} -> {len(new_text)} "
                  f"| {preview_new!r}")
            if args.write:
                with store._connection:
                    store._connection.execute(
                        "UPDATE pages SET text = ? "
                        "WHERE document = ? AND page = ?",
                        (new_text, document, page),
                    )
                store.unmark_rescan(
                    document, page, reason="Б8: поворот исправлен, OCR перепрогнан")
                written += 1
    finally:
        store.close()

    print(f"итог: повёрнутых {rotated} из {len(items)}, "
          f"записано {written}, пропущено {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
