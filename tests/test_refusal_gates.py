# -*- coding: utf-8 -*-
"""Ф1.3б: тесты гейтов отказа по конкретике.

По карточке — тест на каждый гейт: гейт ловит свой случай (fail) и не
трогает корпусный (pass/na); плюс разбор вопроса (исключение чисел из
обозначений), конъюнкция отказа :func:`pdfgraph.gates.decide_refusal`
и интеграция с :func:`pdfgraph.answer.build_answer` (монки-патч
включённых гейтов и канала поиска).
"""

import pytest

import pdfgraph.gates as gates
from pdfgraph.gates import (
    GATE_NAMES,
    VERDICT_NA,
    VERDICT_PASS,
    decide_refusal,
    gate_aircraft_type,
    gate_designators,
    gate_numbers,
    gate_rare_term,
    question_aircraft_types,
    question_designators,
    question_significant_numbers,
    run_all_gates,
)
from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

#: Фикстурные листы одного документа «РЭ.pdf», тип Ми-8МТВ.
PAGES = [
    (10, "Давление масла в двигателе ТВ3-117 при прогреве — 4±0,5 кгс/см².",
     "072.00.00", "072.10.00"),
    (11, "Неисправности коробки приводов ВР-14. Замена и регулировка.",
     "063.00.00", ""),
    (12, "Таблица ограничений: высота полёта не более 500 м.",
     "010.00.00", ""),
    (13, "Раздел 065.50: работы по несущему винту в РТЭ.",
     "065.00.00", "065.50.00"),
]


@pytest.fixture
def store(tmp_path):
    """Мини-Store с четырьмя листами «РЭ.pdf» (тип листа — Ми-8МТВ)."""
    instance = Store(str(tmp_path / "gates.db"))
    try:
        for page, text, system, subsystem in PAGES:
            instance.upsert_page(
                "РЭ.pdf", page, "Ми-8МТВ", system, subsystem,
                text, normalize_for_search(text))
        yield instance
    finally:
        instance.close()


def make_fragment(page: int = 10, document: str = "РЭ.pdf") -> Fragment:
    """Фрагмент-лидер для монки-патча канала поиска."""
    return Fragment(
        document=document, page=page, system_code="072.00.00",
        subsystem_code="072.10.00", snippet="ТВ3-117", char_start=0,
        char_end=7, score=0.9, kind="text")


def test_numbers_in_designations_are_excluded():
    """Числа внутри обозначений не значимы; вне обозначений — значимы."""
    # «117» в «ТВ3-117» и «8» в «Ми-8МТВ-1» исключены, иначе числовой
    # гейт пройден всегда и бесполезен.
    assert question_designators(
        "проворот ротора двигателя ТВ3-117") == ["ТВ3-117"]
    assert question_significant_numbers(
        "проворот ротора двигателя ТВ3-117") == []
    assert question_designators(
        "крепёж вертолёта Ми-8МТВ-1 к ангару") == ["Ми-8МТВ-1"]
    assert question_significant_numbers(
        "крепёж вертолёта Ми-8МТВ-1 к ангару") == []
    # «115» в «КАУ-115АМ» исключено, отдельное «115» — значимо.
    assert question_designators(
        "давление за насосом КАУ-115АМ не более 115") == ["КАУ-115АМ"]
    assert question_significant_numbers(
        "давление за насосом КАУ-115АМ не более 115") == ["115"]
    # Десятичные значения — числа, не обозначения; коды разделов —
    # обозначения, не числа.
    assert question_designators("работам по разделу 065.50 РТЭ") == ["065.50"]
    assert question_significant_numbers(
        "работам по разделу 065.50 РТЭ") == []
    assert question_significant_numbers(
        "прибор показывает 3,5 атмосферы") == ["3.5"]
