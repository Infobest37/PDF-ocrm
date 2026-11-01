# -*- coding: utf-8 -*-
"""Ф1.4: третий исход — уточняющий вопрос вместо отказа.

Классификатор :func:`pdfgraph.gates.decide_outcome` решает исход по выдаче:
``answer`` — как раньше; ``clarify`` — только на провале гейтов конкретики
(в вопросе есть число или обозначение, на листе-лидере его нет, а тема
найдена); уточнение по форме вопроса убрано решением владельца (Ф1.4.1:
маркеры «как», «почему», «сколько»... не отличали полный вопрос от
неполного — раздел 11 .clinerules); обзорные формулировки («какие»,
«что входит»...) не уточняются никогда; ``refuse`` — чужая техника,
провал гейта aircraft_type, однородная выдача или пустая тема.
Плюс варианты уточнения (:func:`pdfgraph.gates.clarify_options`),
память диалога (:mod:`pdfgraph.session`) и код завершения CLI 3.
"""

import json
import pathlib

import pytest

import pdfgraph.cli as cli
import pdfgraph.gates as gates
from pdfgraph.gates import (
    CLARIFY_MAX_OPTIONS,
    CLARIFY_MIN_OPTIONS,
    CLARIFY_TOP_FRAGMENTS,
    clarify_options,
    decide_outcome,
    question_foreign_aircraft,
)
from pdfgraph.query import Fragment
from pdfgraph.session import (
    MAX_CONSECUTIVE_CLARIFIES,
    MAX_TURNS,
    load_context,
    save_context,
)
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

#: Фикстурные листы трёх документов, тип листа — Ми-8МТВ.
PAGES = [
    ("РЭ.pdf", 10, "Давление масла в двигателе ТВ3-117 при прогреве — 4±0,5 кгс/см².",
     "072.00.00", "072.10.00"),
    ("РЭ.pdf", 11, "Неисправности коробки приводов ВР-14. Замена и регулировка.",
     "063.00.00", ""),
    ("АТО.pdf", 3, "Замена масла в маслобаке. Раздел 072.",
     "072.00.00", "072.10.00"),
    ("АТО.pdf", 4, "Заправка шасси воздухом. Раздел 032.",
     "032.00.00", "032.10.00"),
    ("ДВР.pdf", 2, "Регламент работ по радиооборудованию. Проверка перед полётом.",
     "099.00.00", "099.10.00"),
]

#: Узлы с названиями разделов (как их пишет build).
NODES = [
    ("subsystem", "072.10.00", "Маслосистема двигателя"),
    ("system", "072.00.00", "Двигатель ТВ3-117"),
    ("subsystem", "032.10.00", "Шасси"),
    ("subsystem", "099.10.00", "Радиооборудование"),
]


def _fill_store(instance: Store) -> None:
    """Наполнить Store листами и узлами (общая часть фикстур)."""
    for document, page, text, system, subsystem in PAGES:
        instance.upsert_page(
            document, page, "Ми-8МТВ", system, subsystem,
            text, normalize_for_search(text))
    for kind, key, label in NODES:
        instance.add_node(kind, key, label)


@pytest.fixture
def store(tmp_path):
    """Мини-Store: три документа, четыре раздела, узлы с названиями."""
    instance = Store(str(tmp_path / "clarify.db"))
    try:
        _fill_store(instance)
        yield instance
    finally:
        instance.close()


@pytest.fixture
def db_store(tmp_path):
    """Путь к БД с теми же листами; для CLI-тестов Store закрыт заранее."""
    store_path = str(tmp_path / "graph.db")
    instance = Store(store_path)
    try:
        _fill_store(instance)
    finally:
        instance.close()
    return store_path


def make_fragment(document="РЭ.pdf", page=10, system="072.00.00",
                  subsystem="072.10.00", snippet="ТВ3-117",
                  score=0.9) -> Fragment:
    """Фрагмент выдачи с реальными кодами листа фикстуры."""
    return Fragment(
        document=document, page=page, system_code=system,
        subsystem_code=subsystem, snippet=snippet,
        char_start=0, char_end=len(snippet), score=score, kind="text")
# --- Пороги карточки заморожены --------------------------------------------


def test_clarify_thresholds_are_pinned():
    """Пороги уточнения меняются только новым замером полигона."""
    assert CLARIFY_TOP_FRAGMENTS == 10
    assert CLARIFY_MAX_OPTIONS == 3
    assert CLARIFY_MIN_OPTIONS == 2


