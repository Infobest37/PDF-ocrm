"""GUI tests for the builder window (plan task 3.3, pytest-qt / offscreen).

Задача 3.3: покрыть логику окна БЕЗ реального OCR и БЕЗ записи в боевую
graph.db:
- реальный OCR (``pdfprepare.pipeline.process_file``) никогда не вызывается —
  ``GraphBuildWorker`` подменяется сигнальным фейком с no-op ``run()``;
- боевая graph.db не трогается — фейковому воркеру назначается ``store_path``
  в ``tmp_path``, и он всё равно никогда не открывает Store;
- Tesseract не требуется — ``ocr_available()`` замокана;
- реальные PDF не открываются — все пути фиктивные (файлы не существуют).

QMessageBox / QFileDialog замоканы (нативный модальный диалог залип бы на
test run). Проверки пронумерованы по спецификации задачи 3.3 (3.1–3.8), плюс
два дополнения к acceptance: защита от пустого списка и П3 (нет OCR-движка).
"""

from __future__ import annotations

import os

# Must happen before the first QApplication instance is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5 import QtCore, QtWidgets  # noqa: E402

import pdfgraph.gui.builder_window as bw  # noqa: E402

#: Fake paths that never exist on disk — process_file is never called.
FAKE_PDFS = ["C:/tmp/РТЭ кн.1.pdf", "C:/tmp/РТЭ кн.2.pdf"]


# --- dialog / worker stand-ins ---------------------------------------------


class _MsgBoxRecorder:
    """Stands in for the native QMessageBox static dialogs (a modal dialog
    would hang the test run); records (parent, title, text) of each call."""

    def __init__(self) -> None:
        self.info_calls: list[tuple] = []
        self.crit_calls: list[tuple] = []

    def information(self, *args):
        self.info_calls.append(args)
        return QtWidgets.QMessageBox.Ok

    def critical(self, *args):
        self.crit_calls.append(args)
        return QtWidgets.QMessageBox.Ok


def patch_dialogs(monkeypatch) -> _MsgBoxRecorder:
    """Replace QMessageBox.information/critical by recorders."""
    rec = _MsgBoxRecorder()
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(rec.information))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        staticmethod(rec.critical))
    return rec


def patch_file_dialog(monkeypatch, picked: list[str]) -> None:
    """Replace QFileDialog.getOpenFileNames by a fixed (paths, filter)."""
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: (list(picked), "PDF (*.pdf)")),
    )


def patch_fake_worker(monkeypatch, store_path: os.PathLike) -> list[dict]:
    """Replace ``GraphBuildWorker`` with a signal-only dummy.

    The real GUI machinery is still exercised (``_start_worker``: QThread +
    ``moveToThread`` + signal/slot wiring), but ``run()`` is a no-op — no OCR,
    no Store, no writes. The dummy records its constructor arguments so П1
    (default aircraft type) can be asserted on the worker side.
    """
    created: list[dict] = []

    class FakeWorker(QtCore.QObject):
        progress = QtCore.pyqtSignal(int, str)
        finished = QtCore.pyqtSignal(bool, str)

        def __init__(self, pdf_paths, output_dir, aircraft_type):
            super().__init__()
            created.append({"pdf_paths": list(pdf_paths),
                            "output_dir": output_dir,
                            "aircraft_type": aircraft_type})
            self.pdf_paths = list(pdf_paths)
            self.output_dir = output_dir
            self.aircraft_type = aircraft_type
            self.store_path = str(store_path)

        def run(self) -> None:  # pragma: no cover — deliberately inert
            pass

    monkeypatch.setattr(bw, "GraphBuildWorker", FakeWorker)
    return created


class _FakeThread:
    """Mimics the QThread API used by ``closeEvent`` (:415) w/o threads."""

    def __init__(self, running: bool) -> None:
        self._running = running

    def isRunning(self) -> bool:  # noqa: N802 — Qt naming
        return self._running


class _FakeEvent:
    """Records accept()/ignore() on a close event."""

    def __init__(self) -> None:
        self.accepted = False
        self.ignored = False

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


