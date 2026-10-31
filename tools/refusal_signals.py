# -*- coding: utf-8 -*-
"""Ф1.3а: логирование сигналов отказа по классам вопросов — без порогов.

Роль: собрать полигональный набор (корпусные попадающие, корпусные
промахивающиеся, внекорпусные, абляционные, мутационные негативы) и для
каждого вопроса посчитать пять справочных сигналов на листе-лидере
выдачи:

1. ``idf_coverage`` — доля суммы IDF значимых терминов запроса,
   найденных на лидере (IDF формулы BM25 по df, посчитанному в этой БД);
2. ``rare_term_on_leader`` — есть ли на лидере хотя бы один редкий
   термин запроса (df ниже порога; величина порога — :data:
   ``RARE_DF_THRESHOLD`` — только записывается, в решениях не применяется);
3. ``numbers_on_leader`` — сколько чисел из вопроса найдено на лидере;
4. ``type_match``/``ata_match`` — совпадение типа ВС и ATA-кода;
5. ``max_cosine`` — максимум косинуса векторного канала (справочно).

Пороговых решений в скрипте нет: ни одного ``if score < X: refuse``.
Выдача строится штатным :func:`pdfgraph.answer.build_answer` +
``verify_citations`` на копии БД; лидер — первый фрагмент выдачи.

Заморозка: 8 вопросов (3 внекорпусных + 5 корпусных) выбираются
случайно с фиксированным seed (:data:`FROZEN_SEED`) и записываются в
``reports/frozen_questions.json``; в распределениях они не участвуют.
Файл создаётся один раз, перезапуски его только читают.

Отчёты: ``reports/refusal_signals.json`` и
``reports/refusal_signals.md``. Абляционные негативы берутся из
``reports/ablation_negatives.json`` (tools/ablation_negatives.py).

Запуск: ``venv\\Scripts\\python.exe tools\\refusal_signals.py``.
Продакшн-код не правится.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pdfprepare.normalize import normalize_for_search  # noqa: E402
from pdfgraph.answer import build_answer  # noqa: E402
from pdfgraph.embedding import SEMANTIC_BASE_HIGH, SEMANTIC_BASE_LOW  # noqa: E402
from pdfgraph.query import effective_search_mode  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfgraph.verify import all_verified, verify_citations  # noqa: E402
from tests.golden.metrics import (  # noqa: E402
    GoldenQuestion, copy_store, load_questions,
)

#: Seed заморозки вопросов (карточка Ф1.3а, дата создания набора).
FROZEN_SEED = 20261031
#: Порог «редкого термина» — ТОЛЬКО справочная величина, не применяется.
RARE_DF_THRESHOLD = 1000
#: Минимальная длина значимого термина (после нормализации поиска).
MIN_TERM_LEN = 3
#: Формула IDF (записывается в отчёт; согласована по смыслу с BM25).
IDF_FORMULA = "ln(1 + (N - df + 0.5) / (df + 0.5)), N — досягаемые листы"
DEFAULT_STORE = REPO_ROOT / "graph.db"
DEFAULT_QUESTIONS = REPO_ROOT / "tests" / "golden" / "questions.toml"
DEFAULT_ABLATION = REPO_ROOT / "reports" / "ablation_negatives.json"
DEFAULT_FROZEN = REPO_ROOT / "reports" / "frozen_questions.json"
DEFAULT_JSON = REPO_ROOT / "reports" / "refusal_signals.json"
DEFAULT_MD = REPO_ROOT / "reports" / "refusal_signals.md"
#: Количества классов, требуемое приёмкой карточки Ф1.3а.
FROZEN_REFUSAL = 3
FROZEN_CORPUS = 5
REQUIRED_MUTATIONS = 10

# --- Разбор вопроса: термины и числа ------------------------------------

#: Числа вопроса: одиночное число или диапазон «N-M»/«N–N».
_NUMBER_RE = re.compile(r"\d+(?:-\d+)?")
#: Стоп-слова вопроса (не несут темы; согласованы с примером в answer.py).
_STOP_WORDS = frozenset({
    "какой", "какая", "какое", "каких", "каким", "какую", "какие",
    "каким", "чтобы", "для", "при", "все", "это", "что", "как", "чем",
    "какова", "каков", "сколько", "есть", "нет", "или", "либо", "если",
    "должно", "должен", "допускается", "допустимо", "максимальное",
    "минимальное", "предел", "пределах",
})


def question_terms(question: str) -> list[str]:
    """Значимые термины запроса — как их видит FTS-канал поиска.

    Согласовано с pdfgraph.query._by_fts: токены — это слова запроса
    после :func:`pdfprepare.normalize.normalize_for_search`, здесь
    дополнительно убраны стоп-слова и короткие слова (длина
    >= :data:`MIN_TERM_LEN`); повторы усиливают вес, как и в поиске,
    поэтому на IDF они не влияют (сумма — из уникальных терминов).
    """
    words = re.findall(r"[а-яёa-z]+", question.lower())
    stop = {word for word in _STOP_WORDS}
    unique: list[str] = []
    for word in words:
        if word in stop or len(word) < MIN_TERM_LEN:
            continue
        stem = normalize_for_search(word)
        if stem and stem not in unique:
            unique.append(stem)
    return unique


def question_numbers(question: str) -> list[str]:
    """Числа вопроса как строки («40», «0,3», «3,5» → без запятой)."""
    normalized = question.replace(",", ".")
    return _NUMBER_RE.findall(normalized)


def leader_page_text(store: Store, leader: dict | None) -> str:
    """Текст листа-лидера (или пустая строка, если лидера нет)."""
    if not leader:
        return ""
    return store.page_text(str(leader["document"]), int(leader["page"]))

# --- IDF и счётчики по БД ------------------------------------------------

#: Кэш df: {термин: df} в рамках одного Store.
_DF_CACHE: dict[int, dict[str, int]] = {}


def _df_cache(store: Store) -> dict[str, int]:
    """Кэш df, привязанный к объекту Store (обнулять не требуется:
    словарь привязан по id(store), живёт не дольше прогона)."""
    return _DF_CACHE.setdefault(id(store), {})


def document_frequency(store: Store, term: str) -> int:
    """df термина: на скольких досягаемых листах встречается (FTS, body)."""
    cache = _df_cache(store)
    if term not in cache:
        cached = cache[term] = _fts_count(store, f'body: "{term}"')
        return cached
    return cache[term]


def _fts_count(store: Store, match: str) -> int:
    """Число строк pages_fts под MATCH (через сырую БД Store)."""
    try:
        cursor = store._connection.execute(
            "SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH ?",
            (match,))
        return int(cursor.fetchone()[0])
    except sqlite3.OperationalError as error:
        print(f"warn: fts count failed for {match!r}: {error}")
        return 0


def total_searchable(store: Store) -> int:
    """Число досягаемых листов (параметр N IDF, как в search_pages)."""
    return len(store.searchable_pages())


def idf(store: Store, df: int, n_total: int) -> float:
    """Формула IDF BM25 (как в sqlite fts5 bm25()); согласована по смыслу."""
    df_clamped = max(df, 0)
    return math.log(1.0 + (n_total - df_clamped + 0.5)
                    / (df_clamped + 0.5))


# --- Сигналы на листе-лидере --------------------------------------------


def _contains_term(haystack: str, term: str) -> bool:
    """Есть ли нормализованный термин в нормализованном тексте лида."""
    haystack_norm = normalize_for_search(haystack)
    return term in haystack_norm


def idf_coverage(store: Store, terms: list[str], leader: dict | None
                 ) -> tuple[float, list[dict]]:
    """Доля суммы IDF терминов, найденных на лидере, + детали по терминам."""
    if not terms or not leader:
        return 0.0, []
    text = leader_page_text(store, leader)
    if not text:
        return 0.0, []
    covered, total = 0.0, 0.0
    details: list[dict] = []
    for term in terms:
        df = document_frequency(store, term)
        weight = idf(store, df, total_searchable(store))
        total += weight
        hit = _contains_term(text, term)
        covered += weight if hit else 0.0
        details.append({"term": term, "df": df, "idf": round(weight, 3),
                        "on_leader": hit})
    return covered / total if total > 0 else 0.0, details


def rare_term_on_leader(store: Store, terms: list[str],
                        leader: dict | None
                        ) -> tuple[bool, int, list[str]]:
    """Есть ли на лидере редкий термин (df < порога).

    Возвращает (есть, порог, список «термин(df=…)»). Порог только
    записывается рядом, в решениях отказа не участвует.
    """
    if not terms or not leader:
        return False, RARE_DF_THRESHOLD, []
    text = leader_page_text(store, leader)
    if not text:
        return False, RARE_DF_THRESHOLD, []
    picked: list[str] = []
    for term in terms:
        df = document_frequency(store, term)
        if df < RARE_DF_THRESHOLD:
            if _contains_term(text, term):
                picked.append(f"{term}(df={df})")
    return bool(picked), RARE_DF_THRESHOLD, picked


def numbers_on_leader(store: Store, numbers: list[str],
                      leader: dict | None) -> tuple[int, int, list[str]]:
    """Сколько чисел вопроса найдено на лидере подстрокой (запятая и
    точка считаются эквивалентными, ОКР-текст использует запятую)."""
    if not numbers or not leader:
        return 0, len(numbers), []
    text = leader_page_text(store, leader)
    if not text:
        return 0, len(numbers), []
    text_dot = text.replace(",", ".")
    found: list[str] = []
    for number in numbers:
        if number in text or number in text_dot:
            found.append(number)
    return len(found), len(numbers), found


# --- Совпадения типа ВС и ATA, косинус ----------------------------------

def aircraft_type_matches(leader: dict | None, question_type: str
                          ) -> tuple[bool, str | None]:
    """Тип листа-лидера против типа вопроса (по pages.aircraft_type)."""
    if not leader:
        return False, None
    leader_type = leader.get("aircraft_type")
    if not leader_type:
        return True, None  # в базе тип не задан — сравнить не с чем
    return str(leader_type) == str(question_type), str(leader_type)


def normalize_ata(raw: str) -> str:
    """«072.03.00» → «72»; «065» → «65»; пустое — пусто."""
    match = re.match(r"(\d+)", str(raw).strip())
    return str(int(match.group(1))) if match else ""


def ATA_aliases(code: str) -> list[str]:
    """Эквивалентные записи кода: «72» ↔ «072» (с ведущим нулём)."""
    digits = normalize_ata(code)
    if not digits:
        return []
    zero_padded = digits.zfill(3)
    return [digits, zero_padded]


def ata_matches(leader: dict | None, question_ata: str
                ) -> tuple[bool | None, str, str]:
    """Пересечение группы ATA вопроса с (system, subsystem) лидера.

    Возвращает (совпадение/None, system_code, subsystem_code); None —
    в вопросе код не задан, сравнить не с чем.
    """
    system = str((leader or {}).get("system_code") or "")
    subsystem = str((leader or {}).get("subsystem_code") or "")
    wanted = normalize_ata(question_ata)
    if not wanted:
        return None, system, subsystem
    aliases = set(ATA_aliases(question_ata))
    for code in (system, subsystem):
        group = normalize_ata(code)
        if group and group in aliases:
            return True, system, subsystem
    return False, system, subsystem


def best_cosine(answer: object) -> float | None:
    """Максимум косинуса semantic-чанков выдачи (справочная величина).

    Косинус восстанавливается из скорa semantic-канала обратным
    отображением полосы запросных скоров («0.99..1.0 → 0..1),
    согласовано с полосой в pdfgraph.query.
    """
    fragments = list(getattr(answer, "fragments", []))
    semantic = [fragment for fragment in fragments
                if str(fragment.kind).startswith("semantic")]
    if not semantic:
        return None
    span = SEMANTIC_BASE_HIGH - SEMANTIC_BASE_LOW
    if span <= 0:
        return None
    top = semantic[0]
    cosine = (float(top.score) - SEMANTIC_BASE_LOW) / span
    return round(max(0.0, min(1.0, cosine)), 4)


# --- Классы вопросов и прогон одного вопроса ----------------------------

def run_one(store: Store, question_text: str, aircraft_type: str,
            ata: str, qid: str, qclass: str, origin: str,
            expected_doc: str = "", expected_pages: list[int] | None = None
            ) -> dict:
    """Задать вопрос и собрать сигналы на листе-лидере выдачи.

    Порогов нет: независимо от «уверенности» вызывается
    ``verify_citations`` (машинная проверка цитат — обязательна),
    refusal берётся из answer.refusal.
    """
    answer = build_answer(store, question_text, aircraft_type or None)
    checks = verify_citations(answer, store)
    ok_citations = sum(1 for check in checks if check.ok)
    verified = all_verified(checks) and not answer.refusal
    fragments = list(answer.fragments)
    leader = None
    if fragments:
        first = fragments[0]
        leader = {"document": first.document, "page": first.page,
                  "system_code": first.system_code,
                  "subsystem_code": first.subsystem_code,
                  "kind": first.kind, "score": round(first.score, 4)}
    terms = question_terms(question_text)
    numbers = question_numbers(question_text)
    idf_cov, term_details = idf_coverage(store, terms, leader)
    rare_hit, rare_threshold, rare_picked = rare_term_on_leader(
        store, terms, leader)
    found_nums, total_nums, found_num_list = numbers_on_leader(
        store, numbers, leader)
    type_match, leader_type = aircraft_type_matches(leader, aircraft_type)
    ata_match, leader_system, leader_subsystem = ata_matches(
        leader, ata)
    top5 = [{"document": fragment.document, "page": fragment.page,
             "kind": fragment.kind, "score": round(fragment.score, 4)}
            for fragment in fragments[:5]]
    cosine = best_cosine(answer)
    return {
        "id": qid, "question": question_text,
        "class": qclass, "origin": origin,
        "aircraft_type": aircraft_type, "ata": ata,
        "expected_doc": expected_doc,
        "expected_pages": list(expected_pages or []),
        "refusal": bool(answer.refusal),
        "refusal_reason": str(answer.reason or ""),
        "ok_citations": ok_citations,
        "total_citations": len(checks),
        "verified": verified,
        "leader": leader,
        "top5": top5,
        "signals": {
            "idf_coverage": round(idf_cov, 4),
            "idf_total_terms": len(terms),
            "terms": term_details,
            "rare_term_on_leader": rare_hit,
            "rare_df_threshold": rare_threshold,
            "rare_terms_picked": rare_picked,
            "numbers_found_on_leader": found_nums,
            "numbers_total": total_nums,
            "numbers_picked": found_num_list,
            "type_match": type_match,
            "leader_aircraft_type": leader_type,
            "ata_match": ata_match,
            "leader_ata_system": leader_system,
            "leader_ata_subsystem": leader_subsystem,
            "max_cosine": cosine,
        },
    }


# --- Мутационные негативы -----------------------------------------------

def mutation_specs() -> list[tuple[str, str, str]]:
    """Мутации реальных вопросов: (id, текст, примечание).

    Правило карточки: меняем существенное — тип ВС, узел или число.
    Ответа в документации нет по построению (10 штук).
    """
    return [
        ("m01", "не убирается шасси на самолёте Ту-154 после взлёта",
         "другой тип ВС (Ту-154) + другой узел"),
        ("m02", "что входит в основную топливную систему самолёта Ил-76",
         "другой тип ВС (Ил-76)"),
        ("m03",
         "какое давление масла допускается в двигателе Д-30 самолёта Ту-154",
         "другой тип ВС и другой двигатель"),
        ("m04", "какой назначенный ресурс лопастей несущего винта вертолёта Ми-2",
         "другой тип ВС (Ми-2)"),
        ("m05", "не убирается фюзеляжный топливный бак вертолёта",
         "другой узел (бак вместо шасси/двигателя)"),
        ("m06", "как заменить масло в коробке приводов самолёта",
         "другой тип ВС и другой узел"),
        ("m07", "какой элемент редуктора ТВ3-117 выполняет роль маслобака",
         "несуществующий узел (двигателю приписан редуктор-маслобак)"),
        ("m08",
         "каково минимальное время выбега ротора компрессора — не менее 400 с",
         "другое число (400 с вместо 40 с)"),
        ("m09", "не более 30 запусков двигателя подряд разрешено",
         "другое число (30 вместо 5)"),
        ("m10",
         "прибор показывает 3,5 атмосферы на прогреве редуктора АИ-26",
         "другой двигатель (АИ-26) и другое значение"),
    ]

# --- Состав полигона: корпус, абляция, внекорпус, заморозка -------------

def load_ablation_negatives(path: Path, ids: list[str]) -> list[dict]:
    """Абляционные вопросы из отчёта ablation_negatives.json по id."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    wanted = set(ids)
    by_id = {str(row["id"]): row for row in data["results"]}
    found: list[dict] = []
    for entry_id in ids:
        row = by_id.get(entry_id)
        if row is None:
            raise ValueError(
                f"в {path} нет абляционного вопроса {entry_id}")
        found.append(row)
        wanted.discard(entry_id)
    return found


