"""Single-window PyQt5 UI over the knowledge-graph answer pipeline.

The window is a thin shell over :mod:`pdfgraph.gui.viewmodel`: ask a
question, verify every citation against the stored sheets, and let the
verdict plate be the one reversed element on the screen.  A refusal is a
normal, calmly styled outcome — never an error look.

Visual world: a workshop job-aid card — paper-white card on a slate desk,
ink-black type only, a single pine accent reserved for confirmed state,
selection and the primary action; 1px rules, zero radius, integer sizes.
The «Проверено» cell is a stamp mark in a fixed cell: filled pine square
means verified, hollow graphite square means not.
"""

import argparse
import os
import sqlite3
import sys

from PyQt5 import QtCore, QtGui, QtWidgets

from pdfgraph.answer import build_answer
from pdfgraph.gui.viewmodel import AnswerRow, answer_rows, verdict_text
from pdfgraph.store import Store
from pdfgraph.verify import verify_citations

#: Job-aid card palette (role names match DESIGN.md).
INK = "#14181b"
PAPER = "#f4f4f1"
DESK = "#31363a"
PINE = "#20644c"
PINE_HOVER = "#1a5240"
PINE_PRESSED = "#144132"
GRAPHITE = "#575e63"
RULE = "#c9c6bc"
SELECTION = "#dde8e1"
REFUSAL_PLATE = "#454c52"

#: Column titles pinned by the design brief — keep verbatim.
COLUMNS = ("Ответ", "Документ", "Лист", "Код", "Проверено")

_AWAITING_TEXT = "Пока вопрос не задан — показываем только ответы, подтверждённые на листах"
_SNIPPET_HINT = "Фрагмент подтверждённого ответа появится здесь"
#: Missing graph file is not a refusal: name the problem and its recovery,
#: calmly (no plate, no red) — SQLite would otherwise create an empty base.
_MISSING_STORE_TEXT = ("Хранилище не найдено: файл графа отсутствует — "
                       "сначала постройте граф (pdfgraph build)")
#: Same calm treatment for a file that opens but breaks on the first query
#: (corrupted data pages): neutral plate, recovery named, no crash (fix
#: round 1 of the task 3.3 review).
_CORRUPT_STORE_TEXT = ("Хранилище повреждено: файл графа не читается — "
                       "пересоздайте граф (pdfgraph build)")


class _VerifiedDelegate(QtWidgets.QStyledItemDelegate):
    """Paint the «Проверено» cell as a stamp mark: filled or hollow square."""

    _SIZE = 14

    def paint(self, painter: QtGui.QPainter, option, index) -> None:  # type: ignore[no-untyped-def]
        state = index.data(QtCore.Qt.UserRole)
        if state is None:
            super().paint(painter, option, index)
            return
        painter.save()
        rect = option.rect
        side = self._SIZE
        box = QtCore.QRect(
            rect.center().x() - side // 2,
            rect.center().y() - side // 2,
            side,
            side,
        )
        if state is True:
            painter.fillRect(box, QtGui.QColor(PINE))
        else:
            pen = QtGui.QPen(QtGui.QColor(GRAPHITE))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(box.adjusted(0, 0, -1, -1))
        painter.restore()