def test_gate_numbers_catches_and_leaves_corpus(store):
    """Числовой гейт: чужое число — провал, число корпуса — проход."""
    verdict, _ = gate_numbers(
        "не более 30 запусков двигателя подряд разрешено",
        store.page_text("РЭ.pdf", 10))
    assert verdict == "fail"
    # Корпусный: «4» с листа «4±0,5» найдена на границе цифр.
    verdict, _ = gate_numbers(
        "давление масла при прогреве 4", store.page_text("РЭ.pdf", 10))
    assert verdict == VERDICT_PASS
    verdict, _ = gate_numbers(
        "что входит в дублирующую гидросистему",
        store.page_text("РЭ.pdf", 10))
    assert verdict == VERDICT_NA


def test_gate_designators_catches_and_leaves_corpus(store):
    """Гейт обозначений: вымышленный агрегат — провал, корпусный — проход."""
    page10 = store.page_text("РЭ.pdf", 10)
    window = [store.page_text("РЭ.pdf", 9), store.page_text("РЭ.pdf", 11)]
    # Вымышленный агрегат — провал и без окна.
    verdict, _ = gate_designators(
        "крепление агрегата БВК-777 на осмотре", page10, window)
    assert verdict == "fail"
    # Корпусный: обозначение двигателя на лидере — проход.
    verdict, _ = gate_designators(
        "при прогреве двигателя ТВ3-117", page10, window)
    assert verdict == VERDICT_PASS
    # Окно ±1: «ВР-14» на соседнем листе (11), а лидер — лист 10.
    verdict, _ = gate_designators(
        "неисправности коробки приводов ВР-14", page10, window)
    assert verdict == VERDICT_PASS
    verdict, _ = gate_designators(
        "неисправности коробки приводов ВР-14", page10, [])
    assert verdict == "fail"
    # Код раздела: «065.50» на листе 13.
    verdict, _ = gate_designators(
        "работам по разделу 065.50 РТЭ", store.page_text("РЭ.pdf", 13), [])
    assert verdict == VERDICT_PASS
    # Без обозначений в вопросе — «na».
    verdict, _ = gate_designators(
        "что входит в дублирующую гидросистему", page10, window)
    assert verdict == VERDICT_NA


def test_gate_aircraft_type_catches_and_leaves_corpus(store):
    """Гейт типа ВС: чужой тип — провал, свой/не назван — не провал."""
    verdict, _ = gate_aircraft_type(
        "не убирается шасси на самолёте Ту-154 после взлёта", "Ми-8МТВ")
    assert verdict == "fail"
    verdict, _ = gate_aircraft_type(
        "какой назначенный ресурс лопастей несущего винта вертолёта Ми-2",
        "Ми-8МТВ")
    assert verdict == "fail"
    # Корпусный: тип совпадает («Ми-8» — семья «Ми-8МТВ»).
    verdict, _ = gate_aircraft_type(
        "не убирается шасси вертолёта Ми-8 на стоянке", "Ми-8МТВ")
    assert verdict == VERDICT_PASS
    verdict, _ = gate_aircraft_type(
        "давление масла при прогреве двигателя", "Ми-8МТВ")
    assert verdict == VERDICT_NA
    verdict, _ = gate_aircraft_type(
        "не убирается шасси вертолёта Ми-8", None)
    assert verdict == VERDICT_NA


def test_gate_rare_term_catches_and_leaves_corpus(store):
    """Гейт редкого термина: отсутствующий термин — провал, свой — нет."""
    page10 = store.page_text("РЭ.pdf", 10)
    verdict, _ = gate_rare_term(
        store, "опломбирование фильтра ФГО перед запуском", page10)
    assert verdict == "fail"
    # Корпусный: формы вопроса взяты с листа (нормализация поиска не
    # стемминг, падеж должен совпасть, как в вопросах золотого набора).
    verdict, _ = gate_rare_term(
        store, "давление масла в двигателе при прогреве", page10)
    assert verdict == VERDICT_PASS
    # В терминах вопроса пусто — «na».
    verdict, _ = gate_rare_term(store, "как?", page10)
    assert verdict == VERDICT_NA
