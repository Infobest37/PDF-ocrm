"""Ф2.10: вопрос о параметре — норма с числом и единицей.

Три исхода карточки (п. 3) + требования владельца:
- «12–16 мкм» в вопросе про давление — не норма давления (единица не
  того класса);
- «кг/см2» без знака ² — по-прежнему давление (единица берётся с
  листа дословно);
- «какое давление в гидросистеме» (норма есть) — обычный ответ;
- «работа автопилота» (без параметра) — без изменений;
- отказ в обоих случаях («числа нет» и «про другую систему») содержит
  листы во fragments и текст «искал на листах …».

Поиск, гейты, verify, веса, humanize не затронуты — правило работает
поверх готовой выдачи.
"""

from pdfgraph.answer import Answer, build_answer, param_norm_verdict
from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

DOC = "РЭ.pdf"
AIRCRAFT = "Ми-8МТВ"

#: Лист с нормой давления гидросистемы («кгс/см²», со знаком ²).
TEXT_GIDRO = "032.10.00 ГИДРОСИСТЕМА ОСНОВНАЯ давление 155 кгс/см²"
#: Лист с нормой давления масла без знака ² («кгс/см2»).
TEXT_MASLO = "071.10.00 ДВИГАТЕЛЬ ТВ3-117 МАСЛО давление 4,5 кгс/см2"
#: Лист без нормы давления: число есть, но единица «мкм» — не давление.
TEXT_MKM = "071.10.00 ДВИГАТЕЛЬ ТВ3-117 фильтр тонкость очистки 12–16 мкм"
#: Лист про напряжение сети (другой класс величины).
TEXT_VOLT = "024.10.00 напряжение сети 28,5 В"
#: Лист с моментом затяжки.
TEXT_TORQUE = "071.10.00 болт крепления момент затяжки 25 Н·м"


def _make_store(tmp_path, pages: dict[int, str]) -> Store:
    """Мини-граф: один документ, страницы с заданным текстом."""
    store = Store(str(tmp_path / "graph.db"))
    doc = store.add_node("document", DOC, label=DOC)
    for page, text in pages.items():
        sheet_id = store.add_node("sheet", f"{DOC}:{page}",
                                  document=DOC, page=page)
        store.add_edge(doc, sheet_id, "documented_on",
                       document=DOC, page=page)
        store.upsert_page(DOC, page, AIRCRAFT, "071.00.00", "", text,
                          normalize_for_search(text))
    return store


def _fragment(page: int) -> Fragment:
    """Фрагмент выдачи по листу DOC:page (сниппет неполон — как в жизни)."""
    return Fragment(document=DOC, page=page, system_code="071.00.00",
                    subsystem_code="", snippet="…обрезанный сниппет…",
                    char_start=0, char_end=10, score=1.0, kind="sheet")


# --- исход 1: число с единицей относится к параметру — обычный ответ --------

def test_norm_found_is_normal_answer(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_GIDRO})
    assert param_norm_verdict(
        store, "какое давление в гидросистеме", [_fragment(40)]) == ""


def test_unit_kgsm2_without_superscript_is_pressure(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_MASLO})
    assert param_norm_verdict(
        store, "какое давление масла в двигателе ТВ3-117",
        [_fragment(40)]) == ""


def test_voltage_and_torque_classes(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_VOLT, 41: TEXT_TORQUE})
    assert param_norm_verdict(
        store, "какое напряжение сети", [_fragment(40)]) == ""
    assert param_norm_verdict(
        store, "какой момент затяжки болта", [_fragment(41)]) == ""


def test_table_header_unit_without_number_is_norm(tmp_path):
    """Шапка таблицы «Давление масла в магистрали, кгс/см2» — единица с
    листа без числа рядом; это по-прежнему «норма на листе есть»."""
    store = _make_store(
        tmp_path, {40: "Таблица 19. Давление масла в магистрали, кгс/см2"})
    assert param_norm_verdict(
        store, "какое давление масла в магистрали", [_fragment(40)]) == ""


