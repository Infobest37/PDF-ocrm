"""Chat transcript and input widget for PDFGraph."""

from PyQt5 import QtCore, QtGui, QtWidgets


class ChatWidget(QtWidgets.QWidget):
    """Display conversation messages and emit submitted user questions."""

    message_submitted = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message_history: list[tuple[str, str]] = []

        self.history_view = QtWidgets.QTextEdit(self, objectName="chatHistory")
        self.history_view.setReadOnly(True)

        self.question_input = QtWidgets.QLineEdit(
            self, objectName="chatInput",
        )
        self.question_input.setPlaceholderText("Введите сообщение")
        self.send_button = QtWidgets.QPushButton(
            "Отправить", self, objectName="chatSend",
        )
        self.send_button.clicked.connect(self._submit)
        self.question_input.returnPressed.connect(self._submit)

        composer = QtWidgets.QHBoxLayout()
        composer.addWidget(self.question_input, 1)
        composer.addWidget(self.send_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.history_view, 1)
        layout.addLayout(composer)

    @property
    def message_history(self) -> list[tuple[str, str]]:
        """Return a snapshot of role/text pairs in display order."""
        return list(self._message_history)

    def append_message(self, role: str, text: str, citations=None,
                       kind: str | None = None) -> None:
        """Append a user or assistant message and its optional citations."""
        if role not in {"user", "assistant"}:
            raise ValueError("role must be 'user' or 'assistant'")

        self._message_history.append((role, text))
        if role == "user":
            label = "Вы"
        elif kind == "refusal":
            label = "Ассистент — отказ"
        elif kind == "clarify":
            label = "Ассистент — уточнение"
        else:
            label = "Ассистент"

        cursor = self.history_view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        if cursor.position():
            cursor.insertBlock()
        cursor.insertText(f"{label}: {text}")

        if citations:
            cursor.insertBlock()
            cursor.insertText("Источники:")
            for citation in citations:
                document = str(citation.get("document", "—"))
                page = citation.get("page", "—")
                snippet = str(citation.get("snippet", ""))
                cursor.insertBlock()
                cursor.insertText(f"  • {document}, лист {page}: {snippet}")

        self.history_view.setTextCursor(cursor)
        self.history_view.ensureCursorVisible()

    def _submit(self) -> None:
        text = self.question_input.text().strip()
        if not text:
            return
        self.question_input.clear()
        self.append_message("user", text)
        self.message_submitted.emit(text)