def out_of_corpus_questions(questions: list[GoldenQuestion]
                            ) -> list[GoldenQuestion]:
    """Готовые внекорпусные вопросы (expected_pages пуст)."""
    return [question for question in questions
            if question.status == "ready"
            and not question.expected_pages]


def corpus_questions(questions: list[GoldenQuestion]
                     ) -> list[GoldenQuestion]:
    """Готовые корпусные вопросы с известным листом."""
    return [question for question in questions
            if question.status == "ready" and question.expected_pages]


def load_or_create_frozen(questions: list[GoldenQuestion], path: Path,
                          seed: int) -> tuple[dict, bool]:
    """Замороженные 8 вопросов: прочитать файл или создать его.

    Состав: 3 внекорпусных + 5 корпусных, random.Random(seed),
    сортировка по пулам перед выбором (детерминированность). Файл
    создается один раз; повторный запуск только читает.
    """
    if path.is_file():
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), False
    refusals = sorted(out_of_corpus_questions(questions),
                      key=lambda item: item.id)
    corpus = sorted(corpus_questions(questions),
                    key=lambda item: item.id)
    rng = random.Random(seed)
    frozen_refusal = sorted(
        rng.sample([item.id for item in refusals],
                   min(FROZEN_REFUSAL, len(refusals))))
    frozen_corpus = sorted(
        rng.sample([item.id for item in corpus],
                   min(FROZEN_CORPUS, len(corpus))))
    payload = {
        "seed": seed,
        "created_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "refusal_ids": frozen_refusal,
        "corpus_ids": frozen_corpus,
        "rule": (
            "исключены из всех распределений Ф1.3а; проверка — "
            "один раз, в самом конце настроек отказа"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return payload, True

# --- Основной прогон полигона -------------------------------------------

def run_pipeline(store_path: Path, questions_path: Path,
                 ablation_path: Path, frozen_path: Path
                 ) -> tuple[list[dict], dict]:
    """Собрать полигон и посчитать сигналы; вернуть (строки, мета)."""
    questions = load_questions(questions_path)
    frozen, frozen_created = load_or_create_frozen(
        questions, frozen_path, FROZEN_SEED)
    frozen_ids = set(frozen["refusal_ids"]) | set(frozen["corpus_ids"])

    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="refusal_signals_") as tmp:
        copy_source = store_path
        copy_target = Path(tmp) / "graph.db"
        copy_store(copy_source, copy_target)
        store = Store(str(copy_target))
        try:
            ata_by_id = {question.id: question.ata
                         for question in questions}
            # 1) корпусные: попадающие и промахивающиеся (hit@5, вне
            # заморозки).
            for question in corpus_questions(questions):
                if question.id in frozen_ids:
                    continue
                answer = build_answer(
                    store, question.question, question.aircraft_type or None)
                hit = any(fragment.document == question.expected_doc
                          and fragment.page in question.expected_pages
                          for fragment in answer.fragments[:5])
                qclass = "corpus_hit" if hit else "corpus_miss"
                rows.append(run_one(
                    store, question.question, question.aircraft_type,
                    question.ata, question.id, qclass, "golden",
                    question.expected_doc, list(question.expected_pages)))
            # 2) внекорпусные (вне заморозки).
            for question in out_of_corpus_questions(questions):
                if question.id in frozen_ids:
                    continue
                rows.append(run_one(
                    store, question.question, question.aircraft_type,
                    question.ata, question.id, "out_of_corpus", "golden",
                    question.expected_doc, list(question.expected_pages)))
            # 3) абляционные (жёсткие, из отчёта ablation_negatives.json).
            ablation_ids, ablation_note = selected_ablation_ids(
                ablation_path)
            ablation_rows = load_ablation_negatives(
                ablation_path, ablation_ids)
            for row in ablation_rows:
                rows.append(run_one(
                    store, str(row["question"]),
                    str(row["aircraft_type"]),
                    ata_by_id.get(str(row["id"]), ""),
                    str(row["id"]), "ablation", "ablation_negatives.json",
                    str(row["expected_doc"]),
                    [int(page) for page in row["expected_pages"]]))
            # 4) мутационные.
            for qid, text, note in mutation_specs():
                entry = run_one(store, text, "Ми-8МТВ", "", qid,
                                "mutation", "карточка Ф1.3а")
                entry["mutation_note"] = note
                rows.append(entry)
        finally:
            store.close()
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "source_store": str(store_path.resolve()),
        "questions_file": str(questions_path),
        "search_mode": effective_search_mode(),
        "idf_formula": IDF_FORMULA,
        "rare_df_threshold": RARE_DF_THRESHOLD,
        "frozen_seed": FROZEN_SEED,
        "frozen_refusal_ids": list(frozen["refusal_ids"]),
        "frozen_corpus_ids": list(frozen["corpus_ids"]),
        "frozen_created": frozen_created,
        "counts": count_classes(rows),
        "ablation_source": str(ablation_path),
        "ablation_selection_note": ablation_note,
        "total_rows": len(rows),
    }
    return rows, meta