def test_pressure_unit_ocr_noise_is_norm(tmp_path):
    """OCR-шум в ²: «кгс/см?», «кгс/см*», «кгс/см”», «кго/см?» — по-прежнему
    давление (листы 38/75/76 РЭ1, РЭ кн. 3, ВР-14 золотого набора)."""
    for unit in ("кгс/см2", "кгс/см?", "кгс/см*", "кгс/см”", "кго/см?"):
        store = _make_store(tmp_path, {40: f"давление масла не ниже {unit}"})
        assert param_norm_verdict(
            store, "какое давление масла", [_fragment(40)]) == "", unit


def test_flow_per_hour_is_norm(tmp_path):
    """Расход в «л/ч» и «л/час» — норма расхода на листе есть."""
    store = _make_store(
        tmp_path,
        {40: "2.3.15 Расход масла двигателя Не более 0,3 л/ч"})
    assert param_norm_verdict(
        store, "каков максимальный расход масла двигателя",
        [_fragment(40)]) == ""
    store2 = _make_store(tmp_path, {40: "2.2.7 Расход масла, л/час"})
    assert param_norm_verdict(
        store2, "каков расход масла редуктора", [_fragment(40)]) == ""


def test_full_turn_of_handle_is_not_rpm_parameter(tmp_path):
    """«при полном обороте ручки центровки» (ед.ч.) — не параметр
    «обороты» (частота вращения); золотой e07."""
    store = _make_store(
        tmp_path,
        {40: "ручки центровки, полный оборот которых соответствует "
             "10° поворота вертолета, десять делений"})
    assert param_norm_verdict(
        store,
        "на какой угол поворачивается вертолёт при полном обороте "
        "ручки центровки", [_fragment(40)]) == ""


def test_number_on_third_page_counts(tmp_path):
    """Поиск ведётся на лидере и в топ-3: норма на третьем листе видна."""
    store = _make_store(tmp_path, {40: TEXT_MKM, 41: TEXT_MKM,
                                   42: TEXT_GIDRO})
    assert param_norm_verdict(
        store, "какое давление в гидросистеме",
        [_fragment(40), _fragment(41), _fragment(42)]) == ""


# --- «мкм» — не норма давления -----------------------------------------------

def test_mkm_is_not_pressure_norm(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_MKM})
    status = param_norm_verdict(
        store, "какое рабочее давление в двигателе ТВ3-117",
        [_fragment(40)])
    assert "искал на листах" in status
    assert "нормы не нашёл" in status


def test_wrong_unit_class_is_not_norm(tmp_path):
    """Вопрос про ток, на листе вольты — единица не того класса."""
    store = _make_store(tmp_path, {40: TEXT_VOLT})
    status = param_norm_verdict(
        store, "какой ток зарядки аккумулятора", [_fragment(40)])
    assert "искал на листах" in status


# --- исход 2: числа нет ни на одном листе ------------------------------------

def test_no_number_anywhere_status_lists_pages_and_docs(tmp_path):
    store = _make_store(tmp_path,
                        {40: TEXT_MKM,
                         41: "071.10.00 ДВИГАТЕЛЬ ТВ3-117 МАСЛО давление "
                             "контролируется по манометру"})
    status = param_norm_verdict(
        store, "какое рабочее давление в двигателе ТВ3-117",
        [_fragment(40), _fragment(41)])
    assert "искал на листах" in status
    assert f"{DOC} л. 40" in status and f"{DOC} л. 41" in status
    assert DOC in status  # документы, где искал


def test_empty_pages_silent(tmp_path):
    """Тексты листов недоступны — правило молчит (безопасный недоотказ)."""
    store = _make_store(tmp_path, {40: "текст без чисел"})
    empty = Store(str(tmp_path / "empty.db"))
    try:
        assert param_norm_verdict(
            empty, "какое давление в двигателе", [_fragment(40)]) == ""
    finally:
        empty.close()


# --- исход 3: число есть, но про другую систему ------------------------------

