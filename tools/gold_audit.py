# -*- coding: utf-8 -*-
"""Аудит эталонов золотого набора — карточка Ф1.2 (read-only).

Для каждого из 14 спорных вопросов досье Ф1.1 (класс 1 — листа нет в
графике, класс 2 — лист в графике, но вне топ-200 каналов) выводит в
markdown один блок из трёх частей:

1. формулировка вопроса (дословно, из questions.toml) с режимом и notes;
2. эталонный лист: документ, лист(ы), первые 400 символов текста из
   ``pages.text``; при отсутствии листа/документа в графике — пометка и
   короткий список похожих имён документов (difflib) для решения,
   не индексирована ли книга вовсе;
3. ближайший лист того же документа в векторном канале (пере-вычисляется
   скриптом, без опоры на временные артефакты): лист, косинус, первые
   400 символов текста; для отсутствующего документа — справочно
   ближайший лист любого документа (это не «блок», а строка-помощник).

В конце — сводка гипотезы систематического сдвига: разница Δ между
ожидаемым и ближайшим листом, агрегированная по документам; одинаковый
Δ по книге означает одну ошибку издания, а не индивидуальные промахи.

Скрипт ничего не правит: ``graph.db`` открывается только на КОПИИ в
TEMP (Store на копии), служебные запросы — отдельным read-only
соединением на ту же копию. Решения по каждому вопросу принимает
владелец, правка questions.toml — отдельным шагом.

Запуск: ``venv\\Scripts\\python.exe tools\\gold_audit.py [досье.json]``.
Опциональный аргумент — JSON зонда досье; без него берётся встроенный
список 14 id. Выход: stdout + ``reports/gold_audit_f1_2.md``.
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pdfgraph.embedding import (  # noqa: E402
    SEMANTIC_BASE_HIGH,
    SEMANTIC_BASE_LOW,
)
from pdfgraph.query import vector_search  # noqa: E402
from pdfgraph.store import Store  # noqa: E402

#: Спорные вопросы досье Ф1.1: класс 1 (3 шт.) + класс 2 (11 шт.) = 14.
AUDIT_IDS: tuple[str, ...] = (
    "e07", "e08", "e09",
    "e10", "e14", "e15", "e16", "p17", "p19",
    "p25", "p26", "p27", "p28", "p29",
)
#: Глубина векторной выдачи для поиска ближайшего листа документа.
VEC_DEPTH = 300
#: Сколько первых символов текста листа печатать в блоке.
TEXT_CLIP = 400
#: Порог похожести имени документа для списка похожих книг.
DOC_MATCH_CUTOFF = 0.3

STORE_PATH = REPO / "graph.db"
QUESTIONS_PATH = REPO / "tests" / "golden" / "questions.toml"
OUT_PATH = REPO / "reports" / "gold_audit_f1_2.md"
DOSIER_JSON = REPO / "reports" / "diag_semantic_deep_f1_1.json"


def load_metrics():
    """Загрузить tests/golden/metrics.py без пакета."""
    path = REPO / "tests" / "golden" / "metrics.py"
    spec = importlib.util.spec_from_file_location("golden_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["golden_metrics"] = module
    spec.loader.exec_module(module)
    return module


def audit_ids(argv: list[str]) -> list[str]:
    """Список id аудита: встроенный 14 или классы 1+2 из JSON досье."""
    if not argv:
        return list(AUDIT_IDS)
    dossier = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    ids = [row["id"] for row in dossier["rows"]
           if not any(row["expected_sheets_in_db"])
           or (row["lex200_rank"] == 0 and row["vec200_rank"] == 0)]
    return sorted(set(ids) | set(), key=lambda item: ids.index(item))
def _clip(text: str) -> str:
    """Первые 400 символов текста листа одной строкой (для владельца)."""
    flat = " ".join(text.split())
    if len(flat) <= TEXT_CLIP:
        return flat
    return flat[:TEXT_CLIP] + "…"


def _cos_of(score: float) -> float:
    """Косинус близости «запрос — чанк» из полосы скоров вектора."""
    return ((score - SEMANTIC_BASE_LOW)
            / (SEMANTIC_BASE_HIGH - SEMANTIC_BASE_LOW))


def _nearest_same_document(document: str, fragments) -> dict:
    """Ближайший лист ожидаемого документа в векторной выдаче (или пусто)."""
    for fragment in fragments:
        if fragment.document == document:
            return {"page": fragment.page, "cos": _cos_of(fragment.score)}
    return {}


def _nearest_any(fragments) -> dict:
    """Справочно: первый лист в векторной выдаче (любой документ)."""
    if not fragments:
        return {}
    fragment = fragments[0]
    return {"document": fragment.document,
            "page": fragment.page, "cos": _cos_of(fragment.score)}


def _similar_documents(name: str, documents: list[str]) -> list[str]:
    """Похожие имена документов БД (помощник для класса 1)."""
    return difflib.get_close_matches(name, documents, n=5,
                                     cutoff=DOC_MATCH_CUTOFF)


def collect(q, store, documents: list[str], vec_fragments) -> dict:
    """Собрать факты по одному вопросу (тексты, ближайшие листы)."""
    doc = q.expected_doc
    pages = list(q.expected_pages)
    doc_present = bool(doc) and doc in documents
    texts = [((store.page_text(doc, page) or "").strip() or None)
             if doc_present and page else None
             for page in pages]
    nearest = (_nearest_same_document(doc, vec_fragments)
               if doc_present else {})
    nearest_text = ((store.page_text(doc, nearest["page"]))
                    if doc_present and nearest else "")
    return {
        "qid": q.id, "mode": q.mode, "status": q.status,
        "question": q.question, "ata": q.ata,
        "aircraft_type": q.aircraft_type, "notes": q.notes,
        "expected_doc": doc, "expected_pages": pages,
        "doc_present": doc_present,
        "similar": (_similar_documents(doc, documents)
                    if doc and not doc_present else []),
        "texts": texts,
        "nearest": nearest,
        "nearest_text": nearest_text,
        "any_near": _nearest_any(vec_fragments),
    }
def render_block(info: dict) -> str:
    """Один markdown-блок одного вопроса (три части по карточке Ф1.2)."""
    lines = [
        f"## {info['qid']} — «{info['question']}»", "",
        f"- Режим/статус: {info['mode']} / {info['status']}; "
        f"ATA: {info['ata'] or '—'}; "
        f"aircraft_type: {info['aircraft_type']}",
    ]
    if info["notes"]:
        lines.append(f"- Notes эталона: {info['notes']}")
    lines.append(
        f"- Блок 2. Эталонный лист: {info['expected_doc'] or '—'}, "
        f"лист(ы) {info['expected_pages'] or '—'}")
    if not info["doc_present"]:
        similar = "; ".join(info["similar"]) or "похожих имён нет"
        lines.append(
            "- ЭТАЛОННЫЙ ДОКУМЕНТ ОТСУТСТВУЕТ в графике; "
            f"похожие имена: {similar}")
    for page, text_line in zip(info["expected_pages"], info["texts"]):
        if text_line is None:
            lines.append(
                f"  - л.{page}: ЛИСТ ОТСУТСТВУЕТ в графике (текст пуст)")
        else:
            lines.append(
                f"  - л.{page} (первые {TEXT_CLIP}): «{_clip(text_line)}»")
    nearest = info["nearest"]
    if nearest:
        lines.append(
            f"- Блок 3. Ближайший лист того же документа: "
            f"л.{nearest['page']}, cos {nearest['cos']:.4f} "
            f"(первые {TEXT_CLIP}): «{_clip(info['nearest_text'])}»")
    else:
        lines.append(
            "- Блок 3. Ближайший лист того же документа: — "
            "(документ не найден векторным каналом)")
    any_near = info["any_near"]
    if any_near and any_near["document"] != info["expected_doc"]:
        lines.append(
            "- Справка. Ближайший лист в векторе (другой документ): "
            f"{any_near['document']}, л.{any_near['page']}, "
            f"cos {any_near['cos']:.4f}")
    lines += ["- Решение владельца: ______", ""]
    return "\n".join(lines)
def shift_summary(findings: list[dict]) -> str:
    """Сводка гипотезы сдвига: Δ = ближайший − ожидаемый, по документам."""
    groups: dict[str, list[dict]] = {}
    for row in findings:
        if row["nearest"].get("page") is not None and row["expected_pages"]:
            page0 = row["expected_pages"][0]
            groups.setdefault(row["expected_doc"], []).append({
                "id": row["qid"],
                "expected": page0,
                "nearest": row["nearest"]["page"],
                "delta": row["nearest"]["page"] - page0,
                "cos": row["nearest"]["cos"],
            })
    lines = [
        "## Сводка гипотезы систематического сдвига", "",
        "Δ = лист ближайшего по косинусу листа − ожидаемый лист эталона. "
        "Одинаковый Δ у всех вопросов одной книги — одна ошибка издания "
        "(сдвиг печатной нумерации против физических страниц), а не набор "
        "индивидуальных промахов.", "",
    ]
    for document, rows in sorted(groups.items()):
        deltas = sorted({row["delta"] for row in rows})
        verdict = ("Δ ОДИН — похоже на системный сдвиг издания"
                   if len(deltas) == 1 else
                   "Δ разные (" + ", ".join(
                       f"{d:+d}" for d in deltas) + ") — "
                   "править индивидуально")
        lines.append(f"### {document}")
        lines += ["", "| id | ожидаемый | ближайший | Δ | cos |",
                  "|---|---|---|---|---|"]
        for row in sorted(rows, key=lambda item: item["id"]):
            lines.append(
                f"| {row['id']} | {row['expected']} | {row['nearest']} | "
                f"{row['delta']:+d} | {row['cos']:.4f} |")
        lines += ["", f"Вердикт: {verdict}", ""]
    return "\n".join(lines)
def main(argv: list[str] | None = None) -> int:
    """Прогнать аудит по 14 вопросам; записать reports/gold_audit_f1_2.md."""
    argv = list(sys.argv[1:] if argv is None else argv)
    metrics = load_metrics()
    qids = audit_ids(argv)
    questions = {q.id: q for q in metrics.load_questions(QUESTIONS_PATH)}
    missing = [qid for qid in qids if qid not in questions]
    if missing:
        print(f"error: нет в questions.toml: {missing}", file=sys.stderr)
        return 2
    findings: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="gold_audit_") as tmp:
        store_copy = Path(tmp) / "graph.db"
        metrics.copy_store(STORE_PATH, store_copy)
        store = Store(str(store_copy))
        try:
            ro_conn = sqlite3.connect(
                f"file:{store_copy.as_posix()}?mode=ro", uri=True)
            try:
                documents = [row[0] for row in ro_conn.execute(
                    "SELECT DISTINCT document FROM pages ORDER BY document")]
            finally:
                ro_conn.close()
            for qid in qids:
                question = questions[qid]
                vec = vector_search(store, question.question,
                                    limit=VEC_DEPTH)
                findings.append(
                    collect(question, store, documents, vec))
                print(f"{qid}: собрано", flush=True)
        finally:
            store.close()
    parts = [
        "# Аудит эталонов золотого набора — Ф1.2 (read-only)", "",
        "Дата: 31.10.2026. Вход: `tests/golden/questions.toml` (дословные "
        "формулировки и notes), копия `graph.db` в TEMP (Store на копии, "
        "рабочая БД не открывается). 14 спорных вопросов досье Ф1.1: "
        "класс 1 — лист отсутствует в графике (e07, e08, e09), класс 2 — "
        "лист в графике, но вне топ-200 обоих каналов (остальные 11). "
        "Текст листа напечатан первыми 400 символами одной строкой. "
        "Косинус — близость запроса и чанка после обратного перевода "
        "скоров векторного канала. Решения принимает владелец.", "",
    ]
    for row in findings:
        parts.append(render_block(row))
    parts.append(shift_summary(findings))
    text = "\n".join(parts)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"Блоков: {len(findings)}; отчёт: {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":  # скриптовый запуск без pytest
    raise SystemExit(main())