def test_foreign_aircraft_brands_are_pinned():
    """Словарь чужой западной техники — замороженный (пин-тест Ф1.4)."""
    assert gates.FOREIGN_AIRCRAFT_RE.pattern == (
        "(?<![а-яёa-z])(боинг|аэробус|эйрбас|сессна|эмбраер|фоккер|локхид|"
        "дуглас|boeing|airbus|cessna|embraer|fokker|lockheed|douglas)")


def test_question_foreign_aircraft_recognizes_western_brands():
    """Словарь ловит Боинг/Аэробус и латиницу, свои Ми-8 не трогает."""
    assert question_foreign_aircraft(
        "почему не запускается Боинг 747") == ["боинг"]
    assert question_foreign_aircraft(
        "Airbus A320 не убирается шасси") == ["airbus"]
    assert question_foreign_aircraft(
        "сессна и эмбраер без документов") == ["сессна", "эмбраер"]
    assert question_foreign_aircraft("аэропорт Шереметьево открыт") == []
    assert question_foreign_aircraft(
        "не убирается шасси вертолёта Ми-8МТВ") == []
# --- Классификатор исхода answer/clarify/refuse ----------------------------


def test_decide_outcome_answer_for_full_question(store):
    """Полный вопрос (слоты есть, гейты пройдены) — прежний ответ."""
    outcome = decide_outcome(
        store, "давление масла при прогреве 4", [make_fragment()])
    assert outcome.decision == "answer"
    assert outcome.options == []
    assert outcome.reason == ""


def test_decide_outcome_refuses_on_empty_recall(store):
    """Пустая выдача — прежний штатный отказ."""
    outcome = decide_outcome(store, "что сказано про обслуживание?", [])
    assert outcome.decision == "refuse"
    assert outcome.reason == "нет подтверждений в документах"


def test_decide_outcome_refuses_foreign_western_type(store):
    """Чужая западная техника — отказ даже при непустой выдаче (Ф1.4)."""
    outcome = decide_outcome(
        store, "почему не запускается Боинг 747", [make_fragment()])
    assert outcome.decision == "refuse"
    assert "чужая техника" in outcome.reason
    assert "боинг" in outcome.reason


def test_decide_outcome_refuses_foreign_type_in_latin(store):
    """Латиница чужого бренда тоже даёт отказ."""
    outcome = decide_outcome(
        store, "Airbus A320 не убирается шасси", [make_fragment()])
    assert outcome.decision == "refuse"
    assert "airbus" in outcome.reason


def test_decide_outcome_refuses_aircraft_type_gate(store):
    """Провал гейта aircraft_type (Ту в вопросе, Ми на листе) — прежний отказ."""
    outcome = decide_outcome(
        store, "не убирается шасси Ту-154", [make_fragment()])
    assert outcome.decision == "refuse"
    # aircraft_type падает в конъюнкции; рядом может упасть и designators
    # («Ту-154» — токен буква+цифра, на лиде его нет). Проверяем, что
    # отказ с указанием гейта, а не единственность провала.
    assert "aircraft_type" in outcome.reason


def test_decide_outcome_clarifies_when_concreteness_missed(store):
    """Провал numbers, выдача от трёх групп — переспрос с вариантами."""
    fragments = [
        make_fragment(score=0.95),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
        make_fragment(document="АТО.pdf", page=4, system="032.00.00",
                      subsystem="032.10.00", snippet="Заправка шасси",
                      score=0.8),
    ]
    outcome = decide_outcome(store, "не более 30 запусков подряд", fragments)
    assert outcome.decision == "clarify"
    options = outcome.options
    assert CLARIFY_MIN_OPTIONS <= len(options) <= CLARIFY_MAX_OPTIONS
    assert [option["id"] for option in options] == ["1", "2", "3"]
    # ярлык = имя документа без расширения + заголовок раздела из nodes
    assert options[0]["label"] == "РЭ — Маслосистема двигателя"
    assert options[1]["label"] == "АТО — Маслосистема двигателя"
    assert options[2]["label"] == "АТО — Шасси"
    # группы упорядочены по суммарному весу выдачи
    assert options[0]["fragment_ids"] == [0]
    assert options[0]["query_terms"] == ["072.10.00"]
    assert options[2]["query_terms"] == ["032.10.00"]
    assert outcome.fragments == fragments[:CLARIFY_TOP_FRAGMENTS]


