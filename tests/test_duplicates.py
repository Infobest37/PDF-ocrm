"""Карточка 0.1б, Шаг 2: пометки дублей документов (таблица document_aliases).

Тесты гарантируют: пометка убирает листы дубля из каждой ветки поиска
(код, партномер, FTS AND/OR/префикс, trigram), снятие пометки возвращает
выдачу к исходной, счётчики pages/nodes/edges не меняются, а листы и их
текст не удаляются. CLI-команды идемпотентны, каждая запись попадает в
лог рядом с базой.
"""

import sqlite3
import sys

import pytest

from pdfgraph.query import search
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search, normalize_part_number

DUP_DOC = "РТЭ кн.6 (дубль).pdf"
CAN_DOC = "РТЭ кн.6 (канон).pdf"
TEXT_47 = "023.11.30 РАДИОМАЯК ПАРМ-406А ДЕТАЛЬ 8МТ.6101.100"
TEXT_48 = "023.11.30 РАДИОМАЯК В СОСТАВЕ ВЕРТОЛЁТА"
TEXT_12 = "028.10.00 ГИДРОАГРЕГАТ ОСНОВНОЙ"
PART = "8МТ.6101.100"


def _fill(store: Store) -> None:
    """Два документа-клона на трёх одинаковых листах; part-узел один."""
    type_id = store.add_node("type", "Ми-8МТВ", label="Ми-8МТВ")
    sys23 = store.add_node(
        "system", "023.00.00", label="СВЯЗНОЕ РАДИООБОРУДОВАНИЕ")
    sub = store.add_node(
        "subsystem", "023.11.30", label="АВАРИЙНЫЙ РАДИОМАЯК ПАРМ-406А")
    sys28 = store.add_node("system", "028.10.00", label="ГИДРОАГРЕГАТ")
    part = store.add_node("part", normalize_part_number(PART))
    store.add_edge(sub, sys23, "contained_in")
    store.add_edge(sys23, type_id, "contained_in")
    store.add_edge(sys28, type_id, "contained_in")
    sheets = {
        47: (TEXT_47, "023.00.00", "023.11.30"),
        48: (TEXT_48, "023.00.00", "023.11.30"),
        12: (TEXT_12, "028.10.00", ""),
    }
    for document in (DUP_DOC, CAN_DOC):
        doc = store.add_node("document", document, label=document)
        for page, (text, system_code, subsystem_code) in sheets.items():
            sheet = store.add_node(
                "sheet", "%s:%d" % (document, page),
                document=document, page=page)
            store.add_edge(doc, sheet, "documented_on",
                           document=document, page=page)
            container = sub if subsystem_code else sys28
            store.add_edge(sheet, container, "contained_in",
                           document=document, page=page)
            if page == 47:
                store.add_edge(sheet, part, "has_part",
                               document=document, page=page)
            store.upsert_page(document, page, "Ми-8МТВ", system_code,
                              subsystem_code, text,
                              normalize_for_search(text))