def _stop_thread(qtbot, window) -> None:
    """Finish the worker thread created by _start_worker (test cleanup)."""
    thread = window._thread
    if thread is None:
        return
    try:
        thread.quit()
    except RuntimeError:  # C++ object already deleted by the finished chain
        window._thread = None
        return

    def _not_running() -> bool:
        try:
            return not thread.isRunning()
        except RuntimeError:  # deleted by the deleteLater chain mid-wait
            return True

    qtbot.waitUntil(_not_running, timeout=5000)
    # Drop the stale window->_thread reference: the app's deleteLater chain
    # has now destroyed (or is about to destroy) the C++ QThread, and the
    # window closeEvent (:416) would trip over that dangling reference when
    # qtbot closes the window in teardown. (Production keeps the reference
    # and merely prints a traceback to stderr on close — 3.3 backlog nit.)
    window._thread = None


@pytest.fixture
def win(qtbot, qapp):
    """An offscreen BuilderWindow, auto-closed by pytest-qt."""
    window = bw.BuilderWindow()
    qtbot.addWidget(window)
    yield window
    # The app wires thread.finished -> deleteLater, so a completed run may
    # destroy the QThread C++ object while the window still holds the Python
    # reference; closeEvent (:416) would then raise "wrapped C/C++ object
    # deleted" when qtbot closes the window during teardown. Spin the event
    # loop first (letting the deleteLater chain fire), then neutralise the
    # stale reference — the run is over either way. (In production the same
    # stale reference only prints a traceback to stderr on close; noted in
    # the 3.3 report as a backlog nit.)
    qapp.processEvents()
    thread = window._thread
    if thread is not None:
        try:
            thread.isRunning()
        except RuntimeError:
            window._thread = None


def _start_run(qtbot, win, tmp_path, monkeypatch, *, dialogs=None):
    """Common prelude: fake worker + a running (inert) worker thread."""
    if dialogs is None:
        dialogs = patch_dialogs(monkeypatch)
    patch_fake_worker(monkeypatch, tmp_path / "graph.db")
    win._start_worker(["a.pdf"], str(tmp_path / "out"), "Ми-8МТВ")
    qtbot.waitUntil(lambda: win._thread.isRunning())
    return dialogs


# --- 3.1–3.2: the PDF list ---------------------------------------------------


def test_3_1_add_pdfs_fills_list(qtbot, win, monkeypatch):
    """3.1: _on_add_pdfs adds every picked PDF to the QListWidget (:311)."""
    picked = list(FAKE_PDFS)
    patch_file_dialog(monkeypatch, picked)

    win._on_add_pdfs()

    assert win._pdf_list.count() == 2
    got = [win._pdf_list.item(i).text() for i in range(win._pdf_list.count())]
    assert got == [os.path.abspath(p) for p in picked]
    assert win._known_paths == {  # keys are lowercase (:315–:320)
        os.path.abspath(p).lower() for p in picked
    }


def test_3_2_add_pdfs_duplicates_ignored(qtbot, win, monkeypatch):
    """3.2: the same files cannot duplicate list rows (dedupe via
    ``_known_paths``, :317)."""
    patch_file_dialog(monkeypatch, ["X:/a.pdf", "X:/b.pdf"])

    win._on_add_pdfs()
    assert win._pdf_list.count() == 2
    win._on_add_pdfs()   # the same two files again
    win._on_add_pdfs()   # and again
    assert win._pdf_list.count() == 2

    patch_file_dialog(monkeypatch, ["X:/A.PDF"])  # same file, other casing
    win._on_add_pdfs()
    assert win._pdf_list.count() == 2  # comparison is case-insensitive


# --- 3.3–3.4: run state and progress ----------------------------------------


def test_3_3_build_button_disabled_during_run(qtbot, win, tmp_path, monkeypatch):
    """3.3: buttons disabled from _start_worker, re-enabled by finished."""
    dialogs = _start_run(qtbot, win, tmp_path, monkeypatch)

    assert not win._build_button.isEnabled()
    assert not win._open_db_button.isEnabled()
    assert win._progress.value() == 0
    assert win._status.text() == "Запуск сборки…"
    assert dialogs.info_calls == []  # nothing popped up yet

    # The fake worker never emits finished; emit it here (main thread).
    win._worker.finished.emit(True, "ok")
    assert win._build_button.isEnabled()
    _stop_thread(qtbot, win)


