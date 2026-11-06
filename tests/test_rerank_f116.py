"""Тесты переранжировщика этапа А (карточка Ф1.16).

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

- ``pdfgraph.rerank.rerank_coverage(store, question, fragments)`` — порядок
  по покрытию (IDF ``ln(1 + N/df)``) ↓, при равенстве прежний ``score`` ↓,
  затем прежний порядок; только порядок — те же объекты ``Fragment``,
  ``.score`` и сниппеты не перезаписываются.
- Бонус за числа: в вопросе параметр («давление», «температура», «зазор»)
  и на листе число с единицей — фиксированная прибавка.
- Решение владельца (вариант А): точные ветки и листы, найденные только
  семантикой, СОХРАНЯЮТ СВОИ СЛОТЫ слитого списка; текстовые листы
  переранжируются среди себя; лист, который есть и в лексике, и в
  семантике, переранжируется.
- Решение владельца 2026-11-06: по умолчанию ВЫКЛЮЧЕН (замер этапа А:
  doc_hit@5 1.000 → 0.893, приёмка не выполнена); ``PDFGRAPH_RERANK=1``
  включает, ``PDFGRAPH_RERANK=0`` возвращает порядок байт-в-байт прежний
  (обратимость), в том числе через :func:`pdfgraph.rag_fusion.fuse_hybrid`.
- ``fuse_hybrid`` без ``store``/``question`` (прежние вызовы) —
  переранжирования нет, контракт прежний.
"""

from __future__ import annotations

import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

FX_DOC = "РТЭ кн.1.pdf"
FX_AIRCRAFT = "Ми-8МТВ"

#: Три листа: 10 — параметр без числа с единицей; 20 — параметр с числом
#: и единицей; 30 — только слово «масла» и «в» (нулевое покрытие по
#: параметру). Словоформы взяты точными (стемминга в индексе нет).
FX_TEXTS = {
    10: "давление масла в двигателе проверяется визуально",
    20: "давление масла в двигателе 3,5 кгс/см2 при запуске",
    30: "масла в системе циркуляция",
}


def _build_store(tmp_path) -> Store:
    """Store с тремя листами (через pages/pages_fts, без графа)."""
    store = Store(str(tmp_path / "graph.db"))
    for page, text in FX_TEXTS.items():
        store.upsert_page(FX_DOC, page, FX_AIRCRAFT, "", "", text,
                          normalize_for_search(text))
    return store


@pytest.fixture
def rerank_store(tmp_path):
    store = _build_store(tmp_path)
    yield store
    store.close()


def _frag(page: int, score: float, kind: str = "text") -> Fragment:
    """Фрагмент реального листа фикс-магазина; сниппет — срез текста."""
    text = FX_TEXTS[page]
    return Fragment(
        document=FX_DOC, page=page, system_code="", subsystem_code="",
        snippet=text, char_start=0, char_end=len(text), score=score,
        kind=kind,
    )


# --- покрытие и IDF ---------------------------------------------------------


def test_rerank_orders_by_coverage(rerank_store):
    """Покрытие поднимает листы с большим числом слов вопроса."""
    from pdfgraph.rerank import rerank_coverage

    question = "давление масла в двигателе"
    pool = [_frag(30, 0.9), _frag(10, 0.8), _frag(20, 0.7)]
    ordered = rerank_coverage(rerank_store, question, pool)
    # Лист 20: все слова + бонус числа с единицей; 10 — все слова; 30 — нет
    # ни «давление», ни «двигателе».
    assert [fragment.page for fragment in ordered] == [20, 10, 30]


def test_idf_frequent_token_lighter(rerank_store):
    """Частое слово («в» — на всех трёх листах) легче редкого «давление»."""
    from pdfgraph.rerank import token_idf_weights

    weights = dict(
        (token, (df, weight))
        for token, df, weight in token_idf_weights(
            rerank_store, "давление масла в двигателе"))
    assert weights["в"][0] == 3          # на всех листах корпуса
    assert weights["давление"][0] == 2   # только листы 10 и 20
    assert weights["в"][1] < weights["давление"][1]


