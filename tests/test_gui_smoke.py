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
