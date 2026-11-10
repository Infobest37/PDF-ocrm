# -*- coding: utf-8 -*-
"""Тесты канала норм с общим параметром (карточка Ф1.35).

Контракты, закреплённые здесь (пишутся ДО реализации — ожидался RED):

- ``pdfgraph.norms._GENERIC_PARAMS`` = {"размер", "величин"} — общие
  параметры; остальные («высот», «толщин», «диаметр», «длин»,
  «давлен», «температур»…) — частные.
- Канал норм: общий параметр ищет окно по любому стему своего класса
  (объект вопроса в окне обязателен, единица — класса); частный —
  только точный якорь.
- Запрет частное→частное: «диаметр щётки» не находит «высоту щёток».
- Пометка в ответе при расширении: «найдено по параметру: высота
  (в запросе: размер)» — строка-статус, не цитата.
"""

from __future__ import annotations

import pytest

from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

DOC_RTE = "РТЭ кн.3.pdf"
DOC_REG = "Регламент ч.2.pdf"
AIR = "Ми-8МТВ"
PAGE64 = 64
PAGE141 = 141

FX_64 = ("Карта 021.40.00h. Замена щеток электродвигателя. "
         "Минимально допустимая высота щеток равна 13,5 мм. "
         "При износе щеток ниже нормы щетки заменить.")
FX_141 = ("№ пп Высота щеток электродвигателя МВ-1200: при измерении. "
          "Давление 2 кгс/см2. При замене щеток + +")

Q_SIZE = "какой минимальный размер щётки на МВ-1200"
Q_DIAMETER = "какой минимальный диаметр щётки на МВ-1200"
Q_PIN = "какой размер штифта"
Q_HEIGHT = "какая минимальная высота щеток на МВ-1200"


def _build_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "graph.db"))
    for page, text in ((PAGE64, FX_64), (PAGE141, FX_141)):
        store.upsert_page(DOC_RTE if page == PAGE64 else DOC_REG, page,
                          AIR, "", "", text, normalize_for_search(text))
    return store


@pytest.fixture
def store(tmp_path):
    store = _build_store(tmp_path)
    yield store
    store.close()


def _norm_candidates(store, question: str, parameter: str,
                     objects: list[str]) -> list[tuple[str, int, float]]:
    from pdfgraph.norms import norm_candidates
    return norm_candidates(store, question, parameter, objects, limit=10)


def test_generic_param_set():
    """Общие параметры — закрытый список стемов без «параметра»."""
    from pdfgraph.norms import _GENERIC_PARAMS
    assert _GENERIC_PARAMS == {"размер", "величин"}


def test_size_question_finds_page64(store):
    """«размер щётки» — канал норм находит л.64 по классу geometry."""
    from pdfgraph.norms import is_value_question
    ok, parameter, objects = is_value_question(Q_SIZE)
    assert ok and parameter == "размер"
    candidates = _norm_candidates(store, Q_SIZE, parameter, objects)
    assert (DOC_RTE, PAGE64) in [(d, p) for d, p, _s in candidates]


def test_diameter_question_misses_height(store):
    """Частное→частное запрещено: «диаметр» не находит «высоту»."""
    from pdfgraph.norms import is_value_question
    ok, parameter, objects = is_value_question(Q_DIAMETER)
    assert ok and parameter == "диаметр"
    candidates = _norm_candidates(store, Q_DIAMETER, parameter, objects)
    assert (DOC_RTE, PAGE64) not in [(d, p) for d, p, _s in candidates]


def test_pin_object_required(store):
    """«размер штифта»: объекта «штифт» на листе нет — окно не
    признаётся, л.64 не в кандидатах (объект обязателен)."""
    from pdfgraph.norms import is_value_question
    ok, parameter, objects = is_value_question(Q_PIN)
    assert ok and parameter == "размер"
    assert any("штифт" in obj for obj in objects)
    candidates = _norm_candidates(store, Q_PIN, parameter, objects)
    assert (DOC_RTE, PAGE64) not in [(d, p) for d, p, _s in candidates]


def test_private_param_unchanged(store):
    """Частный параметр «высота» — прежнее поведение: build_answer
    строит ответ по л.64, пометки расширения нет (канал норм —
    прежний prefix-AND, включая известную границу AND с дизайнатором
    Ф1.26 — вне карточки)."""
    from pdfgraph.answer import build_answer  # noqa: E402
    from pdfgraph.norms import is_value_question
    ok, parameter, _objects = is_value_question(Q_HEIGHT)
    assert ok and parameter == "высота"
    answer = build_answer(store, Q_HEIGHT)
    assert answer.refusal is False
    assert answer.answer_state == "full"
    assert (answer.fragments[0].document, answer.fragments[0].page) == \
        (DOC_RTE, PAGE64)
    assert "13,5" in answer.claims[0].text
    assert answer.param_norm_missing == ""


def test_build_answer_size_question_full_with_note(store):
    """build_answer: «размер щётки» → л.64, full, пометка расширения."""
    from pdfgraph.answer import build_answer  # noqa: E402
    from pdfgraph.verify import verify_citations  # noqa: E402
    answer = build_answer(store, Q_SIZE)
    assert answer.refusal is False
    assert (answer.fragments[0].document, answer.fragments[0].page) == \
        (DOC_RTE, PAGE64)
    assert answer.answer_state == "full"
    assert "13,5" in answer.claims[0].text
    assert "найдено по параметру: высота" in answer.param_norm_missing
    assert "(в запросе: размер)" in answer.param_norm_missing
    checks = verify_citations(answer, store)
    assert checks and all(check.ok for check in checks)


def test_build_answer_diameter_question_no_false_answer(store):
    """«диаметр щётки» — не полный ответ по л.64 (частное→частное)."""
    from pdfgraph.answer import build_answer  # noqa: E402
    answer = build_answer(store, Q_DIAMETER)
    leader = answer.fragments[0] if answer.fragments else None
    if leader and (leader.document, leader.page) == (DOC_RTE, PAGE64):
        # Если л.64 всё же лидер — только как mention без значения
        # размера: state обязан быть "mention".
        assert answer.answer_state == "mention"
        assert "не указано" in answer.param_norm_missing
    else:
        assert answer.refusal or answer.answer_state != "full" or \
            answer.param_norm_missing == ""


def test_build_answer_height_question_unchanged(store):
    """Частный параметр «высота» — прежнее поведение, без пометки."""
    from pdfgraph.answer import build_answer  # noqa: E402
    answer = build_answer(store, Q_HEIGHT)
    assert answer.refusal is False
    assert answer.answer_state == "full"
    assert "13,5" in answer.claims[0].text
    assert answer.param_norm_missing == ""
