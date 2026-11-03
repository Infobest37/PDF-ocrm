# -*- coding: utf-8 -*-
"""Ф1.9, шаг 1: замер поиска по номерам технологических карт (без правок).

Прогоняет шесть форм запроса через продакшн-цепочку
:func:`pdfgraph.query.hybrid_search` (как CLI ask) на рабочей базе
и печатает для каждой: топ-выдачу (документ, лист, kind, первые 80
символов сниппета) и место карты 035.10.00e в выдаче, если она есть.

Запуск из корня репозитория::

    venv\\Scripts\\python.exe tools\\techcard_measure_f19.py [store]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.query import hybrid_search  # noqa: E402
from pdfgraph.store import Store  # noqa: E402

QUERIES = [
    "035.10.00e",
    "035.10.00 e",
    "035.10.00а,b,e",
    "035.10.00",
    "технологическая карта 035.10.00e",
    "карта 035.10.00e",
]


def main() -> int:
    store_path = sys.argv[1] if len(sys.argv) > 1 else str(REPO_ROOT / "graph.db")
    store = Store(store_path)
    try:
        for query in QUERIES:
            print("=" * 72)
            print(f"ЗАПРОС: {query!r}")
            fragments = hybrid_search(store, query, limit=10)
            if not fragments:
                print("  выдача пустая")
                continue
            for i, fragment in enumerate(fragments[:10], 1):
                head = " ".join(fragment.snippet.split())[:80]
                print(f"  {i}. [{fragment.kind}] {fragment.document} "
                      f"л.{fragment.page} «{head}»")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
