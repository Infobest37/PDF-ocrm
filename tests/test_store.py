import sqlite3


def test_nodes_and_edges_are_idempotent(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    system = store.add_node("system", "023.00.00", label="СВЯЗНОЕ РАДИООБОРУДОВАНИЕ")
    assert store.add_node("system", "023.00.00") == system
    subsystem = store.add_node("subsystem", "023.11.30")
    store.add_edge(subsystem, system, "contained_in")
    store.add_edge(subsystem, system, "contained_in")

    assert store.stats() == {"nodes": 2, "edges": 1, "sheets": 0, "pending": 0}
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
    store.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"


def test_page_text_returns_empty_string_for_missing_page(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    assert store.page_text("missing.pdf", 1) == ""
    store.close()
