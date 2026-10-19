"""Builder window: PDF -> OCR (pdfprepare) -> knowledge graph (pdfgraph).

Standalone entry point::

    python -m pdfgraph.gui.builder_window

The question/answer window (``pdfgraph.gui.app``) is intentionally untouched
(plan task 3.0 decision). The heavy work runs in a worker QObject moved to a
QThread; there are no GUI calls from the worker thread. Per PDF the worker
calls :func:`pdfprepare.pipeline.process_file` (writes the searchable PDF,
schema-v2 JSON and the quality report into the output directory), then builds
the graph with :func:`pdfgraph.build.build` + :func:`pdfgraph.build.mark_defects`
into the store resolved by :func:`pdfgraph.paths.resolve_store_path` — the
exact resolution the CLI and the answer window already use.

Fixes implemented here (plan task 3.1):
- П1: empty "Тип вертолёта" field -> ``"Ми-8МТВ"``, the final message says so;
- П2: when no ``graph.db`` exists anywhere, one is created in the CWD and the
  report says ``База создана заново: <full absolute path>``;
- П3: :func:`pdfprepare.ocr.ocr_available` is checked in the main thread
  BEFORE the worker starts;
- П4 (critical): ``dataclasses.replace(result, source_path=<rewritten PDF>)``
  before ``build()`` — plus an automated post-build check of the document keys
  (``"имя.pdf"``, never ``output_graph/имя.pdf``);
- П5: defects are marked via the public :func:`pdfgraph.build.mark_defects`;
- П6: the :class:`pdfgraph.store.Store` is opened inside the worker and closed
  in ``finally`` (prevents a locked database on Windows);
- П7: the ATA registry is searched relative to the repository root (not CWD);
- П8: progress = ``int(100 * (i + phase) / N)``, two phases per file
  (0.0–0.5 OCR — one tick per processed page via the ``on_progress``
  callback of ``process_file``, 0.5–1.0 build at file boundaries);
- П9: NO cancel button — OCR/build are not interruptible without changes to
  ``process_file``/``build`` (open item), so closing the window mid-run is
  politely refused instead of crashing.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import traceback

from PyQt5 import QtCore, QtGui, QtWidgets

from pdfgraph.build import build, mark_defects
from pdfgraph.paths import resolve_store_path
from pdfgraph.store import Store
from pdfprepare.ocr import ocr_available
from pdfprepare.pipeline import process_file
from pdfprepare.registry import parse_registry

#: Aircraft type used when the operator leaves the field empty (fix П1).
DEFAULT_AIRCRAFT_TYPE = "Ми-8МТВ"

#: Default output directory for the OCR artefacts.
DEFAULT_OUTPUT_DIR = "output_graph"

#: Name of the ATA-100 registry file, searched relative to the repo root.
REGISTRY_FILENAME = "Коды систем Вертолета.txt"

#: Instruction shown when the OCR engine is missing (fix П3).
_OCR_HINT = (
    "OCR-движок Tesseract не найден, сборка невозможна.\n\n"
    "Укажите каталог с Tesseract через переменную окружения\n"
    "PDFPREPARE_TESSERACT (каталог, содержащий tesseract.exe и tessdata/),\n"
    "например:\n"
    "  set PDFPREPARE_TESSERACT=C:\\Program Files\\Tesseract-OCR\n"
    "— либо положите переносимый бандл в pdfprepare/tesseract/ "
    "(см. pdfprepare/bundle.py)."
)


def find_registry_path(store_dir: str | None = None) -> str | None:
    """Locate ``Коды систем Вертолета.txt`` without depending on CWD (fix П7).

    Search order: the repository root (two levels above this module — reliable
    for both ``python -m`` runs and test runs started from any directory),
    then the directory holding the graph store, then the CWD. ``None`` means
    the registry was not found and ``system_code`` will stay empty (as in the
    CLI when ``--registry`` is omitted).
    """
    candidates: list[str] = []
    if not getattr(sys, "frozen", False):
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        candidates.append(os.path.join(repo_root, REGISTRY_FILENAME))
    if store_dir:
        candidates.append(os.path.join(store_dir, REGISTRY_FILENAME))
    candidates.append(os.path.join(os.getcwd(), REGISTRY_FILENAME))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_store_path_for_build() -> tuple[str, bool]:
    """Store path for a fresh build, falling back to the CWD (fix П2).

    Returns ``(path, created_fresh)``. A missing database must not abort a
    build (unlike an answer), so :class:`FileNotFoundError` from the resolver
    downgrades to creating ``graph.db`` in the current working directory; the
    ``created_fresh`` flag lets the report print the exact phrase from fix П2.
    """
    try:
        return resolve_store_path(), False
    except FileNotFoundError:
        return os.path.join(os.getcwd(), "graph.db"), True


class GraphBuildWorker(QtCore.QObject):
    """Runs OCR + graph building off the GUI thread.

    Signals
    -------
    progress(int, str)
        0..100 percent (fix П8) and a one-line status.
    finished(bool, str)
        Success flag and the full report (or the traceback on failure).
    """

    progress = QtCore.pyqtSignal(int, str)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, pdf_paths, output_dir: str, aircraft_type: str):
        super().__init__()
        self.pdf_paths = list(pdf_paths)
        self.output_dir = output_dir
        self.aircraft_type = aircraft_type
        #: Full absolute path of the store actually used (fix П2; the window
        #: reads it after ``finished`` for the open-folder button).
        self.store_path: str = ""
        #: True when the store had to be created in the CWD (fix П2).
        self.store_created_fresh: bool = False
        #: Absolute path of the ATA registry used, or "" when not found (П7).
        self.registry_path: str = ""
        #: Document keys from the post-build П4 check (bare names on success).
        self.documents: list[str] = []

    def run(self) -> None:
        """QThread entry: a raised exception never escapes unreported."""
        try:
            self._run()
        except BaseException:  # noqa: BLE001 - the traceback goes to the UI
            self.finished.emit(False, traceback.format_exc())

    def _run(self) -> None:
        total = len(self.pdf_paths)
        os.makedirs(self.output_dir, exist_ok=True)

        # П2: resolve the store BEFORE the OCR loop so the report can point
        # at the real database even when OCR itself later fails.
        self.store_path, self.store_created_fresh = resolve_store_path_for_build()
        self.store_path = os.path.abspath(self.store_path)
        self.registry_path = (
            find_registry_path(os.path.dirname(self.store_path)) or ""
        )
        registry: dict[str, str] = {}
        if self.registry_path:
            registry = parse_registry(self.registry_path)
        else:
            # П7: the exact wording the report/3.2 acceptance expects.
            self.progress.emit(
                0, "реестр кодов не найден, system_code будет пустым"
            )

        # П8: phase 0.0–0.5 of file i — OCR; phase 0.5–1.0 — graph building.
        entries: list[tuple[str, object]] = []
        for index, source in enumerate(self.pdf_paths):
            self.progress.emit(
                int(100 * index / total),
                f"OCR: файл {index + 1}/{total} — {os.path.basename(source)}",
            )
            def _on_page_progress(
                done: int, page_total: int, _index: int = index
            ) -> None:
                # P1/П8: OCR phase is 0.0–0.5 of file _index, so the page
                # fraction maps to 0.5 * done / page_total. First tick lands
                # as soon as the first page of the first file is processed
                # (~seconds), not only at file boundaries.
                self.progress.emit(
                    int(100 * (_index + 0.5 * done / page_total) / total),
                    f"OCR: файл {_index + 1}/{total}, стр {done}/{page_total}",
                )

            result = process_file(
                source, self.output_dir, registry, on_progress=_on_page_progress
            )
            stem = os.path.splitext(os.path.basename(source))[0]
            prepared_pdf = os.path.join(self.output_dir, stem + ".pdf")

            # П4 (critical): mirror the CLI (_cmd_build) — the document key is
            # derived from the REWRITTEN pdf inside the output dir, yielding a
            # bare "stem.pdf", never "output_graph/stem.pdf".
            result = dataclasses.replace(result, source_path=prepared_pdf)
            entries.append((os.path.join(self.output_dir, stem + ".json"), result))

            self.progress.emit(
                int(100 * (index + 0.5) / total),
                f"OCR готов: {stem}.pdf — {result.page_count} листов",
            )

        self.progress.emit(
            int(100 * (total - 0.5) / total),
            f"Построение графа: {total} документ(ов) → {self.store_path}",
        )

        # П6: the store lives ONLY inside the worker thread and is closed in
        # finally — a leftover connection would keep the DB locked on Windows.
        store = Store(self.store_path)
        try:
            stats = build(
                [entry[1] for entry in entries],
                self.aircraft_type,
                registry,
                store,
                root=self.output_dir,
            )
            defects = mark_defects(store, entries, root=self.output_dir)

            # П4: automatic verification right after the build (the same SQL
            # the acceptance step runs: DISTINCT document ... LIMIT 5).
            self.documents = store.distinct_documents(5)
            strayed = [doc for doc in self.documents if "/" in doc or "\\" in doc]
            if strayed:
                raise RuntimeError(
                    "Проверка ключей документов провалена (П4): найдены "
                    "ключи с путём вместо голого имени: "
                    + "; ".join(strayed)
                    + " — ключи должны быть «имя.pdf», сборка остановлена."
                )
        finally:
            store.close()

        self.progress.emit(100, "Готово: граф построен")
        report_lines = [
            f"PDF обработано: {total}",
            f"Каталог вывода: {os.path.abspath(self.output_dir)}",
            f"База: {self.store_path}",
        ]
        if self.store_created_fresh:
            # П2: the exact phrase with the full absolute path.
            report_lines.insert(3, f"База создана заново: {self.store_path}")
        report_lines.extend([
            "Реестр кодов: "
            + (self.registry_path if self.registry_path else
               "не найден, system_code будет пустым"),
            f"Узлов: {stats['nodes']}, рёбер: {stats['edges']}, "
            f"листов: {stats['sheets']}, pending: {stats['pending']}",
            f"Дефектных листов (всего в базе): {defects}",
            "Ключи документов (5 последних): " + ", ".join(self.documents),
        ])
        self.finished.emit(True, "\n".join(report_lines))


class BuilderWindow(QtWidgets.QWidget):
    """Pick PDFs, choose the output dir, run OCR + graph build with progress."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Построение графа знаний (PDF → OCR → граф)")
        self._worker: GraphBuildWorker | None = None
        self._thread: QtCore.QThread | None = None
        self._store_dir: str = ""
        self._aircraft: str = DEFAULT_AIRCRAFT_TYPE
        self._aircraft_is_default: bool = True
        self._known_paths: set[str] = set()

        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(QtWidgets.QLabel("PDF-документы:"))
        self._pdf_list = QtWidgets.QListWidget(self)
        layout.addWidget(self._pdf_list, stretch=1)

        add_row = QtWidgets.QHBoxLayout()
        self._add_button = QtWidgets.QPushButton("Добавить PDF…", self)
        self._add_button.clicked.connect(self._on_add_pdfs)
        self._remove_button = QtWidgets.QPushButton("Убрать выбранный", self)
        self._remove_button.clicked.connect(self._on_remove_selected)
        add_row.addWidget(self._add_button)
        add_row.addWidget(self._remove_button)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(QtWidgets.QLabel("Каталог вывода:"))
        self._default_output_dir = os.path.join(os.getcwd(), DEFAULT_OUTPUT_DIR)
        self._output_edit = QtWidgets.QLineEdit(self._default_output_dir, self)
        self._output_browse = QtWidgets.QPushButton("Обзор…", self)
        self._output_browse.clicked.connect(self._on_browse_output)
        output_row.addWidget(self._output_edit, stretch=1)
        output_row.addWidget(self._output_browse)
        layout.addLayout(output_row)

        type_row = QtWidgets.QHBoxLayout()
        type_row.addWidget(QtWidgets.QLabel("Тип вертолёта:"))
        self._type_edit = QtWidgets.QLineEdit(self)  # П1: placeholder below
        self._type_edit.setPlaceholderText(f"{DEFAULT_AIRCRAFT_TYPE} (по умолчанию)")
        type_row.addWidget(self._type_edit, stretch=1)
        layout.addLayout(type_row)

        self._build_button = QtWidgets.QPushButton("Создать граф", self)
        self._build_button.clicked.connect(self._on_build_clicked)
        layout.addWidget(self._build_button)

        self._progress = QtWidgets.QProgressBar(self)
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)

        self._status = QtWidgets.QLabel(
            "Выберите PDF и нажмите «Создать граф».", self
        )
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._open_db_button = QtWidgets.QPushButton(
            "Открыть папку с graph.db", self
        )
        self._open_db_button.setEnabled(False)
        self._open_db_button.clicked.connect(self._on_open_db_folder)
        layout.addWidget(self._open_db_button)

    # --- input handlers ---------------------------------------------------

    def _on_add_pdfs(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Выберите PDF-документы", os.getcwd(), "PDF (*.pdf)"
        )
        for path in paths:
            absolute = os.path.abspath(path)
            if absolute.lower() in self._known_paths:  # dedupe, case-insens.
                continue
            self._known_paths.add(absolute.lower())
            self._pdf_list.addItem(absolute)

    def _on_remove_selected(self) -> None:
        for item in self._pdf_list.selectedItems():
            self._known_paths.discard(item.text().lower())
            self._pdf_list.takeItem(self._pdf_list.row(item))

    def _on_browse_output(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Каталог вывода", self._default_output_dir
        )
        if chosen:
            self._output_edit.setText(os.path.abspath(chosen))

    def _on_build_clicked(self) -> None:
        paths = [self._pdf_list.item(i).text()
                 for i in range(self._pdf_list.count())]
        if not paths:
            QtWidgets.QMessageBox.information(
                self, "Сборка графа", "Добавьте хотя бы один PDF."
            )
            return

        # П3: OCR availability is checked in the MAIN thread before starting
        # the worker — the build button must not launch anything.
        if not ocr_available():
            QtWidgets.QMessageBox.critical(self, "OCR-движок не найден", _OCR_HINT)
            return

        output_dir = self._output_edit.text().strip() or self._default_output_dir
        raw_type = self._type_edit.text().strip()
        self._aircraft_is_default = not raw_type  # П1: remember for the report
        self._aircraft = raw_type or DEFAULT_AIRCRAFT_TYPE
        self._start_worker(paths, output_dir, self._aircraft)

    def _on_open_db_folder(self) -> None:
        if self._store_dir and os.path.isdir(self._store_dir):
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(self._store_dir)
            )

    # --- worker wiring ----------------------------------------------------

    def _start_worker(
        self, paths: list[str], output_dir: str, aircraft_type: str
    ) -> None:
        self._build_button.setEnabled(False)
        self._open_db_button.setEnabled(False)
        self._progress.setValue(0)
        self._status.setText("Запуск сборки…")

        self._thread = QtCore.QThread(self)
        self._worker = GraphBuildWorker(paths, output_dir, aircraft_type)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_progress(self, value: int, message: str) -> None:
        self._progress.setValue(value)
        self._status.setText(message)

    def _on_finished(self, success: bool, report: str) -> None:
        # The worker attributes are stable here: the worker's deleteLater is
        # wired to the thread finish, which is processed after this slot.
        if self._worker is not None:
            store_dir = os.path.dirname(self._worker.store_path)
            if store_dir and os.path.isdir(store_dir):
                self._store_dir = store_dir
        self._build_button.setEnabled(True)
        if success:
            self._open_db_button.setEnabled(True)
        aircraft_line = (
            f'aircraft_type = "{self._aircraft}" '
            "(по умолчанию; чтобы сменить — заполните поле)"
            if self._aircraft_is_default
            else f'aircraft_type = "{self._aircraft}"'
        )
        full_text = f"{aircraft_line}\n\n{report}"
        if success:
            QtWidgets.QMessageBox.information(self, "Сборка завершена", full_text)
        else:
            QtWidgets.QMessageBox.critical(
                self, "Ошибка сборки", full_text[:4000]
            )
        self._status.setText(
            "Готово." if success else "Сборка завершилась ошибкой — см. сообщение."
        )

    # П9: no cancel button; closing during a run is blocked so a running
    # worker is never orphaned mid-write (OCR/build are not interruptible).
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._thread is not None and self._thread.isRunning():
            QtWidgets.QMessageBox.information(
                self,
                "Сборка выполняется",
                "Идёт сборка графа — дождитесь завершения.\n"
                "Отмена не реализована (открытый пункт плана, П9).",
            )
            event.ignore()
            return
        event.accept()


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``python -m pdfgraph.gui.builder_window``."""
    app = QtWidgets.QApplication(list(argv) if argv else [])
    window = BuilderWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":  # pragma: no cover - manual launch
    sys.exit(main())