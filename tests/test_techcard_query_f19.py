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
    """Карта e есть в Регламенте л.42 и РТЭ л.648 — обе в выдаче (Ф1.9б:
    руководство выше регламента, роли подписаны)."""
    store = _store(tmp_path)
    try:
        hits = search(store, "035.10.00e")
        assert [(hit.document, hit.page, hit.role) for hit in hits] == [
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648, "manual"),
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42, "reglament"),
        ]
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
        # Ф1.9б: карты раздела ВЫШЕ листов раздела (приёмка: карты в первых
        # пяти результатах); внутри карты руководства выше регламента.
        assert kinds[:5] == ["techcard"] * 5
        assert kinds[-1] == "code"
        cards = [(hit.document, hit.page, hit.role) for hit in bare[:5]]
        assert cards == [
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 640, "manual"),          # a
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 642, "manual"),          # b
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42, "reglament"),  # c
            ("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648, "manual"),          # e
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42, "reglament"),  # e
        ]
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
        # Ф1.9б п.4: карта только в регламенте (периодичность) — карта
        # отсутствует, хотя лист регламента в выдаче будет.
        assert techcard_missing_suffixes(store, "035.10.00c") == \
            ["035.10.00c"]
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


def test_card_in_two_manuals_shown_twice(tmp_path):
    """Карта в двух руководствах — два результата с разными книгами,
    не схлопываются (Ф1.9б п. 3)."""
    store = Store(str(tmp_path / "two-manuals.db"))
    for document, page, text in (
        ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 648,
         "035.10.00е Замена кислородного баллона, порядок работ."),
        ("Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 4"
         ".pdf", 210,
         "035.10.00е Замена баллона, работы при обслуживании."),
    ):
        store.upsert_page(document, page, "Ми-8МТВ", "", "", text,
                          normalize_for_search(text))
    try:
        hits = search(store, "035.10.00e")
        assert [(hit.document, hit.page, hit.role) for hit in hits] == [
            ("РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf", 648, "manual"),
            ("Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 4"
             ".pdf", 210, "manual"),
        ]
    finally:
        store.close()


def test_section_limit_ten_and_techcard_more(tmp_path):
    """Карт раздела больше десяти — первые десять в выдаче, остаток в
    techcard_more (Ф1.9б п. 1, лимит 10 по решению владельца)."""
    from pdfgraph.answer import build_answer
    from pdfgraph.query import techcard_section_more

    store = Store(str(tmp_path / "many-cards.db"))
    for index, suffix in enumerate("abcdefghijkl"):  # 12 карт
        page = 640 + index
        text = f"035.10.00{suffix} Карта раздела кислородного оборудования."
        store.upsert_page("РТЭ Ми-8МТВ-1 кн. 7.pdf", page, "Ми-8МТВ",
                          "", "", text, normalize_for_search(text))
    try:
        hits = search(store, "035.10.00")
        assert len(hits) == 10
        assert all(hit.kind == "techcard" for hit in hits)
        assert techcard_section_more(store, "035.10.00") == 2
        answer = build_answer(store, "035.10.00")
        assert answer.techcard_more == 2
        # Суффиксный запрос лимит раздела не задевает.
        assert techcard_section_more(store, "035.10.00a") == 0
    finally:
        store.close()


def test_answer_reglament_only_carries_missing(tmp_path):
    """Ф1.9б п. 4: карта только в регламенте — лист регламента в выдаче,
    карта в techcard_missing («в базе не найдена»)."""
    from pdfgraph.answer import build_answer

    store = _store(tmp_path)
    try:
        answer = build_answer(store, "035.10.00c")
        assert not answer.refusal
        assert [(f.document, f.page) for f in answer.fragments] == [
            ("Регламент ТО Ч.2 Ми-8МТВ-1.pdf", 42)]
        assert answer.fragments[0].role == "reglament"
        assert answer.techcard_missing == ["035.10.00c"]
    finally:
        store.close()


