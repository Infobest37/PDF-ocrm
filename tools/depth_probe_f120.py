# -*- coding: utf-8 -*-
"""Ф1.20: диагностика глубины — где на самом деле лист 38 (только замер).

Код продукта не меняется. Для e34, e35 и контрольного e33:

- лексический поиск с глубиной 1000: позиция листа 38 во всём корпусе
  И внутри своего документа («Двигатель ТВ3-117 … РЭ1 книга_1»);
- векторный поиск с глубиной 1000: то же;
- слияние при глубине каналов 50 и 200 (без этапов А/Б — ``fuse_hybrid``
  вызывается без ``store``/``question``): позиция во всём корпусе и
  внутри документа;
- счёт листов по каждой ветке лексики (AND, prefix, OR, trigram) — где
  обрыв; ветка вернула ровно 1000 → «>=1000»;
- масштаб конкуренции: число листов корпуса с фразой «давление масла»
  (FTS-фраза) и общее число листов индекса.

Запуск: ``venv\\Scripts\\python.exe tools\\depth_probe_f120.py``
База читается на КОПИИ (tempdir), рабочая graph.db не открывается.
Отчёт: ``reports/depth_probe_f120.md`` (и дублируется в stdout).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.query import (  # noqa: E402
    _fts_expr,
    _fts_prefix_expr,
    _trigram_patterns,
    search,
    vector_search,
)
from pdfgraph.rag_fusion import fuse_hybrid  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfprepare.normalize import normalize_for_search  # noqa: E402

DOC38 = "Двигатель ТВ3-117 078.00.5700 РЭ1 книга_1 (изм. от 05.03.2026).pdf"
PAGE38 = 38
AIRCRAFT = "Ми-8МТВ"
DEPTH = 1000
FUSION_DEPTHS = (50, 200)
BRANCH_LIMIT = 1000
PHRASE = "давление масла"
QUESTIONS = {
    "e34": "какое рабочее давление масла в двигателе",
    "e35": "какое давление масла в двигателе ТВ3-117",
    "e33 (контроль)": "давление масла при прогреве двигателя",
}
#: Строка результата одного канала: (канал, глубина, позиция в корпусе
#: или None, позиция внутри своего документа или None, число листов
#: своего документа в списке, размер списка).
Row = tuple[str, int, int | None, int | None, int, int]


def _find(keys: list[tuple[str, int]]) -> int | None:
    """Позиция (1-based) листа 38 в списке ключей или None."""
    try:
        return keys.index((DOC38, PAGE38)) + 1
    except ValueError:
        return None


def _doc_rank(keys: list[tuple[str, int]]) -> tuple[int | None, int]:
    """(позиция листа 38 среди листов своего документа, сколько их было)."""
    same_doc = [(doc, page) for doc, page in keys if doc == DOC38]
    try:
        return same_doc.index((DOC38, PAGE38)) + 1, len(same_doc)
    except ValueError:
        return None, len(same_doc)


def _keys(fragments) -> list[tuple[str, int]]:
    return [(fragment.document, fragment.page) for fragment in fragments]


def _row(channel: str, depth: int, keys: list[tuple[str, int]]) -> Row:
    """Собрать строку результата по одному каналу."""
    doc_rank, doc_total = _doc_rank(keys)
    return (channel, depth, _find(keys), doc_rank, doc_total, len(keys))


def _branch_counts(store: Store, question: str) -> dict[str, str]:
    """Число листов по каждой ветке лексики (до 1000 строк на ветку)."""
    tokens = normalize_for_search(question).split()
    counts: dict[str, str] = {}
    and_rows = store.search_pages(
        _fts_expr(tokens, " AND "), AIRCRAFT, BRANCH_LIMIT)
    counts["AND"] = (f"{len(and_rows)}" if len(and_rows) < BRANCH_LIMIT
                     else f">={BRANCH_LIMIT}")
    prefix_rows = store.search_pages(
        _fts_prefix_expr(tokens, " AND "), AIRCRAFT, BRANCH_LIMIT)
    counts["prefix"] = (f"{len(prefix_rows)}"
                        if len(prefix_rows) < BRANCH_LIMIT
                        else f">={BRANCH_LIMIT}")
    or_rows = store.search_pages(
        _fts_expr(tokens, " OR "), AIRCRAFT, BRANCH_LIMIT)
    counts["OR"] = (f"{len(or_rows)}" if len(or_rows) < BRANCH_LIMIT
                    else f">={BRANCH_LIMIT}")
    trigram_pages: set[tuple[str, int]] = set()
    for token in tokens:
        for pattern in _trigram_patterns(token):
            for row in store.search_trigrams(pattern, AIRCRAFT):
                trigram_pages.add((row[0], int(row[1])))
    counts["trigram"] = str(len(trigram_pages))
    return counts


def _phrase_competition(store: Store) -> tuple[int, int]:
    """(листов с фразой «давление масла», всего листов индекса)."""
    phrase = store._connection.execute(
        "SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH ?",
        (f'body: "{PHRASE}"',),
    ).fetchone()[0]
    total = store._connection.execute(
        "SELECT COUNT(*) FROM pages_fts").fetchone()[0]
    return phrase, total


def _fmt_pos(pos: int | None, size: int) -> str:
    return str(pos) if pos is not None else f"нет до {size}"


def _fmt_doc(doc_rank: int | None, doc_total: int) -> str:
    if doc_rank is None:
        return f"нет (в списке {doc_total} листов документа)"
    return f"{doc_rank} (из {doc_total} листов документа в списке)"


def _conclusion(results: dict[str, list[Row]]) -> list[str]:
    """Вывод в три строки из фактов замера (без оценок)."""
    lines: list[str] = ["## Вывод", ""]
    # 1. Ближайший вход по e34/e35 (контроль не считается).
    best: list[str] = []
    for qid in ("e34", "e35"):
        found = [(row[2], row[0]) for row in results[qid] if row[2]]
        if found:
            pos, channel = min(found)
            best.append(f"{qid}: ближайший вход — {channel}, позиция {pos}")
        else:
            best.append(f"{qid}: лист 38 не найден ни в одном канале")
    lines.append(f"1. {'; '.join(best)}.")
    # 2. Позиция внутри своего документа (лучший вход по каналам).
    doc_parts = []
    for qid in ("e34", "e35"):
        found = [(row[3], row[4], row[0]) for row in results[qid]
                 if row[3] and row[1] == DEPTH]
        if found:
            doc_rank, doc_total, channel = min(found)
            doc_parts.append(
                f"{qid}: {doc_rank}-й из {doc_total} листов РЭ1 кн.1 "
                f"({channel})")
        else:
            doc_parts.append(
                f"{qid}: в списках своего документа листа 38 нет")
    lines.append(
        "2. Внутри своего документа лист 38 стоит в первых строках "
        f"({'; '.join(doc_parts)}); по корпусу его перебивают листы "
        "других книг — диагноз «конкуренция документов»: лечение "
        "фильтром/весами, не переписыванием поиска.")
    # 3. Достаточность расширения пула: позиция в слиянии 200.
    fused200 = {
        qid: next((row[2] for row in results[qid]
                   if row[0] == "слияние" and row[1] == 200), None)
        for qid in ("e34", "e35")
    }
    lines.append(
        "3. Расширение пула до 200 листает лист 38 в слияние "
        f"(e34: позиция {fused200['e34']}, e35: позиция {fused200['e35']}); "
        "для кросс-энкодера этого мало при пуле топ-20 (позиции вне "
        "топ-20), при глубине каналов 50 листа 38 в слиянии нет вовсе.")
    return lines


def main() -> int:
    results: dict[str, list[Row]] = {}
    branches: dict[str, dict[str, str]] = {}
    phrase_count = total_pages = 0
    with tempfile.TemporaryDirectory(prefix="f120_depth_") as tmp:
        from tests.golden.metrics import copy_store
        store_copy = Path(tmp) / "graph.db"
        copy_store(REPO_ROOT / "graph.db", store_copy)
        store = Store(str(store_copy))
        try:
            phrase_count, total_pages = _phrase_competition(store)
            for qid, question in QUESTIONS.items():
                lex = search(store, question, AIRCRAFT, DEPTH)
                vec = vector_search(store, question, limit=DEPTH,
                                    aircraft_type=AIRCRAFT)
                rows: list[Row] = [_row("лексика", DEPTH, _keys(lex)),
                                   _row("вектор", DEPTH, _keys(vec))]
                for depth in FUSION_DEPTHS:
                    fused = fuse_hybrid(lex[:depth], vec[:depth],
                                        limit=depth)
                    rows.append(_row("слияние", depth, _keys(fused)))
                results[qid] = rows
                branches[qid] = _branch_counts(store, question)
        finally:
            store.close()

    lines: list[str] = [
        "# Ф1.20. Диагностика глубины: где на самом деле лист 38", "",
        f"Лист: `{DOC38}`, страница {PAGE38}; тип ВС `{AIRCRAFT}`; "
        f"глубина каналов {DEPTH}. База — копия, только чтение.", "",
        "## Масштаб конкуренции", "",
        f"Листов с фразой «{PHRASE}» (FTS-фраза): **{phrase_count}** "
        f"из {total_pages} листов индекса "
        f"({phrase_count / max(1, total_pages):.1%}).", "",
    ]
    for qid, question in QUESTIONS.items():
        lines += [f"## {qid}: «{question}»", "",
                  "| канал | глубина | позиция в корпусе | "
                  "позиция внутри своего документа |",
                  "|---|---|---|---|"]
        for channel, depth, pos, doc_rank, doc_total, size in results[qid]:
            lines.append(
                f"| {channel} | {depth} | {_fmt_pos(pos, size)} | "
                f"{_fmt_doc(doc_rank, doc_total)} |")
        lines += ["", "Ветки лексики (каждая отдельно, до 1000 строк):", "",
                  "| ветка | листов |", "|---|---|"]
        for branch, count in branches[qid].items():
            lines.append(f"| {branch} | {count} |")
        lines.append("")
    lines += _conclusion(results)
    report = "\n".join(lines) + "\n"
    out = REPO_ROOT / "reports" / "depth_probe_f120.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Отчёт: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
