# -*- coding: utf-8 -*-
"""Ф1.4.1: приёмочный прогон clarify через production-цепочку CLI ``ask``.

Решение владельца Ф1.4.1: уточнение только на провале гейтов конкретики
(в вопросе есть число или обозначение, которое не подтверждается на
листе-лидере, а тема найдена); уточнение по форме вопроса убрано —
слотless-вопросы идут прежним путём (answer/refuse); форма вопроса не
разделяет полный и неполный вопрос (первый прогон полигона Ф1.4,
раздел 11 .clinerules).

Критерии полигона (2 группы по 10):
- группа 1 — фонарная конкретика (несуществующие числа/обозначения,
  темы есть в корпусе): clarify хотя бы на 7 из 10 (гейт-путь: слот не
  подтвердился на листе-лидере, тема найдена);
- группа 2 — понятные неполные вопросы без конкретики (темы в корпусе):
  НОЛЬ уточнений (слотless-путь убран, исход answer/refuse).
Корпусные 28 golden-вопросов (status=ready, expected_pages непуст):
ноль уточнений на 27; строго e06 (промах ранжирования слота «118.00»)
уточняется на гейт-пути, а не отказывается.

Запуск: ``venv\\Scripts\\python.exe tools/clarify_polygon_f14.py``.
БД берётся КОПИЯ (рабочая не открывается на запись). Замер честный:
результат не подгоняется; при промахе любого критерия код возврата 1.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import tempfile
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import pathname2url

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pdfgraph.cli as cli  # noqa: E402

DEFAULT_STORE_PATH = REPO_ROOT / "graph.db"
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "tests" / "golden" / "questions.toml"
REPORT_JSON = REPO_ROOT / "reports" / "clarify_polygon_f14.json"
REPORT_MD = REPO_ROOT / "reports" / "clarify_polygon_f14.md"

WARMUP_QUESTION = "несущий винт"

# Группа 1: фонарная конкретика (несуществующие числа/обозначения),
# тема есть в корпусе; уточнение только через гейт-путь (слот не на листе).
GROUP1_QUESTIONS: tuple[str, ...] = (
    "давление масла в двигателе 123.00 на крейсерском режиме",
    "температура газов за турбиной 1234 на взлётном режиме",
    "частота вращения ротора компрессора 12.34 на взлётном режиме",
    "расход топлива 98.76 у двигателя в крейсерском полёте",
    "суфлёр маслобака НП-4321 устройство и назначение",
    "усилие натяжения троса управления 5432.1 кгс",
    "уровень масла в редукторе 7.65 л при заправке",
    "давление в дублирующей гидросистеме 98.70 при работе гидронасоса",
    "угол установки лопастей несущего винта 111.11 по приборам",
    "температура масла на входе в двигатель 555.00 на режиме",
)

# Группа 2: понятные неполные вопросы без конкретики (слотless-путь
# убран решением владельца Ф1.4.1) — ноль уточнений, исход answer/refuse.
GROUP2_QUESTIONS: tuple[str, ...] = (
    "почему глохнет двигатель на переходных режимах",
    "какое давление масла при запуске требуется",
    "как заменить масло в редукторе",
    "сколько запусков двигателя подряд разрешено",
    "чем крепится входной вал редуктора",
    "зачем нужен суфлёр маслобака",
    "какое масло заливать в двигатель",
    "почему растёт температура масла в двигателе",
    "как проверить фильтры маслосистемы",
    "какое время выбега ротора допускается",
)

# Корпусной вопрос, которому разрешено уточнение (гейт-путь, решение
# владельца Ф1.4.1): e06 — промах ранжирования слота «118.00».
CORPUS_CLARIFY_ALLOWED = frozenset({"e06"})

DECISION_BY_CODE = {0: "answer", 1: "refuse", 2: "error", 3: "clarify"}

def copy_store(source: Path, target: Path) -> None:
    """Скопировать БД read-only подключением, рабочая БД не пишется."""
    uri = f"file:{pathname2url(str(source))}?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def run_ask(question: str, store_path: Path) -> dict:
    """Прогнать вопрос через cli.main; вернуть код и расшифровку исхода."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["ask", question, "--store", str(store_path),
                           "--json"])
    except Exception as exc:  # ошибка среды: фиксируем, не глушим
        return {"rc": 2, "decision": "error",
                "error": f"{type(exc).__name__}: {exc}"}
    return {"rc": rc, "decision": DECISION_BY_CODE.get(rc, "error"),
            "err": err.getvalue()[-200:]}


