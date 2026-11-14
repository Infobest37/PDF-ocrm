# -*- coding: utf-8 -*-
"""Д1б: интент вопроса ↔ тип листа (RED до реализации ``page_kind_verdict``).

Контракты карточки:

- лидер нужного типа — ответ не меняется (``ok``);
- нужный тип есть в топ-10, но не на лидере — лист поднимается
  (``promote``), claims и evidence переезжают вместе с ним;
- нужного типа нет в топ-10, но он есть в корпусе — отказ
  «… в найденных листах нет. Ближайшее — <тип>, лист N»;
- нужного типа нет в корпусе вовсе — отказ «… в базе нет»;
- ``неполный`` — уточнение: 3–5 вариантов по типам листов и последний
  вариант «▸ Ни один не подходит — уточнить вопрос»;
- ``значение`` разрешает purpose, techcard и table;
- существующие отказ и уточнение не переопределяются.

Все тесты написаны до реализации: без ``page_kind_verdict`` они падают
на импорте.
"""

from __future__ import annotations

import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

DOC = "РТЭ кн.1.pdf"
AIR = "Ми-8МТВ"

TEXT_PURPOSE = ("НАЗНАЧЕНИЕ. Система предназначена для передачи "
                "информации на землю.")
TEXT_CARD = ("ТЕХНОЛОГИЧЕСКАЯ КАРТА № 5. Замена масла в двигателе. "
             "Содержание операции и технические требования.")
TEXT_FAULTS = ("ОТЫСКАНИЕ И УСТРАНЕНИЕ НЕИСПРАВНОСТЕЙ. НЕИСПРАВНОСТЬ: "
               "двигатель не запускается. ВОЗМОЖНАЯ ПРИЧИНА: нет топлива.")
TEXT_CATALOG = ("КАТАЛОГ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ. Рис. 1. "
                "Обозначение 8АТ.7714.150-01, наименование — крышка.")
TEXT_TABLE = ("ТАБЛИЦА 3. Допустимый люфт автомата перекоса: "
              "2,5 мм; 0,7 мм; 1,2 мм.")
TEXT_OTHER = "Общие сведения о вертолёте и его эксплуатации."


def _store(tmp_path, rows: list[tuple[int, str, str]]) -> Store:
    """Store с листами ``(page, text, page_kind)``; kind ``""`` — не размечен."""
    store = Store(str(tmp_path / "graph.db"))
    for page, text, kind in rows:
        store.upsert_page(DOC, page, AIR, "023.00.00", "", text,
                          normalize_for_search(text))
        if kind:
            store.set_page_kind(DOC, page, kind)
    return store


def _fragment(store: Store, page: int, score: float = 0.9) -> Fragment:
    text = store.page_text(DOC, page)
    return Fragment(
        document=DOC, page=page, system_code="023.00.00", subsystem_code="",
        snippet=text, char_start=0, char_end=len(text), score=score,
        kind="fts",
    )


# --- page_kind_verdict: базовые исходы --------------------------------------


def test_ok_when_leader_kind_allowed(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_PURPOSE, "purpose"),
        (2, TEXT_CARD, "techcard"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", fragments)

    assert outcome.decision == "ok"
    assert outcome.intent == "назначение"
    assert outcome.fragments == fragments


def test_promote_when_forbidden_leader_and_purpose_in_top10(tmp_path):
    """Каталог не может быть лидером: поднимается найденный purpose."""
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_PURPOSE, "purpose"),
        (2, TEXT_CATALOG, "catalog"),
    ])
    fragments = [_fragment(store, 2), _fragment(store, 1)]

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", fragments)

    assert outcome.decision == "promote"
    assert outcome.intent == "назначение"
    assert [fragment.page for fragment in outcome.fragments] == [1, 2]


def test_purpose_in_top10_does_not_raise_plain_leader(tmp_path):
    """Лидер не из запрещённых — не переставляем (решение владельца Д1б)."""
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_CARD, "techcard"),
        (2, TEXT_PURPOSE, "purpose"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", fragments)

    assert outcome.decision == "ok"
    assert [fragment.page for fragment in outcome.fragments] == [1, 2]


def test_annotate_when_forbidden_leader_and_no_purpose_in_top10(tmp_path):
    """Тип есть в корпусе, но не в выдаче — показ с пометкой, не отказ."""
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_CATALOG, "catalog"),
        (2, TEXT_CARD, "techcard"),
        (3, TEXT_PURPOSE, "purpose"),  # есть в корпусе, но не в топ-10
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", fragments)

    assert outcome.decision == "annotate"
    assert "Описания назначения не нашёл" in outcome.reason
    assert "Ближайшее — каталог деталей" in outcome.reason
    assert "лист 1" in outcome.reason