def selected_ablation_ids(path: Path) -> tuple[list[str], str]:
    """Выбранные 10 абляционных id из мета отчёта (детерминированный
    механический выбор сделан в ablation_negatives.py, здесь его не
    повторяем) + примечание о дефиците, если жёстких меньше 10."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    meta = data.get("meta", {})
    ids = [str(entry) for entry in meta.get("selected_for_signals", [])]
    note = str(meta.get("skipped_selection", ""))
    if len(ids) < 10:
        note = (f"{note}; ПОЛИГОН НЕДОУКОМПЛЕКТОВАН: абляционных "
                f"{len(ids)} из 10")
    return ids, note


def count_classes(rows: list[dict]) -> dict[str, int]:
    """Сколько вопросов каждого класса вошло в полигон."""
    counts: dict[str, int] = {}
    for row in rows:
        qclass = str(row["class"])
        counts[qclass] = counts.get(qclass, 0) + 1
    return counts

# --- Распределения сигналов по классам ----------------------------------

#: Показ классов в таблицах (негативы в конце).
CLASS_ORDER = ("corpus_hit", "corpus_miss", "out_of_corpus",
               "ablation", "mutation")
CLASS_TITLES = {
    "corpus_hit": "корпусные попадающие (hit@5)",
    "corpus_miss": "корпусные промахивающиеся (нет hit@5)",
    "out_of_corpus": "внекорпусные",
    "ablation": "абляционные (жёсткие)",
    "mutation": "мутационные",
}
NUMERIC_SIGNALS = ("idf_coverage", "max_cosine")
#: Доля чисел, найденных на лидере (значение на вопрос, None если чисел
#: нет вообще).
NUMBERS_SHARE = "numbers_share"
BOOL_SIGNALS = ("rare_term_on_leader", "type_match", "ata_match")


def _numeric_stats(values: list[float]) -> dict | None:
    """Минимум, медиана, максимум (None — нет значений)."""
    if not values:
        return None
    ordered = sorted(values)
    return {"n": len(ordered),
            "min": round(ordered[0], 4),
            "median": round(statistics.median(ordered), 4),
            "max": round(ordered[-1], 4)}


def _numbers_share(row: dict) -> float | None:
    """Доля чисел вопроса, найденных на лидере (None — чисел нет)."""
    total = int(row["signals"]["numbers_total"])
    if total == 0:
        return None
    return row["signals"]["numbers_found_on_leader"] / total


def class_distribution(rows: list[dict], qclass: str) -> dict:
    """Распределения всех сигналов внутри одного класса."""
    subset = [row for row in rows if row["class"] == qclass]
    dist: dict[str, dict] = {"n": len(subset)}
    for signal in NUMERIC_SIGNALS:
        values = [float(value) for value in
                  (row["signals"].get(signal) for row in subset)
                  if value is not None]
        dist[signal] = _numeric_stats(values)
    numbers_shares = [share for share in
                      (_numbers_share(row) for row in subset)
                      if share is not None]
    dist[NUMBERS_SHARE] = _numeric_stats(numbers_shares)
    for signal in BOOL_SIGNALS:
        applicable = 0
        positive = 0
        for row in subset:
            value = row["signals"].get(signal)
            if value is None:  # тип/ATA не с чем сравнить
                continue
            applicable += 1
            positive += 1 if value else 0
        share = positive / applicable if applicable else None
        dist[signal] = {"share_true": round(share, 4) if share is not None
                        else None,
                        "positive": positive,
                        "n_applicable": applicable}
    # Доля вопросов, где найдены ВСЕ числа вопроса.
    with_numbers = [row for row in subset
                    if row["signals"]["numbers_total"] > 0]
    all_found = [row for row in with_numbers
                 if row["signals"]["numbers_found_on_leader"]
                 == row["signals"]["numbers_total"]]
    dist["numbers_all_found"] = {
        "share": round(len(all_found) / len(with_numbers), 4)
        if with_numbers else None,
        "positive": len(all_found), "n_with_numbers": len(with_numbers)}
    dist["refusals"] = sum(1 for row in subset if row["refusal"])
    dist["verified"] = sum(1 for row in subset if row["verified"])
    return dist


def compute_distributions(rows: list[dict]) -> dict[str, dict]:
    """Класс → распределение каждого сигнала (+ сводка по негативам)."""
    dist = {qclass: class_distribution(rows, qclass)
            for qclass in CLASS_ORDER
            if any(row["class"] == qclass for row in rows)}
    negatives = [dict(row, **{"class": "all_negatives"}) for row in rows
                 if row["class"] in ("out_of_corpus", "ablation",
                                     "mutation")]
    if negatives:
        dist["all_negatives"] = class_distribution(
            negatives, "all_negatives")
    return dist

# --- Записи отчётов ------------------------------------------------------

SIGNAL_LABELS = {
    "idf_coverage": "IDF-покрытие лида",
    "rare_term_on_leader": "редкий термин на лиде",
    "numbers_share": "доля чисел вопроса на лиде",
    "type_match": "тип ВС совпал",
    "ata_match": "ATA совпал",
    "max_cosine": "макс. косинус",
}


def _fmt_num(entry: dict | None) -> str:
    """Медиана с коридором для таблицы («мед 0.75 [0.10…1.00], n=12»)."""
    if not entry or entry.get("n", 0) == 0:
        return "n/a"
    return (f"мед {entry['median']:.2f} "
            f"[{entry['min']:.2f}…{entry['max']:.2f}], n={entry['n']}")


def _fmt_bool(entry: dict | None) -> str:
    """Доля True для таблицы («45% (5/11)»)."""
    if not entry or entry["n_applicable"] == 0:
        return "n/a"
    share = entry["share_true"]
    if share is None:
        return "n/a"
    return (f"{share * 100:.0f}% ({entry['positive']}/"
            f"{entry['n_applicable']})")


def _separation_note(dist: dict[str, dict]) -> list[str]:
    """Механическое сравнение корпусных попадающих с негативами.

    Для числа: пересекаются ли коридоры [мин…макс]; для булева:
    разность долей. Это факты для чтения, не решение об отказе
    (пороговых решений в коде нет).
    """
    corpus = dist.get("corpus_hit")
    negatives = dist.get("all_negatives")
    if not corpus or not negatives:
        return []
    lines = ["Сравнение классов (факты, без порогов): корпусные "
             "попадающие против объединённого пула негативов."]
    for signal in NUMERIC_SIGNALS + (NUMBERS_SHARE,):
        left = corpus.get(signal)
        right = negatives.get(signal)
        if not left or not right:
            continue
        overlap = (left["min"] <= right["max"]
                   and right["min"] <= left["max"])
        gap = round(left["median"] - right["median"], 4)
        verdict = ("коридоры НЕ пересекаются" if not overlap
                   else "коридоры пересекаются")
        lines.append(
            f"- {SIGNAL_LABELS.get(signal, signal)}: корпус "
            f"{left['median']:.2f} против негативов "
            f"{right['median']:.2f} (разность медиан {gap:+.2f}), "
            f"{verdict}")
    for signal in BOOL_SIGNALS:
        left = corpus.get(signal)
        right = negatives.get(signal)
        if (not left or not right or not left["n_applicable"]
                or not right["n_applicable"]):
            continue
        delta = ((left["share_true"] or 0) - (right["share_true"] or 0))
        lines.append(
            f"- {SIGNAL_LABELS.get(signal, signal)}: корпус "
            f"{(left['share_true'] or 0) * 100:.0f}% против негативов "
            f"{(right['share_true'] or 0) * 100:.0f}% (разность долей "
            f"{delta * 100:+.0f} п.п.)")
    return lines


def write_json(path: Path, rows: list[dict], dist: dict[str, dict],
               meta: dict) -> None:
    """Машинный отчёт: мета, распределения, построчные записи."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"meta": meta, "distributions": dist, "rows": rows},
                  handle, ensure_ascii=False, indent=2)


