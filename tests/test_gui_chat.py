import os


def test_two_chat_turns_keep_four_messages_and_preserve_first():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication

    from pdfgraph.gui.chat_widget import ChatWidget

    app = QApplication.instance() or QApplication([])
    chat = ChatWidget()
    submitted = []
    chat.message_submitted.connect(submitted.append)

    chat.question_input.setText("Вопрос 1")
    chat.send_button.click()
    assert chat.question_input.text() == ""
    chat.append_message("assistant", "Ответ 1")

    chat.question_input.setText("Вопрос 2")
    chat.send_button.click()
    assert chat.question_input.text() == ""
    chat.append_message("assistant", "Ответ 2")

    assert submitted == ["Вопрос 1", "Вопрос 2"]
    assert chat.message_history == [
        ("user", "Вопрос 1"),
        ("assistant", "Ответ 1"),
        ("user", "Вопрос 2"),
        ("assistant", "Ответ 2"),
    ]
    assert chat.message_history[0] == ("user", "Вопрос 1")

    chat.close()


def test_enter_submits_and_assistant_citations_are_shown():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    from pdfgraph.gui.chat_widget import ChatWidget

    app = QApplication.instance() or QApplication([])
    chat = ChatWidget()
    submitted = []
    chat.message_submitted.connect(submitted.append)

    chat.question_input.setText("Номер техкарты")
    QTest.keyClick(chat.question_input, Qt.Key_Return)
    chat.append_message(
        "assistant", "Найдена техкарта",
        citations=[{
            "document": "РТЭ.pdf",
            "page": 12,
            "snippet": "023.21.00b",
        }],
        kind="answer",
    )

    assert submitted == ["Номер техкарты"]
    assert chat.question_input.text() == ""
    assert chat.history_view.isReadOnly()
    assert "РТЭ.pdf" in chat.history_view.toPlainText()
    assert "лист 12" in chat.history_view.toPlainText()

    chat.close()