def test_decide_refusal_conjunction_and_disablement(store):
    """Отказ — конъюнкция включённых; пустой список гейтов не отказывает."""
    fragment = make_fragment(page=10)
    # Вымышленный агрегат + чужое число: оба включённых гейта в провалах.
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(gates, "ENABLED_GATES",
                        ("numbers", "designators"), raising=True)
        failures = decide_refusal(
            store, "агрегат БВК-777 выдаёт не более 30", [fragment])
        assert [failure["gate"] for failure in failures] == [
            "numbers", "designators"]
        assert all("30" in failure["detail"] or "БВК-777" in failure["detail"]
                   for failure in failures)
        # Ни один гейт не включён — отказ не выносится.
        assert decide_refusal(store, "агрегат БВК-777", [fragment],
                              enabled=()) == []
        # Не все гейты включены: редкий термин вне списка — не в провалах.
        failures = decide_refusal(
            store, "опломбирование фильтра ФГО и агрегат БВК-777",
            [fragment], enabled=("designators",))
        assert [failure["gate"] for failure in failures] == ["designators"]
    # Пустая выдача не проверяется (там штатный отказ «нет подтверждений»).
    assert decide_refusal(store, "агрегат БВК-777", []) == []
    # Все вердикты считаются и вне включения (для полигона).
    verdicts = run_all_gates(store, "агрегат БВК-777 и 30", fragment)
    assert set(verdicts) == set(GATE_NAMES)
    assert verdicts["designators"][0] == "fail"
    assert verdicts["rare_term"][0] == "fail"
    assert verdicts["numbers"][0] == "fail"
    assert verdicts["aircraft_type"][0] == VERDICT_NA


def test_build_answer_refuses_by_gate(store, monkeypatch):
    """build_answer: включённый гейт с провалом даёт отказ с его именем."""
    from pdfgraph.answer import build_answer

    monkeypatch.setattr(gates, "ENABLED_GATES", ("numbers",), raising=True)
    monkeypatch.setattr(
        "pdfgraph.answer.search",
        lambda *args, **kwargs: [make_fragment(page=10)])
    answer = build_answer(
        store, "не более 30 запусков двигателя подряд разрешено",
        "Ми-8МТВ", search_mode="lexical")
    assert answer.refusal is True
    assert "numbers" in answer.reason
    assert "30" in answer.reason
    assert not answer.claims


def test_build_answer_leaves_corpus_question_alone(store, monkeypatch):
    """build_answer: корпусный вопрос со всеми гейтами не отклоняется."""
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import all_verified, verify_citations

    monkeypatch.setattr(gates, "ENABLED_GATES", GATE_NAMES, raising=True)
    monkeypatch.setattr(
        "pdfgraph.answer.search",
        lambda *args, **kwargs: [make_fragment(page=10)])
    answer = build_answer(
        store, "давление масла в двигателе при прогреве",
        "Ми-8МТВ", search_mode="lexical")
    assert answer.refusal is False
    assert answer.claims
    assert all_verified(verify_citations(answer, store))


def test_default_build_answer_without_gates(store, monkeypatch):
    """Без включённых гейтов поведение build_answer прежнее."""
    from pdfgraph.answer import build_answer

    monkeypatch.setattr(gates, "ENABLED_GATES", (), raising=True)
    monkeypatch.setattr(
        "pdfgraph.answer.search",
        lambda *args, **kwargs: [make_fragment(page=10)])
    answer = build_answer(
        store, "давление масла при прогреве двигателя",
        "Ми-8МТВ", search_mode="lexical")
    assert answer.refusal is False
    assert answer.claims


def test_enabled_gates_match_measured_polygon():
    """Включённые гейты = замер + доподтверждение владельца (Ф1.3б.1).

    Замер 2026-10-31: numbers (3 негатива, 0 корпусных) и
    aircraft_type (7 негативов, 0 корпусных); designators замер дал
    12 негативов при 2 задетых (e04/e06 — промахи ранжирования,
    правильного листа в топ-5 нет), включён решением владельца после
    приёмки; rare_term задевает 17 корпусных — не включён.
    Состав обновляется только новым замером и правкой ENABLED_GATES
    вместе с этим тестом.
    """
    assert tuple(sorted(gates.ENABLED_GATES)) == (
        "aircraft_type", "designators", "numbers")