_WINDOW_QSS = f"""
QMainWindow {{ background: {DESK}; }}
QWidget#card {{ background: {PAPER}; border: 1px solid {INK}; }}
QLabel#title {{ color: {INK}; font-size: 13px; font-weight: 600;
    background: transparent; }}
QLineEdit#question {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 9px 11px; font-size: 17px;
    selection-background-color: {PINE}; selection-color: {PAPER}; }}
QLineEdit#question:focus {{ border: 2px solid {PINE}; padding: 8px 10px; }}
QPushButton#ask {{ background: {PINE}; color: {PAPER}; border: none;
    padding: 10px 26px; font-size: 17px; font-weight: 600; }}
QPushButton#ask:hover {{ background: {PINE_HOVER}; }}
QPushButton#ask:pressed {{ background: {PINE_PRESSED}; }}
QPushButton#ask:disabled {{ background: {RULE}; color: {GRAPHITE}; }}
QTableView#answers {{ background: {PAPER}; color: {INK};
    gridline-color: {RULE}; border: 1px solid {INK}; font-size: 15px;
    selection-background-color: {SELECTION}; selection-color: {INK}; }}
QTableView#answers::item {{ padding: 6px 8px; }}
QHeaderView::section {{ background: {PAPER}; color: {INK};
    border: none; border-bottom: 1px solid {INK};
    padding: 7px 8px; font-size: 13px; font-weight: 600; }}
QLabel#section {{ color: {INK}; font-size: 13px; font-weight: 600;
    background: transparent; }}
QWidget#confirm {{ background: {PAPER}; border: 1px solid {INK}; }}
QLabel#snippet {{ color: {INK}; font-size: 15px; background: transparent; }}
QLabel#snippet[hint="yes"] {{ color: {GRAPHITE}; }}
QLabel#verdict {{ font-size: 17px; font-weight: 600; padding: 12px 14px; }}
QLabel#verdict[plate="ink"] {{ background: {INK}; color: {PAPER};
    border: none; }}
QLabel#verdict[plate="refusal"] {{ background: {REFUSAL_PLATE};
    color: {PAPER}; border: none; }}
QLabel#verdict[plate="none"] {{ background: transparent;
    color: {GRAPHITE}; border: none; }}
QDialog {{ background: {DESK}; }}
QWidget#sheetCard {{ background: {PAPER}; border: 1px solid {INK}; }}
QPlainTextEdit#sheetText {{ background: {PAPER}; color: {INK};
    border: none; font-size: 15px; }}
QPushButton#close {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 9px 22px; font-size: 15px; }}
QPushButton#close:hover {{ background: {SELECTION}; }}
"""


