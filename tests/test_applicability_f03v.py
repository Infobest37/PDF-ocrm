# -*- coding: utf-8 -*-
"""Ф0.3в: слои и применимость в ответе и в поиске.

Фильтр по типу ВС в пяти ветвях поиска (код, партномер, FTS, триграмма,
вектор), поля документа в цитатах (doc_layer/doc_applicability/
doc_revision), предупреждение о чужом борте и честный отказ для борта
без документации.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sqlite3

import pytest

from pdfgraph.answer import applicability_verdict, build_answer
from pdfgraph.cli import _base_aircraft_type, _evidence_rows
from pdfgraph.query import search, vector_search
from pdfgraph.store import Store
from pdfgraph.verify import verify_citations

T1 = "Ми-8МТВ"
T2 = "Ми-26"
D1 = "РТЭ Т1.pdf"
D2 = "РЭ Т2.pdf"
CODE = "072.00.00"
PART = "8МТ.6101.100"


def make_store(tmp_path) -> Store:
    """Два типа ВС, одинаковый код и партномер в обоих документах."""
    store = Store(str(tmp_path / "graph.db"))
    for document, aircraft in ((D1, T1), (D2, T2)):
        store.upsert_page(document, 1, aircraft, CODE, "",
                          f"{CODE} МАСЛО ФИЛЬТР 22810", code_norm(document))
    part_id = store.add_node("part", PART)
    for document in (D1, D2):
        sheet_id = store.add_node("sheet", f"{document}:1",
                                  document=document, page=1)
        store.add_edge(sheet_id, part_id, "has_part", detail=PART,
                       document=document, page=1)
    return store


def code_norm(document: str) -> str:
    """Нормализованный текст листа (для upsert_page)."""
    from pdfprepare.normalize import normalize_for_search
    return normalize_for_search(f"{CODE} МАСЛО ФИЛЬТР 22810 {document}")


def add_documents_row(store: Store, document: str, layer: str,
                      scope: str, serials: str = "",
                      revision_no: int | None = None,
                      revision_date: str = "") -> None:
    """Строка documents для сценария ответа."""
    store._connection.execute(
        "INSERT OR REPLACE INTO documents (document, family, aircraft_type, layer, "
        "target, applicability_serials, applicability_scope, "
        "applicability_source, revision_no, revision_date, supersedes, "
        "review_status, notes) VALUES (?, 'Ми-8', ?, ?, '', ?, ?, 'name', "
        "?, ?, '', 'ok', '')",
        (document, T1, layer, serials, scope, revision_no, revision_date))
    store._connection.commit()


# --- Фильтр типа в пяти ветвях ----------------------------------------------


def test_code_branch_filters_type(tmp_path):
    store = make_store(tmp_path)
    try:
        hits = search(store, CODE, T1, 10)
        assert {f.document for f in hits} == {D1}
        hits_all = search(store, CODE, None, 10)
        assert {f.document for f in hits_all} == {D1, D2}
    finally:
        store.close()


def test_part_branch_filters_type(tmp_path):
    store = make_store(tmp_path)
    try:
        hits = search(store, PART, T1, 10)
        assert {f.document for f in hits} == {D1}
    finally:
        store.close()


def test_fts_branch_filters_type(tmp_path):
    store = make_store(tmp_path)
    try:
        hits = search(store, "масло фильтр", T1, 10)
        assert hits, "fts-ветвь должна находить листы"
        assert {f.document for f in hits} == {D1}
    finally:
        store.close()


def test_trigram_branch_filters_type(tmp_path):
    store = make_store(tmp_path)
    try:
        hits = store.search_trigrams("%масл%", T1)
        documents = {row[0] for row in hits}
        assert documents == {D1}
    finally:
        store.close()


def test_vector_branch_filters_type(tmp_path, monkeypatch):
    from pdfgraph import embedding

    store = make_store(tmp_path)
    try:
        fake = [((D2, 1, 0), 0.99), ((D1, 1, 0), 0.98)]
        monkeypatch.setattr(embedding, "search_chunks",
                            lambda *a, **k: fake)
        hits = vector_search(store, "масло", limit=10, aircraft_type=T1)
        assert {f.document for f in hits} == {D1}
        monkeypatch.setattr(embedding, "search_chunks",
                            lambda *a, **k: fake)
        hits_all = vector_search(store, "масло", limit=10)
        assert {f.document for f in hits_all} == {D1, D2}
    finally:
        store.close()


# --- Дефолт типа базы ---------------------------------------------------------


def test_base_aircraft_type(tmp_path):
    store = make_store(tmp_path)
    try:
        # Два типа в базе — дефолт не определяется.
        assert _base_aircraft_type(store) is None
        store._connection.execute(
            "DELETE FROM pages WHERE document = ?", (D2,))
        store._connection.commit()
        assert _base_aircraft_type(store) == T1
        store._connection.execute(
            "UPDATE pages SET aircraft_type = ''")
        store._connection.commit()
        assert _base_aircraft_type(store) is None
    finally:
        store.close()


# --- Поля документа в цитатах ---------------------------------------------------


def test_doc_fields_in_evidence(tmp_path, capsys):
    store = make_store(tmp_path)
    try:
        add_documents_row(store, D1, "supplement", "serials",
                          serials="22217, 22562", revision_no=11,
                          revision_date="2018-12-13")
        answer = build_answer(store, CODE, T1)
        checks = verify_citations(answer, store)
        rows = _evidence_rows(answer, checks, store)
        assert rows
        first = rows[0]
        assert first["doc_layer"] == "supplement"
        assert first["doc_applicability"] == "serials: 22217, 22562"
        assert first["doc_revision"] == "изм. 11 от 2018-12-13"
    finally:
        store.close()


# --- Предупреждение и отказ -----------------------------------------------------


def test_warning_foreign_board(tmp_path):
    """П. 3: лидер — дополнение к чужим бортам, базовые есть → ответ с
    предупреждением (отказ не срабатывает, потому что базовые есть)."""
    from pdfgraph.answer import Fragment

    store = make_store(tmp_path)
    try:
        add_documents_row(store, D1, "supplement", "serials",
                          serials="22217, 22562")
        add_documents_row(store, "База Т1.pdf", "base", "all")
        leader = Fragment(document=D1, page=1, system_code=CODE,
                          subsystem_code="", snippet="т", char_start=0,
                          char_end=1, score=1.0, kind="text")
        reason, warning = applicability_verdict(
            store, f"{CODE} борт 22810", [leader])
        assert reason is None
        assert warning == ("документ применим к бортам 22217, 22562; "
                           "ваш борт не указан")
    finally:
        store.close()


def test_no_warning_for_base_or_matching_board(tmp_path):
    from pdfgraph.answer import Fragment

    store = make_store(tmp_path)
    try:
        leader = Fragment(document=D1, page=1, system_code=CODE,
                          subsystem_code="", snippet="т", char_start=0,
                          char_end=1, score=1.0, kind="text")
        add_documents_row(store, D1, "base", "all")
        reason, warning = applicability_verdict(
            store, f"{CODE} борт 22810", [leader])
        assert reason is None and warning == ""
        add_documents_row(store, D1, "supplement", "serials",
                          serials="22810")
        reason, warning = applicability_verdict(
            store, f"{CODE} борт 22810", [leader])
        assert reason is None and warning == ""
    finally:
        store.close()


def test_refusal_unknown_board(tmp_path):
    """П. 4: нет ни подходящих serials, ни базовых → отказ.

    Борт в формате правила Ф0.3а (2xxxxx): 27777 не входит в serials
    дополнения, базовых в documents нет."""
    store = make_store(tmp_path)
    try:
        add_documents_row(store, D1, "supplement", "serials",
                          serials="22217, 22562")
        reason, warning = applicability_verdict(
            store, f"{CODE} борт 27777", [])
        assert reason == "документации по борту 27777 в базе нет"
        assert warning == ""
    finally:
        store.close()


def test_missing_documents_table_keeps_behavior(tmp_path):
    store = make_store(tmp_path)
    try:
        store._connection.execute("DROP TABLE documents")
        store._connection.commit()
        answer = build_answer(store, f"{CODE} борт 27777", T1)
        # Поведение прежнее: бор-отказ не появляется (может быть отказ
        # гейтов по конкретике — он не про борт).
        assert "документации по борту" not in answer.reason
        assert answer.applicability_warning == ""
    finally:
        store.close()
