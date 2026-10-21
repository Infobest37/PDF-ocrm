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
import pathlib
import sqlite3
import sys

from PyQt5 import QtCore, QtGui, QtWidgets

from pdfgraph.answer import build_answer
from pdfgraph.gui.chat_widget import ChatWidget
from pdfgraph.gui.pdf_opener import open_pdf_at_page, resolve_pdf_dir
from pdfgraph.gui.settings import (LLMSettings, load as load_llm_settings,
                                   save as save_llm_settings)
from pdfgraph.gui.settings_dialog import SettingsDialog
from pdfgraph.gui.viewmodel import AnswerRow, answer_rows, verdict_text
from pdfgraph.paths import resolve_store_path
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

AIRCRAFT_CHOICES = [
    ("Все", None),
    ("Ми-8МТВ", "Ми-8МТВ"),
    ("КА-32", "КА-32"),
    ("Ми-8Т", "Ми-8Т"),
]

#: Missing graph file is not a refusal: name the problem and its recovery,
#: calmly (no plate, no red) — SQLite would otherwise create an empty base.
_MISSING_STORE_TEXT = ("Хранилище не найдено: файл графа отсутствует — "
                       "сначала постройте граф (pdfgraph build)")
#: Same calm treatment for a file that opens but breaks on the first query
#: (corrupted data pages): neutral plate, recovery named, no crash (fix
#: round 1 of the task 3.3 review).
_CORRUPT_STORE_TEXT = ("Хранилище повреждено: файл графа не читается — "
                       "пересоздайте граф (pdfgraph build)")


_WINDOW_QSS = f"""
QMainWindow {{ background: {DESK}; }}
QWidget#card {{ background: {PAPER}; border: 1px solid {INK}; }}
QLabel#title {{ color: {INK}; font-size: 13px; font-weight: 600;
    background: transparent; }}
QLineEdit#question {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 9px 11px; font-size: 17px;
    selection-background-color: {PINE}; selection-color: {PAPER}; }}
QLineEdit#question:focus {{ border: 2px solid {PINE}; padding: 8px 10px; }}
QComboBox#aircraft {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 6px 8px; font-size: 15px; }}
QComboBox#aircraft QAbstractItemView {{ background: {PAPER}; color: {INK};
    selection-background-color: {SELECTION}; selection-color: {INK}; }}
QCheckBox#rag {{ color: {INK}; font-size: 13px; background: transparent;
    spacing: 6px; }}
QCheckBox#rag::indicator {{ width: 14px; height: 14px;
    border: 1px solid {INK}; background: {PAPER}; }}
QCheckBox#rag::indicator:hover {{ border-color: {GRAPHITE}; }}
QCheckBox#rag::indicator:checked {{ background: {GRAPHITE}; }}
QCheckBox#synthesis {{ color: {INK}; font-size: 13px;
    background: transparent; spacing: 6px; }}
QCheckBox#synthesis::indicator {{ width: 14px; height: 14px;
    border: 1px solid {INK}; background: {PAPER}; }}
QCheckBox#synthesis::indicator:hover {{ border-color: {GRAPHITE}; }}
QCheckBox#synthesis::indicator:checked {{ background: {GRAPHITE}; }}
QPushButton#ask {{ background: {PINE}; color: {PAPER}; border: none;
    padding: 10px 26px; font-size: 17px; font-weight: 600; }}
QPushButton#ask:hover {{ background: {PINE_HOVER}; }}
QPushButton#ask:pressed {{ background: {PINE_PRESSED}; }}
QPushButton#ask:disabled {{ background: {RULE}; color: {GRAPHITE}; }}
QTextEdit#chatHistory {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 8px; font-size: 15px; }}
QLabel#section {{ color: {INK}; font-size: 13px; font-weight: 600;
    background: transparent; }}
QDialog {{ background: {DESK}; }}
QWidget#sheetCard {{ background: {PAPER}; border: 1px solid {INK}; }}
QPlainTextEdit#sheetText {{ background: {PAPER}; color: {INK};
    border: none; font-size: 15px; }}
QPushButton#close {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 9px 22px; font-size: 15px; }}
QPushButton#close:hover {{ background: {SELECTION}; }}
QPushButton#settings {{ background: {PAPER}; color: {INK};
    border: 1px solid {INK}; padding: 8px 12px; font-size: 13px; }}
QPushButton#settings:hover {{ background: {SELECTION}; }}
"""