class MainWindow(QtWidgets.QMainWindow):
    """One-window answer screen: question, verified table, confirmation, verdict."""

    def __init__(self, store_path: str, registry_path: str):
        super().__init__()
        self.store_path = store_path
        #: Reserved for the upcoming ATA-100 registry navigation (task 3.3+).
        self.registry_path = registry_path
        self._store: Store | None = None
        self._rows: list[AnswerRow] = []

        self.setWindowTitle("Граф знаний Ми-8МТВ")
        self.setMinimumSize(880, 600)
        self.resize(1040, 700)
        self.setStyleSheet(_WINDOW_QSS)
        self._open_store()
        self._build_central()

    # -- store lifecycle ----------------------------------------------------

    def _open_store(self) -> bool:
        """Open the graph on demand; False when the file simply is not there.

        SQLite would otherwise create an empty database silently and the
        window would show a misleading refusal, so an absent file is reported
        by callers as a calm message and nothing is created. A file that
        exists but is not a readable database (corrupt store) takes the very
        same calm path: the window never crashes on a broken graph file.
        """
        if self._store is None and os.path.isfile(self.store_path):
            try:
                self._store = Store(self.store_path)
            except sqlite3.Error:
                return False
        return self._store is not None

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self._store is not None:
            self._store.close()
            self._store = None
        super().closeEvent(event)

    def _drop_store(self) -> None:
        """Forget the current store connection (after a query-level failure).

        Corruption of the data pages is only discovered when a query reads
        them; the half-broken connection must not be reused. The next
        question reopens from scratch (and reports calmly if still broken).
        """
        if self._store is not None:
            try:
                self._store.close()
            except sqlite3.Error:
                pass
            self._store = None

    def _show_store_note(self, text: str) -> None:
        """Calm store-problem verdict: no rows, empty panel, neutral plate."""
        self._rows = []
        self._fill_table([])
        self._set_snippet("")
        self._set_verdict_plate("none")
        self.verdict_label.setText(text)

    # -- widgets ------------------------------------------------------------

    def _build_central(self) -> None:
        # QMainWindow owns its layout: content goes through setCentralWidget,
        # a transparent frame container leaves the desk margin visible.
        frame = QtWidgets.QWidget(objectName="frame")
        self.setCentralWidget(frame)
        card = QtWidgets.QWidget(objectName="card")
        outer = QtWidgets.QHBoxLayout(frame)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addWidget(card)

        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)

        title = QtWidgets.QLabel("Граф знаний Ми-8МТВ", objectName="title")
        layout.addWidget(title)

        # Zone 1: question + «Спросить».
        layout.addSpacing(16)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.question_edit = QtWidgets.QLineEdit(objectName="question")
        self.question_edit.setPlaceholderText("Вопрос по техдокументации, например: радиомаяк ПАРМ-406А")
        self.question_edit.returnPressed.connect(self._ask_clicked)
        self.ask_button = QtWidgets.QPushButton("Спросить", objectName="ask")
        self.ask_button.setDefault(True)
        self.ask_button.clicked.connect(self._ask_clicked)
        row.addWidget(self.question_edit, 1)
        row.addWidget(self.ask_button)
        layout.addLayout(row)

        # Zone 2: answers table.
        layout.addSpacing(16)
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS), objectName="answers")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column, width in ((1, 180), (2, 70), (3, 110), (4, 110)):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.setItemDelegateForColumn(4, _VerifiedDelegate(self.table))
        # Selection colors are pinned in code as well as QSS: the sandbox
        # palette can carry a broken HighlightedText, and the design keeps
        # ink text on the pale pine tint.
        selection = QtGui.QPalette()
        selection.setColor(QtGui.QPalette.Highlight, QtGui.QColor(SELECTION))
        selection.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(INK))
        self.table.setPalette(selection)
        self.table.itemSelectionChanged.connect(self._show_selected_snippet)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        # Zone 3: «Подтверждение» panel with the fragment text.
        layout.addSpacing(16)
        confirm_label = QtWidgets.QLabel("Подтверждение", objectName="section")
        layout.addWidget(confirm_label)
        layout.addSpacing(6)
        panel = QtWidgets.QWidget(objectName="confirm")
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        self.snippet_label = QtWidgets.QLabel(_SNIPPET_HINT, objectName="snippet")
        self.snippet_label.setProperty("hint", "yes")
        self.snippet_label.setWordWrap(True)
        self.snippet_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        panel_layout.addWidget(self.snippet_label)
        layout.addWidget(panel)

        # Zone 4: verdict plate.
        layout.addSpacing(14)
        self.verdict_label = QtWidgets.QLabel(_AWAITING_TEXT, objectName="verdict")
        self.verdict_label.setWordWrap(True)
        self._set_verdict_plate("none")
        layout.addWidget(self.verdict_label)

    # -- actions ------------------------------------------------------------

    def _ask_clicked(self) -> None:
        self.ask(self.question_edit.text())

    def ask(self, question: str) -> list[AnswerRow]:
        """Answer ``question``, fill the table/panel/verdict, return display rows.

        Citations are machine-checked before anything is shown; the row list
        is returned for tests and callers, UI updates happen as a side effect.
        """
        if not self._open_store():
            self._show_store_note(_MISSING_STORE_TEXT)
            return []
        assert self._store is not None
        try:
            answer = build_answer(self._store, question)
            checks = verify_citations(answer, self._store)
        except sqlite3.Error:
            # The file opened but a query hit broken data pages: same calm
            # treatment as an absent store, never an unhandled exception.
            self._drop_store()
            self._show_store_note(_CORRUPT_STORE_TEXT)
            return []
        rows = answer_rows(answer, checks)

        self._rows = rows
        self._fill_table(rows)
        if rows:
            self.table.selectRow(0)
            self._set_verdict_plate("ink")
        else:
            # Отказ: панель подтверждения пуста — тихо, не подсказка и не ошибка.
            self._set_snippet("")
            self._set_verdict_plate("refusal")
        self.verdict_label.setText(verdict_text(rows))
        return rows

    def open_sheet(self, document: str, page: int) -> None:
        """Show the stored text of one sheet; missing/broken stores stay calm."""
        if not self._open_store():
            body = _MISSING_STORE_TEXT
        else:
            assert self._store is not None
            try:
                missing = not self._store.has_node("sheet", f"{document}:{page}")
                text = self._store.page_text(document, page)
            except sqlite3.Error:
                # Corrupted data pages surface only on the read (fix round 1).
                self._drop_store()
                body = _CORRUPT_STORE_TEXT
            else:
                if text and text.strip():
                    body = text
                else:
                    body = self._blank_sheet_note(document, page, missing)

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Лист {page} — {document}")
        dialog.setModal(True)
        dialog.resize(760, 520)
        outer = QtWidgets.QHBoxLayout(dialog)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QtWidgets.QWidget(objectName="sheetCard")
        outer.addWidget(card)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)

        editor = QtWidgets.QPlainTextEdit(readOnly=True, objectName="sheetText")
        editor.setPlainText(body)
        card_layout.addWidget(editor, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        close = QtWidgets.QPushButton("Закрыть", objectName="close")
        close.setDefault(True)
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        card_layout.addLayout(buttons)
        dialog.exec()

    @staticmethod
    def _blank_sheet_note(document: str, page: int, missing: bool) -> str:
        if missing:
            return f"Лист {page} документа «{document}» не найден в графе — возможно, он ещё не обработан."
        return "Лист пустой — граница раздела или техкарты; это не дефект скана."

    # -- rendering helpers ----------------------------------------------------

    def _fill_table(self, rows: list[AnswerRow]) -> None:
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            text_item = QtWidgets.QTableWidgetItem(row.text)
            text_item.setTextAlignment(
                QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap)
            doc_item = QtWidgets.QTableWidgetItem(row.document or "—")
            page_item = QtWidgets.QTableWidgetItem(str(row.page) if row.page >= 0 else "—")
            page_item.setTextAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignRight)
            code_item = QtWidgets.QTableWidgetItem(row.code or "—")
            mark_item = QtWidgets.QTableWidgetItem()
            mark_item.setData(QtCore.Qt.UserRole, bool(row.verified))
            mark_item.setToolTip("подтверждено на листе" if row.verified else "не подтверждено на листе")
            for item in (text_item, doc_item, page_item, code_item, mark_item):
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            for c, item in enumerate((text_item, doc_item, page_item, code_item, mark_item)):
                self.table.setItem(r, c, item)
        self.table.resizeRowsToContents()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Wrapped claim text is measured against the current column width, so
        # row heights are recomputed whenever the window changes size.
        if hasattr(self, "table"):
            self.table.resizeRowsToContents()

    def _show_selected_snippet(self) -> None:
        row = self._current_row()
        self._set_snippet(row.snippet if row is not None else "")

    def _set_snippet(self, text: str, hint: bool = False) -> None:
        self.snippet_label.setProperty("hint", "yes" if hint else "no")
        self.snippet_label.style().polish(self.snippet_label)
        self.snippet_label.setText(text)

    def _current_row(self) -> AnswerRow | None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        number = indexes[0].row()
        return self._rows[number] if 0 <= number < len(self._rows) else None

    def _on_double_click(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._rows):
            source = self._rows[row]
            self.open_sheet(source.document, source.page)

    def _set_verdict_plate(self, kind: str) -> None:
        self.verdict_label.setProperty("plate", kind)
        self.verdict_label.style().unpolish(self.verdict_label)
        self.verdict_label.style().polish(self.verdict_label)


def main(argv=None) -> int:
    """Entry point of the windowed ``pdfgraph.exe``: show the graph window.

    An absent or broken store file is not fatal: :class:`MainWindow` keeps
    the calm verdict paths ("store not found" / "store corrupted"), so the
    window always opens and explains itself. Returns the Qt event-loop code
    (0 after the window is closed).
    """
    parser = argparse.ArgumentParser(
        prog="pdfgraph",
        description="Оконный интерфейс графа знаний Ми-8МТВ: вопрос — "
                    "ответ с проверенными цитатами.",
    )
    parser.add_argument(
        "--store", default="graph.db", metavar="PATH",
        help="SQLite graph file produced by 'pdfgraph build' "
             "(default: graph.db in the current directory).")
    parser.add_argument(
        "--registry", default="Коды систем Вертолета.txt", metavar="PATH",
        help="ATA-100 code registry file (reserved for navigation).")
    args = parser.parse_args(argv)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(args.store, args.registry)
    window.show()
    return int(app.exec_())


if __name__ == "__main__":
    sys.exit(main())