def test_3_4_progress_signal_updates_bar(qtbot, win, tmp_path, monkeypatch):
    """3.4: worker.progress(int, str) drives QProgressBar + status (:382)."""
    _start_run(qtbot, win, tmp_path, monkeypatch)

    win._worker.progress.emit(50, "OCR")
    assert win._progress.value() == 50
    assert win._status.text() == "OCR"

    win._worker.progress.emit(100, "Готово")
    assert win._progress.value() == 100
    assert win._status.text() == "Готово"
    _stop_thread(qtbot, win)


# --- 3.5–3.6: finished reports ----------------------------------------------


def test_3_5_finished_success_message(qtbot, win, tmp_path, monkeypatch):
    """3.5: finished(True, ...) -> information box with the report text and
    the П1 aircraft line (default variant, :396–401)."""
    dialogs = _start_run(qtbot, win, tmp_path, monkeypatch)

    win._worker.finished.emit(True, "PDF: 2, узлов: 100")
    assert dialogs.crit_calls == []
    assert len(dialogs.info_calls) == 1
    _parent, title, text = dialogs.info_calls[-1]
    assert title == "Сборка завершена"
    assert "PDF: 2, узлов: 100" in text
    # П1 default line — the operator never touched the field.
    assert 'aircraft_type = "Ми-8МТВ" (по умолчанию' in text
    assert win._status.text() == "Готово."
    assert win._build_button.isEnabled()
    assert win._open_db_button.isEnabled()

    # Non-default variant: a typed aircraft type without the hint.
    win._aircraft, win._aircraft_is_default = "Ми-24", False
    win._worker.finished.emit(True, "ok")
    _parent, _title, text2 = dialogs.info_calls[-1]
    assert 'aircraft_type = "Ми-24"' in text2
    assert "по умолчанию" not in text2
    _stop_thread(qtbot, win)


def test_3_6_finished_error_message(qtbot, win, tmp_path, monkeypatch):
    """3.6: finished(False, ...) -> critical box carrying the traceback."""
    dialogs = _start_run(qtbot, win, tmp_path, monkeypatch)

    win._worker.finished.emit(
        False, "Traceback (most recent call last):\nRuntimeError: boom"
    )
    assert dialogs.info_calls == []
    assert len(dialogs.crit_calls) == 1
    _parent, title, text = dialogs.crit_calls[-1]
    assert title == "Ошибка сборки"
    assert "Traceback" in text
    assert "по умолчанию" in text  # aircraft line still prepended (:396)
    assert win._build_button.isEnabled()
    assert not win._open_db_button.isEnabled()  # failure: folder stays off
    assert win._status.text() == "Сборка завершилась ошибкой — см. сообщение."
    _stop_thread(qtbot, win)


# --- 3.7: closing in flight (П9) ---------------------------------------------


def test_3_7_close_event_blocked_during_run(qtbot, win, monkeypatch):
    """3.7: closeEvent ignores closing while the worker thread runs (П9)."""
    dialogs = patch_dialogs(monkeypatch)

    win._thread = _FakeThread(running=True)
    event = _FakeEvent()
    win.closeEvent(event)
    assert event.ignored is True
    assert event.accepted is False
    assert len(dialogs.info_calls) == 1  # «дождитесь завершения» explanation

    # After the run (thread finished / never started) the window closes.
    win._thread = _FakeThread(running=False)
    event2 = _FakeEvent()
    win.closeEvent(event2)
    assert event2.accepted is True
    assert event2.ignored is False
    assert len(dialogs.info_calls) == 1  # no dialog for the idling window


# --- 3.8 (+П1): aircraft type passed to the worker ---------------------------


