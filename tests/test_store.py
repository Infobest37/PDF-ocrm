import sqlite3

import pytest


def test_nodes_and_edges_are_idempotent(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    system = store.add_node(
        "system",
        "023.00.00",
        label="СВЯЗНОЕ РАДИООБОРУДОВАНИЕ",
        document="RTЭ кн.1.pdf",
        page=47,
    )
    assert store.add_node("system", "023.00.00") == system
    node = store._connection.execute(
        "SELECT label, document, page FROM nodes WHERE id = ?", (system,)
    ).fetchone()
    assert node == ("СВЯЗНОЕ РАДИООБОРУДОВАНИЕ", "RTЭ кн.1.pdf", 47)

    store.close()


def test_edge_detail_updates_without_duplicate(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    system = store.add_node("system", "023.00.00")
    subsystem = store.add_node("subsystem", "023.11.30")
    store.add_edge(subsystem, system, "contained_in", detail="первое описание")
    store.add_edge(subsystem, system, "contained_in", detail="уточнённое описание")

    assert store.stats() == {"nodes": 2, "edges": 1, "sheets": 0, "pending": 0}
    detail = store._connection.execute(
        "SELECT detail FROM edges WHERE src = ? AND dst = ?", (subsystem, system)
    ).fetchone()[0]
    assert detail == "уточнённое описание"
    store.close()


def test_pages_round_trip_and_fts_upsert_is_idempotent(tmp_path):
    from pdfgraph.store import Store

    database = tmp_path / "g.db"
    store = Store(str(database))
    store.upsert_page(
        "RTЭ кн.1.pdf", 47, "Ми-8МТВ", "023.00.00", "023.11.30", "полный текст", "search text"
    )
    store.upsert_page(
        "RTЭ кн.1.pdf", 47, "Ми-8МТВ", "023.00.00", "023.11.30", "обновленный текст", "updated search"
    )

    assert store.page_text("RTЭ кн.1.pdf", 47) == "обновленный текст"
    store.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 1
        assert connection.execute("SELECT body FROM pages_fts").fetchone()[0] == "updated search"


def test_schema_wal_and_stats_include_sheet_and_pending_nodes(tmp_path):
    from pdfgraph.store import Store

    database = tmp_path / "g.db"
    store = Store(str(database))
    store.add_node("sheet", "doc:1")
    store.add_node("pending_reference", "ref:1")
    assert store.stats() == {"nodes": 2, "edges": 0, "sheets": 1, "pending": 1}
    assert store._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        store.add_edge(999, 1000, "contained_in")
    store.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "2"
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {"idx_edges_dst", "idx_pages_system"} <= indexes


def test_page_text_returns_empty_string_for_missing_page(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    assert store.page_text("missing.pdf", 1) == ""
    store.close()


def test_mark_defect_is_idempotent_and_defect_sheets_sorted(tmp_path):
    """Задача 2.7: листы из отчёта о пересканировании (пустые листы туда
    не попадают — это свойство ``pdfprepare.quality``, здесь — хранилище)."""
    from pdfgraph.store import Store

    database = str(tmp_path / "g.db")
    store = Store(database)
    assert store.defect_sheets() == []
    store.mark_defect("d.pdf", 47)
    store.mark_defect("d.pdf", 12)
    store.mark_defect("a.pdf", 3)
    store.mark_defect("d.pdf", 47)  # идемпотентно: повтор не дублирует
    assert store.defect_sheets() == [("a.pdf", 3), ("d.pdf", 12), ("d.pdf", 47)]
    store.close()

    reopened = Store(database)
    try:
        assert reopened.defect_sheets() == [("a.pdf", 3), ("d.pdf", 12), ("d.pdf", 47)]
    finally:
        reopened.close()


def test_find_node_id_and_has_node_are_read_only(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    assert store.find_node_id("system", "023.00.00") is None
    assert not store.has_node("system", "023.00.00")
    node_id = store.add_node("system", "023.00.00")
    assert store.has_node("system", "023.00.00")
    assert store.find_node_id("system", "023.00.00") == node_id
    # другой kind с тем же ключом — это другой узел
    assert store.find_node_id("subsystem", "023.00.00") is None
    # зонд ничего не создаёт
    assert store.stats()["nodes"] == 1
    store.close()


def test_sheets_by_code_selects_joins_and_limits(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    store.upsert_page("d.pdf", 1, "Ми-8МТВ", "023.00.00", "023.11.30", "т1", "т1")
    store.upsert_page("d.pdf", 2, "Ми-26", "023.00.00", "", "т2", "т2")

    both = store.sheets_by_code("023.00.00")
    assert both == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
        ("d.pdf", 2, "023.00.00", ""),
    ]
    assert store.sheets_by_code("023.11.30") == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
    ]
    assert store.sheets_by_code("023.00.00", aircraft_type="Ми-8МТВ") == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
    ]
    assert store.sheets_by_code("023.00.00", limit=1) == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
    ]
    assert store.sheets_by_code("999.99.99") == []
    store.close()


def test_sheets_with_part_follows_has_part_edges_only(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    part = store.add_node("part", "8МТ.6101.100")
    sheet1 = store.add_node("sheet", "d.pdf:1")
    sheet2 = store.add_node("sheet", "d.pdf:2")
    sheet3 = store.add_node("sheet", "d.pdf:3")
    store.add_edge(sheet1, part, "has_part", document="d.pdf", page=1)
    store.add_edge(sheet2, part, "has_part", document="d.pdf", page=2)
    # ребро другого вида до той же детали — не попадание
    store.add_edge(sheet3, part, "has_figure", document="d.pdf", page=3)
    store.upsert_page("d.pdf", 1, "Ми-8МТВ", "023.00.00", "023.11.30", "т1", "т1")
    store.upsert_page("d.pdf", 2, "Ми-26", "023.00.00", "", "т2", "т2")
    store.upsert_page("d.pdf", 3, "Ми-8МТВ", "029.00.00", "", "т3", "т3")

    assert store.sheets_with_part("8МТ.6101.100") == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
        ("d.pdf", 2, "023.00.00", ""),
    ]
    assert store.sheets_with_part("8МТ.6101.100", aircraft_type="Ми-8МТВ") == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
    ]
    assert store.sheets_with_part("8МТ.6101.100", limit=1) == [
        ("d.pdf", 1, "023.00.00", "023.11.30"),
    ]
    assert store.sheets_with_part("НЕТ-ТАКОЙ.КЛЮЧ") == []
    store.close()