def test_decide_outcome_refuses_when_output_homogeneous(store):
    """Провал numbers, но выдача однородна — отказ, уточнять нечем."""
    outcome = decide_outcome(
        store, "не более 30 запусков подряд", [make_fragment()])
    assert outcome.decision == "refuse"
    assert "numbers" in outcome.reason
    assert "выдача однородна" in outcome.reason


def test_decide_outcome_answers_slotless_action_question(store):
    """Вопрос-действие («почему») без слотов — ответ, не переспрос (Ф1.4.1).

    Решение владельца: уточнение только при провале гейтов конкретики;
    форма вопроса не отличает полный вопрос от неполного (раздел 11
    .clinerules).
    """
    fragments = [
        make_fragment(),
        make_fragment(document="АТО.pdf", page=4, system="032.00.00",
                      subsystem="032.10.00", snippet="Заправка шасси",
                      score=0.8),
        make_fragment(document="ДВР.pdf", page=2, system="099.00.00",
                      subsystem="099.10.00", snippet="Регламент",
                      score=0.7),
    ]
    outcome = decide_outcome(store, "почему глохнет двигатель?", fragments)
    assert outcome.decision == "answer"
    assert outcome.options == []
    assert outcome.clarify_question == ""
    # исход не зависит от формы вопроса: имя агрегата — тот же ответ
    outcome = decide_outcome(store, "состояние двигателя", fragments)
    assert outcome.decision == "answer"


def test_decide_outcome_answers_slotless_homogeneous_output(store):
    """Без слотов — прежний ответ без переспроса (решение Ф1.4.1)."""
    fragments = [
        make_fragment(),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.8),
    ]
    outcome = decide_outcome(store, "что сказано про обслуживание?", fragments)
    assert outcome.decision == "answer"


def test_decide_outcome_answers_overview_heterogeneous(store):
    """Обзорная формулировка не уточняется даже на разнородной выдаче (Ф1.4).

    Решение владельца: «какие»/«что входит»... — перечень по листу,
    исход answer, если тема найдена; переспрос сломал бы PROD-тесты
    tests/test_answer_nl_question.py и tests/test_answer_known_term.py.
    """
    fragments = [
        make_fragment(),
        make_fragment(document="АТО.pdf", page=4, system="032.00.00",
                      subsystem="032.10.00", snippet="Заправка шасси",
                      score=0.8),
        make_fragment(document="ДВР.pdf", page=2, system="099.00.00",
                      subsystem="099.10.00", snippet="Регламент",
                      score=0.7),
    ]
    outcome = decide_outcome(
        store, "какие подсистемы у двигателя?", fragments)
    assert outcome.decision == "answer"
    assert outcome.options == []


def test_question_overview_markers_frozen():
    """Маркеры обзорных формулировок заморожены (решение владельца, Ф1.4).

    Обзорные формулировки («перечислить состав») не уточняются никогда.
    Уточнение по форме вопроса («как», «сколько», ...) убрано решением
    владельца Ф1.4.1: замер полигона Ф1.4 показал, что маркеры формы не
    отличают полный вопрос от неполного (раздел 11 .clinerules). Менять
    список — только явным решением владельца (вместе с пин-тестом).
    """
    assert gates.OVERVIEW_QUESTION_RE.pattern == (
        r"(?i)\b(?:какие|из\s+чего\s+состоит|из\s+чего\s+состоят"
        r"|что\s+входит|что\s+включает|перечисли|список)\b")
    # обзорные ловятся в любом регистре и во всех формулировках
    assert gates.question_overview("какие подсистемы у двигателя")
    assert gates.question_overview("Что входит в дублирующую гидросистему?")
    assert gates.question_overview("из чего состоит маслобак")
    assert gates.question_overview("из чего состоят фильтры")
    assert gates.question_overview("Что включает дублирующая система?")
    assert gates.question_overview("перечисли настройки")
    assert gates.question_overview("список регламентов")
    # «как» не цепляет «какие»/«какое» (границы слов)
    assert not gates.question_overview("какое давление масла при прогреве?")
# --- Ф1.5.1: отказ при отсутствии обозначения нигде в корпусе --------------


