# -*- coding: utf-8 -*-
"""Ф0.3б: схема v6 documents — миграция, заполнение, CLI, целостность.

Фикстура воспроизводит имена-образцы реальной базы (правила Ф0.3а),
включая пару кн. 7 с решением владельца о supersedes и alias-строкой
дедупликации.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sqlite3

import pytest

from pdfgraph.documents import (
    fill_missing,
    scope_allows,
    set_fields,
    unset_document,
)
from pdfgraph.store import SCHEMA_VERSION, Store

BASE_DOC = "Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 1 (изм. от 11.04.2025).pdf"
SUPP_DOC = "Доп. к РТЭ Д-1594 кн. 1 вертолетов 22810 228811 24543 24543.pdf"
SDK_DOC = "Дополнение к РТЭ и РО вертолета Ми-8МТВ-1 (для оборудованных системой СДК-8).pdf"
SAFIR_DOC = "Вспомогательной силовой установки SafirPG 05-01 (изм. 11 от 13.12.2018).pdf"
K7_NEW = "РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf"
K7_OLD = "Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 7 (файл от 24.08.2023).pdf"
K6_NEW = "РТЭ Ми-8МТВ-1 кн. 6 (изм. от 08.07.2025).pdf"
K6_OLD = "РТЭ Ми-8МТВ-1 кн. 6 (файл от 17.04.2023).pdf"


def make_store(tmp_path, documents=()) -> Store:
    """Store v6 с листами-заглушками перечисленных документов."""
    store = Store(str(tmp_path / "graph.db"))
    for index, document in enumerate(documents, start=1):
        store.upsert_page(document, 1, "Ми-8МТВ", "023.00.00", "",
                          f"ТЕКСТ {index}", f"текст {index}")
    return store


def row(store: Store, document: str) -> dict:
    """Строка documents как словарь (None, если нет)."""
    cursor = store._connection.execute(
        "SELECT * FROM documents WHERE document = ?", (document,))
    cursor.row_factory = sqlite3.Row
    found = cursor.fetchone()
    return dict(found) if found else None


# --- Миграция ---------------------------------------------------------------


def test_migration_creates_documents_table(tmp_path):
    """Store v6: таблица documents существует, schema_version = 6."""
    store = make_store(tmp_path)
    try:
        columns = {row[1] for row in store._connection.execute(
            "PRAGMA table_info(documents)")}
        assert {"document", "family", "aircraft_type", "layer", "target",
                "applicability_serials", "applicability_scope",
                "applicability_source", "revision_no", "revision_date",
                "supersedes", "review_status", "notes"} <= columns
        version = int(store._connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0])
        assert version == SCHEMA_VERSION == 7
    finally:
        store.close()


def test_migration_idempotent(tmp_path):
    """Повторное открытие базы: версия и строки не дублируются."""
    path = str(tmp_path / "graph.db")
    store = Store(path)
    store.upsert_page(BASE_DOC, 1, "Ми-8МТВ", "", "", "т", "т")
    fill_missing(store)
    store.close()
    store = Store(path)  # повторная миграция при открытии
    try:
        version = int(store._connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0])
        assert version == SCHEMA_VERSION
        count = store._connection.execute(
            "SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 1
    finally:
        store.close()


# --- Заполнение --------------------------------------------------------------


def test_fill_rules(tmp_path):
    """Правила Ф0.3а→Ф0.3б: base/serials/equipment/unknown, needs_human."""
    store = make_store(tmp_path, [BASE_DOC, SUPP_DOC, SDK_DOC, SAFIR_DOC])
    try:
        stats = fill_missing(store)
        assert stats["filled"] == 4
        base = row(store, BASE_DOC)
        assert base["layer"] == "base"
        assert base["applicability_scope"] == "all"
        assert base["applicability_serials"] == ""
        assert base["revision_no"] is None
        assert base["revision_date"] == "2025-04-11"
        supp = row(store, SUPP_DOC)
        assert supp["layer"] == "supplement"
        assert supp["applicability_scope"] == "serials"
        assert supp["applicability_serials"] == "22810, 228811, 24543"
        assert supp["revision_no"] is None
        assert supp["revision_date"] == ""
        sdk = row(store, SDK_DOC)
        assert sdk["applicability_scope"] == "equipment"
        assert sdk["review_status"] == "needs_human"
        safir = row(store, SAFIR_DOC)
        assert safir["layer"] == "unknown"
        assert safir["review_status"] == "needs_human"
    finally:
        store.close()


def test_fill_idempotent_and_preserves_manual(tmp_path):
    """Второй fill меняет ноль строк; ручная правка не перезаписывается."""
    store = make_store(tmp_path, [BASE_DOC, SUPP_DOC])
    try:
        fill_missing(store)
        set_fields(store, SUPP_DOC, layer="base", serials="10001",
                   scope="serials")
        before = row(store, SUPP_DOC)
        stats = fill_missing(store)
        assert stats["filled"] == 0
        assert row(store, SUPP_DOC) == before
    finally:
        store.close()


def test_supersedes_auto_and_owner_decision(tmp_path):
    """кн. 6 — авто по датам; кн. 7 — решение владельца + manual/ok."""
    store = make_store(
        tmp_path, [K6_OLD, K6_NEW, K7_OLD, K7_NEW])
    try:
        fill_missing(store)
        k6_new = row(store, K6_NEW)
        assert k6_new["supersedes"] == K6_OLD
        k7_new = row(store, K7_NEW)
        assert k7_new["supersedes"] == K7_OLD
        assert k7_new["applicability_source"] == "manual"
        assert k7_new["review_status"] == "ok"
    finally:
        store.close()


DEFECTS_DOC = "Каталог дефектов Ми-8МТВ кн. 1 (ПиД) (изм. 1 от 25.05.2010).pdf"


def test_supersedes_groups_by_document_class(tmp_path):
    """Регресс Ф0.3б: «РТЭ кн. 1» и «Каталог дефектов кн. 1» — разные
    классы; совпадение номера книги не даёт supersedes."""
    store = make_store(tmp_path, [BASE_DOC, DEFECTS_DOC])
    try:
        fill_missing(store)
        assert row(store, BASE_DOC)["supersedes"] == ""
        assert row(store, DEFECTS_DOC)["supersedes"] == ""
    finally:
        store.close()


# --- Правило наследования ------------------------------------------------------


def test_scope_allows_rule(tmp_path):
    """all — любой борт; serials — только перечисленные; equipment — нет."""
    store = make_store(tmp_path, [BASE_DOC, SUPP_DOC, SDK_DOC])
    try:
        fill_missing(store)
        assert scope_allows(row(store, BASE_DOC), "99999") is True
        assert scope_allows(row(store, SUPP_DOC), "22810") is True
        assert scope_allows(row(store, SUPP_DOC), "99999") is False
        assert scope_allows(row(store, SDK_DOC), "22810") is False
    finally:
        store.close()


# --- docs-set / docs-unset -----------------------------------------------------


def test_set_and_unset(tmp_path):
    """docs-set: поля + manual + ok + строка лога; unset: откат к авто."""
    store = make_store(tmp_path, [SUPP_DOC])
    try:
        fill_missing(store)
        set_fields(store, SUPP_DOC, layer="change", serials="10001",
                   scope="serials")
        edited = row(store, SUPP_DOC)
        assert edited["layer"] == "change"
        assert edited["applicability_serials"] == "10001"
        assert edited["applicability_source"] == "manual"
        assert edited["review_status"] == "ok"
        unset_document(store, SUPP_DOC)
        restored = row(store, SUPP_DOC)
        assert restored["layer"] == "supplement"
        assert restored["applicability_serials"] == "22810, 228811, 24543"
        assert restored["applicability_source"] != "manual"
        assert restored["review_status"] == "ok"
    finally:
        store.close()


# --- Целостность ---------------------------------------------------------------


def test_check_documents_integrity(tmp_path, capsys):
    """pages ⊆ documents: недостающий документ — проблема с кодом 1."""
    import tools.check_documents as checker

    store = make_store(tmp_path, [BASE_DOC, SUPP_DOC])
    try:
        fill_missing(store)
        problems, counts = checker.check(store._connection)
        assert problems == []
        assert counts["documents"] == 2
        # Лист документа без строки documents → проблема.
        store.upsert_page("Новый документ.pdf", 1, "Ми-8МТВ", "", "",
                          "т", "т")
        problems, counts = checker.check(store._connection)
        assert any("Новый документ.pdf" in problem for problem in problems)
    finally:
        store.close()


def test_supersedes_alias_consistency(tmp_path):
    """supersedes ↔ document_aliases: расхождение попадает в отчёт."""
    import tools.check_documents as checker

    store = make_store(tmp_path, [K7_OLD, K7_NEW])
    try:
        fill_missing(store)
        store._connection.execute(
            "INSERT INTO document_aliases (name, canonical_name, status, "
            "reason, created_at) VALUES (?, ?, 'duplicate', '', '2026-11-02')",
            (K7_OLD, K7_NEW))
        store._connection.commit()
        problems, _counts = checker.check(store._connection)
        assert problems == []
        # Ломаем alias: canonical указывает на заменённый документ.
        store._connection.execute(
            "UPDATE document_aliases SET canonical_name = ? WHERE name = ?",
            (K7_OLD, K7_OLD))
        store._connection.commit()
        problems, _counts = checker.check(store._connection)
        assert any("supersedes" in problem for problem in problems)
    finally:
        store.close()