def test_search_pages_returns_ranked_tuples(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    store.upsert_page("d.pdf", 1, "Ми-8МТВ", "023.00.00", "", "текст одного листа", "текст одного листа")
    store.upsert_page("d.pdf", 2, "Ми-26", "023.00.00", "", "текст другого листа", "текст другого листа")

    rows = store.search_pages('"другого"')
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [("d.pdf", 2, "023.00.00", "")]
    assert isinstance(rows[0][4], float) and rows[0][4] < 0  # bm25: меньше — лучше

    assert [(r[0], r[1]) for r in store.search_pages('"текст" AND "другого"')] == [
        ("d.pdf", 2),
    ]
    assert store.search_pages('"одного" AND "другого"') == []           # нет листа с обоими
    assert len(store.search_pages('"одного" OR "другого"')) == 2        # OR берёт оба
    assert store.search_pages('"одного" AND "другого"', aircraft_type="Ми-8МТВ") == []
    assert [(r[0], r[1]) for r in store.search_pages('"текст"', aircraft_type="Ми-26")] == [
        ("d.pdf", 2),
    ]
    assert len(store.search_pages('"текст"', limit=1)) == 1
    assert store.search_pages('"отсутствует"') == []
    # page из pages_fts (строка) возвращается как int через join с pages
    assert all(isinstance(r[1], int) for r in store.search_pages('"текст"'))
    store.close()
