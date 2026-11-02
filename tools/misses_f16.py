# -*- coding: utf-8 -*-
"""Ф1.6: диагностика промахов поиска на корпусных золотых вопросах.

Read-only: работает на КОПИИ graph.db в темп-каталоге (как
tests/golden/metrics.py и tools/weights_grid_f15.py); рабочая БД не
открывается на запись. Продакшн-код не правится и не включается:
включённые гейты, слияние рангов и проверка цитат вызываются как есть.

Что делает скрипт:

1. Прогоняет корпусные ready-вопросы (status=ready, expected_pages
   непуст) штатной цепочкой :func:`pdfgraph.answer.build_answer`
   (use_rag_fusion=False, use_synthesis=False — как в золотом прогоне),
   определяет промахи: ожидаемый лист (документ+лист) не входит в
   топ-5 фрагментов.
2. По каждому промаху собирает:
   - вопрос, ожидаемый документ и лист;
   - лидера выдачи: документ, лист, первые 80 символов сниппета;
   - ранг ожидаемого листа в лексическом канале (глубина 50,
     :func:`pdfgraph.query.search`) и в векторном (глубина 50) плюс
     СЫРОЙ косинус (:func:`pdfgraph.embedding.search_chunks`, до
     какого-либо отображения; недоступно — «н/д»);
   - есть ли на лидере числа и обозначения из вопроса
     (:func:`pdfgraph.gates.gate_numbers`, ``gate_designators``);
   - есть ли ожидаемый лист в топ-20 слитой выдачи.
3. Классифицирует промах: D-кандидат (на ожидаемом листе нет чисел И
   обозначений вопроса, а на лидере есть — решение за владельцем),
   A (ожидаемый лист в топ-20, но не в топ-5), B (ожидаемого листа в
   топ-20 нет, но документ в топ-20 или лист в топ-50 канала),
   C (вне топ-50 обоих каналов).
4. Три гипотезы — замерами, без включения в код:
   - H1 «контекст ±1»: сколько промахов закрывается, если соседний
     лист (±1) ожидаемого листа считать попаданием; сколько корпусных
     попадающих это портит (сосед в топ-5, самого листа нет);
   - H2 «буст заголовка/ATA»: оффлайн-пересортировка слитого топ-50
     с бустом листов, чей ATA-код совпадает с кодом вопроса или чей
     заголовок (первые 100 символов) содержит термин вопроса; сколько
     промахов закрывается, сколько корпусных попадающих портится;
   - H3 «гейты по топ-3 вместо лидера»: имитация гейтов numbers и
     designators с окном из 2-го и 3-го листов выдачи (сигнатура
     ``gate_designators(question, leader, window)`` штатная); сколько
     промахов получает отказ (закрывает), сколько корпусных найденных
     получает новый отказ (портит).
5. Печатает в консоль сводку: всего промахов, распределение A/B/C/D,
   по каждой гипотезе два числа.

Отчёты: reports/misses_f16.json и reports/misses_f16.md (каталог
reports/ вне git). Эталоны, гейты, ENABLED_GATES, verify и конфиг
веса не меняются; пороговых решений в скрипте нет.

Запуск: venv\\Scripts\\python.exe -X utf8 tools\\misses_f16.py [путь_БД].
"""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.answer import build_answer  # noqa: E402
from pdfgraph.embedding import search_chunks  # noqa: E402
from pdfgraph.gates import (  # noqa: E402
    VERDICT_FAIL,
    VERDICT_PASS,
    gate_designators,
    gate_numbers,
)
from pdfgraph.query import search, vector_search  # noqa: E402
from pdfgraph.rag_fusion import (  # noqa: E402
    FUSION_CHANNEL_DEPTH,
    fuse_hybrid,
    make_specificity_fn,
)
from pdfgraph.store import Store  # noqa: E402
from pdfprepare.normalize import normalize_for_search  # noqa: E402
from tests.golden.metrics import copy_store  # noqa: E402
from tools.refusal_signals import question_terms  # noqa: E402

