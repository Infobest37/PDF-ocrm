# -*- coding: utf-8 -*-
"""Б6 (Ф1.36б): карты-числа в книгах по двигателю («ТЕХНОЛОГИЧЕСКАЯ КАРТА № 703»).

parse_techcard_query раньше распознавал только формат «021.10.00a» —
карты двигателя, нумеруемые простыми числами, оставались для системы
невидимы. Проверяются: форма «карта 703» и её написания («карта №703»,
«карта703», «№ 703», ведущие нули), отказ признавать число без слова
«карта»/«№», живучесть суффиксных карт РТЭ, карточка с названием,
кодом раздела из правого нижнего угла и всеми листами диапазона.
Поиск, гейты отказа, verify, Claim.text и pages.text не затронуты.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.query import parse_techcard_query, search  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfgraph.techcard_card import collect_techcards  # noqa: E402
from pdfprepare.normalize import normalize_for_search  # noqa: E402

#: Книга по двигателю: шапка карты-числа на л. 709, продолжения 710–713.
_ENGINE_DOC = "КНИГА ДВИГАТЕЛЯ ТВ3-117.pdf"
#: Шапка карты № 703: название из «Наименование работы: …», диапазон из
#: «На страницах 709...713/714» (первый 709, последний 713), код раздела
#: 072.00.00 — в правом нижнем углу (последние строки листа), не в шапке.
_ENGINE_HEADER = (
    "ТЕХНОЛОГИЧЕСКАЯ КАРТА № 703\n"
    "Наименование работы: Восстановление лакокрасочных покрытий\n"
    "На страницах 709...713/714\n"
    "072.00.00"
)


def _pages() -> list[tuple[str, int, str, str, str]]:
    pages = [(_ENGINE_DOC, 709, "", "", _ENGINE_HEADER)]
    for page in range(710, 714):
        pages.append((_ENGINE_DOC, page, "", "",
                      "Продолжение техкарты\n"
                      "работы, выполняемые при отклонениях\n072.00.00"))
    pages.append(("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648, "", "",
                  "021.40.00h Замена генератора ГС-18ТО, порядок работ."))
    return pages


def _store(tmp_path) -> Store:
    store = Store(str(tmp_path / "f136b.db"))
    for document, page, system_code, subsystem_code, text in _pages():
        store.upsert_page(document, page, "Ми-8МТВ", system_code,
                          subsystem_code, text, normalize_for_search(text))
    return store


def test_card_number_query_finds_card(tmp_path):
    """«карта 703» — лист найден; карточка: название, код 072.00.00."""
    store = _store(tmp_path)
    try:
        assert parse_techcard_query("карта 703") == ("703", [])
        hits = search(store, "карта 703")
        assert [(hit.document, hit.page, hit.kind, hit.score) for hit in hits] == [
            (_ENGINE_DOC, 709, "techcard", 1.0)]
        cards = collect_techcards(store, "карта 703", hits)
        assert len(cards) == 1
        card = cards[0]
        assert card.number == "703"
        assert card.title == "Восстановление лакокрасочных покрытий"
        assert card.section == "072.00.00"
    finally:
        store.close()


def test_card_number_query_writings(tmp_path):
    """«карта №703», «карта703», «№ 703» (и «№ 0703», слитная
    «технологическаякарта»-форма через пробел) — тот же лист."""
    store = _store(tmp_path)
    try:
        for query in ("карта №703", "карта № 703", "карта703", "№ 703",
                      "№703", "технологическая карта № 703", "карта № 0703"):
            assert parse_techcard_query(query) == ("703", []), query
            hits = search(store, query)
            assert hits, query
            assert (hits[0].document, hits[0].page) == (_ENGINE_DOC, 709), query
    finally:
        store.close()


def test_pressure_703_is_not_card(tmp_path):
    """«давление 703» — не карта: без слова «карта»/«№» число остаётся
    числом, techcard-ветка не срабатывает."""
    assert parse_techcard_query("давление 703") is None
    store = _store(tmp_path)
    try:
        hits = search(store, "давление 703")
        assert all(hit.kind != "techcard" for hit in hits)
    finally:
        store.close()


def test_page_703_is_not_card(tmp_path):
    """«страница 703» — не карта."""
    assert parse_techcard_query("страница 703") is None
    store = _store(tmp_path)
    try:
        hits = search(store, "страница 703")
        assert all(hit.kind != "techcard" for hit in hits)
    finally:
        store.close()


def test_rte_suffix_card_still_works(tmp_path):
    """Запрос карты из РТЭ («карта 021.40.00h») продолжает работать."""
    store = _store(tmp_path)
    try:
        assert parse_techcard_query("карта 021.40.00h") == ("021.40.00", ["h"])
        hits = search(store, "карта 021.40.00h")
        assert [(hit.document, hit.page, hit.kind) for hit in hits] == [
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648, "techcard")]
        cards = collect_techcards(store, "карта 021.40.00h", hits)
        assert [card.number for card in cards] == ["021.40.00h"]
    finally:
        store.close()


def test_card_sheets_cover_full_range(tmp_path):
    """Карточка содержит все листы диапазона 709–713, а не только первый."""
    store = _store(tmp_path)
    try:
        hits = search(store, "карта 703")
        cards = collect_techcards(store, "карта 703", hits)
        assert len(cards) == 1
        sheets = cards[0].sheets
        assert [(sheet.document, sheet.page) for sheet in sheets] == [
            (_ENGINE_DOC, page) for page in range(709, 714)]
    finally:
        store.close()
