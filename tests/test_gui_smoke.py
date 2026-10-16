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