def test_param_bonus_beats_higher_score(rerank_store):
    """Бонус «параметр + число с единицей» перебивает более высокий скор."""
    from pdfgraph.rerank import rerank_coverage

    question = "давление масла"
    # Оба листа содержат «давление» и «масла» — покрытие равно; без бонуса
    # лист 10 (скор 0.9) остался бы выше листа 20 (скор 0.5).
    pool = [_frag(10, 0.9), _frag(20, 0.5)]
    ordered = rerank_coverage(rerank_store, question, pool)
    assert [fragment.page for fragment in ordered] == [20, 10]


def test_no_unit_no_bonus(rerank_store):
    """Без параметра в вопросе бонуса нет: равное покрытие → прежний скор."""
    from pdfgraph.rerank import rerank_coverage

    question = "масла в"          # не параметр; оба листа содержат оба слова
    pool = [_frag(30, 0.9), _frag(10, 0.5)]
    ordered = rerank_coverage(rerank_store, question, pool)
    assert [fragment.page for fragment in ordered] == [30, 10]


def test_unknown_tokens_keep_order(rerank_store):
    """Токенов вопроса нет в корпусе — порядок прежний (устойчиво)."""
    from pdfgraph.rerank import rerank_coverage

    pool = [_frag(30, 0.9), _frag(10, 0.8)]
    ordered = rerank_coverage(rerank_store, "абракадабра зюзя", pool)
    assert [fragment.page for fragment in ordered] == [30, 10]


# --- точные ветки и честность ----------------------------------------------


def test_exact_kind_keeps_slot(rerank_store):
    """Точная ветка не переранжируется и сохраняет свой слот (вариант А)."""
    from pdfgraph.rerank import rerank_coverage

    question = "давление масла в двигателе"
    code = _frag(30, 1.0, kind="code")
    pool = [_frag(30, 0.9), _frag(10, 0.8), code]
    lexical_keys = {(fragment.document, fragment.page) for fragment in pool}
    ordered = rerank_coverage(rerank_store, question, pool, lexical_keys)
    # Слот 2 (код) закреплён; текстовые слоты 0 и 1 — по покрытию:
    # лист 10 (все слова) выше листа 30 (только «масла», «в»).
    assert [fragment.page for fragment in ordered] == [10, 30, 30]
    assert ordered[2] is code


def test_semantic_only_sheet_keeps_slot(rerank_store, monkeypatch):
    """Лист, найденный только семантикой, сохраняет слот (Ф1.1 в новой схеме).

    Закреплённый лист с нулевым покрытием не вытесняется из своего слота
    слитого списка текстовыми листами с покрытием.
    """
    from pdfgraph.rag_fusion import fuse_hybrid
    from pdfgraph.rerank import RERANK_ENV

    monkeypatch.setenv(RERANK_ENV, "1")
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8)]
    vector = [_frag(20, 0.95, kind="semantic")]   # ключа 20 в лексике нет
    ordered = fuse_hybrid(lexical, vector, limit=5, store=rerank_store,
                          question=question)
    # Слитый порядок: [30, 20, 10]; слот 1 (только семантика) закреплён,
    # текстовые слоты 0 и 2 заполнены по покрытию (10 выше 30).
    assert [fragment.page for fragment in ordered] == [10, 20, 30]


def test_semantic_kind_in_lexical_is_reranked(rerank_store):
    """Лист и в лексике, и в семантике — считается текстовым (вариант А, п. 4)."""
    from pdfgraph.rerank import rerank_coverage

    question = "давление масла в двигателе"
    pool = [_frag(30, 0.9), _frag(20, 0.7, kind="semantic")]
    lexical_keys = {(fragment.document, fragment.page) for fragment in pool}
    ordered = rerank_coverage(rerank_store, question, pool, lexical_keys)
    # Оба переранжируются по покрытию: лист 20 (все слова + бонус) выше 30.
    assert [fragment.page for fragment in ordered] == [20, 30]


def test_semantic_only_without_lexical_info_is_pinned(rerank_store):
    """Без lexical_keys семантические листы считаются только-семантическими."""
    from pdfgraph.rerank import rerank_coverage

    question = "давление масла в двигателе"
    pool = [_frag(30, 0.9), _frag(20, 0.7, kind="semantic")]
    ordered = rerank_coverage(rerank_store, question, pool)
    # Слот 1 закреплён; текстовый слот 0 остаётся за листом 30.
    assert [fragment.page for fragment in ordered] == [30, 20]


