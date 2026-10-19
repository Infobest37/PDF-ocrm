# -*- coding: utf-8 -*-
"""Этап A массового прогона: настоящий GUI BuilderWindow, 10 файлов.

Логика окна не меняется; драйвер добавляет 10 путей в _pdf_list (тот же
addItem, что в обработчике диалога), проверяет пустоту поля типа и логирует
поток событий через обёртки BuilderWindow._on_progress/_on_finished.
QMessageBox перехвачен (модальный бокс блокировал бы exec_()).
Запуск:  venv\\Scripts\\python.exe _massA.py   (из корня репо)
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
LOG = open(os.path.join(EV, "progress_log.txt"), "w", encoding="utf-8")
START = time.perf_counter()


def elapsed():
    return "%.1fs" % (time.perf_counter() - START)


def log(msg):
    LOG.write(msg + "\n")
    LOG.flush()


def finish_flag(text):
    with open(os.path.join(EV, "progress_done.flag"), "w", encoding="utf-8") as f:
        f.write(text)


SELECTED = [
    "Двигатель ТВ3-117 078.00.5700 РЭ1 книга_1 (изм. от 17.09.2024).pdf",
    "Двигатель ТВ3-117 078.00.5700 РЭ1 книга_2 (изм. от 17.09.2024).pdf",
    "Двигатель ТВ3-117 078.00.5700-04 РЭ книга 3 (изм. от 17.09.2024).sanitized.pdf",
    "Каталок деталей и сборочных едениц КДЭ_Ми-8МТВ-1_книга_1 (файл 02.02.2022).pdf",
    "Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 3 (изм. от 13.12.2024).pdf",
    "Доп. к РО Д-1697 ч.1 для вертолета 22217.pdf",
    "Каталог дефектов Ми-8МТВ кн. 2 (АиРЭО) (изм. 1 от 25.05.2010).pdf",
    "Редуктор ВР-14_7871 РЭ (изм. 17 от 10.09.2021).pdf",
    "Генераторы ГТ40ПЧ8Б  ГТ40ПЧ8В  РТЭ (изм. 10 от 29.02.2012)_OCR.pdf",
    "Регламент технического обслуживания Часть 1 Ми-8МТВ-1 Планер (изм. 63 от 04.04.2025).pdf",
]

log("== mass stage A start ==")

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import pdfgraph.gui.builder_window as bw_mod  # noqa: E402
from pdfgraph.gui.builder_window import BuilderWindow  # noqa: E402

_APP = {}

_orig_information = QMessageBox.information
_orig_critical = QMessageBox.critical


def _cap_information(parent, title, text, *a, **k):
    log("%s MSGBOX information | %s | %s" % (elapsed(), title, text))
    with open(os.path.join(EV, "finished_log.txt"), "a", encoding="utf-8") as f:
        f.write("INFORMATION|%s|%s\n" % (title, text))
    return 0


def _cap_critical(parent, title, text, *a, **k):
    log("%s MSGBOX critical | %s | %s" % (elapsed(), title, text))
    with open(os.path.join(EV, "finished_log.txt"), "a", encoding="utf-8") as f:
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
    log("DISK: " + name)

for name in SELECTED:
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
if len(rows) != len(SELECTED):
    log("STOP: rows mismatch")
    finish_flag("LIST_MISMATCH")
    sys.exit(2)

log("type_edit: %r (placeholder %r)" % (
    w._type_edit.text(), w._type_edit.placeholderText()))
log("output_dir field: %r" % w._output_edit.text())

app.processEvents()
w.grab().save(os.path.join(EV, "a0_selected.png"))
log("screenshot a0_selected.png")

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
    with open(os.path.join(EV, "finished_log.txt"), "a", encoding="utf-8") as f:
        f.write("FINISHED|%s|%s\n" % (success, report))
    w.grab().save(os.path.join(EV, "a9_final.png"))
    log("screenshot a9_final.png")
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
finish_flag("DONE")