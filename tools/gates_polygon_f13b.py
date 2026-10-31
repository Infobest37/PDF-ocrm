# -*- coding: utf-8 -*-
"""Ф1.3б: замер гейтов отказа по конкретике и включение.

Два прохода по одному сценарию:
1) замер без включения: для каждого вопроса полигона — вердикты всех
   четырёх гейтов (:func:`pdfgraph.gates.run_all_gates`) на
   листе-лидере выдачи; таблица «поймано негативов / задето
   корпусных» по каждому гейту;
2) включение по карточке (только гейты с нулём задетых корпусных) —
   повторный прогон через :func:`pdfgraph.answer.build_answer` с
   включёнными гейтами: refusal_recall на негативах (требование
   карточки ≥ 0.80), refusal_false на корпусных (= 0.00) и деление
   отказа на «найденных» (:data:`refusal_false_on_found` — отказ при
   правильном листе в топ-5) и «промахах» (:data:`refusal_on_miss` —
   правильного листа в топ-5 не было, отказ обязателен). Отказ —
   конъюнкция включённых: провален хотя бы один — отказ с сообщением,
   какой именно гейт. Состав константы :data:`pdfgraph.gates.
   ENABLED_GATES` шире замера, если владелец после приёмки добавил
   гейт вручную (Ф1.3б.1: designators); это видно в meta
   константного отчёта (``const_note``, ``const_matches_measurement``).

Состав негативов полигона (20): 3 видимых внекорпусных золотого
набора, 10 мутаций Ф1.3а (tools/refusal_signals.mutation_specs), 7
внекорпусных расширения Ф1.3б (tools/polygon_f13b_extra.toml,
отдельный класс out_of_corpus_ext). Замороженные 8 вопросов
(reports/frozen_questions.json) в полигоны не входят; их прогон —
один раз, в самом конце, полным золотым прогоном
``tests/golden/metrics.py``.

Запуск: ``venv\\Scripts\\python.exe tools\\gates_polygon_f13b.py``;
с включённой константой — ``tools\\gates_polygon_f13b.py
--acceptance-only`` (приёмка на продакшн-константе). Продакшн-код
этим скриптом не правится: включение — правка константы
``ENABLED_GATES`` в pdfgraph/gates.py по результатам замера.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pdfgraph.gates as gates  # noqa: E402
from pdfgraph.answer import build_answer  # noqa: E402
from pdfgraph.query import effective_search_mode  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from tests.golden.metrics import (  # noqa: E402
    copy_store, load_questions,
)

DEFAULT_STORE = REPO_ROOT / "graph.db"
DEFAULT_QUESTIONS = REPO_ROOT / "tests" / "golden" / "questions.toml"
DEFAULT_EXTRA = REPO_ROOT / "tools" / "polygon_f13b_extra.toml"
DEFAULT_FROZEN = REPO_ROOT / "reports" / "frozen_questions.json"
DEFAULT_JSON = REPO_ROOT / "reports" / "gates_polygon_f13b.json"
DEFAULT_MD = REPO_ROOT / "reports" / "gates_polygon_f13b.md"
#: Приёмка на константе пишется отдельно (не затирает замер).
DEFAULT_JSON_ACC = REPO_ROOT / "reports" / "gates_polygon_f13b_acceptance.json"
DEFAULT_MD_ACC = REPO_ROOT / "reports" / "gates_polygon_f13b_acceptance.md"

#: Требование приёмки карточки Ф1.3б.
MIN_NEGATIVE_RECALL = 0.80
#: Все корпусные золотого набора (28 = 23 видимых + 5 замороженных).
CORPUS_TOTAL_TARGET = 28


def _load_tool(module_name: str):
    """Загрузить tools/<имя>.py как модуль (tools — не пакет)."""
    path = REPO_ROOT / "tools" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_extra_questions(path: Path) -> list[dict]:
    """Внекорпусные негативы расширения Ф1.3б из toml-файла."""
    if not path.is_file():
        raise FileNotFoundError(f"нет файла полигона: {path}")
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    items = data.get("question")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path}: нет массива записей [[question]]")
    required = {"id", "question", "aircraft_type", "ata", "notes"}
    for item in items:
        missing = required - set(item)
        if missing:
            raise ValueError(f"{path}: у записи нет полей {sorted(missing)}")
    return [dict(item) for item in items]


def assemble_polygon(extra_path: Path) -> tuple[list[dict], list[dict]]:
    """Собрать полигон: (корпусные, негативы); замороженные исключаются.

    Корпусные — готовые вопросы золотого набора с известным листом
    вне заморозки; негативы — видимые внекорпусные золотого набора
    (класс out_of_corpus), мутации Ф1.3а (класс mutation) и
    расширение Ф1.3б (класс out_of_corpus_ext).
    """
    questions = load_questions(DEFAULT_QUESTIONS)
    if not DEFAULT_FROZEN.is_file():
        raise FileNotFoundError(
            f"нет замороженных вопросов: {DEFAULT_FROZEN} (запрещено "
            "пересоздавать их здесь; файл создаёт прогон Ф1.3а)")
    with open(DEFAULT_FROZEN, encoding="utf-8") as handle:
        frozen = json.load(handle)
    frozen_ids = set(frozen["refusal_ids"]) | set(frozen["corpus_ids"])

    corpus = [
        {"id": question.id, "question": question.question,
         "aircraft_type": question.aircraft_type, "ata": question.ata,
         "expected_doc": question.expected_doc,
         "expected_pages": list(question.expected_pages),
         "class": "corpus", "origin": "golden"}
        for question in questions
        if question.status == "ready" and question.expected_pages
        and question.id not in frozen_ids]
    negatives: list[dict] = [
        {"id": question.id, "question": question.question,
         "aircraft_type": question.aircraft_type, "ata": question.ata,
         "class": "out_of_corpus", "origin": "golden"}
        for question in questions
        if question.status == "ready" and not question.expected_pages
        and question.id not in frozen_ids]
    signals = _load_tool("refusal_signals")
    for qid, text, note in signals.mutation_specs():
        negatives.append({
            "id": qid, "question": text, "aircraft_type": "Ми-8МТВ",
            "ata": "", "class": "mutation", "origin": "карточка Ф1.3а",
            "note": note})
    for item in load_extra_questions(extra_path):
        negatives.append({
            "id": item["id"], "question": item["question"],
            "aircraft_type": item["aircraft_type"], "ata": item["ata"],
            "class": "out_of_corpus_ext", "origin": "карточка Ф1.3б",
            "note": item["notes"]})
    return corpus, negatives


# --- ЗАМЕР И ВКЛЮЧЕНИЕ ---

def measure_row(store: Store, entry: dict) -> dict:
    """Прогон вопроса: выдача, лидер и вердикты всех четырёх гейтов.

    Выдача строится штатным конвейером (build_answer); на шаге замера
    гейты в продакшн-константе ещё выключены, поэтому лидер — тот же,
    что без гейтов. Ответ и цитаты не меняются.
    """
    answer = build_answer(store, entry["question"],
                          entry["aircraft_type"] or None)
    fragments = list(answer.fragments)
    leader = None
    verdicts: dict[str, dict] = {}
    if fragments:
        first = fragments[0]
        leader = {"document": first.document, "page": first.page,
                  "kind": first.kind, "score": round(first.score, 4)}
        verdicts = {
            gate: {"verdict": verdict, "detail": detail}
            for gate, (verdict, detail)
            in gates.run_all_gates(store, entry["question"], first).items()}
    return {
        "id": entry["id"], "class": entry["class"],
        "origin": entry.get("origin", ""),
        "question": entry["question"],
        "answer_refusal": bool(answer.refusal),
        "leader": leader,
        "gates": verdicts,
    }


def pass_gates_table(rows: list[dict], corpus: list[dict],
                     ) -> list[dict]:
    """Таблица по гейтам на шаге замера: поймано негативов / задето
    корпусных (вердикт «fail» на вопросе соответствующего класса)."""
    corpus_ids = {entry["id"] for entry in corpus}
    table: list[dict] = []
    for gate in gates.GATE_NAMES:
        caught = [row["id"] for row in rows
                  if row["id"] not in corpus_ids
                  and row["gates"].get(gate, {}).get("verdict") == "fail"]
        touched = [row["id"] for row in rows
                   if row["id"] in corpus_ids
                   and row["gates"].get(gate, {}).get("verdict") == "fail"]
        table.append({
            "gate": gate,
            "negatives_caught": [str(item) for item in caught],
            "corpus_touched": [str(item) for item in touched],
        })
    return table


def acceptance_pass(store: Store, corpus: list[dict],
                    negatives: list[dict]) -> list[dict]:
    """Прогон приёмки на включённых гейтах (build_answer).

    Для корпусных дополнительно: ``expected_found`` — правильный лист
    (expected_doc + expected_pages) был в топ-5 выдачи; отказ на
    «найденном» — ложный (refusal_false_on_found), отказ на «промахе» —
    обязательный (refusal_on_miss). Негативы не имеют правильного листа
    в принципе (found=False и в деление отказа не входят).
    """
    rows: list[dict] = []
    for entry in [*corpus, *negatives]:
        answer = build_answer(store, entry["question"],
                              entry["aircraft_type"] or None)
        row = {
            "id": entry["id"], "class": entry["class"],
            "question": entry["question"],
            "answer_refusal": bool(answer.refusal),
            "reason": answer.reason or "",
            "found": expected_in_top5(answer, entry),
        }
        if entry["class"] == "corpus":
            row["top5"] = [
                f"{fragment.document}:{fragment.page}"
                for fragment in answer.fragments[:5]]
        rows.append(row)
    return rows


def expected_in_top5(answer: object, entry: dict) -> bool:
    """Правильный лист вопроса входит в топ-5 фрагментов?"""
    expected_doc = entry.get("expected_doc") or ""
    expected_pages = set(entry.get("expected_pages") or [])
    if not expected_doc or not expected_pages:
        return False
    return any(fragment.document == expected_doc
               and fragment.page in expected_pages
               for fragment in answer.fragments[:5])


def refusal_split(rows: list[dict]) -> dict:
    """Деление отказных метрик: on_found (ложный) и on_miss (обязательный).

    - ``refusal_false_on_found`` — отказ при правильном листе в топ-5;
      требование карточки — 0.00;
    - ``refusal_on_miss`` — отказ при отсутствии правильного листа в
      топ-5 (отказ на промахе обязателен); доля выполненных.
    """
    found = [row for row in rows
             if row["class"] == "corpus" and row["found"]]
    missed = [row for row in rows
              if row["class"] == "corpus" and not row["found"]]
    false_on_found = [row for row in found if row["answer_refusal"]]
    on_miss = [row for row in missed if row["answer_refusal"]]
    answered_on_miss = [row for row in missed if not row["answer_refusal"]]

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return {
        "found_corpus_n": len(found),
        "miss_corpus_n": len(missed),
        "refusal_false_on_found": ratio(len(false_on_found), len(found)),
        "false_on_found_ids": [row["id"] for row in false_on_found],
        "refusal_on_miss": ratio(len(on_miss), len(missed)),
        "refused_on_miss_ids": [row["id"] for row in on_miss],
        "answered_on_miss_ids": [row["id"] for row in answered_on_miss],
    }


def run_polygon() -> dict:
    """Замер + включение + приёмка; payload для JSON/MD-отчётов."""
    corpus, negatives = assemble_polygon(DEFAULT_EXTRA)
    if len(negatives) != 20:
        raise RuntimeError(
            f"негативов полигона {len(negatives)}, а требуется 20")
    if len(corpus) != 23:
        raise RuntimeError(
            f"видимых корпусных {len(corpus)}, а требуется 23 "
            "(28 минус 5 замороженных)")
    # Замер всегда идёт без включённых гейтов; константа сохраняется и
    # восстанавливается, поэтому запуск возможен и после включения.
    const_restored = gates.ENABLED_GATES
    gates.ENABLED_GATES = ()
    with tempfile.TemporaryDirectory(prefix="gates_polygon_") as tmp:
        target = Path(tmp) / "graph.db"
        copy_store(DEFAULT_STORE, target)
        store = Store(str(target))
        try:
            rows = [measure_row(store, entry) for entry in
                    [*corpus, *negatives]]
            table = pass_gates_table(rows, corpus)
            winners = [item["gate"] for item in table
                       if not item["corpus_touched"]]
            # Шаг включения (п.2 карточки): конъюнкция включённых в том
            # же процессе; константа в gates.py правится по итогу.
            gates.ENABLED_GATES = tuple(winners)
            acceptance = acceptance_pass(store, corpus, negatives)
        finally:
            store.close()
            gates.ENABLED_GATES = const_restored
    negative_ids = {entry["id"] for entry in negatives}
    refused_negatives = [
        row for row in acceptance
        if row["class"] != "corpus" and row["answer_refusal"]]
    false_corpus = [
        row for row in acceptance
        if row["class"] == "corpus" and row["answer_refusal"]]
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "source_store": str(DEFAULT_STORE.resolve()),
            "search_mode": effective_search_mode(),
            "corpus_visible": len(corpus),
            "corpus_total_gold": CORPUS_TOTAL_TARGET,
            "negatives": len(negatives),
            "negatives_classes": {
                name: sum(1 for entry in negatives
                          if entry["class"] == name)
                for name in sorted({entry["class"]
                                    for entry in negatives})},
            "frozen_file": str(DEFAULT_FROZEN),
        },
        "measurement": {
            "table": table,
            "enabled_by_measurement": winners,
            "enabled_gates_const_at_run": list(const_restored),
        },
        "acceptance": {
            "negative_recall": round(
                len(refused_negatives) / len(negatives), 4)
            if negatives else None,
            "negative_recall_target": MIN_NEGATIVE_RECALL,
            "corpus_false": round(len(false_corpus) / len(corpus), 4)
            if corpus else None,
            "refused_negatives_ids": [
                row["id"] for row in refused_negatives],
            "missed_negatives_ids": sorted(
                negative_ids - {row["id"] for row in refused_negatives}),
            "false_corpus_ids": [row["id"] for row in false_corpus],
            "refusal_split": refusal_split(acceptance),
            "rows": acceptance,
        },
        "rows_measure": rows,
    }


# --- ПРИЁМКА НА КОНСТАНТЕ ---

def run_acceptance_only() -> dict:
    """Приёмка на продакшн-константе ENABLED_GATES (без замера).

    Прогон полигона с константой из pdfgraph/gates.py; результат
    сверяется с ранее записанным решением замера
    (reports/gates_polygon_f13b.json → measurement.enabled_by_measurement).
    """
    corpus, negatives = assemble_polygon(DEFAULT_EXTRA)
    if not gates.ENABLED_GATES:
        raise RuntimeError(
            "полигон на константе: ENABLED_GATES пуст — сначала замер "
            "и правка константы")
    rows = []
    with tempfile.TemporaryDirectory(prefix="gates_polygon_") as tmp:
        target = Path(tmp) / "graph.db"
        copy_store(DEFAULT_STORE, target)
        store = Store(str(target))
        try:
            rows = acceptance_pass(store, corpus, negatives)
        finally:
            store.close()
    refused_negatives = [
        row for row in rows
        if row["class"] != "corpus" and row["answer_refusal"]]
    false_corpus = [
        row for row in rows
        if row["class"] == "corpus" and row["answer_refusal"]]
    const_now = sorted(gates.ENABLED_GATES)
    prior_decision: list[str] | None = None
    if DEFAULT_JSON.is_file():
        with open(DEFAULT_JSON, encoding="utf-8") as handle:
            payload = json.load(handle)
        prior_decision = payload.get("measurement", {}).get(
            "enabled_by_measurement")
    drift = (prior_decision is not None
             and sorted(prior_decision) != const_now)
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "source_store": str(DEFAULT_STORE.resolve()),
            "mode": "acceptance_only_prod_const",
            "enabled_gates_const": const_now,
            "enabled_by_measurement_prior": prior_decision,
            "const_note": (
                "константа шире замера, если designators добавлен "
                "решением владельца (Ф1.3б.1); drift — ожидаемое "
                "расхождение, замер не перебивался"),
            "const_matches_measurement": not drift,
            "search_mode": effective_search_mode(),
        },
        "acceptance": {
            "negative_recall": round(len(refused_negatives)
                                     / len(negatives), 4)
            if negatives else None,
            "corpus_false": round(len(false_corpus) / len(corpus), 4)
            if corpus else None,
            "missed_negatives_ids": sorted(
                {entry["id"] for entry in negatives}
                - {row["id"] for row in refused_negatives}),
            "false_corpus_ids": [row["id"] for row in false_corpus],
            "refusal_split": refusal_split(rows),
        },
        "rows_measure": rows,
    }


def render_md(payload: dict) -> str:
    """Человекочитаемый отчёт замера/приёмки."""
    meta = payload["meta"]
    classes = meta.get("negatives_classes", {})
    lines = [
        "# Ф1.3б: полигон гейтов отказа по конкретике", "",
        f"Сгенерировано: {meta['generated_at']}",
        f"Режим: {meta.get('mode', 'замер + включение + приёмка')}",
        f"База: {meta['source_store']}",
        f"Режим поиска: {meta['search_mode']}",
        f"Корпусных видимых: {meta.get('corpus_visible', '—')} "
        f"(всего золотых {meta.get('corpus_total_gold', '—')} "
        "с замороженными)",
        f"Негативов: {meta.get('negatives', '—')} (" + ", ".join(
            f"{name}: {count}" for name, count in classes.items())
        + (")" if classes else ")"),
        "",
    ]
    table = payload.get("measurement", {}).get("table")
    if table is not None:
        lines += ["## Замер по гейтам (без включения)", "",
                  "| гейт | поймано негативов | задето корпусных |",
                  "|---|---|---|"]
        for item in table:
            lines.append(
                f"| {item['gate']} | {len(item['negatives_caught'])}: "
                + (", ".join(item["negatives_caught"]) or "—") + " | "
                + f"{len(item['corpus_touched'])}: "
                + (", ".join(item["corpus_touched"]) or "—") + " |")
        enabled = payload["measurement"]["enabled_by_measurement"]
        lines += ["",
                  "Включено по карточке (ноль задетых корпусных): "
                  + (", ".join(enabled) or "—") + ".", ""]
    acceptance = payload["acceptance"]
    lines += [
        "## Приёмка", "",
        f"refusal_recall на негативах: {acceptance['negative_recall']} "
        f"(порог {acceptance.get('negative_recall_target', 0.80)})",
        f"refusal_false на корпусных: {acceptance['corpus_false']}",
    ]
    split = acceptance.get("refusal_split") or {}
    if split:
        def _share(value) -> str:
            return "n/a" if value is None else f"{value:.3f}"
        lines += [
            f"refusal_false_on_found (правильный лист был в топ-5, "
            f"отказ ложный): {_share(split['refusal_false_on_found'])} "
            f"(n={split['found_corpus_n']})",
            f"refusal_on_miss (правильного листа в топ-5 не было, "
            f"отказ обязателен): {_share(split['refusal_on_miss'])} "
            f"(n={split['miss_corpus_n']})",
            "Промахи с отказом: "
            + (", ".join(split["refused_on_miss_ids"]) or "—"),
            "Промахи с ответом (без отказа): "
            + (", ".join(split["answered_on_miss_ids"]) or "—"),
        ]
    if acceptance.get("missed_negatives_ids"):
        lines.append("Пропущенные негативы: "
                     + ", ".join(acceptance["missed_negatives_ids"]))
    if acceptance.get("false_corpus_ids"):
        lines.append("Задетые корпусные: "
                     + ", ".join(acceptance["false_corpus_ids"]))
    lines += ["", "## Негативы по строкам (приёмка на включённых)",
              "", "| id | класс | отказ | причина |", "|---|---|---|---|"]
    rows = (payload.get("acceptance", {}).get("rows")
            or payload.get("rows_measure") or [])
    for row in rows:
        if row["class"] == "corpus":
            continue
        refusal = "да" if row["answer_refusal"] else "нет"
        reason = (row.get("reason", "") or "—")[:120]
        lines.append(
            f"| {row['id']} | {row['class']} | {refusal} | {reason} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Запуск: замер (по умолчанию) или приёмка на константе
    (``--acceptance-only``)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--acceptance-only" in argv:
        payload = run_acceptance_only()
        path = Path(DEFAULT_JSON_ACC)
        md_path = Path(DEFAULT_MD_ACC)
    else:
        payload = run_polygon()
        path = Path(DEFAULT_JSON)
        md_path = Path(DEFAULT_MD)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_md(payload))
    acceptance = payload["acceptance"]
    print(f"negative_recall={acceptance['negative_recall']} "
          f"corpus_false={acceptance['corpus_false']}")
    split = acceptance.get("refusal_split") or {}
    if split:
        print(f"refusal_false_on_found={split['refusal_false_on_found']} "
              f"(n={split['found_corpus_n']}) "
              f"refusal_on_miss={split['refusal_on_miss']} "
              f"(n={split['miss_corpus_n']})")
    print(f"Гейты: {payload['meta'].get('enabled_gates_const', payload.get('measurement', {}).get('enabled_by_measurement'))}")  # noqa: E501
    print(f"Отчёты: {path} и {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())