def test_designator_absent_from_corpus_refuses(store):
    """Обозначения нет нигде в корпусе — отказ, а не переспрос (Ф1.5.1).

    «КАУ-115АМ» не встречается ни на одном листе фикстурного Store:
    переспрос предлагал бы разделы, где запрошенного агрегата нет.
    Решение владельца Ф1.5: обозначение не встречается нигде в корпусе —
    отказ; встречается не на листе-лидере — прежний переспрос.
    """
    fragments = [
        make_fragment(),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
        make_fragment(document="ДВР.pdf", page=2, snippet="Регламент",
                      score=0.8),
    ]
    outcome = decide_outcome(
        store, "какой комод КАУ-115АМ применяется и в чём его место?",
        fragments)
    assert outcome.decision == "refuse"
    assert outcome.options == []
    assert outcome.clarify_question == ""
    assert "КАУ-115АМ" in outcome.reason
    assert "в документации не встречается" in outcome.reason
    # Гейт обозначений назван первым сообщением — конъюнкция не потеряна.
    assert "designators" in outcome.reason


def test_designator_corpus_present_not_on_leader_stays_clarify(store):
    """«ВР-14» в корпусе есть (РЭ:11), на листе-лидере нет — переспрос.

    Правило различает «нет нигде» (отказ) и «есть в корпусе, но не
    рядом с лидером» (переспрос): второе загрублять нельзя (Ф1.5.1).
    Лидер — АТО:4 (окно АТО:3), в обоих нет ВР-14; ВР-14 живёт на
    РЭ:11 — далеко от лидера, но внутри корпуса.
    """
    fragments = [
        make_fragment(document="АТО.pdf", page=4, system="032.00.00",
                      subsystem="032.10.00", snippet="Заправка шасси",
                      score=0.95),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
        make_fragment(document="ДВР.pdf", page=2, snippet="Регламент",
                      score=0.8),
    ]
    # корпусная проверка: ВР-14 действительно в корпусе
    assert gates.designator_in_corpus(store, "ВР-14") is True
    outcome = decide_outcome(
        store,
        "неисправности коробки приводов ВР-14 и как их устранить?",
        fragments)
    assert outcome.decision == "clarify"
    assert outcome.options
    assert "не встречаются" not in outcome.reason
    # Провал гейта остаётся в сообщении: рядом с лидером АТО:4 ВР-14 нет.
    assert "ВР-14" in outcome.reason


def test_designator_corpus_presence_predicate(store):
    """designator_in_corpus: то же признание вхождения, что у гейта.

    Дословное вхождение на листе («ВР-14» на РЭ:11, «ТВ3-117» на РЭ:10)
    — «встречается»; отсутствующие «КАУ-115АМ» и «БВК-777» — нет.
    """
    assert gates.designator_in_corpus(store, "ВР-14") is True
    assert gates.designator_in_corpus(store, "ТВ3-117") is True
    assert gates.designator_in_corpus(store, "КАУ-115АМ") is False
    assert gates.designator_in_corpus(store, "БВК-777") is False
    assert gates.corpus_absent_designators(
        store, "комод КАУ-115АМ рядом с ВР-14") == ["КАУ-115АМ"]
    assert gates.corpus_absent_designators(
        store, "неисправности коробки приводов ВР-14") == []


# --- Ф1.5.2: тип ВС в вопросе — по метке листа, а не по строке ---------------


def test_primary_aircraft_type_keys_reads_page_labels(store):
    """«Основные» типы — метки листов: у фикстуры единственный «Ми-8МТВ»."""
    assert gates.primary_aircraft_type_keys(store) == frozenset({("ми", "8")})


def test_aircraft_types_excluded_from_corpus_string_check(store):
    """Типы ВС строки корпуса не проверяют («корпус-правило» не для типов).

    «Ту-154» — тип: упоминание его в тексте листа на правило корпуса
    не влияет (решение владельца Ф1.5.2); «Д-30» — агрегат, строковая
    проверка Ф1.5.1 остаётся. Список типов без листов-основных —
    дословные формы вопроса.
    """
    assert gates.aircraft_types_without_primary(
        store, "ресурс лопастей несущего винта вертолёта Ми-26") == ["Ми-26"]
    assert gates.aircraft_types_without_primary(
        store, "не убирается шасси Ми-8МТВ при рулении") == []
    assert gates.aircraft_types_without_primary(store, "давление масла?") == []
    assert gates.corpus_absent_designators(
        store, "давление в двигателе Д-30 вертолёта Ту-154") == ["Д-30"]