def test_refuse_when_kind_absent_in_corpus(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_OTHER, "other"),
        (2, TEXT_CARD, "techcard"),
    ])
    fragments = [_fragment(store, 2), _fragment(store, 1)]

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", fragments)

    assert outcome.decision == "refuse"
    assert "в базе нет" in outcome.reason
    assert "описание назначения" in outcome.reason


def test_clarify_for_incomplete_question(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_PURPOSE, "purpose"),
        (2, TEXT_CARD, "techcard"),
        (3, TEXT_FAULTS, "faults"),
        (4, TEXT_CATALOG, "catalog"),
    ])
    fragments = [_fragment(store, page) for page in (1, 2, 3, 4)]

    outcome = page_kind_verdict(store, "БСПИ", fragments)

    assert outcome.decision == "clarify"
    assert outcome.intent == "неполный"
    options = outcome.clarification["options"]
    content = [option for option in options if option["fragment_ids"]]
    assert 3 <= len(content) <= 5
    assert len({option["label"] for option in content}) == len(content)
    assert options[-1]["fragment_ids"] == []
    assert "Ни один не подходит" in options[-1]["label"]


@pytest.mark.parametrize("kind", ["purpose", "techcard", "table"])
def test_value_allows_three_kinds(tmp_path, kind):
    from pdfgraph.answer import page_kind_verdict

    text = {"purpose": TEXT_PURPOSE, "techcard": TEXT_CARD,
            "table": TEXT_TABLE}[kind]
    store = _store(tmp_path, [(1, text, kind)])
    fragments = [_fragment(store, 1)]

    outcome = page_kind_verdict(store, "какое давление масла в двигателе",
                                fragments)

    assert outcome.intent == "значение"
    assert outcome.decision == "ok"


def test_procedure_requires_techcard(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_FAULTS, "faults"),
        (2, TEXT_CARD, "techcard"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]

    outcome = page_kind_verdict(store, "как заменить масло в двигателе",
                                fragments)

    assert outcome.intent == "процедура"
    assert outcome.decision == "promote"
    assert outcome.fragments[0].page == 2


def test_defect_requires_faults(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_CARD, "techcard"),
        (2, TEXT_FAULTS, "faults"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]

    outcome = page_kind_verdict(store, "почему не запускается двигатель",
                                fragments)

    assert outcome.intent == "дефект"
    assert outcome.decision == "promote"
    assert outcome.fragments[0].page == 2