class MainWindow(QtWidgets.QMainWindow):
    """One-window chat for answers with machine-verified citations."""

    def __init__(self, store_path: str, registry_path: str,
                 missing_hint: str = "",
                 pdf_dir: str | pathlib.Path | None = None):
        super().__init__()
        self.store_path = store_path
        #: Reserved for the upcoming ATA-100 registry navigation (task 3.3+).
        self.registry_path = registry_path
        #: Step 2: folder of the source PDFs opened on double-click, resolved
        #: once (CLI ``--pdf-dir`` > PDFGRAPH_PDF_DIR > default); ``None``
        #: when nothing exists, and the text-sheet dialog is the fallback.
        self.pdf_dir = resolve_pdf_dir(pdf_dir)
        self.llm_settings: LLMSettings = load_llm_settings()
        self._settings_dialog: SettingsDialog | None = None
        #: Stage 2: when the resolver found nothing, the calm note appends
        #: the exact list of tried paths (see paths.resolve_store_path).
        self._missing_text = _MISSING_STORE_TEXT + (
            f"\n{missing_hint}" if missing_hint else "")
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
        """Append a calm assistant refusal when the graph is unavailable."""
        self._rows = []
        self.chat_widget.append_message("assistant", text, kind="refusal")

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
        aircraft_row = QtWidgets.QHBoxLayout()
        aircraft_row.setSpacing(10)
        aircraft_label = QtWidgets.QLabel("Тип ВС", objectName="section")
        self.aircraft_combo = QtWidgets.QComboBox(objectName="aircraft")
        for label, value in AIRCRAFT_CHOICES:
            self.aircraft_combo.addItem(label, value)
        self.aircraft_combo.setCurrentIndex(0)
        aircraft_row.addWidget(aircraft_label)
        aircraft_row.addWidget(self.aircraft_combo)
        # Step 3: opt-in RAG-Fusion next to the filter — neutral checkbox,
        # unchecked by default so ordinary search stays the norm.
        self.rag_check = QtWidgets.QCheckBox("Умный поиск (RAG-Fusion)",
                                             objectName="rag")
        self.rag_check.setToolTip(
            "Переформулирует вопрос через LLM. Без LLM работает как обычный поиск.")
        self.rag_check.setChecked(self.llm_settings.use_rag_fusion)
        aircraft_row.addWidget(self.rag_check)

        self.synthesis_checkbox = QtWidgets.QCheckBox(
            "LLM-ответ (синтез)", objectName="synthesis")
        self.synthesis_checkbox.setChecked(self.llm_settings.use_synthesis)
        self.synthesis_checkbox.setEnabled(bool(self.llm_settings.api_key.strip()))
        self.synthesis_checkbox.setToolTip(
            "Синтезировать ответ по найденным выдержкам и проверить цитаты.")
        aircraft_row.addWidget(self.synthesis_checkbox)

        self.settings_button = QtWidgets.QPushButton(
            "Настройки LLM", objectName="settings")
        self.settings_button.clicked.connect(self._open_llm_settings)
        aircraft_row.addWidget(self.settings_button)

        self.rag_check.stateChanged.connect(self._save_search_preferences)
        self.synthesis_checkbox.stateChanged.connect(
            self._save_search_preferences)
        aircraft_row.addStretch(1)
        layout.addLayout(aircraft_row)
        layout.addSpacing(8)

        # Replace the one-shot question, results table and snippet panel with
        # the conversation. Citations are rendered inside assistant messages.
        self.chat_widget = ChatWidget(card)
        self.chat_widget.question_input.setObjectName("question")
        self.chat_widget.send_button.setObjectName("ask")
        self.chat_widget.send_button.setDefault(True)
        self.chat_widget.message_submitted.connect(self.ask)
        layout.addSpacing(16)
        layout.addWidget(self.chat_widget, 1)

    # -- actions ------------------------------------------------------------

    def _current_aircraft_type(self) -> str | None:
        """Aircraft type selected in the filter; «Все» maps to ``None``."""
        return self.aircraft_combo.currentData()

    def _save_search_preferences(self, *_args) -> None:
        """Persist the two opt-in search switches with the LLM profile."""
        if not hasattr(self, "rag_check") or not hasattr(
                self, "synthesis_checkbox"):
            return
        self.llm_settings.use_rag_fusion = self.rag_check.isChecked()
        self.llm_settings.use_synthesis = self.synthesis_checkbox.isChecked()
        save_llm_settings(self.llm_settings)

    def _open_llm_settings(self) -> None:
        """Open the profile dialog and reload settings after it closes."""
        dialog = SettingsDialog(self.llm_settings, self)
        self._settings_dialog = dialog
        dialog.finished.connect(self._llm_settings_dialog_finished)
        dialog.open()

    def _llm_settings_dialog_finished(self, result: int) -> None:
        if result == QtWidgets.QDialog.Accepted:
            self.llm_settings = load_llm_settings()
            self.synthesis_checkbox.setEnabled(
                bool(self.llm_settings.api_key.strip()))
            self.rag_check.setChecked(self.llm_settings.use_rag_fusion)
            self.synthesis_checkbox.setChecked(self.llm_settings.use_synthesis)
        self._settings_dialog = None

    def ask(self, question: str) -> list[AnswerRow]:
        """Answer ``question``, append the chat reply, and return display rows.

        Citations are machine-checked before anything is shown; the row list
        is returned for tests and callers, UI updates happen as a side effect.
        With «Умный поиск» checked the retrieval expands the question through
        an LLM, which can take seconds: the wait cursor marks the blocking
        stretch around :func:`build_answer` (no worker threads — the GUI
        design stays synchronous).
        """
        if not self._open_store():
            self._show_store_note(self._missing_text)
            return []
        assert self._store is not None
        use_rag_fusion = self.rag_check.isChecked()
        use_synthesis = (
            self.synthesis_checkbox.isChecked()
            and bool(self.llm_settings.api_key.strip())
        )
        if use_rag_fusion or use_synthesis:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            try:
                answer = build_answer(
                    self._store,
                    question,
                    aircraft_type=self._current_aircraft_type(),
                    use_rag_fusion=use_rag_fusion,
                    use_synthesis=use_synthesis,
                    llm_settings=self.llm_settings,
                )
                checks = verify_citations(answer, self._store)
            except sqlite3.Error:
                # The file opened but a query hit broken data pages: same calm
                # treatment as an absent store, never an unhandled exception.
                self._drop_store()
                self._show_store_note(_CORRUPT_STORE_TEXT)
                return []
        finally:
            if use_rag_fusion or use_synthesis:
                QtWidgets.QApplication.restoreOverrideCursor()
        rows = answer_rows(answer, checks)
        self._rows = rows
        if answer.refusal or not rows:
            message_text = answer.reason or verdict_text(rows)
            message_kind = "refusal"
            citations = []
        else:
            answer_text = "\n\n".join(row.text for row in rows if row.text)
            message_text = "\n\n".join(
                part for part in (answer_text, verdict_text(rows)) if part
            )
            message_kind = "answer"
            citations = [
                {
                    "document": row.document,
                    "page": row.page,
                    "snippet": row.snippet,
                }
                for row in rows
                if row.verified and row.document and row.page >= 0
            ]

        self.chat_widget.append_message(
            "assistant", message_text, citations=citations, kind=message_kind,
        )
        return rows

    def open_sheet(self, document: str, page: int) -> None:
        """Show one sheet: the source PDF first, the stored text as fallback.

        For every sheet whose store read succeeds (blank boundary sheets
        included — the physical PDF page still exists), double-click first
        asks the PDF viewer to open the cited page (step 2); when the PDF
        cannot be opened a calm warning names the problem and the step-1
        body is shown unchanged: stored text or, for a blank sheet, the
        boundary note. Missing/broken stores never reach the viewer: they
        keep the calm text path from the step-1 GUI.
        """
        if not self._open_store():
            body = self._missing_text
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
                if open_pdf_at_page(self.pdf_dir, document, page):
                    return
                QtWidgets.QMessageBox.warning(
                    self, "Открытие PDF",
                    f"PDF не найден: {document}. Проверьте --pdf-dir")
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

    def _on_double_click(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._rows):
            source = self._rows[row]
            self.open_sheet(source.document, source.page)


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
        "--store", default=None, metavar="PATH",
        help="SQLite graph file produced by 'pdfgraph build'. Default: auto "
             "(graph.db beside the exe, up to two levels above it, or in "
             "the current directory — pdfgraph.paths.resolve_store_path).")
    parser.add_argument(
        "--registry", default="Коды систем Вертолета.txt", metavar="PATH",
        help="ATA-100 code registry file (reserved for navigation).")
    parser.add_argument(
        "--pdf-dir", default=None, metavar="PATH",
        help="Folder of the source PDFs opened on double-click. Default: "
             "auto (PDFGRAPH_PDF_DIR, then the prepared «pdfs» folder — "
             "pdfgraph.gui.pdf_opener.resolve_pdf_dir).")
    args = parser.parse_args(argv)

    missing_hint = ""
    try:
        store_path = resolve_store_path(args.store)
    except FileNotFoundError as exc:
        # Nothing found anywhere: the window still opens and explains itself
        # calmly; the hint names every path the resolver actually checked.
        store_path = args.store or "graph.db"
        missing_hint = str(exc)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(store_path, args.registry, missing_hint=missing_hint,
                        pdf_dir=args.pdf_dir)
    window.show()
    return int(app.exec_())


if __name__ == "__main__":
    sys.exit(main())
