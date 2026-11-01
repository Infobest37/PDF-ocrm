# -*- coding: utf-8 -*-
"""Ф1.5: слагаемое совпадения конкретики при слиянии по рангам.

Карточка Ф1.5: для листов топ-20 слияния слитая оценка пополняется на
``WEIGHT * specificity``, где specificity = (найденные числа вопроса +
найденные обозначения) / (все числа и обозначения вопроса); числа из
обозначений техники исключаются тем же правилом, что в гейтах
(:func:`pdfgraph.gates.question_significant_numbers`). Пины:

- ``WEIGHT = 0.0`` сохраняет прежнюю выдачу байт-в-байт: порядок и
  экземпляры как без слагаемого, ``spec_fn`` не вызывается вовсе;
- лист с полным совпадением конкретики обгоняет лист без совпадений
  при равном базовом счёте (RRF-вклад одинаковый);
- ``spec_fn`` считается правилами гейтов: значимые числа (вне
  обозначений), обозначения, вхождение в текст листа;
- гейты отказа, verify и вопросы не затрагиваются.
"""

from unittest.mock import Mock

import pytest

import pdfgraph.answer
import pdfgraph.rag_fusion
from pdfgraph.query import Fragment


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _frag(document: str, page: int, score: float,
          kind: str = "text") -> Fragment:
    """Фрагмент-подставка без Store (как в test_rag_fusion._frag)."""
    snippet = f"фрагмент {document} лист {page}"
    return Fragment(
        document=document, page=page, system_code="023.00.00",
        subsystem_code="023.11.30", snippet=snippet, char_start=0,
        char_end=len(snippet), score=score, kind=kind,
    )


class _FakeStore:
    """page_text по словарю; БД не открывается. ``_connection`` — мок с
    fetchone=None (ярлыки уточнения уходят в fallback «код ...») —
    нужен decide_outcome, который всегда строит clarify-варианты."""

    def __init__(self, pages):
        self._pages = dict(pages)
        cursor = Mock()
        cursor.fetchone.return_value = None
        mock_connection = Mock()
        mock_connection.execute.return_value = cursor
        self._connection = mock_connection

    def page_text(self, document, page):
        return self._pages.get((document, page), "")

    def is_continuation(self, document, page):
        return False


# ---------------------------------------------------------------------------
# конфиг веса (карточка Ф1.5, п. 2): WEIGHT — параметр конфига, дефолт 0.0
# ---------------------------------------------------------------------------

def test_specificity_weight_env_overrides_default(monkeypatch):
    """env старше дефолта; мусор и минус — дефолт; unset — дефолт."""
    monkeypatch.delenv(pdfgraph.rag_fusion.SPECIFICITY_WEIGHT_ENV,
                       raising=False)
    assert pdfgraph.rag_fusion.SPECIFICITY_WEIGHT_ENV == (
        "PDFGRAPH_SPECIFICITY_WEIGHT")
    assert pdfgraph.rag_fusion.effective_specificity_weight() == (
        pdfgraph.rag_fusion.DEFAULT_SPECIFICITY_WEIGHT)
    monkeypatch.setenv(pdfgraph.rag_fusion.SPECIFICITY_WEIGHT_ENV, "0.1")
    assert pdfgraph.rag_fusion.effective_specificity_weight() == 0.1
    for bad in ("abc", "-0.1", ""):
        monkeypatch.setenv(pdfgraph.rag_fusion.SPECIFICITY_WEIGHT_ENV, bad)
        assert pdfgraph.rag_fusion.effective_specificity_weight() == (
            pdfgraph.rag_fusion.DEFAULT_SPECIFICITY_WEIGHT)


# ---------------------------------------------------------------------------
# приёмка: лист с полным совпадением конкретики обгоняет лист без
# совпадений при равном базовом счёте
# ---------------------------------------------------------------------------

def test_full_specificity_beats_empty_at_equal_base_score():
    """Равный RRF-вклад (оба ранг-1 своих каналов); без слагаемого
    первый встреченный (ДокБ); с WEIGHT=0.1 ДокА (spec=1.0) обгоняет."""
    spec = {("ДокБ", 1): 0.0, ("ДокА", 2): 1.0}
    lex = [_frag("ДокБ", 1, 0.9)]
    vec = [_frag("ДокА", 2, 0.95, kind="semantic")]
    spec_fn = lambda document, page: spec[(document, page)]  # noqa: E731

    # Без слагаемого ничья: first-seen — ДокБ (пин прежнего порядка).
    plain = pdfgraph.rag_fusion.rrf_fuse([lex, vec], k=60)
    assert [(f.document, f.page) for f in plain] == [("ДокБ", 1), ("ДокА", 2)]
    # Со слагаемым конкретики ДокА обгоняет ДокБ.
    weighted = pdfgraph.rag_fusion.rrf_fuse(
        [lex, vec], k=60, spec_weight=0.1, spec_fn=spec_fn)
    assert [(f.document, f.page) for f in weighted] == [
        ("ДокА", 2), ("ДокБ", 1)]


# ---------------------------------------------------------------------------
# приёмка: WEIGHT = 0.0 — прежняя выдача байт-в-байт
# ---------------------------------------------------------------------------

def test_weight_zero_keeps_order_and_skips_spec_fn():
    """При WEIGHT=0: порядок и экземпляры как без слагаемого; spec_fn
    не вызывается вовсе (никаких лишних обращений к тексту листа)."""
    lex = [_frag("ДокБ", 1, 0.9), _frag("ДокГ", 4, 0.5)]
    vec = [_frag("ДокА", 2, 0.95, kind="semantic"), _frag("ДокВ", 3, 0.4)]
    spec_fn = Mock(return_value=1.0)

    expected = pdfgraph.rag_fusion.rrf_fuse([lex, vec], k=60)
    got = pdfgraph.rag_fusion.rrf_fuse(
        [lex, vec], k=60, spec_weight=0.0, spec_fn=spec_fn)
    assert got == expected
    assert [id(fragment) for fragment in got] == [
        id(fragment) for fragment in expected]
    spec_fn.assert_not_called()  # слагаемое выключено — тексты не читаются