def test_3_8_aircraft_type_default_and_custom(qtbot, win, tmp_path, monkeypatch):
    """3.8/П1: empty type field -> worker receives "Ми-8МТВ"; a typed value
    passes through verbatim; the worker gets only the widget list (:334)."""
    patch_dialogs(monkeypatch)
    created = patch_fake_worker(monkeypatch, tmp_path / "graph.db")
    monkeypatch.setattr(bw, "ocr_available", lambda: True)

    paths = ["C:/tmp/x1.pdf", "C:/tmp/x2.pdf"]
    for path in paths:
        win._pdf_list.addItem(path)
    assert win._type_edit.text() == ""

    win._on_build_clicked()  # :334–353
    run = created[-1]
    assert run["aircraft_type"] == "Ми-8МТВ"       # П1 default
    assert run["pdf_paths"] == paths               # widget list only
    assert run["output_dir"] == win._output_edit.text().strip()
    assert win._aircraft_is_default is True
    _stop_thread(qtbot, win)

    # A typed value passes through verbatim.
    win._type_edit.setText("Ми-24")
    win._on_build_clicked()
    run2 = created[-1]
    assert run2["aircraft_type"] == "Ми-24"
    assert run2["pdf_paths"] == paths
    assert win._aircraft_is_default is False
    _stop_thread(qtbot, win)


# --- bonus acceptance guards -------------------------------------------------


def test_3_9_missing_ocr_blocks_build(qtbot, win, tmp_path, monkeypatch):
    """П3: no OCR engine -> critical hint, no worker at all."""
    dialogs = patch_dialogs(monkeypatch)
    created = patch_fake_worker(monkeypatch, tmp_path / "graph.db")
    monkeypatch.setattr(bw, "ocr_available", lambda: False)

    win._pdf_list.addItem("C:/tmp/x.pdf")
    win._on_build_clicked()

    assert created == []                    # nothing started
    assert dialogs.crit_calls and not dialogs.info_calls
    assert "Tesseract" in dialogs.crit_calls[-1][2]  # _OCR_HINT, :62
    assert win._build_button.isEnabled()


def test_3_10_empty_list_blocks_build(qtbot, win, tmp_path, monkeypatch):
    """Guard: «Создать граф» with no PDFs -> hint, no worker (:337)."""
    dialogs = patch_dialogs(monkeypatch)
    created = patch_fake_worker(monkeypatch, tmp_path / "graph.db")
    monkeypatch.setattr(bw, "ocr_available", lambda: True)

    win._on_build_clicked()

    assert created == []
    assert dialogs.info_calls and not dialogs.crit_calls
    assert "хотя бы один PDF" in dialogs.info_calls[-1][2]


# --- P1: worker emits per-page progress (not only file boundaries) ----------


def test_worker_progress_emits_per_page(monkeypatch, tmp_path):
    """P1: ``_run()`` now ticks progress per OCR page, not only at file
    boundaries: 0 (start) → per-page ticks → «OCR готов» → build → 100.

    The worker is run directly (no QThread — signals fire synchronously);
    ``process_file`` is faked to invoke the ``on_progress`` hook the worker
    passes in. The store is resolved into ``tmp_path``, so the production
    ``graph.db`` is never touched; PDF paths are fake, no file I/O.
    """
    from pdfprepare.models import DocResult, PageInfo

    emitted: list[tuple[int, str]] = []

    def fake_process_file(src, output_dir, registry, *, force_ocr=False,
                          on_progress=None):
        assert on_progress is not None  # the worker passes the hook
        for done in (1, 2, 3):
            on_progress(done, 3)
        pages = [PageInfo(i, False, False, "023.00.00", "", "стр", [])
                 for i in range(3)]
        return DocResult(src, 3, pages, {})

    monkeypatch.setattr(bw, "process_file", fake_process_file)
    monkeypatch.setattr(bw, "find_registry_path", lambda *a, **k: None)
    monkeypatch.setattr(
        bw, "resolve_store_path_for_build",
        lambda: (str(tmp_path / "graph.db"), True),
    )

    worker = bw.GraphBuildWorker(
        ["C:/tmp/doc.pdf"], str(tmp_path / "out"), "Ми-8МТВ"
    )
    worker.progress.connect(lambda value, message: emitted.append((value, message)))
    worker._run()  # direct call, main thread; no QApplication side effects

    values = [value for value, _ in emitted]
    assert values[0] == 0                     # file 1 starts at 0
    assert values[-1] == 100                  # build finished
    assert values == sorted(values)           # never goes backwards
    assert any(0 < value < 100 for value in values)  # not only 0 and 100
    assert len(emitted) >= 5                  # 0 + 3 pages + ready + build + 100
    assert any("OCR: файл 1/1, стр 2/3" in message for _, message in emitted)