def _table_classes(dist: dict[str, dict]) -> list[str]:
    """Порядок колонок таблицы: классы, в конце — все негативы."""
    classes = [qclass for qclass in CLASS_ORDER if qclass in dist]
    if "all_negatives" in dist:
        classes.append("all_negatives")
    return classes

def _table_classes(dist: dict[str, dict]) -> list[str]:
    """Порядок колонок таблицы: классы, в конце — все негативы."""
    classes = [qclass for qclass in CLASS_ORDER if qclass in dist]
    if "all_negatives" in dist:
        classes.append("all_negatives")
    return classes


def write_md(path: Path, rows: list[dict], dist: dict[str, dict],
             meta: dict) -> None:
    """Человекочитаемая сводка «класс → распределение сигнала»."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Ф1.3а: сигналы полигона отказа (без порогов)")
    lines.append("")
    lines.append(f"Дата: {meta['generated_at']}. База: копия "
                 f"`{meta['source_store']}`; режим поиска: "
                 f"{meta['search_mode']}.")
    lines.append("")
    lines.append(f"IDF: {meta['idf_formula']}. Порог «редкого термина» "
                 f"(df < {meta['rare_df_threshold']}) — только "
                 "записываемая величина, в решениях не применяется.")
    counts = ", ".join(
        f"{CLASS_TITLES.get(key, key)}: {value}"
        for key, value in meta["counts"].items())
    lines.append(f"Классы полигона: {counts}. Всего "
                 f"{meta['total_rows']} вопросов.")
    lines.append("")
    lines.append(f"Замороженные вопросы (исключены из распределений): "
                 f"{', '.join(meta['frozen_refusal_ids'])} (внекорпусные); "
                 f"{', '.join(meta['frozen_corpus_ids'])} (корпусные). "
                 f"Seed: {meta['frozen_seed']}.")
    if meta.get("ablation_selection_note"):
        lines.append("")
        lines.append(f"Абляционные: {meta['ablation_selection_note']}.")
    lines.append("")
    lines.append("## Класс → распределение сигнала")
    lines.append("")
    classes = _table_classes(dist)
    headers = ["сигнал"] + [CLASS_TITLES.get(qclass, qclass)
                            for qclass in classes]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for label, fmt in _signal_formatters(dist):
        lines.append("| " + " | ".join(
            [label] + [fmt(qclass) for qclass in classes]) + " |")
    lines.append("")
    lines.append("Подписи: «мед X [мин…макс], n» — медиана и коридор; "
                 "«доля% (верно/применимо)» — булев сигнал; «верно» — "
                 "доля вопросов, где найдены ВСЕ числа вопроса; n/a — "
                 "сигнал неприменим (чисел/кода/типа нет).")
    lines.append("")
    lines.append("## Разделение классов")
    lines.append("")
    lines.extend(_separation_note(dist))
    lines.append("")
    lines.append("## Выводы")
    lines.append("")
    lines.append(_conclusion_text(dist))
    lines.append("")
    lines.append("Пороговых решений нет: в коде нет ни одного "
                 "«if score < X: refuse». Замороженные вопросы "
                 "не входят в этот отчёт; их проверка — одна, в конце "
                 "всех настроек.")
    lines.append("")
    lines.append("## По вопросам")
    lines.append("")
    lines.append("| id | класс | вопрос | лидер | idf | редкий термин "
                 "| числа | тип | ATA | косинус | отказ |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        signals = row["signals"]
        leader = row["leader"]
        leader_text = (f"…{leader['document'][-32:]} л.{leader['page']}"
                       if leader else "—")
        numbers = (f"{signals['numbers_found_on_leader']}/"
                   f"{signals['numbers_total']}")
        cosine = signals["max_cosine"]
        lines.append(
            f"| {row['id']} | {row['class']} | "
            f"{_clip_q(row['question'])} | {leader_text} "
            f"| {signals['idf_coverage']:.2f} "
            f"| {signals['rare_term_on_leader']} | {numbers} "
            f"| {signals['type_match']} | {signals['ata_match']} "
            f"| {cosine if cosine is not None else '—'} "
            f"| {'да' if row['refusal'] else 'нет'} |")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _signal_formatters(
        dist: dict[str, dict]) -> list[tuple[str, object]]:
    """Строки большой таблицы: подпись → доступ к ячейке по классу."""
    def numeric(signal: str):
        return lambda cls: _fmt_num(dist[cls].get(signal))

    def boolean(signal: str):
        return lambda cls: _fmt_bool(dist[cls].get(signal))

    def numbers(cls: str) -> str:
        share = dist[cls].get(NUMBERS_SHARE)
        all_found = dist[cls].get("numbers_all_found") or {}
        base = _fmt_num(share) if share else "n/a"
        if not all_found or all_found.get("share") is None:
            return base
        return f"{base} · верно {all_found['share'] * 100:.0f}%"

    def outcomes(cls: str) -> str:
        return f"{dist[cls]['refusals']}/{dist[cls]['verified']}"

    return [
        (SIGNAL_LABELS["idf_coverage"], numeric("idf_coverage")),
        (SIGNAL_LABELS["rare_term_on_leader"],
         boolean("rare_term_on_leader")),
        (SIGNAL_LABELS["numbers_share"], numbers),
        (SIGNAL_LABELS["type_match"], boolean("type_match")),
        (SIGNAL_LABELS["ata_match"], boolean("ata_match")),
        (SIGNAL_LABELS["max_cosine"], numeric("max_cosine")),
        ("отказов/верифицированных", outcomes),
    ]


def _conclusion_text(dist: dict[str, dict]) -> str:
    """Механический вердикт: какой сигнал сильнее разводит классы.

    Числовой сигнал считаем «работающим», если коридоры корпусных
    и негативов не пересекаются; булев — если доли расходятся сильнее
    30 п.п. Это наблюдение по данным, не решение в коде.
    """
    corpus = dist.get("corpus_hit")
    negatives = dist.get("all_negatives")
    if not corpus or not negatives:
        return ("Недостаточно данных: нужен и класс корпусных "
                "попадающих, и пул негативов.")
    separated: list[str] = []
    for signal in NUMERIC_SIGNALS + (NUMBERS_SHARE,):
        left = corpus.get(signal)
        right = negatives.get(signal)
        if left and right and not (left["min"] <= right["max"]
                                   and right["min"] <= left["max"]):
            separated.append(f"коридоры «{SIGNAL_LABELS.get(signal,
                                                            signal)}» не "
                             "пересекаются")
    for signal in BOOL_SIGNALS:
        left = corpus.get(signal)
        right = negatives.get(signal)
        if (left and right and left["n_applicable"]
                and right["n_applicable"]):
            l_share = left["share_true"] or 0
            r_share = right["share_true"] or 0
            if abs(l_share - r_share) >= 0.30:
                separated.append(
                    f"доли «{SIGNAL_LABELS.get(signal, signal)}» "
                    f"расходятся сильнее 30 п.п. "
                    f"({l_share * 100:.0f}% против {r_share * 100:.0f}%)")
    if separated:
        return ("Сигналы, которые у корпусных попадающих и негативов "
                "различаются заметнее всего: " + "; ".join(separated)
                + ". Кандидаты для будущей настройки порогов.")
    return ("Ни один сигнал не отделяет корпусные попадающие от "
            "негативов существенно: коридоры числовых пересекаются, "
            "булевы доли расходятся менее чем на 30 п.п. На этом "
            "полигоне не видно сигнала, годного для разделения "
            "классов.")


def _clip_q(text: str, width: int = 60) -> str:
    """Вопрос для таблицы: одной строкой, с обрезкой."""
    single = " ".join(str(text).split())
    return single if len(single) <= width else single[: width - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    """CLI-точка входа полигона сигналов отказа."""
    parser = argparse.ArgumentParser(
        description="Ф1.3а: логирование сигналов отказа по классам "
                    "вопросов (без порогов и правок продакшена).")
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--ablation-json", default=str(DEFAULT_ABLATION))
    parser.add_argument("--frozen", default=str(DEFAULT_FROZEN))
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    args = parser.parse_args(argv)
    ablation_path = Path(args.ablation_json)
    if not ablation_path.is_file():
        # Специально не «проглатываем»: сначала нужен отчёт абляции.
        print(f"error: нет отчёта абляции {ablation_path} — запустите "
              "tools\\ablation_negatives.py", file=sys.stderr)
        return 2
    rows, meta = run_pipeline(
        Path(args.store), Path(args.questions), ablation_path,
        Path(args.frozen))
    dist = compute_distributions(rows)
    write_json(Path(args.out_json), rows, dist, meta)
    write_md(Path(args.out_md), rows, dist, meta)
    print(f"Отчёты: {args.out_md}, {args.out_json}")
    print(f"Замороженные (исключены): {args.frozen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# %% CONT %%