#: Глубина каналов диагностики — как FUSION_CHANNEL_DEPTH в слиянии.
DEPTH = max(50, FUSION_CHANNEL_DEPTH)
#: Граница «в выдаче вообще» для классов A/B (карточка Ф1.6).
TOP_CHECK = 20
#: Граница попадания (метрика hit_at_5).
TOP_HIT = 5
#: Холостой прогрев перед замером (в метрики не попадает).
WARMUP_QUESTION = "несущий винт"
DEFAULT_STORE = REPO_ROOT / "graph.db"
QUESTIONS_PATH = REPO_ROOT / "tests" / "golden" / "questions.toml"
JSON_REPORT = REPO_ROOT / "reports" / "misses_f16.json"
MD_REPORT = REPO_ROOT / "reports" / "misses_f16.md"
#: Длина сниппета лидера в отчёте (карточка Ф1.6: первые 80 символов).
LEADER_SNIPPET_CHARS = 80
#: Длина «заголовка листа» для гипотезы H2 (эвристика замера).
HEADING_CHARS = 100
#: Номера групп классификации (для сортировки в отчёте).
CLASS_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


# --- Вспомогательные проверки текста -------------------------------------


def _contains_term(text: str, term: str) -> bool:
    """Есть ли нормализованный термин в нормализованном тексте.

    Копия логики tools/refusal_signals._contains_term (три строки);
    дублируется, чтобы не тянуть частные имена чужого модуля.
    """
    return term in normalize_for_search(text)


def _norm_key(value: str) -> str:
    """Ключ сравнения ATA-кодов: без точек, пробелов, в нижнем регистре."""
    return "".join(value.split()).replace(".", "").lower()


def _ata_match(page_code: str | None, question_ata: str) -> bool:
    """Совпадает ли код системы листа с ATA-кодом вопроса (эвристика H2)."""
    if not page_code or not question_ata.strip():
        return False
    page_key = _norm_key(page_code)
    ata_key = _norm_key(question_ata)
    return bool(page_key) and page_key.startswith(ata_key)


def _page_text(store: Store, document: str, page: int) -> str:
    """Текст листа или пустая строка, если листа нет."""
    try:
        return store.page_text(str(document), int(page))
    except Exception:  # noqa: BLE001 — диагностика, отсутствие листа — норма
        return ""


# --- Загрузка и прогон ----------------------------------------------------


