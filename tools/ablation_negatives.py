# -*- coding: utf-8 -*-
"""Ф1.3а: абляционные негативы — полигон для калибровки отказа.

Роль: для каждого готового вопроса с известным листом скрыть из выдачи
все листы, содержащие эталонный текст, и задать тот же вопрос снова.
Скрытие — пометкой ``needs_rescan = 1`` (причина ``auto: ablation Ф1.3а``)
на КОПИИ ``graph.db`` в темп-каталоге; рабочая БД открывается только на
чтение. После пометки лист исключается тем же фильтром карточки 0.2,
что и мусорные листы (``Store._garbage_filter``), во всех ветках поиска:
лексической (``Store.search_pages``) и векторной
(``vector_search`` читает ``Store.searchable_pages``).

Эталонный текст ищем дословной подстрокой по всему корпусу, а не только
по эталонным листам: между изданиями текст дублируется, иначе негатив
окажется ложным. Якорь — середина текста эталонного листа
(:data:`DEFAULT_ANCHOR_CHARS` символов), без нормализации.

Жёсткий негатив — после абляции система не отдаёт верифицированный
ответ (отказ или неподтверждённые цитаты). Ложный негатив — ответ всё
же получен (цитаты с листа вне эталонных). Порогов отказа здесь нет:
скрипт только собирает факты для ``reports/refusal_signals.md``.

Отчёты: ``reports/ablation_negatives.md`` (человекочитаемый) и
``reports/ablation_negatives.json`` (машинный, для refusal_signals.py).

Запуск: ``venv\\Scripts\\python.exe tools\\ablation_negatives.py``.
Продакшн-код не правится.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.answer import build_answer  # noqa: E402
from pdfgraph.query import effective_search_mode  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfgraph.verify import all_verified, verify_citations  # noqa: E402
from tests.golden.metrics import copy_store, load_questions  # noqa: E402

#: Длина якоря в символах исходного текста листа.
DEFAULT_ANCHOR_CHARS = 300
#: Минимальная длина текста листа, с которой якорь считается надёжным.
MIN_ANCHOR_SOURCE = 40
#: Причина, которой помечается скрытый лист (префикс auto: — важно,
#: не «manual», такие пометки переживают только ручной CLI).
ABLATION_REASON = "auto: ablation Ф1.3а"
DEFAULT_STORE = REPO_ROOT / "graph.db"
DEFAULT_QUESTIONS = REPO_ROOT / "tests" / "golden" / "questions.toml"
DEFAULT_JSON = REPO_ROOT / "reports" / "ablation_negatives.json"
DEFAULT_MD = REPO_ROOT / "reports" / "ablation_negatives.md"
#: Сколько скрытых листов печатать в отчёте по вопросу.
HIDDEN_PREVIEW = 40

def _escape_like(anchor: str) -> str:
    """Экранировать спецсимволы LIKE (\\, %, _)."""
    return (anchor.replace("\\", "\\\\")
                  .replace("%", "\\%")
                  .replace("_", "\\_"))


def anchor_for(page_text: str, anchor_chars: int) -> str:
    """Дословный срез середины текста листа, весь текст если короткий.

    Середина выбрана, чтобы не цеплять титульные и колон­точные фразы,
    которые дублируются по всему корпусу. Нормализации нет — якорь
    проверяется подстрокой по исходному хранимому тексту.
    """
    text = page_text.strip()
    if not text:
        return ""
    if len(text) <= anchor_chars:
        return text
    start = (len(text) - anchor_chars) // 2
    return text[start:start + anchor_chars]


def sheets_with_anchor(connection: sqlite3.Connection,
                       anchor: str) -> list[tuple[str, int]]:
    """Все листы корпуса, содержащие якорь дословной подстрокой.

    Ищется по всей таблице ``pages``, включая листы документов-дублей
    и мусорные: они и так скрыты из выдачи, но для отчёта нужен полный
    счётчик. Регистрозависимый случай не различаем — якорь берётся из
    того же текста и совпадает байт-в-байт.
    """
    rows = connection.execute(
        "SELECT document, page FROM pages "
        "WHERE text LIKE '%' || ? || '%' ESCAPE '\\'",
        (_escape_like(anchor),),
    ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def flag_state(connection: sqlite3.Connection,
               sheets: list[tuple[str, int]]) -> dict[
                   tuple[str, int], tuple[int, str]]:
    """Сохранить (needs_rescan, reason) листов для точного отката."""
    result: dict[tuple[str, int], tuple[int, str]] = {}
    for document, page in sheets:
        row = connection.execute(
            "SELECT needs_rescan, needs_rescan_reason FROM pages "
            "WHERE document = ? AND page = ?", (document, page),
        ).fetchone()
        result[(document, page)] = (
            (int(row[0]), str(row[1])) if row is not None else (0, ""))
    return result


def hide_sheets(connection: sqlite3.Connection,
                sheets: list[tuple[str, int]],
                state: dict[tuple[str, int], tuple[int, str]]) -> int:
    """Пометить листы needs_rescan=1; вернуть число реально скрытых.

    Уже скрытые (needs_rescan = 1) не считаем и не перезаписываем —
    их причина остаётся нетронутой.
    """
    hidden = 0
    for (document, page), (was_rescan, _reason) in state.items():
        if was_rescan >= 1:
            continue
        with connection:
            connection.execute(
                "UPDATE pages SET needs_rescan = 1, "
                "needs_rescan_reason = ? "
                "WHERE document = ? AND page = ?",
                (ABLATION_REASON, document, page),
            )
        hidden += 1
    return hidden


def restore_sheets(connection: sqlite3.Connection,
                   state: dict[tuple[str, int], tuple[int, str]]) -> None:
    """Точный откат прежних значений needs_rescan/reason."""
    with connection:
        for (document, page), (was_rescan, reason) in state.items():
            connection.execute(
                "UPDATE pages SET needs_rescan = ?, "
                "needs_rescan_reason = ? "
                "WHERE document = ? AND page = ?",
                (was_rescan, reason, document, page),
            )

def _searchable_sheets(store: Store,
                       sheets: list[tuple[str, int]]) -> set[tuple[str, int]]:
    """Пересечение скрытых листов с досягаемыми для выдачи."""
    allowed = set(store.searchable_pages())
    return set(sheets) & allowed


def ask_again(store: Store, question: str, aircraft_type: str
              ) -> dict[str, object]:
    """Задать вопрос после абляции: refusal, верификация, лидер выдачи."""
    answer = build_answer(store, question, aircraft_type or None)
    checks = verify_citations(answer, store)
    ok_citations = sum(1 for check in checks if check.ok)
    leader = None
    if answer.fragments:
        first = answer.fragments[0]
        leader = {"document": first.document, "page": first.page,
                  "kind": first.kind, "score": round(first.score, 4)}
    verified = all_verified(checks) and not answer.refusal
    found = bool(answer.claims) and verified
    return {
        "refusal": bool(answer.refusal),
        "refusal_reason": str(answer.reason or "") if answer.refusal else "",
        "ok_citations": ok_citations,
        "total_citations": len(checks),
        "verified": verified,
        "found": found,
        "leader": leader,
        "top5": [
            {"document": fragment.document, "page": fragment.page,
             "kind": fragment.kind, "score": round(fragment.score, 4)}
            for fragment in answer.fragments[:5]
        ],
    }


def prepare_questions(questions_path: Path
                      ) -> list[tuple[str, str, str, str, tuple[int, ...]]]:
    """Готовые вопросы с известным листом: (id, вопрос, тип, документ, листы)."""
    with open(questions_path, "rb") as handle:
        data = tomllib.load(handle)
    prepared: list[tuple[str, str, str, str, tuple[int, ...]]] = []
    for item in data["question"]:
        if item["status"] != "ready":
            continue
        if not item["expected_doc"] or not item["expected_pages"]:
            continue
        prepared.append((
            str(item["id"]), str(item["question"]),
            str(item["aircraft_type"]), str(item["expected_doc"]),
            tuple(int(page) for page in item["expected_pages"]),
        ))
    return prepared

def run_ablation(source_store: Path, questions_path: Path,
                 anchor_chars: int) -> tuple[list[dict], dict]:
    """Полный абляционный прогон на копии БД; вернуть (результаты, мета)."""
    prepared = prepare_questions(questions_path)
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="ablation_f1_3a_") as tmp:
        store_path = Path(tmp) / "graph.db"
        copy_store(source_store, store_path)
        store = Store(str(store_path))
        try:
            connection = store._connection
            for qid, question, aircraft_type, expected_doc, pages in prepared:
                entry: dict[str, object] = {
                    "id": qid, "question": question,
                    "aircraft_type": aircraft_type,
                    "expected_doc": expected_doc,
                    "expected_pages": list(pages),
                    "anchors": [], "hidden": [],
                    "hidden_count": 0, "hidden_searchable_count": 0,
                    "skipped": "", "after": None, "found": None,
                    "hard_negative": None,
                }
                anchors: list[tuple[int, str]] = []
                for page in pages:
                    text = store.page_text(expected_doc, page)
                    if not text.strip():
                        entry["skipped"] = (
                            f"нет текста листа {expected_doc} л.{page}")
                        continue
                    anchor = anchor_for(text, anchor_chars)
                    if not anchor:
                        entry["skipped"] = (
                            f"пустой якорь листа {expected_doc} л.{page}")
                        continue
                    anchors.append((page, anchor))
                if not anchors and not entry["skipped"]:
                    entry["skipped"] = "не построен ни один якорь"
                if entry["skipped"]:
                    results.append(entry)
                    print(f"{qid}: пропущен ({entry['skipped']})")
                    continue
                entry["anchors"] = [
                    {"page": page, "len": len(anchor),
                     "head": anchor[:60].replace("\n", " ") + "…"}
                    for page, anchor in anchors
                ]
                touched: list[tuple[str, int]] = []
                state: dict[tuple[str, int], tuple[int, str]] = {}
                try:
                    for page, anchor in anchors:
                        sheets = sheets_with_anchor(connection, anchor)
                        for document, sheet in sheets:
                            if (document, sheet) not in state:
                                touched.append((document, sheet))
                        state.update(flag_state(connection, sheets))
                    entry["hidden"] = [
                        {"document": document, "page": page}
                        for document, page in touched]
                    entry["hidden_count"] = len(touched)
                    entry["hidden_searchable_count"] = len(
                        _searchable_sheets(store, touched))
                    hide_sheets(connection, touched, state)
                    entry["after"] = ask_again(store, question,
                                               aircraft_type)
                    found = bool(entry["after"]["found"])
                    entry["found"] = found
                    entry["hard_negative"] = not found
                finally:
                    restore_sheets(connection, state)
                after = entry["after"] or {}
                verdict = ("жёсткий" if entry["hard_negative"]
                           else "ложный")
                print(f"{qid}: скрыто {entry['hidden_count']}, "
                      f"лидер {after.get('leader')}, "
                      f"вердикт {verdict}")
                results.append(entry)
        finally:
            store.close()
    selected, skipped = select_hard_negatives(results, 10)
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "source_store": str(source_store.resolve()),
        "questions_file": str(questions_path),
        "search_mode": effective_search_mode(),
        "anchor_chars": anchor_chars,
        "ablation_reason": ABLATION_REASON,
        "total_questions": len(prepared),
        "hard_negatives": sum(1 for row in results
                              if row["hard_negative"]),
        "false_negatives": sum(1 for row in results
                               if row["found"]),
        "selected_for_signals": selected,
        "selected_note": (
            "первые 10 жёстких негативов по id — детерминированный "
            "механический выбор без подгонки; использует "
            "refusal_signals.py"),
        "skipped_selection": skipped,
    }
    return results, meta


def select_hard_negatives(results: list[dict], limit: int
                          ) -> tuple[list[str], str]:
    """Первые ``limit`` жёстких негативов по id (механический выбор)."""
    hard = sorted((row for row in results if row["hard_negative"]),
                  key=lambda row: str(row["id"]))
    selected = [str(row["id"]) for row in hard[:limit]]
    if len(hard) >= limit:
        skipped = f"жёстких всего {len(hard)}; выбрано {len(selected)}"
    else:
        skipped = (f"жёстких всего {len(hard)} — ДЕФИЦИТ к требуемым "
                   f"{limit}")
    return selected, skipped

def _clip(text: str, width: int) -> str:
    """Обрезать строку под колонку таблицы."""
    single = " ".join(str(text).split())
    return single if len(single) <= width else single[: width - 1] + "…"


def _fmt_leader(after: dict | None) -> str:
    """Лидер выдачи для таблицы отчёта."""
    if not after:
        return "-"
    leader = after.get("leader")
    if not leader:
        return "(выдача пустая)"
    return (f"{_clip(leader['document'], 34)} л.{leader['page']} "
            f"({leader['kind']}, {leader['score']})")


def write_json(path: Path, results: list[dict], meta: dict) -> None:
    """Записать машинный отчёт (UTF-8, ensure_ascii=False)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"meta": meta, "results": results}, handle,
                  ensure_ascii=False, indent=2)


