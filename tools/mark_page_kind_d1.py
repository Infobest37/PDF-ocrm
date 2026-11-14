# -*- coding: utf-8 -*-
"""Д1а: разметка типов листов ``pages.page_kind`` (dry-run по умолчанию).

Классификатор :func:`pdfgraph.page_kind.classify_page` не обращается к
базе: он отсекает колонтитул книги и смотрит шапку листа. Скрипт только
читает ``pages`` и либо печатает отчёт (dry-run), либо пишет типы одной
транзакцией (``--write``). В dry-run база открывается read-only, схема
не мигрирует.

Отчёт (по умолчанию ``reports/page_kind_d1.md``) содержит:

1. счётчики по восьми содержательным типам, ``blank`` и отдельно
   цифру ``other``;
2. разбивку по документам;
3. по пять примеров на тип (документ, лист, первые 150 символов);
4. десять случайных листов из ``other`` — для решения, какие маркеры
   дописать (условие перед Д1б: other больше 40% — Д1б не начинать).

Запуск из корня репозитория::

    venv\\Scripts\\python.exe -X utf8 tools\\mark_page_kind_d1.py
    venv\\Scripts\\python.exe -X utf8 tools\\mark_page_kind_d1.py --write

Код 0 — отчёт построен; 1 — нет базы или таблицы ``pages``; 2 — ошибка
записи/миграции.
"""

from __future__ import annotations

import argparse
import collections
import random
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.page_kind import (  # noqa: E402
    ALL_KINDS,
    classify_page,
    page_kind_label,
)

