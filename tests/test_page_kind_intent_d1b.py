"""Д1б: интенты вопроса и строгий чертёжный номер (RED до реализации).

Контракты:

- ``question_intent`` различает семь интентов;
- приоритет — от конкретного к общему:
  поиск_карты > обозначение > дефект > процедура > значение > назначение >
  неполный;
- «что такое» + параметр = значение, «что такое» без параметра = назначение;
- чертёжный номер строгий: не ``13,5``, не ``2.3.16``, не ``072.00.00``,
  не ``2024.11.14``;
- голое имя/буквенный код (``БСПИ``, ``МУ-615``) — неполный.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("question, expected", [
    # поиск_карты
    ("карта 703", "поиск_карты"),
    ("ТЕХНОЛОГИЧЕСКАЯ КАРТА № 208", "поиск_карты"),
    # обозначение
    ("что за деталь 8АТ.7714.150-01", "обозначение"),
    ("какой номер у клапана МКТ-163", "обозначение"),
    # дефект
    ("почему не запускается двигатель", "дефект"),
    ("течёт масло из редуктора", "дефект"),
    # процедура
    ("как заменить масло в двигателе", "процедура"),
    ("порядок проверки тормозов", "процедура"),
    # значение
    ("какая минимальная высота щёток на МВ-1200", "значение"),
    ("какое давление масла в двигателе ТВ3-117", "значение"),
    # назначение
    ("для чего нужен МУ-615А", "назначение"),
    ("что делает БСПИ-4-2", "назначение"),
    # неполный
    ("БСПИ", "неполный"),
    ("МУ-615", "неполный"),
])
def test_question_intent_seven(question, expected):
    from pdfgraph.page_kind import question_intent

    assert question_intent(question) == expected


def test_what_is_with_parameter_is_value():
    """«Что такое высота щёток» — значение: параметр есть."""
    from pdfgraph.page_kind import question_intent

    assert question_intent("что такое высота щёток") == "значение"
    assert question_intent("что такое давление масла") == "значение"


def test_what_is_without_parameter_is_purpose():
    """«Что такое МУ-615» — назначение: параметра нет."""
    from pdfgraph.page_kind import question_intent

    assert question_intent("что такое МУ-615") == "назначение"


def test_concrete_action_wins_over_value():
    """Процедура/дефект конкретнее параметра."""
    from pdfgraph.page_kind import question_intent

    assert question_intent("как проверить давление масла") == "процедура"
    assert question_intent("почему не держит давление масла") == "дефект"


def test_value_question_with_designator_is_not_designator():
    """«какая высота щёток МВ-1200» — значение, не обозначение."""
    from pdfgraph.page_kind import question_intent

    assert question_intent("какая высота щёток МВ-1200") == "значение"


@pytest.mark.parametrize("token", [
    "172.1768.000",
    "7888.0139",
    "3126.030",
    "8АТ.7714.150.017",
    "8АТ.7714.150-01",
])
def test_strict_designator_accepts(token):
    from pdfgraph.page_kind import is_designator

    assert is_designator(token)


@pytest.mark.parametrize("token", [
    "13,5",
    "2.3.16",
    "072.00.00",
    "2024.11.14",
])
def test_strict_designator_rejects(token):
    from pdfgraph.page_kind import is_designator

    assert not is_designator(token)


def test_section_code_is_card_query_not_designator():
    """Код раздела идёт через поиск_карты, не через обозначение."""
    from pdfgraph.page_kind import question_intent

    assert question_intent("072.00.00") == "поиск_карты"


@pytest.mark.parametrize("question", [
    "пункт 2.3.16",
    "13,5",
    "2024.11.14",
])
def test_false_numbers_are_not_designator_intent(question):
    from pdfgraph.page_kind import question_intent

    assert question_intent(question) != "обозначение"


def test_bare_drawing_number_is_designator_intent():
    from pdfgraph.page_kind import question_intent

    assert question_intent("8АТ.7714.150-01") == "обозначение"


def test_standard_is_designator_intent():
    from pdfgraph.page_kind import question_intent

    assert question_intent("ГОСТ 10054-82") == "обозначение"
    assert question_intent("ОСТ 1 31241-86") == "обозначение"
    assert question_intent("ТУ 38.101") == "обозначение"


def test_extract_designator_and_name():
    from pdfgraph.page_kind import extract_designator, extract_question_name

    assert extract_designator(
        "что за деталь 8АТ.7714.150-01") == "8АТ.7714.150-01"
    assert extract_designator("пункт 2.3.16") is None
    assert "МКТ-163" in extract_question_name(
        "какой номер у клапана МКТ-163")