def _count(connection: sqlite3.Connection, table: str) -> int:
    """Число строк в таблице (проверка целостности счётчиков)."""
    return connection.execute(
        "SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def _branch_hits(store: Store) -> dict:
    """Выдача каждой ветки: {(документ, лист)} и вес лидера ветки.

    Вес лидера (penalty ветки) доказывает, что запрос дошёл именно до
    этой ветки: 1.0 — точный код/партномер/FTS-AND, 0.8 — OR, 0.6 —
    префикс, 0.5 — trigram.
    """
    def hits(question: str):
        fragments = search(store, question)
        scores = [fragment.score for fragment in fragments]
        return (
            {(fragment.document, fragment.page) for fragment in fragments},
            max(scores) if scores else 0.0,
        )

    return {
        "code_system": hits("023.00.00"),
        "code_subsystem": hits("023.11.30"),
        "part": hits(PART),
        "fts_and": hits("радиомаяк"),
        "fts_or": hits("радиомаяк гидроагрегат"),
        "fts_prefix": hits("радиом"),
        "trigram": hits("омаяк"),
    }


def _expected_penalty() -> dict:
    """Ожидаемые веса лидеров веток из query.py."""
    return {
        "code_system": 1.0, "code_subsystem": 1.0, "part": 1.0,
        "fts_and": 1.0, "fts_or": 0.8, "fts_prefix": 0.6, "trigram": 0.5,
    }


def test_mark_filters_every_branch(tmp_path):
    """Пометка убирает листы дубля из всех веток; счётчики — без потерь."""
    store = Store(str(tmp_path / "g.db"))
    try:
        _fill(store)
        before_hits = _branch_hits(store)
        before_stats = dict(store.stats())
        expected = _expected_penalty()
        for name, (_, weight) in before_hits.items():
            assert abs(weight - expected[name]) < 0.05, (name, weight)
        assert any(doc == DUP_DOC for doc, _ in before_hits["fts_and"][0])
        assert _count(store._connection, "document_aliases") == 0

        touched = store.mark_duplicate(
            DUP_DOC, CAN_DOC, reason="дубль кн.6, правило 2")
        assert touched == [(DUP_DOC, 12), (DUP_DOC, 47), (DUP_DOC, 48)]

        after_hits = _branch_hits(store)
        for name, (hit_set, weight) in after_hits.items():
            assert all(doc == CAN_DOC for doc, _ in hit_set), name
            assert abs(weight - expected[name]) < 0.05, (name, weight)

        # Ничего не удалено: страницы, узлы, рёбра и текст листа на месте.
        assert dict(store.stats()) == before_stats
        assert _count(store._connection, "pages") == 6
        assert _count(store._connection, "nodes") == 13
        assert _count(store._connection, "edges") == 17
        assert _count(store._connection, "pages_fts") == 6
        assert _count(store._connection, "pages_fts_trigram") == 6
        assert store.page_text(DUP_DOC, 47) == TEXT_47
        # Пометки: дубль + канон.
        aliases = store.list_aliases()
        assert len(aliases) == 2
        statuses = {name: status
                    for name, status, _c, _r, _t in aliases}
        assert statuses == {DUP_DOC: "duplicate", CAN_DOC: "canonical"}
    finally:
        store.close()


def test_unmark_restores_original_hits(tmp_path):
    """Снятие пометки возвращает выдачу каждой ветки к исходной."""
    store = Store(str(tmp_path / "g.db"))
    try:
        _fill(store)
        original = _branch_hits(store)
        original_stats = dict(store.stats())
        store.mark_duplicate(DUP_DOC, CAN_DOC)
        store.unmark_duplicate(DUP_DOC)
        restored = _branch_hits(store)
        assert restored == original
        assert dict(store.stats()) == original_stats
        assert _count(store._connection, "document_aliases") == 0
    finally:
        store.close()


def test_mark_and_unmark_are_idempotent(tmp_path):
    """Повторные вызовы оставляют то же состояние и не плодят строки."""
    store = Store(str(tmp_path / "g.db"))
    try:
        _fill(store)
        store.mark_duplicate(DUP_DOC, CAN_DOC, reason="раз")
        store.mark_duplicate(DUP_DOC, CAN_DOC, reason="повтор")
        assert _count(store._connection, "document_aliases") == 2
        hits = _branch_hits(store)["code_system"][0]
        assert all(doc == CAN_DOC for doc, _ in hits)

        store.unmark_duplicate(DUP_DOC)
        store.unmark_duplicate(DUP_DOC)  # повтор — без ошибки и без эффекта
        assert _count(store._connection, "document_aliases") == 0
    finally:
        store.close()


def test_mark_validates_documents(tmp_path):
    """Ошибки входа: документ отсутствует; имя равно канону."""
    store = Store(str(tmp_path / "g.db"))
    try:
        _fill(store)
        with pytest.raises(LookupError):
            store.mark_duplicate("нет такого.pdf", CAN_DOC)
        with pytest.raises(LookupError):
            store.mark_duplicate(DUP_DOC, "нет и этого.pdf")
        with pytest.raises(ValueError):
            store.mark_duplicate(CAN_DOC, CAN_DOC)
        assert _count(store._connection, "document_aliases") == 0
        store.unmark_duplicate("нет такого.pdf")  # идемпотентный no-op
    finally:
        store.close()


def test_cli_mark_unmark_roundtrip(tmp_path, capsys):
    """CLI: пометка/снятие сквозными командами, лог рядом с базой."""
    from pdfgraph.cli import main

    store_path = str(tmp_path / "g.db")
    store = Store(store_path)
    try:
        _fill(store)
    finally:
        store.close()

    assert main(["mark-duplicate", store_path, "--name", DUP_DOC,
                 "--canonical", CAN_DOC, "--reason", "дубль кн.6"]) == 0
    first_out = capsys.readouterr().out
    assert DUP_DOC in first_out and CAN_DOC in first_out
    assert "лист" in first_out

    assert main(["mark-duplicate", store_path, "--name", DUP_DOC,
                 "--canonical", CAN_DOC, "--reason", "дубль кн.6"]) == 0
    capsys.readouterr()

    assert main(["unmark-duplicate", store_path, "--name", DUP_DOC]) == 0
    restored_out = capsys.readouterr().out
    assert DUP_DOC in restored_out

    log_text = (tmp_path / "duplicates.log").read_text(encoding="utf-8")
    assert log_text.count(" mark-duplicate ") == 2
    assert log_text.count(" unmark-duplicate ") == 1

    # Ошибки входа: exit 2 и объяснение в stderr.
    assert main(["mark-duplicate", store_path, "--name", "нет такого.pdf",
                 "--canonical", CAN_DOC]) == 2
    assert "error" in capsys.readouterr().err
    assert main(["mark-duplicate", store_path, "--name", CAN_DOC,
                 "--canonical", CAN_DOC]) == 2
    assert "error" in capsys.readouterr().err
    assert main(["unmark-duplicate", store_path, "--name", DUP_DOC]) == 0

    reader = Store(store_path)
    try:
        assert _count(reader._connection, "document_aliases") == 0
        hits = {doc for doc, _ in _branch_hits(reader)["fts_and"][0]}
        assert {DUP_DOC, CAN_DOC}.issubset(hits)
    finally:
        reader.close()


def test_cli_mark_output_survives_codepage_console(tmp_path, monkeypatch):
    """Печать mark-duplicate не роняет команду в cp1251-консоли Windows."""
    from pdfgraph.cli import main

    class _CodePageStream:
        """TextIOWrapper-подобный поток: cp1251 без «→» и «…»."""

        encoding = "cp1251"

        def __init__(self) -> None:
            self.written = ""

        def write(self, text: str) -> int:
            for index, char in enumerate(text):
                try:
                    char.encode(self.encoding)
                except UnicodeEncodeError:
                    raise UnicodeEncodeError(
                        self.encoding, text, index, index + 1,
                        "not representable") from None
            self.written += text
            return len(text)

    store_path = str(tmp_path / "g.db")
    store = Store(store_path)
    try:
        _fill(store)
    finally:
        store.close()

    fake = _CodePageStream()
    monkeypatch.setattr(sys, "stdout", fake)
    assert main(["mark-duplicate", store_path, "--name", DUP_DOC,
                 "--canonical", CAN_DOC]) == 0
    assert DUP_DOC in fake.written and CAN_DOC in fake.written
    assert "листов скрыто" in fake.written


def test_refusal_answer_without_duplicate_documents(tmp_path):
    """Ответ на вопрос радиомаяка после пометки — только по канону."""
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import all_verified, verify_citations

    store = Store(str(tmp_path / "g.db"))
    try:
        _fill(store)
        store.mark_duplicate(DUP_DOC, CAN_DOC)
        answer = build_answer(store, "радиомаяк ПАРМ", "Ми-8МТВ")
        assert not answer.refusal
        assert all(fragment.document == CAN_DOC
                   for fragment in answer.fragments)
        assert all_verified(verify_citations(answer, store))
    finally:
        store.close()