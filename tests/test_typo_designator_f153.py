r"""Ф1.5.3: слово с цифрой на конце — опечатка, не обозначение.

Кейс: вопрос «…в двигателе7» (опечатка) получал отказ «обозначение
двигателе7 в документации не встречается». Решение владельца: оба гейта
игнорируют цифры, приклеенные к буквам —
- is_designator: токен «^[а-яё]+\d$» длиннее 6 символов — не
  обозначение (у технических обозначений — ТВ3-117, ВР-14, Д-1697,
  035.10.00e — есть дефис, точка или заглавные);
- question_significant_numbers: число берётся только как отдельный
  токен, слева и справа от него нет букв; цифры внутри слов с буквами
  («двигателе7», «ТВ3», «ГТ40») числами вопроса не считаются.

Пин-тест tests/test_refusal_gates.py и ENABLED_GATES не тронуты.
"""

from pdfgraph.gates import (
    VERDICT_NA,
    gate_designators,
    gate_numbers,
    is_designator,
    question_designators,
    question_significant_numbers,
)

LEADER = "Лист про двигатель, без конкретики и чисел"


def test_typo_word_with_digit_is_not_designator():
    assert is_designator("двигателе7") is False
    assert question_designators("что проверить в двигателе7") == []


def test_long_typo_only_is_filtered():
    assert is_designator("маслорадиаторе3") is False


def test_real_designators_unchanged():
    for token in ("ТВ3-117", "ВР-14", "Д-1697", "035.10.00e", "ГТ40ПЧ8Б",
                  "КАУ-115АМ", "Ми-8МТВ-1", "065.50", "118.00"):
        assert is_designator(token) is True, token


def test_short_cyrillic_digit_token_is_designator():
    """До 6 символов включительно — обозначение, как раньше."""
    assert is_designator("ан2") is True
    assert is_designator("вд7") is True


def test_numbers_ignore_digits_glued_to_letters():
    assert question_significant_numbers("что проверить в двигателе7") == []
    assert question_significant_numbers("двигатель ТВ3") == []
    assert question_significant_numbers("ГТ40ПЧ8Б") == []


def test_standalone_numbers_extracted():
    assert question_significant_numbers("давление 5 кгс/см2") == ["5"]
    assert question_significant_numbers("не более 0,3 л/ч") == ["0.3"]


def test_designator_and_separate_number():
    assert question_designators("Д-1697 на листе 12") == ["Д-1697"]
    assert question_significant_numbers("Д-1697 на листе 12") == ["12"]


def test_tv3_designator_without_numbers():
    assert question_designators("в двигателе ТВ3-117") == ["ТВ3-117"]
    assert question_significant_numbers("в двигателе ТВ3-117") == []


def test_gates_na_for_typo_question():
    """«в двигателе7» — отказ не возникает ни по designators, ни по
    numbers: оба гейта «na» (обозначений и значимых чисел нет)."""
    question = "что проверить в двигателе7"
    numbers_verdict, _numbers_detail = gate_numbers(question, LEADER)
    designators_verdict, _designators_detail = gate_designators(
        question, LEADER)
    assert numbers_verdict == VERDICT_NA
    assert designators_verdict == VERDICT_NA


def test_gates_still_fail_for_real_designator():
    """«ТВ3-117» — гейты работают как раньше (обозначение не на лиде)."""
    question = "неисправность двигателя ТВ3-117"
    _verdict, detail = gate_designators(question, LEADER)
    assert "ТВ3-117" in detail
