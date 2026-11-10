# -*- coding: utf-8 -*-
"""Тесты классов параметров (карточка Ф1.34).

Контракты, закреплённые здесь (пишутся ДО реализации — ожидался RED):

- ``pdfgraph.norms._PARAM_CLASSES`` + ``param_class()``: параметры
  делятся на классы (geometry, pressure, temperature, torque, flow,
  frequency); «время», «ресурс», «напряжение» классов не имеют.
- ``_valid_windows``: если якоря параметра вопроса на листе НЕТ
  (вхождений нет) — окно ищется по любому стему того же класса;
  единицы проверяются по классу вопроса (geometry → мм/мкм/см).
  Если якорь на листе есть, но валидных окон нет — fallback НЕ
  делается (решение владельца: подстановка другого параметра может
  найти его число — ложный ответ хуже отказа, случай e35).
- «размер щётки» находит «высота щеток равна 13,5 мм»; «толщина»
  находит «высоту»; давление-вопрос не хватает геометрическую норму.
- ``build_answer`` на value-вопросе с классовым fallback: лидер —
  лист со значением, ``answer_state="full"``, verify зелёный,
  ``Claim.text`` дословный.
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

#: Лист со значением: параметр назван «высота щеток», вопрос —
#: «размер щётки»; «МВ-1200» на листе нет (реальный л.64).
FX_64 = ("Карта 021.40.00h. Замена щеток электродвигателя. "
         "Минимально допустимая высота щеток равна 13,5 мм. "
         "При износе щеток ниже нормы щетки заменить.")
#: Лист с упоминанием и чужой единицей: «давление 2 кгс/см2» —
#: другой класс (pressure) не должен распознаваться geometry-вопросом.
FX_141 = ("№ пп Высота щеток электродвигателя МВ-1200: при измерении. "
          "Давление 2 кгс/см2. При замене щеток + +")

Q_SIZE = "какой минимальный размер щётки на МВ-1200"
Q_THICKNESS = "какая минимальная толщина щётки на МВ-1200"
Q_PRESSURE = "какое давление масла в системе"


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


def test_param_class_mapping():
    """Параметры делятся на классы; без класса — None."""
    from pdfgraph.norms import param_class
    assert param_class("высота") == "geometry"
    assert param_class("размер") == "geometry"
    assert param_class("толщина") == "geometry"
    assert param_class("давление") == "pressure"
    assert param_class("температура") == "temperature"
    assert param_class("момент") == "torque"
    assert param_class("расход") == "flow"
    assert param_class("частота") == "frequency"
    assert param_class("время") is None
    assert param_class("ресурс") is None
    assert param_class("напряжение") is None
    assert param_class("несуществующий") is None


def test_class_stems_order():
    """Стемы класса в порядке словаря; «высот» первый в geometry."""
    from pdfgraph.norms import _class_stems, _PARAMETERS
    stems = _class_stems(_PARAMETERS["размер"])
    assert stems[0] == "высот"
    assert "размер" in stems and "толщин" in stems
    assert _class_stems(_PARAMETERS["давление"]) == ["давлен", "напор",
                                                     "разрежен"]
    assert _class_stems(_PARAMETERS["время"]) == []


def test_size_question_finds_height_window(store):
    """«размер щётки» находит «высота щеток равна 13,5 мм» (fallback)."""
    from pdfgraph.norms import (_PARAMETERS, _valid_windows,  # noqa: E402
                                is_value_question)
    from pdfgraph.textclean import clean_search_text  # noqa: E402
    from pdfprepare.normalize import normalize_for_search  # noqa: E402
    ok, parameter, objects = is_value_question(Q_SIZE)
    assert ok and parameter == "размер"
    object_stems = ["щеток", "мв-1200"]
    text = store.page_text(DOC_RTE, PAGE64)
    text_norm = normalize_for_search(clean_search_text(text))
    # Вхождений «размер» на листе нет, но валидное окно находится по
    # стемам класса geometry.
    assert "размер" not in text_norm
    assert _valid_windows(text_norm, _PARAMETERS["размер"],
                          object_stems, _PARAMETERS["размер"]) > 0


def test_thickness_question_private_no_fallback(store):
    """Ф1.35: «толщина» — частный параметр, частное→частное запрещено:
    «высота щеток 13,5 мм» НЕ распознаётся как её норма."""
    from pdfgraph.norms import (_PARAMETERS, _valid_windows,  # noqa: E402
                                is_value_question)
    from pdfgraph.textclean import clean_search_text  # noqa: E402
    from pdfprepare.normalize import normalize_for_search  # noqa: E402
    ok, parameter, _objects = is_value_question(Q_THICKNESS)
    assert ok and parameter == "толщина"
    text = store.page_text(DOC_RTE, PAGE64)
    text_norm = normalize_for_search(clean_search_text(text))
    assert "толщин" not in text_norm
    assert _valid_windows(text_norm, _PARAMETERS["толщина"],
                          ["щеток"], _PARAMETERS["толщина"]) == 0


def test_pressure_question_misses_geometry_norm(store):
    """Давление-вопрос не хватает геометрическую норму: единицы
    pressure-класса на листе есть, но окна давления нет (якорь
    «давлен» отсутствует, fallback-стемы тоже)."""
    from pdfgraph.norms import (_PARAMETERS, _valid_windows,  # noqa: E402
                                is_value_question)
    from pdfgraph.textclean import clean_search_text  # noqa: E402
    from pdfprepare.normalize import normalize_for_search  # noqa: E402
    ok, parameter, _objects = is_value_question(Q_PRESSURE)
    assert ok and parameter == "давление"
    text = store.page_text(DOC_RTE, PAGE64)
    text_norm = normalize_for_search(clean_search_text(text))
    assert _valid_windows(text_norm, _PARAMETERS["давление"],
                          ["масл"], _PARAMETERS["давление"]) == 0


def test_anchor_present_no_value_no_fallback(tmp_path):
    """Якорь есть, но числа нет — fallback НЕ делается (решение
    владельца Ф1.34): окно остаётся невалидным."""
    from pdfgraph.norms import (_PARAMETERS, _valid_windows,  # noqa: E402
                                is_value_question)
    from pdfgraph.textclean import clean_search_text  # noqa: E402
    from pdfprepare.normalize import normalize_for_search  # noqa: E402
    store = _build_store(tmp_path)
    try:
        ok, parameter, _objects = is_value_question(Q_SIZE)
        # Л.141: «Высота щеток электродвигателя МВ-1200» — «высот» есть,
        # числа с единицей нет; вопрос «размер» — якоря «размер» нет,
        # fallback по классу на «высот» даёт окно без числа → 0.
        text = store.page_text(DOC_REG, PAGE141)
        text_norm = normalize_for_search(clean_search_text(text))
        assert "высот" in text_norm
        assert _valid_windows(text_norm, _PARAMETERS["размер"],
                              ["щеток"], _PARAMETERS["размер"]) == 0
    finally:
        store.close()


def test_build_answer_size_question_leader64(store):
    """build_answer: «размер щётки» — лидер л.64 со значением, verify."""
    from pdfgraph.answer import build_answer  # noqa: E402
    from pdfgraph.verify import verify_citations  # noqa: E402
    answer = build_answer(store, Q_SIZE)
    assert answer.refusal is False
    assert (answer.fragments[0].document, answer.fragments[0].page) == \
        (DOC_RTE, PAGE64)
    assert answer.answer_state == "full"
    assert "13,5" in answer.claims[0].text
    checks = verify_citations(answer, store)
    assert checks and all(check.ok for check in checks)
