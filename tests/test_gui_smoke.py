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
