import pytest


@pytest.mark.parametrize(
    ("selected_index", "expected_type"),
    [(None, None), (2, "КА-32"), (1, "Ми-8МТВ"), (3, "Ми-8Т")],
)
def test_ask_passes_selected_aircraft_type(
    fx_store_path, monkeypatch, selected_index, expected_type
):
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from unittest.mock import Mock

    from pdfgraph.answer import Answer, Claim
    from pdfgraph.query import Fragment
    import pdfgraph.gui.app as gui_app

    app = QApplication.instance() or QApplication([])
    document = "РТЭ кн.1.pdf"
    text = (
        "023.11.30 АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А "
        "СМ. 028.10.00"
    )
    answer = Answer(
        question="радиомаяк",
        claims=[Claim(text=text, evidence=[0])],
        fragments=[Fragment(
            document=document,
            page=47,
            system_code="023.00.00",
            subsystem_code="023.11.30",
            snippet=text,
            char_start=0,
            char_end=len(text),
            score=1.0,
            kind="text",
        )],
    )
    build_answer = Mock(return_value=answer)
    monkeypatch.setattr(gui_app, "build_answer", build_answer)

    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    assert window.aircraft_combo.currentIndex() == 0
    if selected_index is not None:
        window.aircraft_combo.setCurrentIndex(selected_index)

    window.ask("радиомаяк")

    assert build_answer.call_args.kwargs["aircraft_type"] == expected_type
    window.close()
