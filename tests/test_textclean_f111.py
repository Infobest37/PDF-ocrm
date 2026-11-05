# -*- coding: utf-8 -*-
"""Ф1.11: чистка поискового текста — заполнители строк и OCR-мусор.

pages.text не трогается; чистится только копия для pages_fts.body.
Правило гребёнки (уточнение владельца): пробег 8+ строчных букв из
{н, е, м, и, ы} вырезается целиком; «менее», «мнение», «имение» не
вырезаются. Векторы не пересчитываются.
"""

from pdfgraph.stemmer import stem_text
from pdfgraph.textclean import clean_search_text
from pdfgraph.store import Store
from pdfgraph.cli import main
from pdfprepare.normalize import normalize_for_search

RAW_LEAF38 = (
    "Таблица 23 Параметры работы двигателя и его агрегатов в эксплуатации "
    "Temperature масла перед 2.3.13. регулятором ...........нннннннннннен"
    "ннннннннннннннннннн +70 °С 2.3.16. Давление масла на режиме малого "
    "газа ........нмненнннныеннннннееееенееееееее He менее 2 кгс/см? на "
    "режиме выше малого газа ........... меннее (3,5+0,5) кго/см? "
    "2.3.19 — Количество запусков, следующих один за друтим"
    "..................... He более 5")


# --- правила чистки ------------------------------------------------------------


def test_dot_runners_removed():
    cleaned = clean_search_text("Давление масла ....... не менее 2 кгс/см2")
    assert "...." not in cleaned
    assert "Давление масла не менее 2 кгс/см2" == cleaned


def test_comb_runs_removed():
    assert "нннннннненннннн" not in clean_search_text(
        "параметр ....нннннннненнннннннннннн +70 °С")
    cleaned = clean_search_text("газа ........нмненнннныеннннннееееенееееееее "
                                "He менее 2 кгс/см?")
    assert "нмненнннныеннннннееееенееееееее" not in cleaned
    assert "менее 2" in cleaned


def test_real_words_not_removed():
    """«менее» (5), «мнение» и «имение» (6) — короче восьми, не гребёнка."""
    for word in ("менее", "мнение", "имение"):
        assert word in clean_search_text(f"не менее {word} 2 кгс/см2")


def test_pressure_units_normalized():
    for raw in ("кгс/см?", "кгс/см2", "кго/см?", "кг/см?"):
        cleaned = clean_search_text(f"не менее 2 {raw}")
        assert "кгс/см2" in cleaned, raw
        # Идемпотентность: повторная чистка не меняет результат.
        assert clean_search_text(cleaned) == cleaned


def test_celsius_unified():
    for raw in ("°С", "°C", "°с", "°c"):
        cleaned = clean_search_text(f"до +70 {raw} в эксплуатации")
        assert "°C" in cleaned, raw
        if raw != "°C":
            assert raw not in cleaned, raw


def test_case_boundary_glue_split():
    cleaned = clean_search_text("ДавлениеМасла КоличествоЗапусков ТВ3-117")
    assert "Давление Масла" in cleaned
    assert "Количество Запусков" in cleaned
    # Обозначения с дефисом не задеты.
    assert "ТВ3-117" in cleaned


def test_real_leaf38_fragment_cleaned():
    """Реальный фрагмент листа 38: гребёнки и точки уходят, нормы видны."""
    cleaned = clean_search_text(RAW_LEAF38)
    assert "Давление масла на режиме малого газа" in cleaned
    assert "Количество запусков" in cleaned
    assert "кгс/см2" in cleaned
    for junk in ("нмненнннныеннннннееееенееееееее",
                 "ннннннннннненнннннннннннннннннн", "..........."):
        assert junk not in cleaned


def test_clean_is_idempotent():
    once = clean_search_text(RAW_LEAF38)
    assert clean_search_text(once) == once


# --- интеграция: reindex-text --------------------------------------------------

_LEAF_WITH_JUNK = ("Давление масла ........нннннннннннннненннннннннннн "
                   "не менее 2 кгс/см? запуск")
_QUERY = "какое давление масла в двигателе"


def _make_store(tmp_path):
    store = Store(str(tmp_path / "graph.db"))
    store.upsert_page("РЭ1.pdf", 38, "Ми-8МТВ", "071.00.00", "",
                      _LEAF_WITH_JUNK,
                      normalize_for_search(_LEAF_WITH_JUNK))
    store.upsert_page("РЭ1.pdf", 39, "Ми-8МТВ", "071.00.00", "",
                      "Совершенно другой лист про шасси",
                      normalize_for_search("Совершенно другой лист "
                                           "про шасси"))
    return store


def test_reindex_text_finds_leaf_and_keeps_text(tmp_path):
    store = _make_store(tmp_path)
    try:
        # До переиндексации: мусор в body, AND-запрос не находит лист.
        body_before = store._connection.execute(
            "SELECT body FROM pages_fts WHERE document = 'РЭ1.pdf' "
            "AND page = '38'").fetchone()[0]
        assert "нннннннннннннненннннннннннн" in body_before
        assert main(["reindex-text", str(tmp_path / "graph.db")]) == 0
        # pages.text не изменился (цитаты дословные).
        assert store.page_text("РЭ1.pdf", 38) == _LEAF_WITH_JUNK
        body_after = store._connection.execute(
            "SELECT body FROM pages_fts WHERE document = 'РЭ1.pdf' "
            "AND page = '38'").fetchone()[0]
        assert "нннннннннннннненннннннннннн" not in body_after
        assert "давление масла" in body_after and "кгс/см2" in body_after
        # FTS AND-запрос находит лист (до чистки мусор рвал поиск).
        hits = store.search_pages('"давление" AND "масла" AND "запуск"',
                                  None, 5)
        assert [("РЭ1.pdf", 38)] == [(row[0], row[1]) for row in hits]
    finally:
        store.close()


def test_reindex_text_idempotent(tmp_path):
    store = _make_store(tmp_path)
    try:
        assert main(["reindex-text", str(tmp_path / "graph.db")]) == 0
        body_first = store._connection.execute(
            "SELECT body FROM pages_fts WHERE document = 'РЭ1.pdf' "
            "AND page = '38'").fetchone()[0]
        assert main(["reindex-text", str(tmp_path / "graph.db")]) == 0
        body_second = store._connection.execute(
            "SELECT body FROM pages_fts WHERE document = 'РЭ1.pdf' "
            "AND page = '38'").fetchone()[0]
        assert body_first == body_second
        # Trigram-таблица держит тот же очищенный body.
        trigram = store._connection.execute(
            "SELECT body FROM pages_fts_trigram WHERE document = 'РЭ1.pdf' "
            "AND page = '38'").fetchone()[0]
        assert trigram == body_first
    finally:
        store.close()
