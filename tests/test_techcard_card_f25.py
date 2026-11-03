# -*- coding: utf-8 -*-
"""Ф2.5: ответ на запрос карты — карточка, а не абзац.

Проверяются: извлечение наименования работы и трудоёмкости (варианты
маркеров, сводный лист регламента — пустые поля), end-to-end карточка
035.10.00e с дословными title/labor с листа и порядком листов (РТЭ выше
Регламента), три карточки на «035.10.00а,b,e», отсутствие humanize-абзаца,
CLI-блок без сырых сниппетов, JSON-ключ techcard. Verify, гейты, humanize
для обычных вопросов, поиск — не затронуты.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.answer import build_answer  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfgraph.techcard_card import (  # noqa: E402
    extract_labor,
    extract_title,
)
from pdfprepare.normalize import normalize_for_search  # noqa: E402

#: Дословные фрагменты листа РТЭ кн.7 л.648 (карта 035.10.00е).
_MANUAL_TEXT = (
    "Ми-8МТВ-1\nРУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ\n035.10.00\n"
    "Стр. 211/212\nЯнв 21/22\nК РО Ми-8МТВ-1\nТЕХНОЛОГИЧЕСКАЯ КАРТА\n"
    "На странице\n211/212\nПункт РО\n035.10.00 е\nНаименование работы:\n"
    "Проверка запаса кислорода и\nдозарядка баллона\nТрудоемкость,\n"
    "0,01 чел.ч.\nСодержание операции и технические требования (ТТ)\n"
    "а) Установите ручки кранов в положения ВЫКЛ.")
#: Сводный лист Регламента л.42 — маркеров карточки нет.
_REGLAMENT_TEXT = (
    "Ми-8МТВ-1\nРЕГЛАМЕНТ ТЕХНИЧЕСКОГО ОБСЛУЖИВАНИЯ\nПункт РО\n"
    "Наименование объекта обслуживания и содержание работы\n"
    "Форма обслуживания Примечание ОВ-1 А-1 А-2\n035.10.00\n"
    "КИСЛОРОДНОЕ ОБОРУДОВАНИЕ ДЛЯ ЭКИПАЖА ККО-ЛС2\n035.10.00e\n"
    "Проверьте запас кислорода и дозарядите баллон\n+ + +")
#: РТЭ л.640 — карта 035.10.00а (для списка суффиксов).
_MANUAL_A_TEXT = (
    "Ми-8МТВ-1\nРУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ\n035.10.00\n"
    "Стр. 203/204\nПункт РО\n035.10.00 а\nНаименование работы:\n"
    "Монтаж кислородного оборудования на рабочих местах\n"
    "Трудоемкость,\n0,10 чел.ч.\nСодержание операции и технические "
    "требования (ТТ)\nОткройте замки.")
_MANUAL_B_TEXT = _MANUAL_A_TEXT.replace("035.10.00 а", "035.10.00 b").replace(
    "Монтаж кислородного оборудования", "Демонтаж кислородного оборудования")


def _store(tmp_path) -> Store:
    store = Store(str(tmp_path / "f25.db"))
    pages = [
        ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 648, _MANUAL_TEXT),
        ("Регламент технического обслуживания Часть 2 Ми-8МТВ-1 А и РЭО "
         "(изм. 57 от 15.12.2023).pdf", 42, _REGLAMENT_TEXT),
        ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 640, _MANUAL_A_TEXT),
        ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 642, _MANUAL_B_TEXT),
    ]
    for document, page, text in pages:
        store.upsert_page(document, page, "Ми-8МТВ", "", "", text,
                          normalize_for_search(text))
    return store


def test_extract_title_and_labor_variants():
    """Варианты написания маркеров; сводный лист — пусто, не выдумывать."""
    title = extract_title(_MANUAL_TEXT)
    assert title == "Проверка запаса кислорода и дозарядка баллона"
    assert extract_labor(_MANUAL_TEXT) == "0,01 чел.ч"
    # Маркер «Трудоёмкость» через «ё» и без запятой.
    text_yo = _MANUAL_TEXT.replace("Трудоемкость,", "Трудоёмкость")
    assert extract_labor(text_yo) == "0,01 чел.ч"
    # Сводный лист регламента: маркеров карточки нет.
    assert extract_title(_REGLAMENT_TEXT) == ""
    assert extract_labor(_REGLAMENT_TEXT) == ""
    # Маркера нет вовсе.
    assert extract_title("совершенно другой лист") == ""
    assert extract_labor("трудоемкость без числа") == ""


def test_card_answer_with_verbatim_title_and_labor(tmp_path):
    """Приёмка Ф2.5: title и labor дословно с листа РТЭ л.648; листы с
    ролями — РТЭ (как выполняется) выше Регламента (когда выполняется);
    humanize-абзац не собирается."""
    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00e")
        assert answer.techcard and not answer.refusal
        card = answer.techcard[0]
        assert card.number == "035.10.00e"
        assert card.title == "Проверка запаса кислорода и дозарядка баллона"
        assert card.labor == "0,01 чел.ч"
        # Дословность: значение совпадает с текстом листа (переносы строк
        # OCR схлопнуты — содержимое то же символ в символ).
        page = store.page_text("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf",
                               648)
        assert "".join(card.title.split()) in "".join(page.split())
        assert "".join(card.labor.split()) in "".join(page.split())
        # Листы и роли.
        assert [(s.document, s.page, s.role) for s in card.sheets] == [
            ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 648,
             "как выполняется"),
            ("Регламент технического обслуживания Часть 2 Ми-8МТВ-1 А и "
             "РЭО (изм. 57 от 15.12.2023).pdf", 42, "когда выполняется"),
        ]
        # Человеческий абзац не собирается; дословная цитата на месте.
        assert answer.claims and answer.claims[0].answer_text is None
        assert answer.claims[0].text
    finally:
        store.close()


def test_suffix_list_builds_three_cards(tmp_path):
    """035.10.00а,b,e — три карточки, каждая со своим номером и названием."""
    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00а,b,e")
        assert [card.number for card in answer.techcard] == [
            "035.10.00a", "035.10.00b", "035.10.00e"]
        assert answer.techcard[0].title == (
            "Монтаж кислородного оборудования на рабочих местах")
        assert answer.techcard[1].title == (
            "Демонтаж кислородного оборудования на рабочих местах")
        assert answer.techcard[2].title == (
            "Проверка запаса кислорода и дозарядка баллона")
    finally:
        store.close()


def test_section_query_has_no_cards(tmp_path):
    """Запрос раздела без суффиксов — прежнее поведение, без карточек."""
    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00")
        assert answer.techcard == []
    finally:
        store.close()


def test_cli_prints_card_block_without_raw_snippets(tmp_path, capsys):
    """CLI: блок карточки + [ok]-листы + вердикт; сырых сниппетов нет."""
    from pdfgraph.cli import _print_text

    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00e")
        from pdfgraph.verify import verify_citations

        rows = []
        by_sheet = {(f.document, f.page): f for f in answer.fragments}
        for check in verify_citations(answer, store):
            rows.append({
                "claim_index": check.claim_index,
                "document": check.document, "page": check.page,
                "system_code": "", "subsystem_code": "", "ok": check.ok,
                "reason": check.reason, "doc_layer": "",
                "doc_applicability": "", "doc_revision": "",
            })
        _print_text(answer, rows, True, store)
        out = capsys.readouterr().out
        assert "Карта 035.10.00e" in out
        assert "Наименование работы: Проверка запаса кислорода и " \
            "дозарядка баллона" in out
        assert "Трудоёмкость: 0,01 чел.ч" in out
        assert "как выполняется" in out and "когда выполняется" in out
        assert "[ok]" in out
        assert "Подтверждено" in out
        # Сырой сниппет сводной таблицы не печатается.
        assert "Форма обслуживания" not in out
        assert "Содержание операции" not in out
    finally:
        store.close()


def test_json_payload_carries_techcard(tmp_path, capsys):
    """JSON: ключ techcard с карточками; claims и verify не изменены."""
    from pdfgraph.answer import build_answer
    from pdfgraph.cli import _print_json
    from pdfgraph.verify import verify_citations

    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00e")
        checks = verify_citations(answer, store)
        rows = [{
            "claim_index": check.claim_index,
            "document": check.document, "page": check.page,
            "system_code": "", "subsystem_code": "", "ok": check.ok,
            "reason": check.reason, "doc_layer": "",
            "doc_applicability": "", "doc_revision": "",
        } for check in checks]
        _print_json(answer, rows, True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["techcard"][0]["number"] == "035.10.00e"
        assert payload["techcard"][0]["title"] == (
            "Проверка запаса кислорода и дозарядка баллона")
        assert payload["techcard"][0]["labor"] == "0,01 чел.ч"
        assert payload["techcard"][0]["sheets"][0]["role"] == \
            "как выполняется"
    finally:
        store.close()


# --- Ф2.8: цифровой индекс в номере карты -----------------------------------

#: Кн.7 л.372 — базовая карта 033.10.00а в шапке карты.
_KN7_BASE_TEXT = (
    "Ми-8МТВ-1\nРУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ\n033.10.00\n"
    "Стр. 203/204\nК РО Ми-8МТВ-1\nТЕХНОЛОГИЧЕСКАЯ КАРТА\n"
    "Пункт РО\n033.10.00 a\nНаименование работы:\n"
    "Осмотр осветительного оборудования кабины экипажа\nТрудоемкость,\n"
    "0,1 чел.ч.\nСодержание операции и технические требования (ТТ)\n"
    "1. Осмотрите стекла плафонов и световых табло.")
#: Д-1697 кн.2 л.132 — карта 033.10.00а1; индекс OCR даёт «;».
_SUPPLEMENT_A1_TEXT = (
    "Ми-8МТВ-1\nДОПОЛНЕНИЕ К РУКОВОДСТВУ ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ\n"
    "МИ 8МТВ-1 ТЕХНОЛОГИЧЕСКАЯ КАРТА 203/204\nПункт РО\n033.10.00 a;\n"
    "Наименование работы:\nОсмотр светотехнического оборудования кабины "
    "экипажа\nТрудоемкость,\n0,1 чел.ч.\n"
    "Содержание операции и технические требования (ТТ)\n"
    "1. Осмотрите стекла потолочных светильников.")


def _store_f28(tmp_path) -> Store:
    """Корпус Ф2.8: базовая карта в кн.7, карта-дополнение а1 в Д-1697."""
    from pdfgraph import documents

    store = Store(str(tmp_path / "f28card.db"))
    pages = [
        ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 372,
         _KN7_BASE_TEXT),
        ("Доп. к РТЭ Д-1697 кн.2 для вертолета 22217 22562.pdf", 132,
         _SUPPLEMENT_A1_TEXT),
    ]
    for document, page, text in pages:
        store.upsert_page(document, page, "Ми-8МТВ", "", "", text,
                          normalize_for_search(text))
    documents.fill_missing(store)
    return store


def test_base_card_answer_without_note_f28(tmp_path):
    """Приёмка Ф2.8: 033.10.00a — базовая карта кн.7 «Осмотр осветительного
    оборудования», без пометки применимости."""
    store = _store_f28(tmp_path)
    try:
        answer = build_answer(store, "033.10.00a")
        assert not answer.refusal and answer.techcard
        card = answer.techcard[0]
        assert card.number == "033.10.00a"
        assert card.title == (
            "Осмотр осветительного оборудования кабины экипажа")
        assert card.labor == "0,1 чел.ч"
        assert card.applicability == ""
        assert [(sheet.document, sheet.page) for sheet in card.sheets] == [
            ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 372)]
    finally:
        store.close()


def test_index_card_answer_with_note_f28(tmp_path, capsys):
    """Приёмка Ф2.8: 033.10.00a1 — карта Д-1697 «Осмотр светотехнического
    оборудования» с пометкой про борта 22217, 22562 и базовую карту кн.7."""
    store = _store_f28(tmp_path)
    try:
        answer = build_answer(store, "033.10.00a1")
        assert not answer.refusal and answer.techcard
        card = answer.techcard[0]
        assert card.number == "033.10.00a1"
        assert card.title == (
            "Осмотр светотехнического оборудования кабины экипажа")
        assert card.applicability == (
            "карта из дополнения Д-1697; применима к бортам 22217, 22562; "
            "для остальных бортов — базовая карта в РТЭ Ми-8МТВ-1 кн. 7")
        assert [(sheet.document, sheet.page) for sheet in card.sheets] == [
            ("Доп. к РТЭ Д-1697 кн.2 для вертолета 22217 22562.pdf", 132)]
        # CLI печатает пометку в блоке карточки.
        from pdfgraph.cli import _print_text

        _print_text(answer, [], True)
        out = capsys.readouterr().out
        assert "карта из дополнения Д-1697" in out
        assert "22217, 22562" in out
    finally:
        store.close()


# --- Ф2.9: многолистовые карты -----------------------------------------------

_F29_DOC = "РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf"
_F29_P10 = (
    "Ми-8МТВ-1\nТЕХНОЛОГИЧЕСКАЯ КАРТА\nПункт РО\n033.10.00 a\n"
    "Наименование работы:\nОсмотр оборудования\nТрудоемкость,\n"
    "0,1 чел.ч.\n1. Осмотрите стекла.")


def _store_f29(tmp_path, page11_text: str, mark_continuation: bool = False):
    """Корпус Ф2.9: карта на л.10, второй лист л.11 — по варианту теста."""
    store = Store(str(tmp_path / "f29.db"))
    store.upsert_page(_F29_DOC, 10, "Ми-8МТВ", "", "", _F29_P10,
                      normalize_for_search(_F29_P10))
    store.upsert_page(_F29_DOC, 11, "Ми-8МТВ", "", "", page11_text,
                      normalize_for_search(page11_text))
    if mark_continuation:
        store.set_page_continuation(_F29_DOC, 11, True)
    return store


def test_multisheet_card_parts_f29(tmp_path, capsys):
    """Ф2.9: карта на двух листах с повторённым номером — оба листа в
    карточке: первый «начало карты», следующий «продолжение»."""
    page11 = ("Продолжение табл.\n033.10.00 a\n"
              "2. Осмотрите плафоны и светильники.")
    store = _store_f29(tmp_path, page11)
    try:
        answer = build_answer(store, "033.10.00a")
        assert not answer.refusal and answer.techcard
        card = answer.techcard[0]
        assert [(sheet.page, sheet.part) for sheet in card.sheets] == [
            (10, "начало карты"), (11, "продолжение")]
        assert all(sheet.role == "как выполняется"
                   for sheet in card.sheets)
        # CLI печатает метку продолжения.
        from pdfgraph.cli import _print_text

        _print_text(answer, [], True)
        out = capsys.readouterr().out
        assert "(продолжение)" in out
        assert "(начало карты)" in out
    finally:
        store.close()


def test_continuation_without_number_attached_f29(tmp_path):
    """Ф2.9: продолжение карты без повторённого номера — признак
    «Продолжение» (is_continuation) приклеивает лист к карточке."""
    page11 = "Продолжение табл.\n2. Осмотрите плафоны и светильники."
    store = _store_f29(tmp_path, page11, mark_continuation=True)
    try:
        answer = build_answer(store, "033.10.00a")
        assert not answer.refusal and answer.techcard
        card = answer.techcard[0]
        assert [(sheet.page, sheet.part) for sheet in card.sheets] == [
            (10, "начало карты"), (11, "продолжение")]
    finally:
        store.close()


def test_continuation_of_other_card_not_attached_f29(tmp_path):
    """Ф2.9: лист-продолжение с другой картой той же базы (сводная
    таблица) к карточке не приклеивается."""
    page11 = "Продолжение табл.\n033.10.00 b Проверка креплений кронштейнов."
    store = _store_f29(tmp_path, page11, mark_continuation=True)
    try:
        answer = build_answer(store, "033.10.00a")
        assert not answer.refusal and answer.techcard
        card = answer.techcard[0]
        assert [sheet.page for sheet in card.sheets] == [10]
        assert card.sheets[0].part == ""
    finally:
        store.close()
