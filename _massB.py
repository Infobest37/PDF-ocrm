# -*- coding: utf-8 -*-
"""Этап B массового прогона: все 49 PDF через настоящий GUI BuilderWindow.

Тот же механизм, что _massA.py (обёртки BuilderWindow._on_progress/
_on_finished, QMessageBox перехвачен), но SELECTED = все pdfs/*.pdf.
Лог: gui-stage-mass/progress_log_B.txt. Запуск: python _massB.py (из корня).
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

EV = os.path.join(ROOT, ".superpowers", "sdd",
                  "2026-10-16-knowledge-graph-plan", "gui-stage-mass")
os.makedirs(EV, exist_ok=True)
LOG = open(os.path.join(EV, "progress_log_B.txt"), "w", encoding="utf-8")
START = time.perf_counter()


def elapsed():
    return "%.1fs" % (time.perf_counter() - START)


def log(msg):
    LOG.write(msg + "\n")
    LOG.flush()


def finish_flag(text):
    with open(os.path.join(EV, "progress_done.flag"), "w", encoding="utf-8") as f:
        f.write(text)


from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pdfgraph.gui.builder_window import BuilderWindow  # noqa: E402

_APP = {}

_orig_information = QMessageBox.information
_orig_critical = QMessageBox.critical


def _cap_information(parent, title, text, *a, **k):
    log("%s MSGBOX information | %s | %s" % (elapsed(), title, text))
    with open(os.path.join(EV, "finished_log_B.txt"), "a", encoding="utf-8") as f:
        f.write("INFORMATION|%s|%s\n" % (title, text))
    return 0


def _cap_critical(parent, title, text, *a, **k):
    log("%s MSGBOX critical | %s | %s" % (elapsed(), title, text))
    with open(os.path.join(EV, "finished_log_B.txt"), "a", encoding="utf-8") as f:
        f.write("CRITICAL|%s|%s\n" % (title, text))
    return 0


QMessageBox.information = _cap_information
QMessageBox.critical = _cap_critical

app = QApplication(sys.argv)
_APP["app"] = app

w = BuilderWindow()
w.show()
app.processEvents()

pdf_dir = os.path.join(ROOT, "pdfs")
all_pdfs = sorted(f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf"))
log("pdfs/ on disk: %d files" % len(all_pdfs))

for name in all_pdfs:
    path = os.path.abspath(os.path.join(pdf_dir, name))
    if not os.path.isfile(path):
        log("STOP: file not found: " + path)
        finish_flag("FILE_NOT_FOUND")
        sys.exit(2)
    w._pdf_list.addItem(path)

rows = [w._pdf_list.item(i).text() for i in range(w._pdf_list.count())]
log("QListWidget rows: %d" % len(rows))
for r in rows:
    log("ROW: " + r)
if len(rows) != len(all_pdfs):
    log("STOP: rows mismatch")
    finish_flag("LIST_MISMATCH")
    sys.exit(2)

log("type_edit: %r" % w._type_edit.text())
log("output_dir field: %r" % w._output_edit.text())

app.processEvents()
w.grab().save(os.path.join(EV, "b0_selected.png"))
log("screenshot b0_selected.png")

_orig_on_progress = BuilderWindow._on_progress
_orig_on_finished = BuilderWindow._on_finished
_state = {"prev": -1}


def _wrap_on_progress(self, value, message):
    prev = _state["prev"]
    if value < prev:
        log("NON-MONOTONIC %d -> %d" % (prev, value))
    _state["prev"] = value
    log("%s tick %d|%s" % (elapsed(), value, message))
    if prev < 0:
        log("%s FIRST TICK" % elapsed())
    return _orig_on_progress(self, value, message)


def _wrap_on_finished(self, success, report):
    log("%s FINISHED ok=%s" % (elapsed(), success))
    for line in report.splitlines():
        log("  | " + line)
    with open(os.path.join(EV, "finished_log_B.txt"), "a", encoding="utf-8") as f:
        f.write("FINISHED|%s|%s\n" % (success, report))
    w.grab().save(os.path.join(EV, "b9_final.png"))
    log("screenshot b9_final.png")
    rv = _orig_on_finished(self, success, report)
    QTimer.singleShot(300, lambda: _APP["app"].quit())
    return rv


BuilderWindow._on_progress = _wrap_on_progress
BuilderWindow._on_finished = _wrap_on_finished

log("clicking build_button")
w._build_button.click()
log("clicked at %s" % elapsed())

app.exec_()
log("== app exec returned at %s ==" % elapsed())
LOG.close()
finish_flag("DONE_B")