def write_md(path: Path, results: list[dict], meta: dict) -> None:
    """Записать человекочитаемый отчёт по шаблону карточки."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Ф1.3а: абляционные негативы")
    lines.append("")
    lines.append(f"Дата: {meta['generated_at']}. База: копия "
                 f"`{meta['source_store']}`. Режим поиска: "
                 f"{meta['search_mode']}.")
    lines.append("")
    lines.append("Якорь: середина текста эталонного листа, "
                 f"{meta['anchor_chars']} симв., дословно, без "
                 "нормализации; ищется подстрокой по всему корпусу "
                 "(все листы, включая дубли и мусорные). "
                 f"Вопросов с известным листом: "
                 f"{meta['total_questions']}.")
    lines.append("")
    lines.append("## Сводка")
    lines.append("")
    lines.append(f"- жёстких негативов: **{meta['hard_negatives']}** "
                 f"из {meta['total_questions']};")
    lines.append(f"- ложных негативов (система всё же отвечает): "
                 f"**{meta['false_negatives']}**;")
    lines.append("- выбрано для полигона сигналов: "
                 f"{len(meta['selected_for_signals'])} → "
                 f"{', '.join(meta['selected_for_signals'])};")
    lines.append(f"- выбор: {meta['selected_note']}.")
    lines.append("")
    lines.append("## По вопросам")
    lines.append("")
    lines.append("| id | вопрос | эталон | якорь (60 симв.) | скрыто "
                 "листов (досягаемых) | после абляции | лидер выдачи "
                 "| вердикт |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in results:
        anchor = row["anchors"][0]["head"] if row["anchors"] else "-"
        if row["skipped"]:
            lines.append(
                f"| {row['id']} | {_clip(row['question'], 46)} "
                f"| {_clip(row['expected_doc'], 26)} "
                f"л.{row['expected_pages']} | {anchor} | — | "
                f"пропущен: {row['skipped']} | — | — |")
            continue
        after = row["after"] or {}
        summary = (f"refusal={'да' if after['refusal'] else 'нет'}; "
                   f"цитаты ok {after['ok_citations']}/"
                   f"{after['total_citations']}")
        lines.append(
            f"| {row['id']} | {_clip(row['question'], 46)} "
            f"| {_clip(row['expected_doc'], 26)} "
            f"л.{row['expected_pages']} | {anchor} "
            f"| {row['hidden_count']} ({row['hidden_searchable_count']}) "
            f"| {summary} | {_fmt_leader(after)} "
            f"| {'жёсткий' if row['hard_negative'] else 'ложный'} |")
    lines.append("")
    lines.append("Примечания.")
    lines.append("")
    lines.append("1. «Скрыто (досягаемых)» — полное число листов с "
                 "якорем по всей таблице pages и их доля, реально "
                 "досягаемая для выдачи (листы документов-дублей и "
                 "мусорные скрыты из выдачи ещё до абляции).")
    lines.append("2. Жёсткий негатив — после скрытия система не "
                 "отдаёт верифицированный ответ (отказ или "
                 "неподтверждённые цитаты). Ложный — ответ получен, "
                 "тема найдена на листах вне эталонных.")
    lines.append("3. У рабочего графа изменений нет: работаем на "
                 "копии в темп-каталоге, пометки откатываются после "
                 "каждого вопроса.")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    """CLI-точка входа; код 0 — прогон состоялся (кроме ошибок среды)."""
    parser = argparse.ArgumentParser(
        description="Ф1.3а: абляционные негативы на копии graph.db.")
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    parser.add_argument("--anchor-chars", type=int,
                        default=DEFAULT_ANCHOR_CHARS)
    args = parser.parse_args(argv)
    source = Path(args.store)
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    results, meta = run_ablation(source, Path(args.questions),
                                 args.anchor_chars)
    write_json(Path(args.out_json), results, meta)
    write_md(Path(args.out_md), results, meta)
    print(f"Отчёты: {args.out_md}, {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())