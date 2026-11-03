# -*- coding: utf-8 -*-
"""Ф1.9: поиск по номерам технологических карт.

Проверяются: нормализация номера (пробелы/точки/регистр/кириллица→латиница
дают один ключ), одинаковый первый результат для всех форм одного номера,
список суффиксов («035.10.00а,b,e») — отдельные карты, запрос без буквы —
раздел плюс перечисление карт, все листы карты в выдаче, строка
techcard_missing в Answer/CLI для отсутствующих карт, «035.10е.00» картой
не считается. Verify, гейты, веса и порог попадания не затронуты.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.query import (  # noqa: E402
    parse_techcard_query,
    search,
    techcard_missing_suffixes,
)
from pdfgraph.store import Store  # noqa: E402
from pdfprepare.normalize import normalize_for_search  # noqa: E402

#: Страницы тестового корпуса: карта e — в двух документах (Регламент и
#: РТЭ), суффиксы a/b — только в РТЭ, кириллическая «е» на л. 648.
#: Коды как в реальной базе: лист-раздел несёт system_code «035.10.00»,
#: листы карт — без кода (в code-выдачу раздела они не попадают).
_PAGES = [
    ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42, "", "",
     "035.10.00c Проверьте запас кислорода. 035.10.00e Замените баллон."),
    ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 626, "035.10.00", "",
     "035.10.00 КИСЛОРОДНОЕ ОБОРУДОВАНИЕ Описание и работа"),
    ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 640, "", "",
     "035.10.00а Описание кислородного прибора, таблица режимов."),
    ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 642, "", "",
     "035.10.00b Проверка герметичности системы."),
    ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648, "", "",
     "035.10.00е Замена кислородного баллона, порядок работ."),
]


def _store(tmp_path) -> Store:
    store = Store(str(tmp_path / "f19.db"))
    for document, page, system_code, subsystem_code, text in _PAGES:
        store.upsert_page(document, page, "Ми-8МТВ", system_code,
                          subsystem_code, text, normalize_for_search(text))
    return store


def test_parse_normalizes_writings():
    """Все формы одного номера дают один ключ (шаг 2 карточки)."""
    for query in ("035.10.00e", "035.10.00 e", "035.10.00Е", "0351000e",
                  "035 10 00 e"):
        assert parse_techcard_query(query) == ("035.10.00", ["e"]), query
    assert parse_techcard_query("035.10.00а,b,e") == \
        ("035.10.00", ["a", "b", "e"])
    # Слово «карта» не мешает разбору.
    assert parse_techcard_query("карта 035.10.00e") == \
        ("035.10.00", ["e"])
    assert parse_techcard_query("технологическая карта 035.10.00 e") == \
        ("035.10.00", ["e"])
    # Без суффикса — раздел: суффикс-список пуст.
    assert parse_techcard_query("035.10.00") == ("035.10.00", [])
    # Буква внутри номера — не карта.
    assert parse_techcard_query("035.10е.00") is None
    # Обычный вопрос — не запрос карты.
    assert parse_techcard_query("какое давление масла") is None


def test_all_writings_same_first_result(tmp_path):
    """Приёмка: все формы одного номера дают одинаковый первый результат."""
    store = _store(tmp_path)
    try:
        queries = ("035.10.00e", "035.10.00 e", "035.10.00Е", "0351000e",
                   "технологическая карта 035.10.00e", "карта 035.10.00e")
        expected = None
        for query in queries:
            hits = search(store, query)
            assert hits, query
            first = (hits[0].document, hits[0].page)
            if expected is None:
                expected = first
            assert first == expected, query
    finally:
        store.close()


def test_suffix_query_returns_every_sheet_of_the_card(tmp_path):
    """Карта e есть в Регламенте л.42 и РТЭ л.648 — обе в выдаче."""
    store = _store(tmp_path)
    try:
        hits = search(store, "035.10.00e")
        assert {(hit.document, hit.page) for hit in hits} == {
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42),
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648),
        }
        assert all(hit.kind == "techcard" and hit.score == 1.0
                   for hit in hits)
    finally:
        store.close()


def test_suffix_list_returns_each_card(tmp_path):
    """035.10.00а,b,e — три карты отдельными результатами (шаг 3).

    Порядок суффиксов — как в запросе (a, b, e); внутри суффикса листы
    документа (бинарный порядок SQL: «РТЭ…» раньше «Регламента…»).
    """
    store = _store(tmp_path)
    try:
        hits = search(store, "035.10.00а,b,e")
        assert all(hit.kind == "techcard" for hit in hits)
        assert [(hit.document, hit.page) for hit in hits[:2]] == [
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 640),  # a
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 642),  # b
        ]
        assert {(hit.document, hit.page) for hit in hits[2:]} == {
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42),  # e
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648),        # e
        }
    finally:
        store.close()


def test_section_query_lists_cards(tmp_path):
    """035.10.00 без буквы — раздел (code) плюс перечисление карт (шаг 4);
    со словом «карта» — то же (приёмка «со словами и без слов одинаково»)."""
    store = _store(tmp_path)
    try:
        bare = search(store, "035.10.00")
        with_word = search(store, "карта 035.10.00")
        assert [(h.document, h.page, h.kind) for h in bare] == \
            [(h.document, h.page, h.kind) for h in with_word]
        kinds = [hit.kind for hit in bare]
        # Раздел — code-ветка впереди, карты — techcard в хвосте.
        assert kinds[0] == "code"
        assert set(kinds[1:]) == {"techcard"}
        cards = {(hit.document, hit.page) for hit in bare[1:]}
        assert cards == {
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42),   # c и e
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 640),         # a
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 642),         # b
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648),         # e
        }
    finally:
        store.close()


def test_limit_zero_returns_nothing(tmp_path):
    """limit=0 на картах — пустая выдача; раздел (code-ветка) лимитом
    не режется — существующая семантика «not truncated by default limit»."""
    store = _store(tmp_path)
    try:
        assert search(store, "035.10.00e", limit=0) == []
        assert search(store, "035.10.00а,b,e", limit=0) == []
        assert search(store, "035.10.00", limit=0)
    finally:
        store.close()


def test_snippets_are_verbatim_slices(tmp_path):
    """Инвариант цитат не задет: snippet — точный срез исходного текста."""
    store = _store(tmp_path)
    try:
        for query in ("035.10.00e", "035.10.00а,b,e", "035.10.00"):
            for hit in search(store, query):
                text = store.page_text(hit.document, hit.page)
                assert text[hit.char_start:hit.char_end] == hit.snippet
    finally:
        store.close()


def test_techcard_missing_suffixes(tmp_path):
    """Ненайденные суффиксы перечисляются честно; полный комплект — []"""
    store = _store(tmp_path)
    try:
        assert techcard_missing_suffixes(store, "035.10.00а,b,e") == []
        assert techcard_missing_suffixes(store, "035.10.00е") == []
        assert techcard_missing_suffixes(store, "035.10.00а,z") == \
            ["035.10.00z"]
        assert techcard_missing_suffixes(store, "035.10.00y,z") == \
            ["035.10.00y", "035.10.00z"]
        # Не запрос карты — пусто.
        assert techcard_missing_suffixes(store, "давление масла") == []
        assert techcard_missing_suffixes(store, "035.10.00") == []
    finally:
        store.close()


def test_answer_carries_techcard_missing(tmp_path):
    """Answer.techcard_missing: только отсутствующие суффиксы; при полном
    комплекте список пуст (приёмка Ф1.9)."""
    from pdfgraph.answer import build_answer

    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00а,z")
        assert answer.techcard_missing == ["035.10.00z"]
        assert not answer.refusal
        complete = build_answer(store, "035.10.00е")
        assert complete.techcard_missing == []
        # Ни одной найденной карты — отказ, список ненайденных при нём.
        refusal = build_answer(store, "035.10.00y,z")
        assert refusal.refusal
        assert refusal.techcard_missing == ["035.10.00y", "035.10.00z"]
    finally:
        store.close()


def test_cli_prints_missing_cards(tmp_path, capsys):
    """CLI: строка «Карта … в базе не найдена» и ключ techcard_missing."""
    import json

    from pdfgraph.answer import Answer
    from pdfgraph.cli import _print_json, _print_text

    answer = Answer(question="035.10.00а,z", claims=[], fragments=[],
                    refusal=True, reason="нет подтверждений в документах",
                    techcard_missing=["035.10.00z"])
    _print_text(answer, [], True)
    out = capsys.readouterr().out
    assert "Карта 035.10.00z в базе не найдена" in out

    _print_json(answer, [], True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["techcard_missing"] == ["035.10.00z"]


def test_letter_inside_code_is_not_a_techcard(tmp_path):
    """«035.10е.00» картой не считается — прежний поиск без techcard-ветки."""
    store = _store(tmp_path)
    try:
        assert parse_techcard_query("035.10е.00") is None
        hits = search(store, "035.10е.00")
        assert all(hit.kind != "techcard" for hit in hits)
    finally:
        store.close()
