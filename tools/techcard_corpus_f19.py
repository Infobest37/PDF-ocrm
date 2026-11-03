# -*- coding: utf-8 -*-
"""Ф1.9д: полигон карт из Регламента ТО (только чтение, код поиска не тронут).

Пройти по листам документов-регламентов, вытащить все номера карт
``NNN.NN.NN + буква`` (пробелы/точки не значимы, двойники суффикса
приравнены к латинице), для каждого номера вызвать :func:`pdfgraph.query.search`
(не build_answer — гейты не проверяются) и определить судьбу карты:

- ``manual``    — карта есть в руководстве (РТЭ/книга двигателя и т. п.);
- ``reglament`` — листы найдены, но только в регламенте (периодичность);
- ``missing``   — search() не вернул ни одного листа.

Ложное срабатывание — лист в выдаче, на котором этого номера нет
(проверка вхождения: дословно, слипше, нормализация, двойники суффикса —
та же, что в гейте designators, :func:`pdfgraph.gates._designator_in_text`).

Отчёт: ``reports/techcard_corpus_f19.md`` + ``reports/techcard_corpus_f19.csv``.

Запуск из корня репозитория::

    venv\\Scripts\\python.exe -X utf8 tools\\techcard_corpus_f19.py [store]
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.gates import _designator_in_text  # noqa: E402
from pdfgraph.query import document_role, search  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfprepare.normalize import (  # noqa: E402
    normalize_for_search,
)

#: Номер карты в нормализованном тексте: NNN.NN.NN + одна буква-суффикс.
_CARD_RE = re.compile(
    r"(?<![\da-zа-яё.])(\d{3})[.\s]*(\d{2})[.\s]*(\d{2})[.\s]*"
    r"([a-zа-яё])(?![a-zа-яё])")
#: Двойники суффикса — копия таблицы Ф1.9в (pdfgraph.gates._SUFFIX_HOMOGLYPHS);
#: локальная копия, чтобы скрипт-замер не зависел от приватного имени.
_SUFFIX_HOMOGLYPHS = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
                      "х": "x", "у": "y", "к": "k", "м": "m", "т": "t",
                      "в": "b", "н": "h"}
#: Судьбы карты в отчёте.
STATUS_MANUAL = "manual"
STATUS_REGLAMENT = "reglament"
STATUS_MISSING = "missing"


def canonical_number(match: re.Match) -> str:
    """«035.10.00e» из групп regex; кириллический суффикс → латиница."""
    suffix = match.group(4).lower()
    suffix = _SUFFIX_HOMOGLYPHS.get(suffix, suffix)
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}{suffix}"


def collect_reglament_cards(store: Store) -> dict[str, list[tuple[str, int]]]:
    """Номера карт с листов документов-регламентов: {номер: [(док, лист)]}."""
    sql = (
        "SELECT document, page, text FROM pages "
        "WHERE document LIKE '%егламент%'"
    )
    sql += store._dup_filter()
    sql += store._garbage_filter()
    sql += " ORDER BY document, page"
    cards: dict[str, list[tuple[str, int]]] = {}
    for document, page, text in store._connection.execute(sql):
        norm = normalize_for_search(text or "")
        for match in _CARD_RE.finditer(norm):
            cards.setdefault(canonical_number(match), []).append(
                (document, page))
    return cards


def classify(store: Store, number: str) -> tuple[str, list[dict], list[dict]]:
    """Судьба карты: (статус, найденные листы, ложные листы)."""
    fragments = search(store, number)
    rows: list[dict] = []
    false_rows: list[dict] = []
    for fragment in fragments:
        text = store.page_text(fragment.document, fragment.page)
        if not _designator_in_text(text, number):
            false_rows.append({
                "document": fragment.document, "page": fragment.page,
                "role": fragment.role, "kind": fragment.kind,
            })
            continue
        rows.append({
            "document": fragment.document, "page": fragment.page,
            "role": document_role(store, fragment.document),
            "kind": fragment.kind,
        })
    if not fragments:
        status = STATUS_MISSING
    elif any(row["role"] != "reglament" for row in rows):
        status = STATUS_MANUAL
    else:
        status = STATUS_REGLAMENT
    return status, rows, false_rows


def sheet_label(document: str, page: int) -> str:
    """«короткое имя документа л.N» для отчёта."""
    return f"{Path(document).name} л.{page}"


def main() -> int:
    store_path = sys.argv[1] if len(sys.argv) > 1 else str(
        REPO_ROOT / "graph.db")
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    store = Store(store_path)
    try:
        cards = collect_reglament_cards(store)
        results: list[dict] = []
        for number in sorted(cards):
            status, rows, false_rows = classify(store, number)
            results.append({
                "number": number,
                "status": status,
                "reglament_sheets": cards[number],
                "found": rows,
                "false_hits": false_rows,
            })
    finally:
        store.close()

    total = len(results)
    manuals = sum(1 for r in results if r["status"] == STATUS_MANUAL)
    only_reglament = sum(1 for r in results if r["status"] == STATUS_REGLAMENT)
    missing = [r for r in results if r["status"] == STATUS_MISSING]
    false_cards = [r for r in results if r["false_hits"]]

    csv_path = reports_dir / "techcard_corpus_f19.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["номер", "судьба",
                         "найдена (документ, лист, роль)",
                         "листы в регламенте",
                         "ложные листы (номера на листе нет)"])
        for result in results:
            writer.writerow([
                result["number"], result["status"],
                "; ".join(f"{sheet_label(r['document'], r['page'])} "
                          f"({r['role']})" for r in result["found"]),
                "; ".join(sheet_label(doc, page)
                          for doc, page in result["reglament_sheets"]),
                "; ".join(f"{sheet_label(r['document'], r['page'])} "
                          f"({r['kind']})" for r in result["false_hits"]),
            ])

    lines = [
        "# Ф1.9д: полигон карт из Регламента ТО",
        "",
        "Источник номеров: листы документов с «Регламент» в имени; ",
        "поиск — `pdfgraph.query.search` (без гейтов); ложный — лист в ",
        "выдаче, на котором номера нет. База: `" + store_path + "`.",
        "",
        "## Сводка",
        "",
        f"- всего карт в Регламенте: **{total}**",
        f"- найдено в руководствах: **{manuals}**",
        f"- только в регламенте: **{only_reglament}**",
        f"- не найдено нигде: **{len(missing)}**",
        f"- карт с ложными листами в выдаче: **{len(false_cards)}**",
        "",
        "## Не найдено нигде (проверить глазами: карта есть в базе",
        "или регламент-лист указывает на номер вне корпуса)",
        "",
    ]
    if missing:
        for result in missing:
            places = "; ".join(sheet_label(doc, page)
                               for doc, page in result["reglament_sheets"])
            lines.append(f"- `{result['number']}` — упомянут в регламенте: "
                         f"{places}")
    else:
        lines.append("- (нет)")
    lines += ["", "## Только в регламенте", ""]
    only_rows = [r for r in results if r["status"] == STATUS_REGLAMENT]
    if only_rows:
        for result in only_rows:
            places = "; ".join(
                f"{sheet_label(r['document'], r['page'])} ({r['role']})"
                for r in result["found"])
            lines.append(f"- `{result['number']}` — {places}")
    else:
        lines.append("- (нет)")
    lines += ["", "## Ложные срабатывания", ""]
    if false_cards:
        for result in false_cards:
            bad = "; ".join(
                f"{sheet_label(r['document'], r['page'])} ({r['kind']})"
                for r in result["false_hits"])
            lines.append(f"- `{result['number']}` — листы без номера: {bad}")
    else:
        lines.append("- (нет)")
    lines += ["", "Полная таблица: `reports/techcard_corpus_f19.csv`.", ""]
    report_path = reports_dir / "techcard_corpus_f19.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"карт в регламенте: {total}; в руководствах: {manuals}; "
          f"только в регламенте: {only_reglament}; не найдено: {len(missing)}")
    print(f"карт с ложными листами: {len(false_cards)}")
    print(f"отчёт: {report_path}")
    print(f"csv: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
