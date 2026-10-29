"""Проверка ссылок на исходные PDF — карточка 0.1б, Шаг 3 (read-only).

Для каждого имени документа в графе проверяет, разрешается ли оно
в существующий PDF-файл той же логикой, что и GUI
(``pdfgraph.gui.pdf_opener``: точное имя → ``+ .pdf`` → NFC/casefold-скан).
Пути к PDF в самой БД не хранятся, поэтому «битой ссылкой» считается
документ, чьё имя не находится в папке PDF — цитата по такому листу
открывается «в никуда».

Read-only: единственный SQL — read-only-снимок каталога документов.
Exit-код: 0 — все пути найдены; 1 — есть ненайденные; 2 — не найден
ни каталог PDF, ни хранилище.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.request import pathname2url

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from pdfgraph.gui.pdf_opener import _find_pdf_file, resolve_pdf_dir
except ModuleNotFoundError:  # запуск как скрипт вне pytest
    sys.path.insert(0, str(REPO_ROOT))
    from pdfgraph.gui.pdf_opener import _find_pdf_file, resolve_pdf_dir


def _ro_connect(store_path: str | Path) -> sqlite3.Connection:
    """Read-only-подключение к хранилищу (запись не допускается)."""
    uri = f"file:{pathname2url(str(store_path))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def main(argv: list[str] | None = None) -> int:
    """Проверить каждый документ и напечатать таблицу найдено/нет."""
    parser = argparse.ArgumentParser(
        prog="check_links",
        description="Сверка имён документов графа с файлами в папке PDF "
                    "(read-only; карточка 0.1б, Шаг 3).")
    parser.add_argument("--store", default=str(REPO_ROOT / "graph.db"))
    parser.add_argument("--pdf-dir", default=None,
                        help="Папка PDF; по умолчанию PDFGRAPH_PDF_DIR, "
                             "затем DEFAULT_PDF_DIR (как в GUI).")
    parser.add_argument("--json", action="store_true",
                        help="Машиночитаемый вывод одной JSON-строкой.")
    args = parser.parse_args(argv)

    pdf_dir = resolve_pdf_dir(args.pdf_dir)
    if pdf_dir is None:
        print("error: папка PDF не найдена (задайте --pdf-dir или "
              "PDFGRAPH_PDF_DIR)", file=sys.stderr)
        return 2
    try:
        con = _ro_connect(args.store)
    except sqlite3.Error as exc:
        print(f"error: хранилище не читается: {args.store!r}: {exc}",
              file=sys.stderr)
        return 2
    try:
        documents = [row[0] for row in
                     con.execute("SELECT DISTINCT document FROM pages "
                                 "ORDER BY document").fetchall()]
    finally:
        con.close()

    rows: list[dict[str, object]] = []
    for name in documents:
        found = _find_pdf_file(pdf_dir, name)
        rows.append({
            "document": name,
            "found": found is not None,
            "path": str(found) if found is not None else None,
            "size_bytes": found.stat().st_size if found is not None else None,
        })

    missing = [row for row in rows if not row["found"]]
    if args.json:
        print(json.dumps({"pdf_dir": str(pdf_dir), "documents": rows,
                          "missing": missing}, ensure_ascii=False))
    else:
        print(f"PDF-папка: {pdf_dir}")
        for row in rows:
            size = row["size_bytes"]
            size_mb = f"{size / (1024 * 1024):.1f} MB" if size else "—"
            print(f"{'ок ' if row['found'] else 'НЕТ'} "
                  f"{size_mb:>10}  {row['document']}")
        summary = (f"Итого: {len(rows) - len(missing)}/{len(rows)} найдено")
        print(summary)
        if missing:
            print("Битые ссылки (цитаты по ним открываются «в никуда»): "
                  f"{len(missing)}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())