DEFAULT_STORE = REPO_ROOT / "graph.db"
DEFAULT_REPORT = REPO_ROOT / "reports" / "page_kind_d1.md"
EXAMPLE_CHARS = 150


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Read-only подключение: dry-run не мигрирует схему."""
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _schema_version(connection: sqlite3.Connection) -> str:
    """Версия схемы из meta; отсутствие meta — «н/д»."""
    try:
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error:
        return "н/д"
    return str(row[0]) if row is not None else "н/д"


def _has_pages(connection: sqlite3.Connection) -> bool:
    """Есть ли таблица pages (иначе это не база графа)."""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pages'"
    ).fetchone()
    return row is not None


def _page_kind_column(connection: sqlite3.Connection) -> bool:
    """Есть ли уже колонка page_kind (до миграции её нет)."""
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(pages)")}
    return "page_kind" in columns


def _read_pages(connection: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """Все листы в стабильном порядке (document, page)."""
    return [(str(row[0]), int(row[1]), str(row[2] or ""))
            for row in connection.execute(
                "SELECT document, page, text FROM pages "
                "ORDER BY document, page")]


def _snippet(text: str) -> str:
    """Первые 150 символов без переводов строк."""
    return re.sub(r"\s+", " ", text or "").strip()[:EXAMPLE_CHARS]


def _classify_all(
    pages: list[tuple[str, int, str]],
) -> tuple[list[tuple[str, int, str]], dict[str, int]]:
    """Классифицировать все листы; вернуть (строки для записи, счётчики)."""
    rows: list[tuple[str, int, str]] = []
    counts: dict[str, int] = collections.Counter()
    for document, page, text in pages:
        kind = classify_page(text)
        rows.append((document, page, kind))
        counts[kind] += 1
    for kind in ALL_KINDS:
        counts.setdefault(kind, 0)
    return rows, counts


def _document_matrix(
    rows: list[tuple[str, int, str]],
) -> dict[str, dict[str, int]]:
    """Матрица «документ → тип → число листов»."""
    matrix: dict[str, dict[str, int]] = {}
    for document, _page, kind in rows:
        matrix.setdefault(document, collections.Counter())[kind] += 1
    return matrix


def _examples(
    pages: list[tuple[str, int, str]],
    kinds: list[str],
    per_kind: int,
) -> dict[str, list[tuple[str, int, str]]]:
    """Первые ``per_kind`` листов каждого типа."""
    examples: dict[str, list[tuple[str, int, str]]] = {
        kind: [] for kind in kinds}
    for document, page, text in pages:
        kind = classify_page(text)
        if len(examples[kind]) < per_kind:
            examples[kind].append((document, page, text))
    return examples


def _other_samples(
    pages: list[tuple[str, int, str]],
    limit: int,
    seed: int,
) -> list[tuple[str, int, str]]:
    """Случайные листы типа other (для добора маркеров)."""
    other = [(document, page, text) for document, page, text in pages
             if classify_page(text) == "other"]
    if len(other) <= limit:
        return other
    return random.Random(seed).sample(other, limit)


def _format_report(
    *,
    store_path: Path,
    write: bool,
    schema_before: str,
    schema_after: str,
    pages_before: int,
    pages_after: int,
    counts: dict[str, int],
    matrix: dict[str, dict[str, int]],
    examples: dict[str, list[tuple[str, int, str]]],
    other_samples: list[tuple[str, int, str]],
    written: int,
) -> str:
    """Собрать markdown-отчёт Д1а."""
    total = sum(counts.values())
    lines: list[str] = [
        "# Д1а: типы листов (page_kind)",
        "",
        f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"База: `{store_path}`",
        f"Режим: {'--write' if write else 'dry-run (без записи)'}",
        "",
        "## Счётчики",
        "",
        f"Строк `pages` до: {pages_before}; после: {pages_after}",
        f"schema_version до: {schema_before}; после: {schema_after}",
        f"Записано строк: {written}",
        "",
        "| Тип | Подпись | Листов | Доля |",
        "|---|---|---:|---:|",
    ]
    for kind in ALL_KINDS:
        count = counts.get(kind, 0)
        share = count / total * 100 if total else 0.0
        lines.append(
            f"| `{kind}` | {page_kind_label(kind)} | {count} | "
            f"{share:.1f}% |")
    other_share = counts.get("other", 0) / total * 100 if total else 0.0
    lines += [
        "",
        f"**other: {counts.get('other', 0)} из {total} "
        f"({other_share:.1f}%)**",
        "",
        "## Разбивка по документам",
        "",
        "| Документ | " + " | ".join(f"`{kind}`" for kind in ALL_KINDS)
        + " |",
        "|---|" + "---:|" * len(ALL_KINDS),
    ]
    for document in sorted(matrix):
        row = matrix[document]
        cells = " | ".join(str(row.get(kind, 0)) for kind in ALL_KINDS)
        lines.append(f"| {document} | {cells} |")
    lines += ["", "## Примеры по типам", ""]
    for kind in ALL_KINDS:
        lines.append(f"### `{kind}` — {page_kind_label(kind)}")
        lines.append("")
        if not examples.get(kind):
            lines.append("_нет листов_")
        for document, page, text in examples.get(kind, []):
            lines.append(f"- {document}, л.{page}: `{_snippet(text)}`")
        lines.append("")
    lines += [
        f"## other: {len(other_samples)} случайных листов",
        "",
        "Если other больше 40%, Д1б не начинать: сначала дописать маркеры.",
        "",
    ]
    if not other_samples:
        lines.append("_листов other нет_")
    for document, page, text in other_samples:
        lines.append(f"- {document}, л.{page}: `{_snippet(text)}`")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Точка входа: dry-run или --write, отчёт в markdown."""
    parser = argparse.ArgumentParser(
        description="Д1а: разметка типов листов page_kind")
    parser.add_argument("store", nargs="?", default=str(DEFAULT_STORE),
                        help="путь к graph.db (по умолчанию graph.db)")
    parser.add_argument("--write", action="store_true",
                        help="записать page_kind (по умолчанию dry-run)")
    parser.add_argument("--report", default=str(DEFAULT_REPORT),
                        help="путь к markdown-отчёту")
    parser.add_argument("--examples", type=int, default=5,
                        help="примеров на тип (по умолчанию 5)")
    parser.add_argument("--other-samples", type=int, default=10,
                        help="случайных листов other (по умолчанию 10)")
    parser.add_argument("--seed", type=int, default=42,
                        help="зерно выборки other (по умолчанию 42)")
    args = parser.parse_args(argv)

    store_path = Path(args.store)
    if not store_path.is_file():
        print(f"error: нет базы: {store_path}", file=sys.stderr)
        return 1

    connection = _connect_readonly(store_path)
    try:
        if not _has_pages(connection):
            print(f"error: в базе нет таблицы pages: {store_path}",
                  file=sys.stderr)
            return 1
        schema_before = _schema_version(connection)
        pages = _read_pages(connection)
        pages_before = len(pages)
        had_page_kind = _page_kind_column(connection)
    finally:
        connection.close()

    rows, counts = _classify_all(pages)
    matrix = _document_matrix(rows)
    examples = _examples(pages, list(ALL_KINDS), args.examples)
    other_samples = _other_samples(pages, args.other_samples, args.seed)

    written = 0
    schema_after = schema_before
    pages_after = pages_before
    if args.write:
        try:
            from pdfgraph.store import Store
            store = Store(str(store_path))
            try:
                schema_after = store.get_meta("schema_version")
                pages_after = store._connection.execute(
                    "SELECT COUNT(*) FROM pages").fetchone()[0]
                written = store.set_page_kinds(rows)
            finally:
                store.close()
        except (sqlite3.Error, RuntimeError) as error:
            print(f"error: запись page_kind не выполнена: {error}",
                  file=sys.stderr)
            return 2

    report = _format_report(
        store_path=store_path,
        write=args.write,
        schema_before=schema_before,
        schema_after=schema_after,
        pages_before=pages_before,
        pages_after=pages_after,
        counts=counts,
        matrix=matrix,
        examples=examples,
        other_samples=other_samples,
        written=written,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    total = sum(counts.values())
    other_share = counts.get("other", 0) / total * 100 if total else 0.0
    print(f"листов: {total}; schema_version: {schema_before} -> "
          f"{schema_after}; page_kind был: {'да' if had_page_kind else 'нет'}")
    for kind in ALL_KINDS:
        print(f"  {kind:9s} {counts.get(kind, 0):6d}")
    print(f"other: {counts.get('other', 0)} ({other_share:.1f}%)")
    if args.write:
        print(f"записано строк: {written}")
    print(f"отчёт: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