def test_gate_card_suffix_homoglyphs():
    """Ф1.9в: двойники суффикса карты приравнены в _designator_in_text;
    агрегатные обозначения, числа и границы не задеты."""
    from pdfgraph.gates import _card_suffix_in_text, _designator_in_text

    cyrillic = "035.10.00е замена кислородного баллона"
    latin = "035.10.00e проверка герметичности системы"
    assert _designator_in_text(cyrillic, "035.10.00e")
    assert _designator_in_text(latin, "035.10.00е")
    assert _card_suffix_in_text(cyrillic, "035.10.00e")
    # Обратный суффикс-двойник: вопрос «…в», текст «…в» латиницей —
    # приравнивается через таблицу двойников.
    assert _card_suffix_in_text("035.10.00b демонтаж", "035.10.00в")
    # Суффикс не продолжается в слово: карточная форма «035.10.00
    # кислородное» — не карта «…к» (слипшая форма гейта — прежнее
    # поведение, Ф1.9в её не меняет).
    assert not _card_suffix_in_text("035.10.00 кислородное оборудование",
                                    "035.10.00к")
    # Перед базой цифра — часть более длинного номера.
    assert not _card_suffix_in_text("1035.10.00e монтаж", "035.10.00e")
    # Суффикса нет в таблице двойников — приравнивания нет.
    assert not _card_suffix_in_text(cyrillic, "035.10.00z")
    # Агрегатные обозначения карточной формой не проверяются вовсе.
    assert not _card_suffix_in_text("редуктор вр-14е", "ВР-14Е")


def test_answer_with_cyrillic_suffix_on_sheet(tmp_path):
    """Ф1.9в: лист с «035.10.00е» (кириллица), вопрос с латинской «e» —
    ответ, не уточнение; карты «035.10.00z» в базе нет — не ответ
    (правило корпусного отсутствия Ф1.5.1 даёт отказ)."""
    from pdfgraph.answer import build_answer

    store = Store(str(tmp_path / "f19v.db"))
    text = "035.10.00е Замена кислородного баллона, порядок работ."
    store.upsert_page("РТЭ Ми-8МТВ-1 кн. 7.pdf", 648, "Ми-8МТВ", "", "",
                      text, normalize_for_search(text))
    try:
        answer = build_answer(store, "карта 035.10.00e")
        assert not answer.refusal and not answer.clarification
        assert answer.claims
        missing = build_answer(store, "карта 035.10.00z")
        assert missing.refusal
    finally:
        store.close()


def test_homoglyph_tables_in_sync():
    """Ф1.9д: таблицы двойников суффикса в query и gates совпадают —
    рассинхрон (Ф1.9в: без к/м/т/в/н) ломал поиск карт с этими суффиксами."""
    from pdfgraph.gates import _SUFFIX_HOMOGLYPHS
    from pdfgraph.query import _TECHCARD_HOMOGLYPHS

    assert _TECHCARD_HOMOGLYPHS == _SUFFIX_HOMOGLYPHS


def test_cli_prints_roles_and_section_more(capsys):
    """CLI: подпись роли в доказательствах и строка «Ещё N карт раздела»."""
    from pdfgraph.answer import Answer
    from pdfgraph.cli import _print_text
    from pdfgraph.query import Fragment

    answer = Answer(question="035.10.00", claims=[], fragments=[
        Fragment(document="РТЭ кн.7.pdf", page=648, system_code="035.10.00",
                 subsystem_code="", snippet="035.10.00е Замена баллона",
                 char_start=0, char_end=21, score=1.0, kind="techcard",
                 role="manual"),
    ], techcard_missing=["035.10.00c"], techcard_more=2)
    row = {"claim_index": 0, "document": "РТЭ кн.7.pdf", "page": 648,
           "system_code": "035.10.00", "subsystem_code": "", "ok": True,
           "reason": "", "doc_layer": "", "doc_applicability": "",
           "doc_revision": ""}
    _print_text(answer, [row], True)
    out = capsys.readouterr().out
    assert "— карта, как выполняется" in out
    assert "Карта 035.10.00c в базе не найдена" in out
    assert "Ещё 2 карт раздела" in out
