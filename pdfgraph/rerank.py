"""Переранжировщик выдачи, этап А (карточка Ф1.16) — детерминированный.

Поиск и слияние не трогаются (карточка: «не трогать сам поиск, индекс,
pages.text, verify, гейты, веса rag_fusion, humanize»): переранжируется
ТОЛЬКО уже слитый список кандидатов, и только порядок — ``Fragment.score``
никогда не перезаписывается, сниппеты остаются дословными срезами
``pages.text``.

Схема этапа А для каждого листа пула:

1. Слова вопроса (после :func:`pdfprepare.normalize_for_search`) получают
   вес IDF ``ln(1 + N/df)``, где ``df`` — число листов корпуса с этим
   словом (FTS, тот же подсчёт, что :func:`pdfgraph.gates.document_frequency`),
   ``N`` — число листов индекса. Частые слова («в», «на», «какое»)
   получают малый вес автоматически — стоп-списка нет сознательно.
2. Покрытие листа = сумма весов тех слов, что на нём есть (присутствие —
   тем же FTS-признанием, каким гейт проверяет числа).
3. Бонус за числа: если в вопросе есть параметр («давление», «температура»,
   «зазор» — список карточки) и на листе есть число с единицей измерения,
   к покрытию прибавляется фиксированный :data:`PARAM_BONUS`.
4. Сортировка: покрытие ↓, при равенстве — прежний ``score`` ↓, затем
   прежний порядок слитого списка (устойчивая сортировка).

Решение владельца Ф1.16 (вариант А): не переранжируются и СОХРАНЯЮТ СВОИ
СЛОТЫ слитого списка (лист 41 теста Ф1.1 обязан оставаться в топ-5):

- точные ветки (``code``/``part``/``techcard``) — «точные не тонут»;
- лист, найденный ТОЛЬКО семантикой (ключа нет в лексической выдаче).

Лист, который есть и в лексике, и в семантике, считается текстовым
и переранжируется. Текстовые листы заполняют оставшиеся слоты по
покрытию; порядок закреплённых не меняется.

Выключатель: ``PDFGRAPH_RERANK=0`` возвращает порядок байт-в-байт прежний
(замер до/после, тест на обратимость). Этап Б (векторная оценка пары)
— отдельная карточка, здесь не реализован.

Решение владельца 2026-11-06 (отчёт
``docs/plans/2026-11-06-Ф1.16-reranker-stage-a-report.md``): этап А замерен —
hit@5 не упал (0.6786), но doc_hit@5 упал 1.000 → 0.893 (e06/e14/e16), лист 38
в топ-5 не попал; приёмка карточки не выполнена → переранжировщик ОСТАВЛЕН
В КОДЕ, но ПО УМОЛЧАНИЮ ВЫКЛЮЧЕН (``PDFGRAPH_RERANK=1`` — включить).
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Iterable

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

#: Выключатель переранжировщика; читается при каждом вызове, без кэша.
#: Решение владельца Ф1.16 (2026-11-06): по умолчанию ВЫКЛЮЧЕН — этап А
#: замерен, doc_hit@5 упал 1.000 → 0.893, приёмка не выполнена.
#: «1»/«true»/«yes»/«on» (без учёта регистра) — включён;
#: «0»/«false»/«no»/«off» — выключен; пусто/мусор — выключено
#: (безопасный дефолт: прежнее поведение байт-в-байт).
RERANK_ENV = "PDFGRAPH_RERANK"
_DEFAULT_RERANK_ENABLED = False
_ON_VALUES = {"1", "true", "yes", "on"}
_OFF_VALUES = {"0", "false", "no", "off"}

#: Фрагменты точных веток: не переранжируются, слот закреплён.
EXACT_RERANK_KINDS = ("code", "part", "techcard")

#: Бонус листу с «число + единица», когда в вопросе есть параметр.
#: Сопоставим с весом среднечастотного термина (df ~ N/3 → ln 2 ≈ 0.69);
#: выбран фиксированным, чтобы поведение было воспроизводимым.
PARAM_BONUS = 1.0

#: Параметры вопроса (основы словоформ), включающие бонус за числа.
_PARAM_WORDS = ("давлен", "температур", "зазор")

#: Единицы измерения на листе: «число с единицей» признаётся, когда после
#: числа (десятичный разделитель — точка или запятая) идёт единица из
#: списка. Регистр не важен; «2 мм», «3,5 кгс/см2», «+15 °С», «100 об/мин».
_UNIT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*"
    r"(?:кгс/см\s*[2²]|кг/см\s*[2²]|кгс|мпа|кпа|мм\s*рт|об/мин|"
    r"°\s*[сc]|град|атм|мм|см|кг|л\b|сек|мин|час|\bч\b)",
    re.IGNORECASE,
)

#: Кэш «(id(store), токен) → множество (document, page)» — присутствие
#: токена на листе корпуса (один FTS-запрос на токен, не на лист×токен).
_TOKEN_PAGES_CACHE: dict[tuple[int, str], set[tuple[str, int]]] = {}
#: Кэш «id(store) → число листов индекса N».
_TOTAL_PAGES_CACHE: dict[int, int] = {}

logger = logging.getLogger(__name__)


def effective_rerank_enabled() -> bool:
    """Выключатель Ф1.16: ``PDFGRAPH_RERANK``; по умолчанию выключен.

    Решение владельца 2026-11-06: этап А замерен, doc_hit@5 упал 1.000 →
    0.893 — по умолчанию прежнее поведение байт-в-байт; включать явно
    ``PDFGRAPH_RERANK=1``. Стиль :func:`pdfgraph.query.effective_search_mode`:
    читается при каждом вызове (правка видна без перезапуска), мусорное
    значение не включает переранжировщик.
    """
    raw = os.environ.get(RERANK_ENV, "").strip().casefold()
    if not raw:
        return _DEFAULT_RERANK_ENABLED
    if raw in _ON_VALUES:
        return True
    return raw not in _OFF_VALUES and _DEFAULT_RERANK_ENABLED


def _question_tokens(question: str) -> list[str]:
    """Токены вопроса для переранжирования: ключ поиска, без пустых."""
    needle = normalize_for_search(question)
    tokens = []
    for token in needle.split():
        # Двойная кавычка — синтаксис FTS-фразы; других спецсимволов после
        # нормализации в токенах не остаётся (дефис остаётся разделителем
        # токенов FTS и матчится фразой).
        cleaned = token.replace('"', "")
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _total_pages(store: Store) -> int:
    """Число листов индекса N (кэш в рамках Store)."""
    total = _TOTAL_PAGES_CACHE.get(id(store))
    if total is None:
        total = int(store._connection.execute(
            "SELECT COUNT(*) FROM pages_fts").fetchone()[0])
        _TOTAL_PAGES_CACHE[id(store)] = total
    return total


def _token_pages(store: Store, token: str) -> set[tuple[str, int]]:
    """Множество листов корпуса с токеном (FTS-фраза; кэш в рамках Store).

    df токена = размер множества — тот же подсчёт, что в
    :func:`pdfgraph.gates.document_frequency` (по строкам ``pages_fts``,
    без мусор-фильтра), но с сохранением самих листов для проверки
    присутствия без запроса «лист × токен».
    """
    key = (id(store), token)
    pages = _TOKEN_PAGES_CACHE.get(key)
    if pages is None:
        rows = store._connection.execute(
            "SELECT document, CAST(page AS INTEGER) FROM pages_fts "
            "WHERE pages_fts MATCH ?",
            (f'body: "{token}"',),
        ).fetchall()
        pages = {(row[0], int(row[1])) for row in rows}
        _TOKEN_PAGES_CACHE[key] = pages
    return pages


def token_idf_weights(store: Store, question: str
                      ) -> list[tuple[str, int, float]]:
    """(токен, df, вес IDF) по порядку слов вопроса — для отчёта и тестов.

    Вес = ``ln(1 + N/df)``; df = 0 (токена нет в корпусе) даёт максимальный
    вес, но покрытию такой токен не помогает — его нет ни на одном листе.
    """
    total = max(1, _total_pages(store))
    result: list[tuple[str, int, float]] = []
    seen: set[str] = set()
    for token in _question_tokens(question):
        if token in seen:
            continue
        seen.add(token)
        df = len(_token_pages(store, token))
        result.append((token, df, math.log(1.0 + total / df) if df else
                       math.log(1.0 + total)))
    return result


def has_param_question(question: str) -> bool:
    """Есть ли в вопросе параметр из списка карточки (давление и пр.)."""
    lowered = normalize_for_search(question)
    return any(word in lowered for word in _PARAM_WORDS)


def _page_has_number_with_unit(text: str) -> bool:
    """Число с единицей измерения на листе (регэксп :data:`_UNIT_RE`)."""
    return _UNIT_RE.search(text or "") is not None


def coverage_scores(store: Store, question: str,
                    fragments: Iterable[Fragment]) -> dict[tuple[str, int],
                                                           float]:
    """Покрытие каждого листа пула: сумма IDF-весов найденных слов.

    Точные ветки в карту не включаются (не переранжируются); ключи с нулевым
    покрытием в карту входят значением 0.0 — сортировка устойчива и не меняет
    их порядок.
    """
    tokens = _question_tokens(question)
    if not tokens:
        return {}
    weights = {token: math.log(1.0 + max(1, _total_pages(store))
                               / max(1, len(_token_pages(store, token))))
               for token in tokens}
    pages_sets = {token: _token_pages(store, token) for token in tokens}
    param_bonus = (PARAM_BONUS
                   if has_param_question(question) else 0.0)
    scores: dict[tuple[str, int], float] = {}
    for fragment in fragments:
        if fragment.kind in EXACT_RERANK_KINDS:
            continue
        key = (fragment.document, fragment.page)
        score = sum(weights[token] for token in tokens
                    if key in pages_sets[token])
        if param_bonus and _page_has_number_with_unit(
                store.page_text(fragment.document, fragment.page) or ""):
            score += param_bonus
        scores[key] = score
    return scores


def rerank_coverage(store: Store, question: str,
                    fragments: list[Fragment],
                    lexical_keys: set[tuple[str, int]] | None = None
                    ) -> list[Fragment]:
    """Этап А: покрытие ↓, при равенстве прежний ``score`` ↓, затем прежний
    порядок — но только внутри текстового подмножества.

    Решение владельца (вариант А): точные ветки и листы, найденные только
    семантикой (ключа нет в ``lexical_keys``), СОХРАНЯЮТ СВОИ СЛОТЫ слитого
    списка; текстовые листы переранжируются среди себя и заполняют
    оставшиеся слоты по покрытию. Вход — слитый список кандидатов (до
    отсечения топ-N); выход — те же объекты ``Fragment`` (скор и сниппеты
    не тронуты) в новом порядке.

    ``lexical_keys`` — ключи лексической выдачи (даёт
    :func:`pdfgraph.rag_fusion.fuse_hybrid`); без него все семантические
    листы считаются найденными только семантикой и закрепляются —
    консервативно. Токенов вопроса нет — порядок прежний.
    """
    if not fragments:
        return list(fragments)
    if lexical_keys is None:
        lexical_keys = set()
    scores = coverage_scores(store, question, fragments)
    keys = [(fragment.document, fragment.page) for fragment in fragments]
    pinned_slots: list[int] = []
    text_slots: list[int] = []
    for index, fragment in enumerate(fragments):
        # Точная ветка — всегда закреплена. Семантический лист, которого нет
        # в лексической выдаче, — «найденный только семантикой» (закреплён);
        # без lexical_keys консервативно закрепляются все семантические
        # листы. Семантический лист, который есть и в лексике, и текстовый
        # лист — переранжируются (решение владельца Ф1.16, вариант А).
        semantic_only = (fragment.kind == "semantic"
                         and keys[index] not in lexical_keys)
        if fragment.kind in EXACT_RERANK_KINDS or semantic_only:
            pinned_slots.append(index)
        else:
            text_slots.append(index)
    if not text_slots or not scores:
        return list(fragments)
    result = list(fragments)
    reranked = sorted(
        text_slots,
        key=lambda index: (-scores.get(keys[index], 0.0),
                           -fragments[index].score, index),
    )
    for slot, index in zip(text_slots, reranked):
        result[slot] = fragments[index]
    logger.debug(
        "rerank: question=%r pool=%d text=%d pinned=%d top=%s",
        question, len(fragments), len(text_slots), len(pinned_slots),
        [(f.document, f.page,
          round(scores.get((f.document, f.page), 0.0), 3))
         for f in result[:5]],
    )
    return result