def test_aircraft_type_mention_without_primary_sheet_refuses(store):
    """Тип упомянут в чужом документе — отказ «документации по типу нет».

    Лист-примечание помечен «Ми-8МТВ», но содержит слово «Ми 26» —
    это упоминание в тексте: для типа оно не основание ни отказа,
    ни уточнения (Ф1.5.2). Тип проверяется меткой aircraft_type:
    листа с основной меткой «Ми-26» в базе нет — отказ вместо
    прежнего уточнения.
    """
    mention = "Примечание. Дополнения к руководству вертолёта Ми 26."
    store.upsert_page(
        "Доп.pdf", 368, "Ми-8МТВ", "065.00.00", "",
        mention, normalize_for_search(mention))
    fragments = [
        make_fragment(score=0.95),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
        make_fragment(document="ДВР.pdf", page=2, snippet="Регламент",
                      score=0.8),
    ]
    # упоминание в тексте корпусом признаётся — исход от него не зависит
    assert gates.designator_in_corpus(store, "Ми-26") is True
    outcome = decide_outcome(
        store, "ресурс лопастей несущего винта вертолёта Ми-26", fragments)
    assert outcome.decision == "refuse"
    assert outcome.options == []
    assert "aircraft_type" in outcome.reason
    assert "документации по типу Ми-26 в базе нет" in outcome.reason
    assert "не встречается" not in outcome.reason
    assert "Уточните" not in outcome.clarify_question


def test_aircraft_type_with_primary_sheet_uses_gate_path(store):
    """Свой тип «Ми-8МТВ» (метки есть) идёт прежним путём, без отказа.

    Тип строково не проверяется вовсе: рядом с лидером «Ми-8МТВ» нет
    и в корпусе-тексте его нет, но листы с меткой есть — отказ по типу
    невозможен (Ф1.5.2). «ВР-14» в корпусе есть — уточнение (Ф1.5.1).
    """
    fragments = [
        make_fragment(score=0.95),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
        make_fragment(document="ДВР.pdf", page=2, snippet="Регламент",
                      score=0.8),
    ]
    outcome = decide_outcome(
        store, "неисправности коробки приводов ВР-14 вертолёта Ми-8МТВ?",
        fragments)
    assert outcome.decision == "clarify"
    assert outcome.options
    assert "документации по типу" not in outcome.reason
    assert "Ми-8МТВ" in outcome.reason      # провал гейта остаётся в тексте
    assert "не встречаются" not in outcome.reason


def test_aircraft_type_refusal_dominates_corpus_refusal(store):
    """Тип без листов-основных отвечает отказом, строка корпуса не участвует.

    Смешанный вопрос: «Ми-26» — тип без основной метки, «БВК-777» —
    агрегат, отсутствующий и строково. Отказ отвечает типом (Ф1.5.2),
    а не строковым «не встречается», и уточнение не предлагает.
    """
    fragments = [
        make_fragment(score=0.95),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
    ]
    outcome = decide_outcome(
        store, "ресурс несущего винта и агрегата БВК-777 вертолёта Ми-26?",
        fragments)
    assert outcome.decision == "refuse"
    assert "документации по типу Ми-26 в базе нет" in outcome.reason
    assert "не встречается" not in outcome.reason


# --- Варианты уточнения: разбиение и ярлыки --------------------------------


def test_clarify_options_cap_three_by_weight(store):
    """Четыре группы — берутся три, варианты идут по убыванию веса."""
    fragments = [
        make_fragment(score=0.95),
        make_fragment(document="АТО.pdf", page=3, snippet="Замена масла",
                      score=0.9),
        make_fragment(document="АТО.pdf", page=4, system="032.00.00",
                      subsystem="032.10.00", snippet="Заправка шасси",
                      score=0.85),
        make_fragment(document="ДВР.pdf", page=2, system="099.00.00",
                      subsystem="099.10.00", snippet="Регламент",
                      score=0.7),
    ]
    top, options = clarify_options(store, fragments)
    assert len(options) == 3                     # четвёртая группа отброшена
    assert [option["label"] for option in options] == [
        "РЭ — Маслосистема двигателя", "АТО — Маслосистема двигателя",
        "АТО — Шасси"]
    assert [option["fragment_ids"] for option in options] == [[0], [1], [2]]
    assert top == fragments[:CLARIFY_TOP_FRAGMENTS]