def test_fragments_objects_and_scores_untouched(rerank_store):
    """Только порядок: те же объекты, скоры и сниппеты не изменены."""
    from pdfgraph.rerank import rerank_coverage

    question = "давление масла в двигателе"
    pool = [_frag(30, 0.9), _frag(20, 0.7), _frag(10, 0.8)]
    ordered = rerank_coverage(rerank_store, question, pool)
    assert all(any(ordered[i] is pool[j] for j in range(len(pool)))
               for i in range(len(pool)))
    for fragment in ordered:
        source = next(p for p in pool if p is fragment)
        assert fragment.score == source.score
        assert fragment.snippet == source.snippet


# --- выключатель и подключение в fuse_hybrid --------------------------------


def test_env_off_returns_order_unchanged(rerank_store, monkeypatch):
    """PDFGRAPH_RERANK=0: fuse_hybrid возвращает порядок байт-в-байт прежний."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.rerank import RERANK_ENV

    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8)]
    vector = [_frag(20, 0.95, kind="semantic")]

    expected = rag_fusion.rrf_fuse([lexical, vector],
                                   k=rag_fusion.effective_fusion_k(),
                                   boost=rag_fusion.effective_exact_boost(),
                                   boost_kinds=rag_fusion.EXACT_KINDS)[:10]
    monkeypatch.setenv(RERANK_ENV, "0")
    assert rag_fusion.fuse_hybrid(lexical, vector, limit=10,
                                  store=rerank_store,
                                  question=question) == expected


def test_default_is_off(rerank_store, monkeypatch):
    """Решение владельца 2026-11-06: без env переранжирования нет."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.rerank import RERANK_ENV, effective_rerank_enabled

    monkeypatch.delenv(RERANK_ENV, raising=False)
    assert effective_rerank_enabled() is False
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8)]
    vector = [_frag(20, 0.95, kind="semantic")]
    expected = rag_fusion.rrf_fuse([lexical, vector],
                                   k=rag_fusion.effective_fusion_k(),
                                   boost=rag_fusion.effective_exact_boost(),
                                   boost_kinds=rag_fusion.EXACT_KINDS)[:10]
    assert rag_fusion.fuse_hybrid(lexical, vector, limit=10,
                                  store=rerank_store,
                                  question=question) == expected


def test_env_on_reranks(rerank_store, monkeypatch):
    """PDFGRAPH_RERANK=1 включает переранжирование этапа А."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.rerank import RERANK_ENV

    monkeypatch.setenv(RERANK_ENV, "1")
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8)]
    vector = [_frag(20, 0.95, kind="semantic")]
    reranked = rag_fusion.fuse_hybrid(lexical, vector, limit=10,
                                      store=rerank_store,
                                      question=question)
    # Слитый порядок [30, 20, 10]; слот 1 (только семантика) закреплён,
    # текстовые слоты 0 и 2 — по покрытию (10 выше 30).
    assert [fragment.page for fragment in reranked] == [10, 20, 30]


def test_fuse_hybrid_without_store_question_no_rerank(rerank_store):
    """Прежние вызовы (без store/question) — переранжирования нет."""
    from pdfgraph.rag_fusion import (
        EXACT_KINDS,
        effective_exact_boost,
        effective_fusion_k,
        fuse_hybrid,
        rrf_fuse,
    )

    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8)]
    vector = [_frag(20, 0.95, kind="semantic")]
    expected = rrf_fuse([lexical, vector], k=effective_fusion_k(),
                        boost=effective_exact_boost(),
                        boost_kinds=EXACT_KINDS)[:10]
    assert fuse_hybrid(lexical, vector, limit=10) == expected


def test_fuse_hybrid_reranks_wide_pool(rerank_store, monkeypatch):
    """С включённым переранжировщиком лист с покрытием входит в топ-N.

    Лист 30 — лидер лексики по скору, но покрытие нулевое; лист 20 в хвосте
    поднимается бонусом и попадает в топ-2, лист 30 опускается.
    """
    from pdfgraph.rag_fusion import fuse_hybrid
    from pdfgraph.rerank import RERANK_ENV

    monkeypatch.setenv(RERANK_ENV, "1")
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8), _frag(20, 0.5)]
    ordered = fuse_hybrid(lexical, [], limit=2, store=rerank_store,
                          question=question)
    assert [fragment.page for fragment in ordered] == [20, 10]
