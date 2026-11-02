# -*- coding: utf-8 -*-
"""Ф0.3б: проверка целостности таблицы documents (read-only).

Проверки (карточка Ф0.3б, п. 5):

1. каждый документ из ``pages`` присутствует в ``documents``
   (обратное невозможно: document — PRIMARY KEY, строка без листов
   допустима, лист без строки — нет);
2. значения layer и applicability_scope — из допустимых множеств;
3. supersedes согласован с document_aliases: для строки с supersedes=X
   в алиасах должна быть пара (name=X, canonical_name=документ,
   status='duplicate') — канон дедупликации и действующая редакция
   обязаны указывать на одно и то же имя; расхождение попадает в
   отчёт, молча не правится (решение владельца Ф0.3б).

Запуск: venv\\Scripts\\python.exe -X utf8 tools\\check_documents.py
[путь_БД]. Код 0 — целостность, 1 — есть проблемы, 2 — нет базы.
Read-only подключение (mode=ro), база не изменяется.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = REPO_ROOT / "graph.db"

ALLOWED_LAYERS = {"base", "supplement", "change", "unknown"}
ALLOWED_SCOPES = {"all", "serials", "equipment"}
ALLOWED_REVIEW = {"ok", "needs_human"}


def check(connection: sqlite3.Connection
          ) -> tuple[list[str], dict[str, int]]:
    """Вернуть (список проблем, счётчики); read-only, без правок."""
    problems: list[str] = []
    pages_docs = {row[0] for row in connection.execute(
        "SELECT DISTINCT document FROM pages")}
    rows = {row[0]: row for row in connection.execute(
        "SELECT document, layer, applicability_scope, applicability_source,"
        " review_status, supersedes FROM documents")}
    missing = sorted(pages_docs - set(rows))
    for document in missing:
        problems.append(f"документ есть в pages, нет в documents: "
                        f"{document}")
    counts = {
        "pages_documents": len(pages_docs),
        "documents": len(rows),
        "needs_human": 0,
    }
    for document, layer, scope, source, review, supersedes in rows.values():
        if layer not in ALLOWED_LAYERS:
            problems.append(f"{document}: недопустимый layer={layer!r}")
        if scope not in ALLOWED_SCOPES:
            problems.append(
                f"{document}: недопустимый applicability_scope={scope!r}")
        if source not in {"name", "text", "manual"}:
            problems.append(f"{document}: недопустимый "
                            f"applicability_source={source!r}")
        if review not in ALLOWED_REVIEW:
            problems.append(f"{document}: недопустимый "
                            f"review_status={review!r}")
        if review == "needs_human":
            counts["needs_human"] += 1
        if supersedes:
            alias = connection.execute(
                "SELECT canonical_name, status FROM document_aliases "
                "WHERE name = ?", (supersedes,)).fetchone()
            if alias is None:
                problems.append(
                    f"{document}: supersedes={supersedes!r} отсутствует "
                    f"в document_aliases")
            elif alias[1] != "duplicate" or alias[0] != document:
                problems.append(
                    f"{document}: supersedes={supersedes!r} расходится с "
                    f"document_aliases (canonical={alias[0]!r}, "
                    f"status={alias[1]!r})")
    return problems, counts


def main(argv: list[str] | None = None) -> int:
    """Read-only проверка; код 0/1/2 (как CLI-контракт среды)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(argv[0]) if argv else DEFAULT_STORE
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    uri = f"file:{source.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        problems, counts = check(connection)
    finally:
        connection.close()
    print(f"documents: {counts['documents']} (pages: "
          f"{counts['pages_documents']}), needs_human: "
          f"{counts['needs_human']}")
    for problem in problems:
        print(f"ПРОБЛЕМА: {problem}")
    if not problems:
        print("Целостность: OK")
        return 0
    print(f"Итого проблем: {len(problems)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