def load_corpus(path: Path) -> list[dict]:
    """Корпусные golden-вопросы: status=ready, expected_pages непуст."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [q for q in data["question"]
            if q["status"] == "ready" and q["expected_pages"]]


def main() -> int:
    """Прогнать корпус + полигон через CLI и оценить критерии карточки."""
    source = DEFAULT_STORE_PATH
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="clarify_polygon_") as tmp:
        store_copy = Path(tmp) / "graph.db"
        copy_store(source, store_copy)
        cli.main(["ask", WARMUP_QUESTION, "--store", str(store_copy),
                  "--json"])  # холостой прогрев, вне учёта
        corpus = load_corpus(DEFAULT_QUESTIONS_PATH)
        corpus_rows = [{"id": q["id"], "question": q["question"],
                        **run_ask(q["question"], store_copy)}
                       for q in corpus]
        group1_rows = [{"question": question,
                        **run_ask(question, store_copy)}
                       for question in GROUP1_QUESTIONS]
        group2_rows = [{"question": question,
                        **run_ask(question, store_copy)}
                       for question in GROUP2_QUESTIONS]
    counts = {name: sum(1 for r in corpus_rows if r["decision"] == name)
              for name in ("answer", "refuse", "clarify", "error")}
    counts["group1_clarify"] = sum(1 for r in group1_rows
                                   if r["decision"] == "clarify")
    counts["group2_clarify"] = sum(1 for r in group2_rows
                                   if r["decision"] == "clarify")
    corpus_clarify_ids = [r["id"] for r in corpus_rows
                          if r["decision"] == "clarify"]
    criterion_corpus = (counts["clarify"] == 1
                        and set(corpus_clarify_ids) <= CORPUS_CLARIFY_ALLOWED)
    criterion_group1 = counts["group1_clarify"] >= 7
    criterion_group2 = counts["group2_clarify"] == 0
    criterion_errors = counts["error"] == 0
    meta = {
        "source_store": str(source.resolve()),
        "questions_file": str(DEFAULT_QUESTIONS_PATH),
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "n_corpus": len(corpus_rows),
        "n_group1": len(group1_rows), "n_group2": len(group2_rows),
        "corpus_clarify_ids": corpus_clarify_ids,
        **counts,
        "criterion_corpus_only_e06": criterion_corpus,
        "criterion_group1_clarify_ge_7": criterion_group1,
        "criterion_group2_clarify_eq_0": criterion_group2,
        "criterion_errors_eq_0": criterion_errors,
    }
    REPORT_JSON.write_text(
        json.dumps({"meta": meta, "corpus": corpus_rows,
                    "group1": group1_rows, "group2": group2_rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["Ф1.4.1: полигон clarify (production-цепочка CLI ask)",
             f"корпусных: {len(corpus_rows)}, clarify: "
             f"{counts['clarify']} {corpus_clarify_ids!r} "
             f"(критерий: только e06), answer: {counts['answer']}, "
             f"refuse: {counts['refuse']}, error: {counts['error']}",
             f"группа 1 (фонарная конкретика): clarify: "
             f"{counts['group1_clarify']} из 10 (критерий >= 7)",
             f"группа 2 (понятные неполные): clarify: "
             f"{counts['group2_clarify']} из 10 (критерий == 0)", "",
             "Корпусные вопросы:"]
    for row in corpus_rows:
        lines.append(f"  {row['id']:<5} {row['decision']:<8} rc={row['rc']} "
                     f"{row['question'][:60]}")
    lines.append("Группа 1 (фонарная конкретика):")
    for row in group1_rows:
        lines.append(f"  {row['decision']:<8} rc={row['rc']} "
                     f"{row['question'][:60]}")
    lines.append("Группа 2 (понятные неполные):")
    for row in group2_rows:
        lines.append(f"  {row['decision']:<8} rc={row['rc']} "
                     f"{row['question'][:60]}")
    lines += ["",
              "Критерий корпусных (только e06, остальные 0): "
              + ("ВЫПОЛНЕН" if criterion_corpus else "ПРОВАЛЕН"),
              "Критерий группы 1 (clarify >= 7): "
              + ("ВЫПОЛНЕН" if criterion_group1 else "ПРОВАЛЕН"),
              "Критерий группы 2 (clarify == 0): "
              + ("ВЫПОЛНЕН" if criterion_group2 else "ПРОВАЛЕН"),
              "Критерий ошибок среды (error == 0): "
              + ("ВЫПОЛНЕН" if criterion_errors else "ПРОВАЛЕН")]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Отчёт: {REPORT_JSON}")
    ok = (criterion_corpus and criterion_group1 and criterion_group2
          and criterion_errors)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())