def test_designator_requires_catalog(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [
        (1, TEXT_CATALOG, "catalog"),
        (2, TEXT_CARD, "techcard"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]

    outcome = page_kind_verdict(store, "что за деталь 8АТ.7714.150-01",
                                fragments)

    assert outcome.intent == "обозначение"
    assert outcome.decision == "ok"


def test_unknown_intent_is_noop(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [(1, TEXT_CARD, "techcard")])
    fragments = [_fragment(store, 1)]

    outcome = page_kind_verdict(store, "пункт 2.3.16", fragments)

    assert outcome.intent == ""
    assert outcome.decision == "ok"
    assert outcome.fragments == fragments


def test_empty_fragments_is_noop(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [(1, TEXT_PURPOSE, "purpose")])

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", [])

    assert outcome.decision == "ok"
    assert outcome.fragments == []


def test_unmarked_page_kind_falls_back_to_classifier(tmp_path):
    from pdfgraph.answer import page_kind_verdict

    store = _store(tmp_path, [(1, TEXT_PURPOSE, "")])
    fragments = [_fragment(store, 1)]

    outcome = page_kind_verdict(store, "для чего нужен МУ-615А", fragments)

    assert outcome.decision == "ok"


# --- build_answer: встраивание вердикта -------------------------------------


def test_build_answer_annotates_forbidden_leader(tmp_path, monkeypatch):
    """Каталог-лидер на вопрос о назначении: ответ есть + честная пометка."""
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer

    store = _store(tmp_path, [
        (1, TEXT_CATALOG, "catalog"),
        (2, TEXT_CARD, "techcard"),
        (3, TEXT_PURPOSE, "purpose"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    monkeypatch.setattr("pdfgraph.gates.ENABLED_GATES", ())

    answer = build_answer(store, "для чего нужен МУ-615А",
                          search_mode="lexical")

    assert answer.refusal is False
    assert answer.claims
    assert "Описания назначения не нашёл" in answer.page_kind_missing
    assert "Ближайшее — каталог деталей" in answer.page_kind_missing


def test_build_answer_keeps_answer_when_purpose_not_in_top10(
        tmp_path, monkeypatch):
    """Тип есть в корпусе, но не в выдаче — прежний ответ без пометки."""
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer

    store = _store(tmp_path, [
        (1, TEXT_CARD, "techcard"),
        (2, TEXT_OTHER, "other"),
        (3, TEXT_PURPOSE, "purpose"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    monkeypatch.setattr("pdfgraph.gates.ENABLED_GATES", ())

    answer = build_answer(store, "для чего нужен МУ-615А",
                          search_mode="lexical")

    assert answer.refusal is False
    assert answer.page_kind_missing == ""
    assert answer.fragments[0].page == 1


def test_build_answer_promotes_and_remaps_claims(tmp_path, monkeypatch):
    """Процедура: лидер не карта, карта второй — поднимается, claims едут."""
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer

    store = _store(tmp_path, [
        (1, TEXT_FAULTS, "faults"),
        (2, TEXT_CARD, "techcard"),
    ])
    fragments = [_fragment(store, 1), _fragment(store, 2)]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    monkeypatch.setattr("pdfgraph.gates.ENABLED_GATES", ())

    answer = build_answer(store, "как заменить масло в двигателе",
                          search_mode="lexical")

    assert answer.refusal is False
    assert answer.fragments[0].page == 2
    assert answer.claims[0].text == store.page_text(DOC, 2)
    assert answer.claims[0].evidence == [0]


def test_build_answer_clarifies_incomplete_question(tmp_path, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer

    store = _store(tmp_path, [
        (1, TEXT_PURPOSE, "purpose"),
        (2, TEXT_CARD, "techcard"),
        (3, TEXT_FAULTS, "faults"),
        (4, TEXT_CATALOG, "catalog"),
    ])
    fragments = [_fragment(store, page) for page in (1, 2, 3, 4)]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    monkeypatch.setattr("pdfgraph.gates.ENABLED_GATES", ())

    answer = build_answer(store, "БСПИ", search_mode="lexical")

    assert answer.refusal is False
    assert answer.clarification is not None
    assert answer.clarification["decision"] == "clarify"
    assert "Ни один не подходит" in answer.clarification["options"][-1]["label"]


def test_build_answer_keeps_existing_refusal(tmp_path, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer

    store = _store(tmp_path, [(1, TEXT_PURPOSE, "purpose")])
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: [])

    answer = build_answer(store, "для чего нужен МУ-615А",
                          search_mode="lexical")

    assert answer.refusal is True
    assert answer.reason == "нет подтверждений в документах"


def test_build_answer_keeps_existing_clarification(tmp_path, monkeypatch):
    import pdfgraph.answer as answer_module
    import pdfgraph.gates as gates_module
    from pdfgraph.answer import build_answer

    store = _store(tmp_path, [(1, TEXT_PURPOSE, "purpose")])
    fragments = [_fragment(store, 1)]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    decision = {
        "decision": "clarify",
        "question": "Уточните вопрос",
        "reason": "гейты конкретики",
        "options": [{"id": "1", "label": "группа", "fragment_ids": [0],
                     "query_terms": []}],
    }
    monkeypatch.setattr(
        gates_module, "decide_outcome",
        lambda *_args, **_kwargs: gates_module.Outcome(
            "clarify", "гейты конкретики", "Уточните вопрос",
            decision["options"], fragments),
    )

    answer = build_answer(store, "для чего нужен МУ-615А",
                          search_mode="lexical")

    assert answer.clarification is not None
    assert answer.clarification["reason"] == "гейты конкретики"
