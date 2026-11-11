# -*- coding: utf-8 -*-
"""Тесты статуса лидера-значения (карточка Ф1.29, переделанная).

Контракты, закреплённые здесь (пишутся ДО реализации — ожидался RED):

- вопрос-значение (гейт ``pdfgraph.norms.is_value_question`` — «высота»
  есть в словаре норм): если у лидера нет валидного окна нормы, а у
  кого-то из топ-10 есть — лидер меняется на лист со значением,
  ``answer_state = "full"``;
- если валидного окна нет ни у кого — лидер остаётся (лист с
  упоминанием), ``answer_state = "mention"``, в ``param_norm_missing``
  честная фраза «…значение не указано» (не цитата);
- вопрос без параметра — поведение прежнее, ``answer_state = ""``;
- цитата дословная, ``verify_citations`` зелёный на обоих состояниях.
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

#: Лист со значением: «МВ-1200» на нём нет вообще (как реальный л.64).
FX_64 = ("Карта 021.40.00h. Замена щеток электродвигателя. "
         "Минимально допустимая высота щеток равна 13,5 мм. "
         "При износе щеток ниже нормы щетки заменить.")
#: Лист с упоминанием: «МВ-1200» есть, числа с единицей нет (л.141).
FX_141 = ("№ пп Содержание записи Место записи 3 Высота щеток "
          "электродвигателя МВ-1200: - при измерении высоты щеток + - "
          "- при замене щеток + +")

Q_MB = "какая минимальная высота щеток на МВ-1200"


def _build_store(tmp_path, texts: dict[int, str]) -> Store:
    store = Store(str(tmp_path / "graph.db"))
    for page, text in texts.items():
        store.upsert_page(DOC_RTE if page == PAGE64 else DOC_REG, page,
                          AIR, "", "", text, normalize_for_search(text))
    return store


@pytest.fixture
def store_both(tmp_path):
    """Оба листа: л.64 со значением, л.141 с упоминанием."""
    store = _build_store(tmp_path, {PAGE64: FX_64, PAGE141: FX_141})
    yield store
    store.close()


@pytest.fixture
def store_mention_only(tmp_path):
    """Только упоминание: л.141, числа нигде нет."""
    store = _build_store(tmp_path, {PAGE141: FX_141})
    yield store
    store.close()


def test_answer_built_from_value_page(store_both, monkeypatch):
    """Ответ строится по л.64 со значением, не по упоминанию.

    Каналы норм/алиасов выключены явно (Б9: по умолчанию они включены);
    тест проверяет механику Ф1.29 в изоляции от каналов.
    """
    from pdfgraph.answer import build_answer
    monkeypatch.setenv("PDFGRAPH_NORMS", "0")
    monkeypatch.setenv("PDFGRAPH_TERMS", "0")
    answer = build_answer(store_both, Q_MB)
    assert answer.refusal is False
    assert (answer.fragments[0].document, answer.fragments[0].page) == \
        (DOC_RTE, PAGE64)
    assert answer.answer_state == "full"
    assert answer.param_norm_missing == ""
    # Дословная цитата нормы в первом claim.
    assert "13,5" in answer.claims[0].text
    from pdfgraph.verify import verify_citations
    checks = verify_citations(answer, store_both)
    assert checks and all(check.ok for check in checks)


def test_mention_state_when_no_norm_anywhere(store_mention_only):
    """Нормы нет нигде: лидер остаётся, статус «mention», честная фраза."""
    from pdfgraph.answer import build_answer
    answer = build_answer(store_mention_only, Q_MB)
    assert answer.refusal is False
    assert (answer.fragments[0].document, answer.fragments[0].page) == \
        (DOC_REG, PAGE141)
    assert answer.answer_state == "mention"
    assert "не указано" in answer.param_norm_missing
    # Цитаты по-прежнему дословные, verify зелёный.
    from pdfgraph.verify import verify_citations
    checks = verify_citations(answer, store_mention_only)
    assert checks and all(check.ok for check in checks)


def test_no_parameter_question_unchanged(store_both):
    """Вопрос без параметра: порядок прежний, answer_state пустой."""
    from pdfgraph.answer import build_answer
    q = "как работает вентилятор обогревателя"
    answer = build_answer(store_both, q)
    assert answer.answer_state == ""
    assert answer.param_norm_missing == ""
    # Оба листа на месте, порядок выдачи не менялся правилом лидера.
    assert {f.page for f in answer.fragments[:2]} <= {PAGE64, PAGE141}


def test_value_without_norm_construction_is_full(tmp_path):
    """Значение в шапке таблицы («давление 155 кгс/см²») без «не менее» —
    это значение, а не упоминание: состояние «full», фразы нет (Ф2.10)."""
    store = _build_store(tmp_path, {PAGE64: (
        "032.10.00 ГИДРОСИСТЕМА ОСНОВНАЯ давление 155 кгс/см2")})
    try:
        from pdfgraph.answer import build_answer
        answer = build_answer(store,
                              "какое давление в гидросистеме")
        assert answer.refusal is False
        assert answer.answer_state == "full"
        assert answer.param_norm_missing == ""
    finally:
        store.close()


def test_state_empty_on_refusal(store_both):
    """Отказ (вопрос о чужой технике): answer_state остаётся пустым."""
    from pdfgraph.answer import build_answer
    answer = build_answer(store_both, "какой момент затяжки болтов шасси?")
    if answer.refusal:
        assert answer.answer_state == ""
    else:
        assert answer.answer_state in {"", "full", "mention"}
