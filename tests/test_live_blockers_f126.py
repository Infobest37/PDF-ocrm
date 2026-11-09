"""Тесты блокирующих фиксов живых вопросов (карточка Ф1.26).

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

1. Даты и номера изменений — не обозначения: «13.12.2024», «2024.12.13»,
   «изм. 5», «изм. от» не попадают в ``question_designators`` и не
   становятся значимыми числами в ``question_significant_numbers``.
2. Гомоглифы при сравнении: ``_designator_in_text`` находит «ТВ3-117»
   на листе с латинским «TB3-117» и кириллическим «ТВЗ-117» (З→3 в
   токене с цифрами); ``pages.text`` не меняется.
3. Словарь параметров norms: «высота», «износ», «толщина», «натяг»
   распознаются; «высота щёток не менее 8 мм» находит лист.
4. Веб-сессии: выбор варианта уточнения через ``selected_option`` +
   ``session_id`` принимает выбор и ищет дальше (второй ответ — не
   clarify), по образцу ``resolve_clarification_reply``.
"""

from __future__ import annotations

import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

# --- пункт 1: даты и «изм.» — не обозначения ---------------------------------


def test_dates_not_designators():
    from pdfgraph.gates import question_designators
    question = ("какое давление масла в двигателе ТВ3-117 "
                "изм. от 13.12.2024")
    assert question_designators(question) == ["ТВ3-117"]


def test_revision_number_not_designator():
    from pdfgraph.gates import question_designators
    question = "какие работы по несущему винту описаны в изм. 5 РТЭ"
    assert question_designators(question) == []


def test_reverse_date_not_designator():
    from pdfgraph.gates import question_designators
    question = "какое давление масла в двигателе по РЭ 2024.12.13"
    assert question_designators(question) == []


def test_dates_not_significant_numbers():
    from pdfgraph.gates import question_significant_numbers
    question = "что изменяет изм. 5 от 13.12.2024 к РЭ"
    assert question_significant_numbers(question) == []


def test_designator_gate_no_refusal_on_dated_question():
    """Вопрос с датой издания: дата не попала в обозначения (гейт
    designators проверяет только настоящие обозначения — РЭ1, кн.1)."""
    import re

    from pdfgraph.gates import question_designators
    question = ("какое давление масла должно быть на режимах выше малого "
                "газа по РЭ1 кн.1 изм. от 05.03.2026")
    designators = question_designators(question)
    assert not any(re.fullmatch(r"\d{1,2}\.\d{2}\.\d{4}", designator)
                   for designator in designators)
    assert "РЭ1" in designators


# --- пункт 2: гомоглифы при сравнении ---------------------------------------


def test_homoglyph_latin_found():
    from pdfgraph.gates import _designator_in_text
    text = "Двигатель TB3-117 078.00.5700. Руководство."
    assert _designator_in_text(text, "ТВ3-117") is True


def test_homoglyph_cyrillic_z_found():
    from pdfgraph.gates import _designator_in_text
    text = "двигатель ТВЗ-117 (двойник буквы З)"
    assert _designator_in_text(text, "ТВ3-117") is True


def test_homoglyph_text_untouched():
    from pdfgraph.gates import _designator_in_text
    text = "Двигатель TB3-117 078.00.5700."
    _designator_in_text(text, "ТВ3-117")
    assert text == "Двигатель TB3-117 078.00.5700."


def test_homoglyph_no_false_positive():
    from pdfgraph.gates import _designator_in_text
    # Латиница не «лечится» там, где обозначения нет вовсе.
    assert _designator_in_text("совсем другой текст", "ТВ3-117") is False


# --- пункт 3: словарь параметров norms ---------------------------------------


def test_norms_new_parameters_recognized():
    from pdfgraph.norms import is_value_question
    for question, parameter in (
            ("какая минимальная высота щёток на МВ-1200", "высота"),
            ("каков предельный износ втулки", "износ"),
            ("какая толщина накладки тормоза", "толщина"),
            ("какой натяг подшипника допускается", "натяг")):
        ok, found, _objects = is_value_question(question)
        assert ok is True, question
        assert found == parameter, question


def _norms_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "graph.db"))
    text = ("Высота щёток генератора не менее 8 мм. "
            "Износ накладки не более 2 мм.")
    store.upsert_page("Регламент ч.2.pdf", 141, "Ми-8МТВ", "", "", text,
                      normalize_for_search(text))
    return store


def test_norms_height_sheet_found(tmp_path):
    store = _norms_store(tmp_path)
    try:
        from pdfgraph.norms import norm_candidates
        candidates = norm_candidates(
            store, "какая минимальная высота щёток на МВ-1200",
            "высота", ["щёток"], limit=10)
        keys = [(document, page) for document, page, _ in candidates]
        assert ("Регламент ч.2.pdf", 141) in keys
    finally:
        store.close()


# --- пункт 4: веб-сессии, выбор варианта ------------------------------------


def test_web_option_selection_continues_search():
    """Выбор варианта принимается: второй ответ — не clarify (нет цикла)."""
    from fastapi.testclient import TestClient

    from pdfgraph.answer import Answer
    from pdfgraph.query import Fragment
    from pdfgraph.web.app import create_app

    def fragment(page: int) -> Fragment:
        return Fragment(document="РТЭ.pdf", page=page, system_code="",
                        subsystem_code="", snippet="текст",
                        char_start=0, char_end=5, score=0.9, kind="text")

    calls: list[str] = []

    def scripted_ask(store, question, aircraft_type):
        calls.append(question)
        if len(calls) == 1:
            return Answer(
                question=question, claims=[], fragments=[fragment(38)],
                refusal=False, reason="",
                clarification={
                    "decision": "clarify",
                    "question": "Уточните изделие",
                    "reason": "несколько изделий",
                    "options": [{
                        "id": "opt1", "label": "ТВ3-117",
                        "fragment_ids": [0],
                        "query_terms": ["ТВ3-117"],
                    }],
                })
        return Answer(question=question, claims=[], fragments=[fragment(38)],
                      refusal=False, reason="")

    with pytest.MonkeyPatch.context() as patch:
        # производственная цепочка не запускается: ask_fn инъецирован
        patch.setattr("pdfgraph.web.app._production_ask", scripted_ask,
                      raising=False)
        app = create_app(":memory:", ask_fn=scripted_ask)
        client = TestClient(app)

        first = client.post("/api/ask", json={
            "question": "какое давление масла в двигателе"})
        assert first.status_code == 200
        body = first.json()
        assert body["status"] == "clarify"
        assert body["exit_code"] == 3
        session_id = body["session_id"]
        assert session_id

        second = client.post("/api/ask", json={
            "question": "opt1", "session_id": session_id,
            "selected_option": "opt1"})
        assert second.status_code == 200
        body2 = second.json()
        assert body2["status"] == "answer", body2
        assert body2["exit_code"] == 0
        # Выбор принят: вариант ушёл в поиск обогащённым запросом,
        # а не как новый самостоятельный вопрос.
        assert calls[1] != "opt1"
        assert "ТВ3-117" in calls[1]
        assert len(calls) == 2          # цикла нет
