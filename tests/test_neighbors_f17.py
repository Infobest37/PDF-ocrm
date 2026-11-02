# -*- coding: utf-8 -*-
"""Ф1.7: окно соседних листов в выдаче (neighbors, без правки ранжирования).

Проверяется: соседи топ-5 с метками «предыдущий лист» / «следующий лист,
возможно продолжение», границы документа, фильтр needs_rescan/is_service,
дедупликация, изоляция основной выдачи при выключенном флаге
``PDFGRAPH_NEIGHBORS``, отсутствие соседей в цитатах (только отдельный
список ссылок) и новая метрика ``hit_at_5_with_neighbors`` в золотом
прогоне (старая ``hit_at_5`` не меняется).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from pdfgraph.answer import build_answer
from pdfgraph.cli import _evidence_rows, _print_json, _print_text
from pdfgraph.store import Store
from pdfgraph.verify import all_verified, verify_citations
from pdfprepare.normalize import normalize_for_search

from tests.golden.metrics import GoldenQuestion, _bucket_metrics, _row_for

DOC = "Д1.pdf"
LABEL_PREV = "предыдущий лист"
LABEL_NEXT = "следующий лист, возможно продолжение"

#: (документ, лист) → текст; ключевые слова подобраны так, чтобы
#: точные вопросы попадали в нужный лист через ветку AND.
SHEETS: dict[tuple[str, int], str] = {
    (DOC, 1): "СЛИВА МАСЛА ДВИГАТЕЛЯ ПЕРЕД ЗАМЕНОЙ ФИЛЬТРА",
    (DOC, 2): "ЗАМЕНА МАСЛА ДВИГАТЕЛЯ ИНСТРУМЕНТ КЛЮЧ",
    (DOC, 3): "ЗАПРАВКА МАСЛА СМАЗОЧНОЙ СИСТЕМЫ ОБЪЁМ",
    (DOC, 4): "КОНТРОЛЬ УРОВНЯ МАСЛА ЩУП",
}


def make_store(tmp_path, sheets=SHEETS) -> Store:
    """Store с листами фиксура; графовые узлы не нужны — поиск по pages."""
    store = Store(str(tmp_path / "graph.db"))
    for (document, page), text in sheets.items():
        store.upsert_page(document, page, "Ми-8МТВ", "", "", text,
                          normalize_for_search(text))
    return store


def neighbor_keys(answer) -> set[tuple[str, int]]:
    """Множество (документ, лист) из списка соседей ответа."""
    return {(item["document"], item["page"])
            for item in getattr(answer, "neighbors", [])}


# --- Базовое поведение ------------------------------------------------------


def test_neighbor_shown_with_labels(tmp_path):
    """Соседи листа 2: лист 1 «предыдущий лист», лист 3 «следующий лист…»."""
    store = make_store(tmp_path)
    try:
        answer = build_answer(store, "замена масла двигателя инструмент",
                              None)
        assert not answer.refusal
        assert neighbor_keys(answer) == {(DOC, 1), (DOC, 3)}
        by_page = {(item["document"], item["page"]): item["label"]
                   for item in answer.neighbors}
        assert by_page[(DOC, 1)] == LABEL_PREV
        assert by_page[(DOC, 3)] == LABEL_NEXT
    finally:
        store.close()


def test_document_boundary_no_neighbor(tmp_path):
    """Первый лист без соседа «−1», последний — без «+1»."""
    store = make_store(tmp_path)
    try:
        first = build_answer(store, "слива масла перед заменой фильтра",
                             None)
        assert neighbor_keys(first) == {(DOC, 2)}
        assert all(item["label"] == LABEL_NEXT
                   for item in first.neighbors)
        last = build_answer(store, "контроль уровня масла щуп", None)
        assert neighbor_keys(last) == {(DOC, 3)}
        assert all(item["label"] == LABEL_PREV
                   for item in last.neighbors)
    finally:
        store.close()


def test_flagged_neighbors_excluded(tmp_path):
    """needs_rescan и is_service в соседи не берутся; блок пустой."""
    store = make_store(tmp_path)
    try:
        assert store.mark_rescan(DOC, 3, "auto: тест")
        assert store.mark_service(DOC, 1, "auto: тест")
        answer = build_answer(store, "замена масла двигателя инструмент",
                              None)
        assert neighbor_keys(answer) == set()
    finally:
        store.close()


def test_deep_rank_neighbor_still_listed(tmp_path):
    """Лист из выдачи глубже топ-5 не мешает соседу от топ-5 (кейс e18).

    Ф1.6/H1 закрывал e18: ожидаемый лист соседен топ-5, даже если сам
    стоит на ранге 6–10 выдачи. Сосед строится от топ-5; дубликаты
    исключаются внутри списка соседей и против топ-5, но не против
    глубокой части выдачи.
    """
    from pdfgraph.answer import Fragment, collect_neighbors

    sheets = {("Р.pdf", page): f"ТЕКСТ ЛИСТА {page}"
              for page in (9, 10, 13, 14, 17, 18, 21, 22, 23, 27, 28)}
    store = make_store(tmp_path, sheets)
    try:
        fragments = [
            Fragment(document="Р.pdf", page=page, system_code="",
                     subsystem_code="", snippet="т", char_start=0,
                     char_end=1, score=1.0 - index * 0.01, kind="semantic")
            for index, page in enumerate((22, 28, 14, 10, 18, 23))]
        neighbors = collect_neighbors(store, fragments)
        keys = [(item["document"], item["page"]) for item in neighbors]
        assert ("Р.pdf", 23) in keys
        assert ("Р.pdf", 21) in keys
        assert len(keys) == len(set(keys))
    finally:
        store.close()


def test_duplicates_not_repeated(tmp_path):
    """Смежные фрагменты топ-5: сосед уникален и не дублирует топ-5."""
    store = make_store(tmp_path)
    try:
        answer = build_answer(store, "замена заправка", None)
        top5 = {(fragment.document, fragment.page)
                for fragment in answer.fragments[:5]}
        keys = [item for item in
                [(item["document"], item["page"])
                 for item in answer.neighbors]]
        assert len(keys) == len(set(keys)), "соседи повторяются"
        assert set(keys) & top5 == set(), "сосед дублирует топ-5"
        assert neighbor_keys(answer) == {(DOC, 1), (DOC, 4)}
    finally:
        store.close()


# --- Изоляция основной выдачи ------------------------------------------------


def test_flag_off_fragments_identical(tmp_path, monkeypatch):
    """Флаг off: neighbors пуст, выдача байт-в-байт равна базовой."""
    store = make_store(tmp_path)
    try:
        monkeypatch.setenv("PDFGRAPH_NEIGHBORS", "0")
        off = build_answer(store, "замена масла двигателя инструмент", None)
        assert getattr(off, "neighbors", None) == []
        baseline = [dataclasses.asdict(fragment)
                    for fragment in off.fragments]
        monkeypatch.setenv("PDFGRAPH_NEIGHBORS", "1")
        on = build_answer(store, "замена масла двигателя инструмент", None)
        assert neighbor_keys(on)  # соседи появились
        assert [dataclasses.asdict(fragment)
                for fragment in on.fragments] == baseline
    finally:
        store.close()


# --- Соседи не в цитатах -----------------------------------------------------


def test_neighbors_not_in_citations(tmp_path, capsys):
    """Цитаты — только листы топ-5; соседи в отдельном списке ссылок."""
    store = make_store(tmp_path)
    try:
        answer = build_answer(store, "щуп", None)
        assert not answer.refusal
        assert neighbor_keys(answer) == {(DOC, 3)}
        checks = verify_citations(answer, store)
        assert checks
        cited = {(check.document, check.page) for check in checks}
        top5 = {(fragment.document, fragment.page)
                for fragment in answer.fragments[:5]}
        assert cited <= top5
        assert cited & neighbor_keys(answer) == set()
        # Текстовый вывод: блок соседей есть, самих соседей среди цитат нет.
        rows = _evidence_rows(answer, checks, store)
        _print_text(answer, rows, all_verified(checks), store=store)
        text = capsys.readouterr().out
        assert "Соседние листы" in text
        assert "может быть продолжение" in text
        assert f"{DOC} лист 3" in text
        # JSON: ключ neighbors; цитаты не содержат соседей.
        _print_json(answer, rows, all_verified(checks))
        payload = json.loads(capsys.readouterr().out)
        assert payload["neighbors"] == [
            {"document": DOC, "page": 3, "label": LABEL_PREV}]
        json_cited = {(row["document"], row["page"])
                      for claim in payload["claims"]
                      for row in claim["citations"]}
        assert json_cited & neighbor_keys(answer) == set()
    finally:
        store.close()


def test_no_neighbors_block_on_refusal(tmp_path, capsys):
    """При отказе блок соседей в тексте не печатается."""
    from pdfgraph.answer import Answer, Claim, Fragment

    answer = Answer(
        question="вопрос", claims=[], fragments=[
            Fragment(document=DOC, page=2, system_code="", subsystem_code="",
                     snippet="текст", char_start=0, char_end=5, score=1.0,
                     kind="text")],
        refusal=True, reason="нет подтверждений в документах",
        neighbors=[{"document": DOC, "page": 1, "label": LABEL_PREV}])
    _print_text(answer, [], False, store=None)
    text = capsys.readouterr().out
    assert "Соседние листы" not in text


# --- Метрика hit_at_5_with_neighbors -----------------------------------------


def _golden(expected_pages: tuple[int, ...]) -> GoldenQuestion:
    return GoldenQuestion(id="x01", mode="fault", status="ready",
                          question="вопрос", aircraft_type="Ми-8МТВ",
                          ata="", expected_doc=DOC,
                          expected_pages=expected_pages, notes="")


def test_metric_counts_neighbor_hit(tmp_path):
    """Ожидаемый лист только в соседях: hit_at_5=0, with_neighbors=1."""
    from pdfgraph.answer import Answer, Fragment

    fragment = Fragment(document=DOC, page=2, system_code="",
                        subsystem_code="", snippet="текст", char_start=0,
                        char_end=5, score=1.0, kind="text")
    answer = Answer(question="вопрос", claims=[], fragments=[fragment],
                    neighbors=[{"document": DOC, "page": 3,
                                "label": LABEL_NEXT}])
    row = _row_for(_golden((3,)), answer, [], 0.0)
    assert row["hit"] is False
    assert row["hit_at_5_with_neighbors"] is True
    metrics = _bucket_metrics([row])
    assert metrics["hit_at_5"]["value"] == 0.0
    assert metrics["hit_at_5_with_neighbors"]["value"] == 1.0


def test_metric_any_expected_page_in_neighbors(tmp_path):
    """Несколько expected_pages: любой лист в топ-5 или в соседях — hit."""
    from pdfgraph.answer import Answer, Fragment

    fragment = Fragment(document=DOC, page=2, system_code="",
                        subsystem_code="", snippet="текст", char_start=0,
                        char_end=5, score=1.0, kind="text")
    answer = Answer(question="вопрос", claims=[], fragments=[fragment],
                    neighbors=[{"document": DOC, "page": 3,
                                "label": LABEL_NEXT}])
    row = _row_for(_golden((3, 9)), answer, [], 0.0)
    assert row["hit"] is False
    assert row["hit_at_5_with_neighbors"] is True
    row_hit = _row_for(_golden((2, 9)), answer, [], 0.0)
    assert row_hit["hit"] is True
    assert row_hit["hit_at_5_with_neighbors"] is True
    row_miss = _row_for(_golden((7, 9)), answer, [], 0.0)
    assert row_miss["hit"] is False
    assert row_miss["hit_at_5_with_neighbors"] is False