def test_other_system_note(tmp_path):
    store = _make_store(tmp_path, {42: TEXT_GIDRO})
    status = param_norm_verdict(
        store, "какое рабочее давление в двигателе ТВ3-117",
        [_fragment(42)])
    assert "искал на листах" in status
    assert "гидросистему" in status and "не про двигатель" in status


def test_other_system_without_identifiable_node(tmp_path):
    """Число есть, узел из вопроса на листе не упомянут, узнать «про что»
    лист нельзя — честная пометка «про другим узлом»."""
    store = _make_store(tmp_path, {40: "тормоз давление 155 кгс/см²"})
    status = param_norm_verdict(
        store, "какое рабочее давление в двигателе ТВ3-117",
        [_fragment(40)])
    assert "искал на листах" in status
    assert "не про двигатель" in status


def test_system_mentioned_on_hit_page_is_normal(tmp_path):
    """Узел из вопроса упомянут на листе с числом — обычный ответ."""
    store = _make_store(tmp_path, {40: TEXT_MASLO})
    assert param_norm_verdict(
        store, "какое рабочее давление в двигателе ТВ3-117",
        [_fragment(40)]) == ""


# --- гейт: вопросительное слово или «?» (решение владельца) ------------------

def test_no_question_word_rule_silent(tmp_path):
    """«минимальное давление масла» — не вопрос о норме, правило молчит."""
    store = _make_store(tmp_path, {40: "давление масла контролируется"})
    assert param_norm_verdict(
        store, "минимальное давление масла", [_fragment(40)]) == ""


def test_continuation_phrase_rule_silent(tmp_path):
    """«Продолжение табл. 18 давление масла» — не вопрос о норме."""
    store = _make_store(tmp_path, {40: "Таблица 18 Расход масла",
                                   41: "Продолжение табл. 18 давление масла"})
    assert param_norm_verdict(
        store, "Продолжение табл. 18 давление масла",
        [_fragment(40), _fragment(41)]) == ""


def test_question_mark_alone_triggers(tmp_path):
    """Без вопросительного слова, но с «?» в конце — правило работает."""
    store = _make_store(tmp_path, {40: "давление масла контролируется"})
    status = param_norm_verdict(
        store, "давление масла в системе?", [_fragment(40)])
    assert "искал на листах" in status


def test_chему_triggers(tmp_path):
    store = _make_store(tmp_path, {40: "давление масла контролируется"})
    status = param_norm_verdict(
        store, "чему равно давление масла", [_fragment(40)])
    assert "искал на листах" in status


# --- не вопрос о параметре — правило не работает -----------------------------

def test_non_parameter_question_untouched(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_MKM})
    assert param_norm_verdict(
        store, "работа автопилота", [_fragment(40)]) == ""


def test_current_word_stem_boundaries(tmp_path):
    """«поток» — не параметр «ток»; «тока» — параметр."""
    store = _make_store(tmp_path, {40: TEXT_VOLT})
    assert param_norm_verdict(
        store, "какой поток воздуха", [_fragment(40)]) == ""
    assert param_norm_verdict(
        store, "какой величины тока зарядки", [_fragment(40)]) != ""


# --- интеграция: build_answer конвертирует в отказ, листы сохраняются --------

def test_build_answer_refuses_and_keeps_fragments(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_MKM})
    answer = build_answer(store,
                          "какое рабочее давление в двигателе ТВ3-117")
    assert answer.refusal is True
    assert "искал на листах" in answer.reason
    assert answer.param_norm_missing == answer.reason
    assert answer.fragments  # листы выдачи при отказе сохранены
    assert answer.claims == []


def test_build_answer_normal_when_norm_present(tmp_path):
    store = _make_store(tmp_path, {40: TEXT_GIDRO})
    answer = build_answer(store, "какое давление в гидросистеме")
    assert answer.refusal is False
    assert answer.claims
    assert answer.param_norm_missing == ""


def test_refusal_dataclass_default_field():
    answer = Answer(question="вопрос", claims=[], fragments=[])
    assert answer.param_norm_missing == ""