def load_corpus(path: Path) -> list[dict]:
    """Корпусные golden-вопросы: status=ready, expected_pages непуст."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [q for q in data["question"]
            if q["status"] == "ready" and q["expected_pages"]]


def expected_hit(fragments: list, question: dict) -> bool:
    """Ожидаемый лист (документ+лист) входит в топ-5 фрагментов."""
    return any(
        fragment.document == question["expected_doc"]
        and fragment.page in question["expected_pages"]
        for fragment in fragments[:TOP_HIT])


def _rank_of(fragments: list, document: str, pages: tuple[int, ...]
             ) -> int | None:
    """Позиция (1-based) первого ожидаемого листа в списке фрагментов."""
    for index, fragment in enumerate(fragments, start=1):
        if fragment.document == document and fragment.page in pages:
            return index
    return None


def _vector_rank_cosine(store: Store, question: str, document: str,
                        pages: tuple[int, ...]) -> tuple[int | None, float | None]:
    """Ранг (1-based) и СЫРОЙ косинус ожидаемого листа в векторном канале.

    Порядок — первый (лучший) чанк листа в выдаче
    :func:`pdfgraph.embedding.search_chunks` (до отображения score).
    Пустая выдача (нет индекса/модели) — (None, None).
    """
    hits = search_chunks(store, question, limit=DEPTH)
    for index, ((doc, page, _chunk), cosine) in enumerate(hits, start=1):
        if doc == document and page in pages:
            return index, float(cosine)
    if hits:
        return None, None
    return None, None  # канал пуст: н/д, а не «листа нет»


def _gate_verdict_on(question: str, text: str, which: str) -> dict:
    """Вердикт гейта numbers/designators на произвольном листе."""
    gate = gate_numbers if which == "numbers" else gate_designators
    verdict, detail = gate(question, text)
    return {"verdict": verdict, "detail": detail}


def _first_term_hits(question: str, text: str) -> int:
    """Сколько значимых терминов вопроса встречается в тексте."""
    terms = question_terms(question)
    return sum(1 for term in terms if _contains_term(text, term))


def _is_d_candidate(question: str, leader_text: str,
                    expected_text: str) -> bool:
    """Кандидат D: конкретика есть на лидере и нет на ожидаемом листе.

    «Конкретика» — числа и обозначения вопроса. На ожидаемом листе нет
    НИ чисел, НИ обозначений (оба гейта FAIL), на лидере есть хотя бы
    одно из двух (PASS). Только кандидат: решение за владельцем.
    """
    leader_numbers = _gate_verdict_on(question, leader_text, "numbers")
    leader_desig = _gate_verdict_on(question, leader_text, "designators")
    expected_numbers = _gate_verdict_on(question, expected_text, "numbers")
    expected_desig = _gate_verdict_on(question, expected_text, "designators")
    leader_has = (leader_numbers["verdict"] == VERDICT_PASS
                  or leader_desig["verdict"] == VERDICT_PASS)
    expected_lacks = (expected_numbers["verdict"] == VERDICT_FAIL
                      and expected_desig["verdict"] == VERDICT_FAIL)
    return bool(leader_has and expected_lacks)


def classify(miss: dict) -> str:
    """Класс промаха: D-кандидат, иначе A (в топ-20), B (рядом), C (вне)."""
    if miss["d_candidate"]:
        return "D"
    if miss["fused_rank"] is not None and miss["fused_rank"] <= TOP_CHECK:
        return "A"
    if (miss["doc_in_top20"]
            or (miss["lex_rank"] is not None and miss["lex_rank"] <= DEPTH)
            or miss["vec_rank"] is not None):
        return "B"
    return "C"


def diagnose_question(store: Store, question: dict) -> dict:
    """Диагностика одного вопроса: выдача, каналы, гейты, класс."""
    fused = fuse_hybrid(
        search(store, question["question"], question["aircraft_type"], DEPTH),
        vector_search(store, question["question"], limit=DEPTH),
        limit=DEPTH,
        spec_fn=make_specificity_fn(store, question["question"]),
    )
    answer = build_answer(store, question["question"],
                          question["aircraft_type"])
    fragments = answer.fragments
    expected_doc = question["expected_doc"]
    expected_pages = tuple(question["expected_pages"])
    leader = fragments[0] if fragments else None
    leader_text = (_page_text(store, leader.document, leader.page)
                   if leader else "")
    fused_rank = _rank_of(fused, expected_doc, expected_pages)
    top20_docs = {fragment.document for fragment in fused[:TOP_CHECK]}
    lex_rank = _rank_of(
        search(store, question["question"], question["aircraft_type"], DEPTH),
        expected_doc, expected_pages)
    vec_rank, vec_cosine = _vector_rank_cosine(
        store, question["question"], expected_doc, expected_pages)
    hit = expected_hit(fragments, question)
    miss: dict = {
        "id": question["id"],
        "question": question["question"],
        "aircraft_type": question["aircraft_type"],
        "ata": question["ata"],
        "expected_doc": expected_doc,
        "expected_pages": list(expected_pages),
        "hit_at_5": hit,
        "refusal": bool(answer.refusal),
        "clarify": answer.clarification is not None,
        "answer_fragments": [
            {"document": fragment.document, "page": fragment.page,
             "kind": fragment.kind}
            for fragment in fragments[:TOP_HIT]],
        "leader": ({"document": leader.document, "page": leader.page,
                    "snippet": leader.snippet[:LEADER_SNIPPET_CHARS]}
                   if leader else None),
        "fused_rank": fused_rank,
        "fused_rank_meaning": (
            None if fused_rank is None
            else ("в топ-5" if fused_rank <= TOP_HIT
                  else f"место {fused_rank} (вне топ-5)")),
        "doc_in_top20": expected_doc in top20_docs,
        "lex_rank": lex_rank,
        "vec_rank": vec_rank,
        "vec_cosine": vec_cosine,
        "expected_in_top20": fused_rank is not None
        and fused_rank <= TOP_CHECK,
    }
    if not hit:
        miss["leader_gates"] = {
            which: _gate_verdict_on(question["question"], leader_text, which)
            for which in ("numbers", "designators")}
        miss["leader_term_hits"] = _first_term_hits(question["question"],
                                                    leader_text)
        miss["expected_gates"] = {}
        miss["expected_term_hits"] = {}
        miss["d_candidate"] = False
        for page in expected_pages:
            text = _page_text(store, expected_doc, page)
            miss["expected_gates"][page] = {
                which: _gate_verdict_on(question["question"], text, which)
                for which in ("numbers", "designators")}
            miss["expected_term_hits"][page] = _first_term_hits(
                question["question"], text)
            if _is_d_candidate(question["question"], leader_text, text):
                miss["d_candidate"] = True
        miss["class"] = classify(miss)
    else:
        miss["class"] = None
    return miss


# --- Гипотезы --------------------------------------------------------------


def hypothesis_h1(records: list[dict]) -> dict:
    """H1 «контекст ±1»: сосед ожидаемого листа в топ-5 того же документа.

    Закрывает: промахи, у которых сосед (±1) ожидаемого листа стоит в
    топ-5, а сам лист — нет. Портит: корпусные попадающие, у которых
    сосед ожидаемого листа в топ-5 при отсутствии самого листа (при
    учёте ±1 это стало бы ложным попаданием).
    """
    closes: list[str] = []
    hurts: list[str] = []
    for record in records:
        if record["hit_at_5"]:
            continue
        expected_doc = record["expected_doc"]
        top5 = record["answer_fragments"]
        for page in record["expected_pages"]:
            if any(fragment["document"] == expected_doc
                   and abs(fragment["page"] - page) == 1
                   for fragment in top5):
                closes.append(record["id"])
                break
    for record in records:
        if not record["hit_at_5"]:
            continue
        expected_doc = record["expected_doc"]
        top5 = record["answer_fragments"]
        expected_in = any(fragment["document"] == expected_doc
                          and fragment["page"] in record["expected_pages"]
                          for fragment in top5)
        neighbor_in = any(fragment["document"] == expected_doc
                          and any(abs(fragment["page"] - page) == 1
                                  for page in record["expected_pages"])
                          for fragment in top5)
        if neighbor_in and not expected_in:
            hurts.append(record["id"])
    return {"closes": closes, "hurts": hurts}


def _boosted_page(store: Store, question: dict, document: str,
                  page: int) -> bool:
    """Буст листа в H2: ATA-код листа совпадает с кодом вопроса ИЛИ
    заголовок (первые 100 символов текста) содержит термин вопроса."""
    row = store._connection.execute(
        "SELECT system_code FROM pages WHERE document = ? AND page = ?",
        (str(document), int(page))).fetchone()
    page_code = row[0] if row else None
    if _ata_match(page_code, question["ata"]):
        return True
    text = _page_text(store, document, page)[:HEADING_CHARS]
    terms = question_terms(question["question"])
    return any(_contains_term(text, term) for term in terms)


def hypothesis_h2(store: Store, records: list[dict]) -> dict:
    """H2 «буст заголовка/ATA»: оффлайн-пересортировка слитого топ-50.

    Для каждого вопроса слитый топ-50 (тот же вызов, что в диагностике)
    пересортируется стабильно: сначала листы с бустом (ATA/заголовок),
    потом остальные, внутри групп — прежний порядок. Замер: сколько
    промахов получает ожидаемый лист в топ-5 (закрывает) и сколько
    корпусных попадающих теряет его из топ-5 (портит).
    """
    closes: list[str] = []
    hurts: list[str] = []
    details: dict[str, dict] = {}
    for record in records:
        question = {
            "question": record["question"],
            "aircraft_type": record["aircraft_type"],
            "ata": record["ata"],
        }
        fused = fuse_hybrid(
            search(store, record["question"], record["aircraft_type"], DEPTH),
            vector_search(store, record["question"], limit=DEPTH),
            limit=DEPTH,
            spec_fn=make_specificity_fn(store, record["question"]),
        )
        reordered = sorted(
            enumerate(fused),
            key=lambda pair: (
                0 if _boosted_page(store, question, pair[1].document,
                                   pair[1].page) else 1,
                pair[0]))
        new_rank = _rank_of([fragment for _ix, fragment in reordered],
                            record["expected_doc"],
                            tuple(record["expected_pages"]))
        details[record["id"]] = {"old_rank": record["fused_rank"],
                                 "new_rank": new_rank}
        if not record["hit_at_5"] and new_rank is not None \
                and new_rank <= TOP_HIT:
            closes.append(record["id"])
        if record["hit_at_5"] and (
                new_rank is None or new_rank > TOP_HIT):
            hurts.append(record["id"])
    return {"closes": closes, "hurts": hurts, "ranks": details}


def hypothesis_h3(store: Store, records: list[dict]) -> dict:
    """H3 «гейты по топ-3 вместо лидера»: имитация только numbers/designators.

    Штатная сигнатура: numbers — конъюнкция чисел на объединённом тексте
    первых трёх листов; designators — ``gate_designators(question,
    leader, window=[лист 2, лист 3])``. Авиа-тип и rare_term в имитации
    не участвуют (у них окно не меняется), поэтому базой сравнения
    служат те же два гейта на одном лидере.
    """
    closes: list[str] = []
    hurts: list[str] = []
    for record in records:
        answer_fragments = record["answer_fragments"]
        # Топ-3 листа выдачи берём из слитого списка диагностики
        # (hit/промах уже посчитан по топ-5 ответа).
        fused = fuse_hybrid(
            search(store, record["question"], record["aircraft_type"], DEPTH),
            vector_search(store, record["question"], limit=DEPTH),
            limit=DEPTH,
            spec_fn=make_specificity_fn(store, record["question"]))
        top3 = fused[:3]
        if not top3:
            continue
        leader_text = _page_text(store, top3[0].document, top3[0].page)
        window = [_page_text(store, fragment.document, fragment.page)
                  for fragment in top3[1:]]
        numbers_top3 = gate_numbers(record["question"],
                                    "\n".join([leader_text, *window]))
        designators_top3 = gate_designators(record["question"], leader_text,
                                            window=window)
        fail_top3 = (numbers_top3[0] == VERDICT_FAIL
                     or designators_top3[0] == VERDICT_FAIL)
        leader_text_full = _page_text(
            store, answer_fragments[0]["document"],
            answer_fragments[0]["page"]) if answer_fragments else ""
        if leader_text_full:
            numbers_leader = gate_numbers(record["question"],
                                          leader_text_full)
            designators_leader = gate_designators(record["question"],
                                                  leader_text_full)
            fail_leader = (numbers_leader[0] == VERDICT_FAIL
                           or designators_leader[0] == VERDICT_FAIL)
        else:
            fail_leader = False
        if not record["hit_at_5"] and not fail_leader and fail_top3:
            closes.append(record["id"])
        if record["hit_at_5"] and not fail_leader and fail_top3:
            hurts.append(record["id"])
    return {"closes": closes, "hurts": hurts}


# --- Отчёты ----------------------------------------------------------------


def miss_table_row(miss: dict) -> str:
    """Строка сводной таблицы промахов."""
    leader = miss["leader"] or {"document": "—", "page": "—",
                                "snippet": "—"}
    lex = str(miss["lex_rank"]) if miss["lex_rank"] is not None else "—"
    if miss["vec_rank"] is not None:
        vec = str(miss["vec_rank"])
    elif miss["vec_cosine"] is not None:
        vec = "—"
    else:
        vec = "н/д"
    cosine = (f"{miss['vec_cosine']:.4f}"
              if miss["vec_cosine"] is not None else "н/д")
    top20 = "да" if miss["expected_in_top20"] else "нет"
    return (f"| {miss['id']} | {miss['expected_doc']} "
            f"{miss['expected_pages']} | {leader['document']}:"
            f"{leader['page']} | {lex} | {vec} | {cosine} | {top20} | "
            f"{miss['class']} |")


def miss_details_md(miss: dict) -> list[str]:
    """Раздел отчёта по одному промаху."""
    leader = miss["leader"]
    if leader is not None:
        leader_line = (f"- Лидер: {leader['document']} лист "
                       f"{leader['page']} — «{leader['snippet']}»")
    else:
        leader_line = "- Лидер: выдача пуста"
    cosine = (f"{miss['vec_cosine']:.4f}"
              if miss['vec_cosine'] is not None else "н/д")
    in_top20 = "да" if miss["expected_in_top20"] else "нет"
    doc_top20 = "да" if miss["doc_in_top20"] else "нет"
    if miss["refusal"]:
        outcome = "отказ"
    elif miss["clarify"]:
        outcome = "уточнение"
    else:
        outcome = "ответ"
    lines = [
        f"### {miss['id']} — класс {miss['class']}",
        "",
        f"- Вопрос: {miss['question']}",
        f"- Ожидание: {miss['expected_doc']}, листы "
        f"{miss['expected_pages']}",
        leader_line,
        f"- Ранг ожидаемого листа: слитый {miss['fused_rank']}, "
        f"лексика {miss['lex_rank']}, вектор {miss['vec_rank']} "
        f"(косинус {cosine})",
        f"- Ожидаемый лист в топ-20: {in_top20}; "
        f"документ в топ-20: {doc_top20}",
        f"- Исход: {outcome}",
    ]
    lines.append(f"- Гейты на лидере: numbers="
                 f"{miss['leader_gates']['numbers']['verdict']} "
                 f"({miss['leader_gates']['numbers']['detail']}); "
                 f"designators="
                 f"{miss['leader_gates']['designators']['verdict']} "
                 f"({miss['leader_gates']['designators']['detail']}); "
                 f"терминов вопроса на лидере: "
                 f"{miss['leader_term_hits']}")
    for page, gates in miss["expected_gates"].items():
        lines.append(
            f"- Гейты на ожидаемом листе {page}: "
            f"numbers={gates['numbers']['verdict']} "
            f"({gates['numbers']['detail']}); "
            f"designators={gates['designators']['verdict']} "
            f"({gates['designators']['detail']}); "
            f"терминов вопроса: {miss['expected_term_hits'][page]}")
    if miss["d_candidate"]:
        lines.append("- **Кандидат D: конкретика вопроса есть на лидере "
                     "и отсутствует на ожидаемом листе. Решение за "
                     "владельцем — автоматически ошибкой эталона не "
                     "считать** (на ожидаемом листе ответ может быть "
                     "в другой формулировке).")
    return lines


def hypothesis_md(title: str, what: str, result: dict) -> list[str]:
    """Блок отчёта по гипотезе: описание и два числа."""
    return [
        f"### {title}",
        "",
        what,
        "",
        "| показатель | значение |",
        "|---|---|",
        f"| закрывает промахов | {len(result['closes'])} "
        f"({', '.join(result['closes']) or '—'}) |",
        f"| портит корпусных | {len(result['hurts'])} "
        f"({', '.join(result['hurts']) or '—'}) |",
    ]


def render_md(meta: dict, records: list[dict], misses: list[dict],
              distribution: dict[str, int], h1: dict, h2: dict,
              h3: dict, hit_at_5: float) -> str:
    """Полный markdown-отчёт: сводка, таблица, разделы, гипотезы, D-блок."""
    hits = sum(1 for record in records if record["hit_at_5"])
    lines = [
        "# Ф1.6: разбор промахов поиска (корпусные золотые вопросы)",
        "",
        f"Прогон: build_answer на копии graph.db ({meta['store']}); "
        f"каналы глубиной {DEPTH}; дата {meta['generated_at']}.",
        "",
        f"База: hit@5 = {hit_at_5:.4f} ({hits}/{len(records)}). "
        f"Промахов: {len(misses)}.",
        "",
        "## Распределение классов",
        "",
        "| A (сдвиг листа) | B (другой раздел) | C (вне выдачи) | "
        "D (эталон под вопросом) |",
        "|---|---|---|---|",
        f"| {distribution.get('A', 0)} | {distribution.get('B', 0)} | "
        f"{distribution.get('C', 0)} | {distribution.get('D', 0)} |",
        "",
        "## Сводная таблица промахов",
        "",
        "| id | ожидание | лидер | лексика (из 50) | вектор (из 50) | "
        "косинус | в топ-20 | класс |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(miss_table_row(miss) for miss in sorted(
        misses, key=lambda miss: CLASS_ORDER[miss["class"]]))
    lines += ["", "## Разбор по промахам", ""]
    for miss in sorted(misses, key=lambda miss: CLASS_ORDER[miss["class"]]):
        lines.extend(miss_details_md(miss))
        lines.append("")
    lines += ["## Гипотезы (замеры, без правок кода)", ""]
    lines += hypothesis_md(
        "H1: соседний лист ±1 считать попаданием",
        "Закрывает — промахи, где сосед (±1) ожидаемого листа в топ-5 "
        "того же документа. Портит — корпусные попадающие, где сосед "
        "ожидаемого листа в топ-5, а сам лист нет (ложное попадание "
        "при таком учёте).",
        h1)
    lines.append("")
    lines += hypothesis_md(
        "H2: буст заголовка листа и кода ATA в ранжировании",
        "Оффлайн-пересортировка слитого топ-50: выше листы с совпадающим "
        "ATA-кодом вопроса или термином вопроса в заголовке (первые 100 "
        "символов). Закрывает — промахи с ожидаемым листом в топ-5 после "
        "пересортировки; портит — попадающие, потерявшие его из топ-5.",
        h2)
    lines.append("")
    lines += hypothesis_md(
        "H3: проверка первых трёх листов в гейтах вместо лидера",
        "Имитация гейтов numbers/designators по топ-3 листам выдачи "
        "(штатная сигнатура окна designators). Закрывает — промахи, "
        "получившие отказ; портит — попадающие с новым отказом "
        "(ложный отказ).",
        h3)
    lines += ["", "## Кандидаты D — список владельцу", ""]
    d_misses = [miss for miss in misses if miss["class"] == "D"]
    if not d_misses:
        lines.append("Кандидатов D нет.")
    else:
        lines.append("Автоматически ошибкой эталона не считать: решение "
                     "по каждому — за владельцем, текст листа рядом.")
        for miss in d_misses:
            for page, gates in miss["expected_gates"].items():
                lines.append(
                    f"- {miss['id']}: ожидаемый лист {page} — "
                    f"numbers={gates['numbers']['verdict']}, "
                    f"designators={gates['designators']['verdict']}; "
                    f"лидер — {miss['leader']['document']}:"
                    f"{miss['leader']['page']}")
    return "\n".join(lines)


def print_summary(misses: list[dict], distribution: dict[str, int],
                  h1: dict, h2: dict, h3: dict, hit_at_5: float,
                  total: int) -> None:
    """Сводка в консоль: промахи, классы, по гипотезе два числа."""
    print(f"Сводка Ф1.6: hit@5 {hit_at_5:.4f} на {total} вопросах; "
          f"промахов {len(misses)}")
    print(f"Классы: A={distribution.get('A', 0)} "
          f"B={distribution.get('B', 0)} C={distribution.get('C', 0)} "
          f"D={distribution.get('D', 0)}")
    print(f"H1 (контекст ±1): закрывает {len(h1['closes'])} "
          f"({', '.join(h1['closes']) or '—'}); "
          f"портит {len(h1['hurts'])} "
          f"({', '.join(h1['hurts']) or '—'})")
    print(f"H2 (заголовок/ATA): закрывает {len(h2['closes'])} "
          f"({', '.join(h2['closes']) or '—'}); "
          f"портит {len(h2['hurts'])} "
          f"({', '.join(h2['hurts']) or '—'})")
    print(f"H3 (гейты по топ-3): закрывает {len(h3['closes'])} "
          f"({', '.join(h3['closes']) or '—'}); "
          f"портит {len(h3['hurts'])} "
          f"({', '.join(h3['hurts']) or '—'})")


# --- main -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Диагностика целиком на копии БД; json + md в reports/, сводка."""
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(argv[0]) if argv else DEFAULT_STORE
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    questions = load_corpus(QUESTIONS_PATH)
    with tempfile.TemporaryDirectory(prefix="misses_f16_") as tmp:
        copy_path = Path(tmp) / "graph.db"
        copy_store(source, copy_path)
        store = Store(str(copy_path))
        try:
            build_answer(store, WARMUP_QUESTION, None)  # холостой прогрев
            records = [diagnose_question(store, question)
                       for question in questions]
            h2 = hypothesis_h2(store, records)
            h3 = hypothesis_h3(store, records)
        finally:
            store.close()
    misses = [record for record in records if not record["hit_at_5"]]
    distribution: dict[str, int] = {}
    for miss in misses:
        distribution[miss["class"]] = distribution.get(miss["class"], 0) + 1
    hits = sum(1 for record in records if record["hit_at_5"])
    hit_at_5 = hits / len(records) if records else 0.0
    h1 = hypothesis_h1(records)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {"generated_at": generated, "store": str(source.resolve()),
            "depth": DEPTH, "top_check": TOP_CHECK,
            "questions": len(records), "hits": hits,
            "hit_at_5": hit_at_5}
    JSON_REPORT.parent.mkdir(parents=True, exist_ok=True)
    JSON_REPORT.write_text(json.dumps(
        {"meta": meta, "records": records, "misses": misses,
         "distribution": distribution, "h1": h1, "h2": h2, "h3": h3},
        ensure_ascii=False, indent=2), encoding="utf-8")
    MD_REPORT.write_text(render_md(
        meta, records, misses, distribution, h1, h2, h3, hit_at_5),
        encoding="utf-8")
    print_summary(misses, distribution, h1, h2, h3, hit_at_5,
                  len(records))
    print(f"Отчёты: {JSON_REPORT} и {MD_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