def test_clarify_option_labels_fall_back_to_code_and_document_name(store):
    """Без узла — «код 063.00.00»; без кода вовсе — имя файла."""
    fragments = [
        make_fragment(page=11, system="063.00.00", subsystem="",
                      snippet="Коробка приводов ВР-14", score=0.9),
        make_fragment(document="ЛЕТО.pdf", page=1, system="",
                      subsystem="", snippet="Что-то ещё", score=0.8),
    ]
    _, options = clarify_options(store, fragments)
    assert [option["label"] for option in options] == [
        "РЭ — код 063.00.00", "ЛЕТО"]
    assert options[0]["query_terms"] == ["063.00.00"]
    assert options[1]["query_terms"] == []
# --- Память диалога (pdfgraph.session) -------------------------------------


def test_session_constants_are_pinned():
    """Лимиты сессии заморожены карточкой Ф1.4."""
    assert MAX_TURNS == 3
    assert MAX_CONSECUTIVE_CLARIFIES == 2


def test_session_record_caps_to_three_turns(tmp_path):
    session = load_context(str(tmp_path / "graph.db"), "d1")
    for index in range(4):
        session.record(f"вопрос {index}", "answer")
    assert len(session.turns) == MAX_TURNS
    assert [turn.question for turn in session.turns] == [
        "вопрос 1", "вопрос 2", "вопрос 3"]


def test_session_refine_option_id_terms_and_freetext(tmp_path):
    session = load_context(str(tmp_path / "graph.db"), "d1")
    assert session.refine("прямой вопрос") == "прямой вопрос"  # нет уточнения
    session.record(
        "как заменить масло?", "clarify",
        options=[
            {"id": "1", "label": "РЭ — Маслосистема двигателя",
             "query_terms": ["072.10.00"], "fragment_ids": [0]},
            {"id": "2", "label": "АТО — Регламент", "query_terms": []},
        ])
    # выбор по id добавляет query_terms варианта
    assert session.refine("1") == "как заменить масло? 072.10.00"
    # повторный выбор того же варианта не плодит дубль термина
    assert session.refine("1") == "как заменить масло? 072.10.00"
    # пустые query_terms — fallback на ярлык (как в Conversation GUI)
    assert session.refine("2") == "как заменить масло? АТО — Регламент"
    # свободный текст добавляется к теме уточнения
    assert session.refine("затронь предохранительный клапан") == (
        "как заменить масло? затронь предохранительный клапан")


def test_session_refine_skips_merge_after_answer(tmp_path):
    session = load_context(str(tmp_path / "graph.db"), "d1")
    session.record(
        "что настроить?", "clarify",
        options=[{"id": "1", "label": "РЭ", "query_terms": ["065.50.00"]}])
    session.record("настроить давление", "answer")
    assert session.refine("новый вопрос") == "новый вопрос"


def test_session_clarify_limit_two_in_a_row(tmp_path):
    session = load_context(str(tmp_path / "graph.db"), "d1")
    session.record("первый", "clarify")
    assert not session.clarify_limit_reached()
    session.record("второй", "clarify")
    assert session.clarify_limit_reached()
    session.record("третий", "answer")
    assert not session.clarify_limit_reached()


def test_session_persists_and_strips_fragment_ids(tmp_path):
    store_path = tmp_path / "graph.db"
    session = load_context(str(store_path), "d1")
    session.record(
        "вопрос про винт", "clarify", reason="нет конкретики",
        options=[{"id": "1", "label": "РЭ — Винт",
                  "query_terms": ["065.50.00"], "fragment_ids": [3]}])
    save_context(session)
    assert (store_path.parent / "dialog-d1.json").is_file()
    reloaded = load_context(str(store_path), "d1")
    assert reloaded.turns[-1].question == "вопрос про винт"
    assert reloaded.turns[-1].reason == "нет конкретики"
    assert "fragment_ids" not in reloaded.turns[-1].options[0]
    assert reloaded.refine("1") == "вопрос про винт 065.50.00"


def test_session_rejects_bad_context_id(tmp_path):
    store_path = str(tmp_path / "graph.db")
    with pytest.raises(ValueError):
        load_context(store_path, "плохой id")
    with pytest.raises(ValueError):
        load_context(store_path, "")
