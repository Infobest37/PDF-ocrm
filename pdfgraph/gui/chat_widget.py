"""Chat transcript and input widget for PDFGraph."""

from PyQt5 import QtCore, QtGui, QtWidgets


class ChatWidget(QtWidgets.QWidget):
    """Display conversation messages and emit submitted user questions."""

    message_submitted = QtCore.pyqtSignal(str)
    citation_clicked = QtCore.pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message_history: list[tuple[str, str]] = []
        self._citation_link_formats: dict[str, QtGui.QTextCharFormat] = {}
        self._next_citation_link = 0

        self.history_view = QtWidgets.QTextBrowser(
            self, objectName="chatHistory",
        )
        self.history_view.setOpenLinks(False)
        self.history_view.setOpenExternalLinks(False)
        self.history_view.anchorClicked.connect(self._on_anchor_clicked)

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
                cursor.insertText("  • ")
                if document != "—" and type(page) is int and page >= 0:
                    href = f"pdfgraph-citation:{self._next_citation_link}"
                    self._next_citation_link += 1
                    citation_format = QtGui.QTextCharFormat()
                    citation_format.setAnchor(True)
                    citation_format.setAnchorHref(href)
                    citation_format.setProperty(
                        QtCore.Qt.UserRole, (document, page),
                    )
                    citation_format.setForeground(QtGui.QColor("#20644c"))
                    citation_format.setFontUnderline(True)
                    self._citation_link_formats[href] = citation_format
                    cursor.insertText(f"{document}, лист {page}",
                                      citation_format)
                    cursor.insertText(f": {snippet}",
                                      QtGui.QTextCharFormat())
                else:
                    cursor.insertText(f"{document}, лист {page}: {snippet}")

        self.history_view.setTextCursor(cursor)
        self.history_view.ensureCursorVisible()

    @QtCore.pyqtSlot(QtCore.QUrl)
    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        """Emit source navigation for a citation anchor, never open a URL."""
        citation_format = self._citation_link_formats.get(url.toString())
        if citation_format is None:
            return
        target = citation_format.property(QtCore.Qt.UserRole)
        if (isinstance(target, tuple) and len(target) == 2
                and isinstance(target[0], str) and type(target[1]) is int):
            self.citation_clicked.emit(target[0], target[1])

    def _submit(self) -> None:
        text = self.question_input.text().strip()
        if not text:
            return
        self.question_input.clear()
        self.append_message("user", text)
        self.message_submitted.emit(text)