def test_weight_zero_by_explicit_zero_env(monkeypatch):
    """Явный WEIGHT=0.0 (env) — прежний порядок fuse_hybrid; spec_fn
    не вызывается (карточка Ф1.5: 0.0 сохраняет выдачу байт-в-байт)."""
    monkeypatch.setenv(pdfgraph.rag_fusion.SPECIFICITY_WEIGHT_ENV, "0.0")
    spec_fn = Mock(return_value=1.0)
    lex = [_frag("ДокБ", 1, 0.9)]
    vec = [_frag("ДокА", 2, 0.95, kind="semantic")]
    fused = pdfgraph.rag_fusion.fuse_hybrid(lex, vec, limit=10,
                                            spec_fn=spec_fn)
    assert [(f.document, f.page) for f in fused] == [
        ("ДокБ", 1), ("ДокА", 2)]
    spec_fn.assert_not_called()


# ---------------------------------------------------------------------------
# make_specificity_fn: правила гейтов (числа вне обозначений, обозначения)
# ---------------------------------------------------------------------------

def test_make_specificity_fn_counts_numbers_and_designators():
    """Полное совпадение — 1.0; частичное — доля; без конкретики — None."""
    store = _FakeStore({
        ("ДокА", 1): "Радиомаяк 118.00 устанавливается, давление 0,3 "
                     "кгс/см2 по ТВ3-117.",
        ("ДокБ", 2): "Радиомаяк 118.00 устанавливается, давление 0,35 "
                     "кгс/см2.",
        ("ДокВ", 3): "Пусто и без конкретики вопроса.",
    })
    question = "что за узел 118.00 и ТВ3-117 при давлении 0,3"
    fn = pdfgraph.rag_fusion.make_specificity_fn(store, question)
    assert fn is not None
    # 118.00 + ТВ3-117 + 0.3 — все три найдены.
    assert fn("ДокА", 1) == pytest.approx(1.0)
    # ТВ3-117 на листе нет; «0,35» НЕ содержит «0.3» — граница цифр
    # (правило гейтов, _number_in_text): только 118.00 → 1/3.
    assert fn("ДокБ", 2) == pytest.approx(1.0 / 3.0)
    # Ни числа, ни обозначения нет: 0.0 (лист без конкретики — не отказ).
    assert fn("ДокВ", 3) == pytest.approx(0.0)
    assert fn("ДокГ", 9) == pytest.approx(0.0)  # листа нет в текстах


def test_make_specificity_fn_none_without_specificity():
    """Вопрос без чисел и обозначений — None: слагаемое неприменимо."""
    store = _FakeStore({("ДокА", 1): "текст листа"})
    assert pdfgraph.rag_fusion.make_specificity_fn(
        store, "какое давление масла в воздушной системе") is None


# ---------------------------------------------------------------------------
# конфиг в прод-пути: fuse_hybrid берёт WEIGHT из effective_specificity_weight
# ---------------------------------------------------------------------------

def test_fuse_hybrid_uses_weight_from_env(monkeypatch):
    """env WEIGHT=0.1 активирует слагаемое в fuse_hybrid (тот же расчёт)."""
    monkeypatch.setenv(pdfgraph.rag_fusion.SPECIFICITY_WEIGHT_ENV, "0.1")
    spec = {("ДокБ", 1): 0.0, ("ДокА", 2): 1.0}
    spec_fn = lambda document, page: spec[(document, page)]  # noqa: E731
    fused = pdfgraph.rag_fusion.fuse_hybrid(
        [_frag("ДокБ", 1, 0.9)],
        [_frag("ДокА", 2, 0.95, kind="semantic")], limit=10,
        spec_fn=spec_fn)
    assert [(f.document, f.page) for f in fused] == [
        ("ДокА", 2), ("ДокБ", 1)]


# ---------------------------------------------------------------------------
# точки вызова: build_answer (гибрид) передаёт spec_fn из вопроса и store
# ---------------------------------------------------------------------------

def test_build_answer_hybrid_passes_spec_fn(monkeypatch):
    """Гибридная ветка build_answer собирает spec_fn(store, question):
    полный список на листе — 1.0, пустой — 0.0."""
    store = _FakeStore({
        ("ДокА", 2): "Радиомаяк 118.00, агрегат ТВ3-117.",
    })
    monkeypatch.setenv("PDFGRAPH_SEARCH_MODE", "hybrid_gated")
    lex = [_frag("ДокБ", 1, 0.9)]
    vec = [_frag("ДокА", 2, 0.95, kind="semantic")]
    monkeypatch.setattr(pdfgraph.answer, "search",
                        Mock(return_value=lex))
    monkeypatch.setattr(pdfgraph.answer, "vector_search",
                        Mock(return_value=vec))
    mock_fuse = Mock(return_value=[lex[0], vec[0]])
    monkeypatch.setattr(pdfgraph.rag_fusion, "fuse_hybrid", mock_fuse)

    answer = pdfgraph.answer.build_answer(
        store, "что за узел 118.00 и ТВ3-117")
    assert mock_fuse.call_count == 1
    spec_fn = mock_fuse.call_args.kwargs.get("spec_fn")
    assert spec_fn is not None
    assert spec_fn("ДокА", 2) == pytest.approx(1.0)
    assert spec_fn("ДокБ", 1) == pytest.approx(0.0)
    assert answer.refusal is False