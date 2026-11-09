# -*- coding: utf-8 -*-
"""Ф1.21: диагностическая матрица по всем ready-вопросам golden set —
где теряется эталон (только замер, код продукта не меняется).

Для каждого вопросного вопроса (31 шт. с эталоном; 6 внекорпусных —
отдельным списком):

- позиция эталона в трёх каналах: лексика (1000), вектор (1000),
  слияние (200; каналы усечены до 200, этапы А/Б выключены —
  ``fuse_hybrid`` вызывается без ``store``/``question``);
- книга-лидер слияния (``fused[0].document``), сколько её листов
  в топ-10, совпадает ли с книгой эталона, сколько разных книг в топ-10;
- есть ли в вопросе десигнатор изделия (``question_designators`` без
  ATA-кодов и чистых номеров — потолок документного буста);
- группа (ровно одна): в топ-5 / в топ-20 но не в топ-5 / вне топ-20
  лидер чужая книга / вне топ-20 лидер своя книга.

Запуск: ``venv\\Scripts\\python.exe tools\\diag_matrix_f121.py``
База — копия; отчёт: ``reports/diag_matrix_f121.md``.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.gates import question_designators  # noqa: E402
from pdfgraph.query import search, vector_search  # noqa: E402
from pdfgraph.rag_fusion import fuse_hybrid  # noqa: E402
from pdfprepare.normalize import normalize_for_search  # noqa: E402  # noqa: F401

AIRCRAFT = "Ми-8МТВ"
DEPTH = 1000
FUSION_DEPTH = 200
TOP = 20
#: ATA-коды («065.50», «118.00», «072.00.00») и чистые номера — не изделия:
#: документный буст по ним бессмыслен (в именах файлов их нет).
_NOT_EQUIPMENT_RE = re.compile(r"\d+(?:\.\d+)*")

G_OK = "в топ-5"
G_POOL = "в топ-20, не в топ-5"
G_FOREIGN = "вне топ-20, лидер чужая книга"
G_OWN = "вне топ-20, лидер своя книга"


def _find(keys: list[tuple[str, int]], expected_doc: str,
          expected_pages: tuple[int, ...]) -> int | None:
    """Минимальная позиция (1-based) эталонного листа или None."""
    best = None
    for index, (document, page) in enumerate(keys, start=1):
        if document == expected_doc and page in expected_pages:
            best = index if best is None else min(best, index)
    return best


def _designators(question: str) -> list[str]:
    """Десигнаторы изделия вопроса (без ATA-кодов и чистых номеров)."""
    return [designator for designator in question_designators(question)
            if not _NOT_EQUIPMENT_RE.fullmatch(designator)]


def _fmt(pos: int | None, size: int) -> str:
    return str(pos) if pos is not None else f"нет до {size}"


def _group(pos_fused: int | None, leader_is_expected: bool) -> str:
    if pos_fused is not None and pos_fused <= 5:
        return G_OK
    if pos_fused is not None and pos_fused <= TOP:
        return G_POOL
    return G_OWN if leader_is_expected else G_FOREIGN


def main() -> int:
    from tests.golden.metrics import copy_store, load_questions

    questions = [question for question in load_questions()
                 if question.in_metrics and question.expected_pages]
    ooc = [question for question in load_questions()
           if question.in_metrics and not question.expected_pages]

    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="f121_matrix_") as tmp:
        store_copy = Path(tmp) / "graph.db"
        copy_store(REPO_ROOT / "graph.db", store_copy)
        from pdfgraph.store import Store
        store = Store(str(store_copy))
        try:
            for question in questions:
                lex = search(store, question.question, AIRCRAFT, DEPTH)
                vec = vector_search(store, question.question, limit=DEPTH,
                                    aircraft_type=AIRCRAFT)
                fused = fuse_hybrid(lex[:FUSION_DEPTH], vec[:FUSION_DEPTH],
                                    limit=FUSION_DEPTH)
                # Прод-конфигурация слияния (answer.py, без rerank):
                # каналы глубиной 50 + слагаемое специфичности Ф1.5 —
                # для колонки «прод», чтобы видеть, где теряет прод.
                from pdfgraph.rag_fusion import make_specificity_fn
                fused_prod = fuse_hybrid(
                    lex[:50], vec[:50], limit=50,
                    spec_fn=make_specificity_fn(store, question.question),
                    store=store, question=question.question)
                lex_keys = [(f.document, f.page) for f in lex]
                vec_keys = [(f.document, f.page) for f in vec]
                fused_keys = [(f.document, f.page) for f in fused]
                prod_keys = [(f.document, f.page) for f in fused_prod]
                pos_lex = _find(lex_keys, question.expected_doc,
                                question.expected_pages)
                pos_vec = _find(vec_keys, question.expected_doc,
                                question.expected_pages)
                pos_fused = _find(fused_keys, question.expected_doc,
                                  question.expected_pages)
                leader_doc = fused_keys[0][0] if fused_keys else ""
                leader_count10 = (sum(1 for document, _ in fused_keys[:10]
                                      if document == leader_doc)
                                  if leader_doc else 0)
                rows.append({
                    "id": question.id,
                    "question": question.question,
                    "expected_doc": question.expected_doc,
                    "pos_lex": pos_lex, "pos_vec": pos_vec,
                    "pos_fused": pos_fused,
                    "pos_prod": _find(prod_keys, question.expected_doc,
                                      question.expected_pages),
                    "leader_doc": leader_doc,
                    "leader_count10": leader_count10,
                    "leader_is_expected": leader_doc == question.expected_doc,
                    "books10": len({document for document, _
                                    in fused_keys[:10]}),
                    "designators": _designators(question.question),
                    "group": _group(pos_fused,
                                    leader_doc == question.expected_doc),
                    "anomaly": (pos_fused is not None and pos_fused <= TOP
                                and pos_lex is None and pos_vec is None),
                })
        finally:
            store.close()

    lines: list[str] = [
        "# Ф1.21. Диагностическая матрица: где теряется эталон", "",
        f"Вопросов с эталоном: {len(questions)}; внекорпусных (без эталона, "
        f"вне матрицы): {len(ooc)} — "
        + ", ".join(question.id for question in ooc) + ".",
        f"Каналы: лексика/вектор глубина {DEPTH}; слияние {FUSION_DEPTH} "
        "(каналы усечены до 200, этапы А/Б выключены) — группы считаются "
        "по нему; колонка «прод» — прод-конфигурация слияния (каналы 50 + "
        "слагаемое специфичности Ф1.5, как в answer.py без rerank). "
        "«Книга» = имя PDF-файла.", "",
        "## Матрица", "",
        "| id | дес. изделия | лексика 1000 | вектор 1000 | слияние 200 | "
        "прод 50+спец | книга-лидер | листов лидера в топ-10 | лидер = "
        "эталон | книг в топ-10 | группа |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        note = " (!аномалия)" if row["anomaly"] else ""
        lines.append(
            f"| {row['id']} | {', '.join(row['designators']) or '—'} | "
            f"{_fmt(row['pos_lex'], DEPTH)} | {_fmt(row['pos_vec'], DEPTH)} | "
            f"{_fmt(row['pos_fused'], FUSION_DEPTH)} | "
            f"{_fmt(row['pos_prod'], 50)} | "
            f"{row['leader_doc'][:32]} | {row['leader_count10']} | "
            f"{'да' if row['leader_is_expected'] else 'НЕТ'} | "
            f"{row['books10']} | {row['group']}{note} |")

    lines += ["", "## Группы", ""]
    for group in (G_OK, G_POOL, G_FOREIGN, G_OWN):
        members = [row for row in rows if row["group"] == group]
        ids = ", ".join(row["id"] for row in members)
        lines += [f"### {group} — {len(members)} шт.", "",
                  f"ids: {ids or '—'}", ""]
        if members:
            example = members[0]
            lines += [
                f"Пример **{example['id']}** («{example['question']}»): "
                f"лексика {_fmt(example['pos_lex'], DEPTH)}, вектор "
                f"{_fmt(example['pos_vec'], DEPTH)}, слияние "
                f"{_fmt(example['pos_fused'], FUSION_DEPTH)}; лидер — "
                f"{example['leader_doc'][:48]} "
                f"({example['leader_count10']} листов в топ-10, "
                f"книг в топ-10: {example['books10']}).", ""]
        if group == G_POOL:
            anomalies = [row for row in members if row["anomaly"]]
            lines += [
                f"Аномалии (эталона нет в лексике и векторе на 1000, но он "
                f"в слиянии — признак ошибки определения позиции): "
                f"{', '.join(row['id'] for row in anomalies) or 'нет'}.", ""]

    foreign = [row for row in rows if row["group"] == G_FOREIGN]
    foreign_with_des = [row for row in foreign if row["designators"]]
    pool = [row for row in rows if row["group"] == G_POOL]
    own = [row for row in rows if row["group"] == G_OWN]
    hopeless = [row for row in rows
                if row["group"] in (G_FOREIGN, G_OWN)
                and row["pos_lex"] is None and row["pos_vec"] is None]
    lines += [
        "## Итог (что лечится чем)", "",
        f"1. Документный буст (чужая книга-лидер): {len(foreign)} промахов; "
        f"из них с десигнатором изделия в вопросе — "
        f"{len(foreign_with_des)} "
        f"({', '.join(row['id'] for row in foreign_with_des) or '—'}) — "
        "это потолок буста.", "",
        f"2. Чанки/переранжирование пула (в топ-20, не в топ-5): "
        f"{len(pool)} ({', '.join(row['id'] for row in pool) or '—'}).", "",
        f"3. Не лечится ни бустом, ни чанками: {len(own)} (лидер — своя "
        f"книга) + {len(hopeless)} (эталона нет в обоих каналах до 1000): "
        f"{', '.join(row['id'] for row in own + hopeless) or '—'}.", ""]
    report = "\n".join(lines) + "\n"
    out = REPO_ROOT / "reports" / "diag_matrix_f121.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Отчёт: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
