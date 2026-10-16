"""Smoke test for the knowledge-graph window (task 3.2, offscreen Qt)."""


def test_window_answers_and_shows_verified(fx_store_path, monkeypatch):
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from pdfgraph.gui.app import MainWindow
    w = MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    rows = w.ask("радиомаяк ПАРМ-406А")
    assert rows and rows[0].verified is True
    assert "Подтверждено" in w.verdict_label.text()
    w.close()


def test_missing_store_is_calm_and_never_created(tmp_path):
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from pdfgraph.gui.app import MainWindow
    missing = str(tmp_path / "no-such-graph.db")
    w = MainWindow(missing, "Коды систем Вертолета.txt")
    rows = w.ask("что угодно")
    assert rows == []
    assert "Хранилище не найдено" in w.verdict_label.text()
    assert not os.path.exists(missing)
    w.close()


def _run_main_capture_window(monkeypatch, args):
    """Call ``pdfgraph.gui.app.main(args)`` offscreen without the event loop.

    Returns ``(exit_code, windows, apps)``; ``windows`` holds the live
    MainWindow instances built by ``main`` and ``apps`` the QApplication.
    The caller must keep both referenced during the test: sip destroys the
    C++ objects with the last Python reference, and ``main``'s own locals
    die on return.
    """
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import pdfgraph.gui.app as gui_app
    from PyQt5.QtWidgets import QApplication
    made = []
    kept_apps = []
    real = gui_app.MainWindow

    def factory(*factory_args, **factory_kwargs):
        window = real(*factory_args, **factory_kwargs)
        made.append(window)
        return window

    def fake_exec(self):
        kept_apps.append(self)  # keep qApp (and its widgets) alive
        return 0

    monkeypatch.setattr(gui_app, "MainWindow", factory)
    monkeypatch.setattr(QApplication, "exec_", fake_exec)
    return gui_app.main(args), made, kept_apps


def test_main_shows_window_and_exits_zero(fx_store_path, monkeypatch):
    """``main`` is the entry of pdfgraph.exe: window opens, close -> exit 0."""
    code, windows, _apps = _run_main_capture_window(
        monkeypatch, ["--store", fx_store_path])
    assert code == 0
    assert len(windows) == 1
    window = windows[0]
    assert window.isVisible()
    assert window.windowTitle() == "Граф знаний Ми-8МТВ"
    rows = window.ask("радиомаяк ПАРМ-406А")
    assert rows and rows[0].verified is True
    window.close()


def test_main_with_corrupt_store_stays_calm(tmp_path, monkeypatch):
    """A broken database file never kills the window: calm verdict instead."""
    corrupt = tmp_path / "graph.db"
    corrupt.write_bytes(b"this is definitely not a sqlite database")
    code, windows, _apps = _run_main_capture_window(
        monkeypatch, ["--store", str(corrupt)])
    assert code == 0
    assert len(windows) == 1
    window = windows[0]
    rows = window.ask("что угодно")
    assert rows == []
    assert "Хранилище не найдено" in window.verdict_label.text()
    window.close()


# ---------------------------------------------------------------------------
# Fix round 1 (review): corruption can surface only on the FIRST QUERY, not
# at Store() open — the schema is intact while table data pages are broken.
# ask()/open_sheet must take the same calm path, never crash the window.
# ---------------------------------------------------------------------------


def _query_corrupt_store(tmp_path):
    """Create a real store, then destroy the ``pages`` root page at byte level.

    ``Store()`` still opens (its schema-only statements never touch the page),
    while ``page_text``/``search_pages`` raise
    ``sqlite3.DatabaseError: database disk image is malformed`` — exactly the
    corruption profile that used to kill the window on the first question.
    """
    import sqlite3
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search
    path = str(tmp_path / "graph.db")
    store = Store(path)
    store.add_node("type", "Ми-8МТВ")
    store.add_node("sheet", "ми8-тест.pdf:1", document="ми8-тест.pdf", page=1)
    store.upsert_page("ми8-тест.pdf", 1, "Ми-8МТВ", "023.00.00", "023.11.30",
                      "АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А",
                      normalize_for_search("АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А"))
    store.close()
    raw = sqlite3.connect(path)
    try:
        rootpage, = raw.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='pages'").fetchone()
        page_size, = raw.execute("PRAGMA page_size").fetchone()
    finally:
        raw.close()
    with open(path, "r+b") as handle:
        handle.seek((rootpage - 1) * page_size)
        handle.write(b"\xff" * 64)
    return path


def test_ask_survives_query_level_corruption(tmp_path):
    """First-query DatabaseError -> calm verdict, empty rows, no crash."""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.app import MainWindow
    path = _query_corrupt_store(tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(path, "Коды систем Вертолета.txt")
    rows = window.ask("радиомаяк ПАРМ-406А")
    assert rows == []
    assert "Хранилище повреждено" in window.verdict_label.text()
    assert window.table.rowCount() == 0
    # The broken connection is dropped; the same question (which reads the
    # damaged page) stays calm on a repeat ask, too.
    assert window.ask("радиомаяк ПАРМ-406А") == []
    assert "Хранилище повреждено" in window.verdict_label.text()
    window.close()


def test_open_sheet_survives_query_level_corruption(tmp_path, monkeypatch):
    """Double-clicking a sheet on a broken store shows the calm text, no crash."""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QDialog, QPlainTextEdit
    from pdfgraph.gui.app import MainWindow
    path = _query_corrupt_store(tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(path, "Коды систем Вертолета.txt")
    dialogs = []
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: (dialogs.append(self), 0)[1])
    window.open_sheet("ми8-тест.pdf", 1)  # must not raise
    assert len(dialogs) == 1
    editor = dialogs[0].findChild(QPlainTextEdit)
    assert "Хранилище повреждено" in editor.toPlainText()
    window.close()
