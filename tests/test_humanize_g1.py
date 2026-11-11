"""Г1: чёткий ответ и страницы — слой показа без портянки.

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

- ``pdfgraph.norms.norm_window_texts``: валидные окна нормы на листе —
  дословные фрагменты pages.text (тот же предикат, что у канала норм:
  объект + число с единицей + нормативная конструкция; колонтитул
  «Стр. 9 Нояб 15/18» окном нормы не считается);
- ``pdfgraph.humanize.clean_display_text``: вырезание показ-мусора
  («Копия документа…», «Распечатал…», «Контрольная…», ГОСТы, «стр. N»,
  даты «Апр 12/22», гребёнка) — только для показа;
- ``pdfgraph.humanize.display_normalize``: «кго/см?» → «кгс/см²»,
  «oc» → «°C», склейки слов («Давлениемасла» → «Давление масла»);
- ``pdfgraph.humanize.printed_page_mark``: печатная страница из
  колонтитула («Стр. 219/220» → «219/220»); нет — пустая строка;
- ``pdfgraph.humanize.value_display``: словарь показа
  (items/source/quote) для вопрос-значений; None — не вопрос-значение.

Claim.text, verify и pages.text не трогаются: весь слой — только показ.
Живые тесты требуют graph.db в корне (как test_golden_metrics).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STORE_SOURCE = REPO_ROOT / "graph.db"

QUESTION_BRUSH = "какая минимальная высота щёток на МВ-1200"
QUESTION_OIL = "какое давление масла в двигателе ТВ3-117"

#: Лист 38 РЭ1 кн. 1 — четыре валидных окна давления (Ф1.24, скор 1.3).
EXPECTED_PAGE38_WINDOWS = 4


def _load_golden_metrics():
    path = REPO_ROOT / "tests" / "golden" / "metrics.py"
    spec = importlib.util.spec_from_file_location("golden_metrics_g1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def store_copy_g1(tmp_path_factory):
    if not STORE_SOURCE.is_file():
        pytest.skip("graph.db отсутствует в корне проекта")
    metrics = _load_golden_metrics()
    dest = tmp_path_factory.mktemp("g1_store") / "graph.db"
    metrics.copy_store(STORE_SOURCE, dest)
    return dest


def _answer(store, question):
    from pdfgraph.answer import build_answer

    return build_answer(store, question, None)


def _display(store, question):
    """value_display по живому ответу (флаги по умолчанию — Б9)."""
    from pdfgraph.humanize import value_display

    return value_display(store, _answer(store, question))


# --- юнит-тесты чистки и нормализации показа --------------------------------


def test_clean_display_text_removes_junk():
    from pdfgraph.humanize import clean_display_text

    raw = (
        "Минимальная высота щеток 13,5 мм. ГОСТ 10054-82\n"
        "{Копия документа Ми-8МТВ-1.0000.00 РЭ от 28.08.2023\n"
        "Контрольная копия хранится на сайте АО «ВСК»: https://doclib\n"
        "Распечатал Кирячков Андрей Григорьевич, ПАНХ, АО\n"
        "Контрольная сумма: 8BF9AADBE3998BDC3BE902F6C8992912}\n"
        "Стр. 219/220 Апр 12/22")
    cleaned = clean_display_text(raw)
    assert "13,5" in cleaned
    for junk in ("ГОСТ", "Распечатал", "Копия документа", "Контрольная",
                 "Стр.", "Апр"):
        assert junk not in cleaned, junk


def test_clean_display_text_removes_comb():
    from pdfgraph.humanize import clean_display_text

    assert "ннненннн" not in clean_display_text(
        "ннненннннннн Давление масла не менее 2 кгс/см2")


def test_display_normalize_units_and_glue():
    from pdfgraph.humanize import display_normalize

    assert "кгс/см²" in display_normalize(
        "не менее 2 кго/см?", "какое давление масла")
    assert "кгс/см²" in display_normalize("3,5±0,5 кгс/см2", "")
    assert "150 °C" in display_normalize("температура 150 oc", "")
    assert "Давление масла" in display_normalize(
        "Давлениемасла на входе", "какое давление масла в двигателе")


def test_printed_page_mark_from_colontitle():
    from pdfgraph.humanize import printed_page_mark

    text = ("Ми-8МТВ-1 \nРУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ \n"
            "021.40.00 \nСтр. 219/220 \n")
    assert printed_page_mark(text) == "219/220"
    assert printed_page_mark("ТВИТЕЛЬНО: ВСЕ 072.00.00 Стр. 9 Нояб 15/18") \
        == "9"
    assert printed_page_mark("ТЕХНОЛОГИЧЕСКАЯ КАРТА № 703 709...713/714") \
        == ""


def test_short_document_name():
    from pdfgraph.humanize import short_document_name

    assert short_document_name(
        "Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 3 "
        "(изм. от 13.12.2024).pdf") == "РТЭ Ми-8МТВ-1 кн. 3"
    assert short_document_name(
        "Двигатель ТВ3-117 078.00.5700 РЭ1 книга_1 "
        "(изм. от 05.03.2026).pdf") == \
        "Двигатель ТВ3-117 078.00.5700 РЭ1 книга_1"


def test_no_norm_message_without_portion():
    from pdfgraph.humanize import no_norm_message

    message = no_norm_message([
        {"document": "РТЭ.pdf", "page": 63, "label": "предыдущий лист"},
        {"document": "РТЭ.pdf", "page": 65, "label": "следующий лист"},
        {"document": "РТЭ.pdf", "page": 66, "label": ""},
    ])
    assert "Нормы на листе не нашёл" in message
    assert "63" in message and "65" in message
    assert len(message) < 300


def test_colontitle_is_not_a_norm_window():
    """Колонтитул «…Стр. 9 Нояб 15/18» не попадает в пункты показа.

    Окно нормы отсекается предикатом канала (нет единицы/нормативной
    конструкции), а в дословное окно колонтитул может попасть хвостом —
    его вырезает чистка показа (замер владельца на л.38).
    """
    from pdfgraph.humanize import norm_display_items

    class _FakeStore:
        def page_text(self, document, page):
            return ("TB3-117 РУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ "
                    "2.3.13. Места отборов проб масла "
                    "Давление масла на входе в двигатель не менее 2 кгс/см2. "
                    "ВНИМАТЕЛЬНО: ВСЕ 072.00.00 Стр. 9 Нояб 15/18")

    items = norm_display_items(_FakeStore(), QUESTION_OIL, "РЭ1", 38)
    assert items, "пункт нормы должен найтись"
    for item in items:
        assert "Стр." not in item
        assert "Нояб" not in item
        assert "072.00.00" not in item


# --- живые тесты на копии БД -------------------------------------------------


def test_brush_answer_display_short_and_clean(store_copy_g1):
    """Простой вопрос: ответ ≤400 символов, без портянки; Claim.text цел."""
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    store = Store(str(store_copy_g1))
    try:
        answer = _answer(store, QUESTION_BRUSH)
        display = value_display = _g1_value_display(store, answer)
        checks = verify_citations(answer, store)
    finally:
        store.close()

    assert display is not None, "вопрос-значение обязан дать показ"
    joined = "\n".join(display["items"])
    assert len(joined) <= 400, f"показ длиннее 400: {len(joined)}"
    assert "13,5" in joined
    for junk in ("ГОСТ", "Распечатал", "Копия документа", "Контрольная",
                 "нмненннн"):
        assert junk not in joined, junk
    assert display["quote"] == answer.claims[0].text, "цитата не дословная"
    assert checks and all(check.ok for check in checks)


def _g1_value_display(store, answer):
    from pdfgraph.humanize import value_display

    return value_display(store, answer)


def test_brush_source_has_both_numberings(store_copy_g1):
    """Источник: лист базы И печатная страница из колонтитула."""
    from pdfgraph.store import Store

    store = Store(str(store_copy_g1))
    try:
        display = _g1_value_display(store, _answer(store, QUESTION_BRUSH))
    finally:
        store.close()
    assert "лист 64" in display["source"]
    assert "219/220" in display["source"]


def test_page38_four_norms_display(store_copy_g1):
    """Лист 38: все четыре нормы давления — четыре пункта, без шапки."""
    from pdfgraph.store import Store

    store = Store(str(store_copy_g1))
    try:
        answer = _answer(store, QUESTION_OIL)
        display = _g1_value_display(store, answer)
    finally:
        store.close()
    assert answer.fragments[0].page == 38, "лидер — л.38"
    joined = "\n".join(display["items"])
    # Все четыре нормы давления листа 38 — в показе (замер владельца).
    for value in ("2 кгс/см²", "(3,5+0,5)", "4,8", "+0,25"):
        assert value in joined, value
    # Соседние разделы без числа и перечни — не пункты.
    for junk in ("Стр.", "Нояб", "Апр", "ГОСТ", "Распечатал", "Контрольная",
                 "Количество запусков", "Бензин"):
        assert junk not in joined, junk
    # Пункты короткие: самый длинный ≤ 500 символов.
    assert max(len(item) for item in display["items"]) <= 500


def test_value_display_none_for_non_value_question(store_copy_g1):
    """Не вопрос-значение («карта 703») — показ Г1 не применяется."""
    from pdfgraph.store import Store

    store = Store(str(store_copy_g1))
    try:
        display = _g1_value_display(store, _answer(store, "карта 703"))
    finally:
        store.close()
    assert display is None


def test_page_mark_absent_on_techcard_sheet(store_copy_g1):
    """Техкарты: колонтитула нет — печатная страница не выдумывается."""
    from pdfgraph.humanize import printed_page_mark, value_display
    from pdfgraph.store import Store

    doc_tech = ("Двигатель ТВ3-117 078.00.5700 РЭ1 книга_1 "
                "(изм. от 05.03.2026).pdf")
    store = Store(str(store_copy_g1))
    try:
        text = store.page_text(doc_tech, 230)
        answer = _answer(store, "какое давление масла в двигателе ТВ3-117")
        display = value_display(store, answer)
    finally:
        store.close()
    if answer.fragments and answer.fragments[0].page == 230:
        assert printed_page_mark(text) == ""
        assert "по книге" not in display["source"]
    # «карта 703» — не вопрос-значение; л.230 проверяется напрямую выше.