# --- CLI: код 3, текстовый и JSON вывод, --context -------------------------


def fake_outcome(store, question, fragments):
    """Стабильный clarify для проверки контракта CLI (код 3)."""
    return gates.Outcome(
        decision="clarify",
        reason="проверка контракта clarify",
        clarify_question="Уточните, какой вариант имеется в виду.",
        options=[
            {"id": "1", "label": "РЭ — Маслосистема двигателя",
             "fragment_ids": [0], "query_terms": ["072.10.00"]},
            {"id": "2", "label": "АТО — Регламент",
             "fragment_ids": [], "query_terms": []},
        ],
        fragments=list(fragments),
    )


def test_cli_clarify_exit_code_3_json_and_text(db_store, monkeypatch, capsys):
    """clarify в JSON как answer.clarification, в тексте — блок вариантов."""
    monkeypatch.setattr(
        "pdfgraph.answer.search", lambda *args, **kwargs: [make_fragment()])
    monkeypatch.setattr(gates, "decide_outcome", fake_outcome)
    assert cli.main(["ask", "как заменить масло?", "--store", db_store,
                     "--json", "--search-mode", "lexical"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["refusal"] is False
    assert payload["claims"] == []
    assert payload["clarify"] == {
        "question": "Уточните, какой вариант имеется в виду.",
        "options": [
            {"id": "1", "label": "РЭ — Маслосистема двигателя",
             "fragment_ids": [0], "query_terms": ["072.10.00"]},
            {"id": "2", "label": "АТО — Регламент",
             "fragment_ids": [], "query_terms": []},
        ],
    }
    assert cli.main(["ask", "как заменить масло?", "--store", db_store,
                     "--search-mode", "lexical"]) == 3
    out = capsys.readouterr().out
    assert "Требуется уточнение" in out
    assert "1 — РЭ — Маслосистема двигателя" in out
    assert "2 — АТО — Регламент" in out


def test_cli_context_session_file_and_limit(db_store, monkeypatch, capsys):
    """--context ведёт файл сессии рядом с БД; 3-е уточнение подряд — отказ."""
    monkeypatch.setattr(
        "pdfgraph.answer.search", lambda *args, **kwargs: [make_fragment()])
    monkeypatch.setattr(gates, "decide_outcome", fake_outcome)
    assert cli.main(["ask", "как заменить масло?", "--store", db_store,
                     "--json", "--search-mode", "lexical",
                     "--context", "dialog1"]) == 3
    capsys.readouterr()  # опустошаем вывод первого запроса
    assert cli.main(["ask", "1", "--store", db_store, "--json",
                     "--search-mode", "lexical",
                     "--context", "dialog1"]) == 3
    session_path = pathlib.Path(db_store).with_name("dialog-dialog1.json")
    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert [turn["question"] for turn in data["turns"]] == [
        "как заменить масло?", "как заменить масло? 072.10.00"]
    # третье уточнение подряд — детерминированный отказ с причиной
    capsys.readouterr()  # опустошаем вывод второго запроса
    assert cli.main(["ask", "1", "--store", db_store, "--json",
                     "--search-mode", "lexical",
                     "--context", "dialog1"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["refusal"] is True
    assert "лимит уточнений" in payload["reason"]
    assert "clarify" not in payload
    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert len(data["turns"]) == 3
    assert data["turns"][-1]["outcome"] == "refuse"


def test_cli_context_bad_id_exit_2(db_store, capsys):
    """Неверный формат --context — ошибка среды (код 2), до поиска."""
    assert cli.main(["ask", "вопрос", "--store", db_store, "--json",
                     "--context", "плохой ид"]) == 2
    assert "--context" in capsys.readouterr().err


def test_cli_full_question_answers_code0(db_store, capsys):
    """Полный вопрос проходит гейты сразу — прежний ответ, код 0."""
    rc = cli.main(["ask", "давление масла при прогреве 4", "--store", db_store,
                   "--json", "--search-mode", "lexical"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["refusal"] is False
    assert "clarify" not in payload


def test_cli_refusal_stays_code1_without_clarify(db_store, capsys):
    """Провал конкретики на однородной выдаче — прежний отказ, код 1."""
    rc = cli.main(["ask", "не более 30 запусков подряд", "--store", db_store,
                   "--json", "--search-mode", "lexical"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["refusal"] is True
    assert payload["claims"] == []
    assert "clarify" not in payload