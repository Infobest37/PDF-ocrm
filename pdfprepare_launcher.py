"""Double-click launcher for the portable ``pdfprepare`` pipeline (new version).

Two ways to use it:

* Double-click ``prepare_pdf.bat`` (or the desktop shortcut) — the script asks
  for the folder with source PDFs and for the folder to write results into,
  runs the pipeline from the project root, then shows a summary.
* Command line, no dialogs at all::

      venv\\Scripts\\python.exe pdfprepare_launcher.py <src-dir> <dst-dir>

The pipeline itself lives in the ``pdfprepare`` package and is invoked exactly
as its documented CLI (``python -m pdfprepare``); this script only picks
folders, pins the working directory to the project root (so the ATA-100
registry file is found) and reports what was produced.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

#: Project root — the directory holding this script, ``pdfprepare/`` and the
#: ATA-100 registry file. Also the pipeline's working directory.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

#: Default place for results when the operator cancels the second dialog.
DEFAULT_DST = os.path.join(PROJECT_ROOT, "out")

#: ATA-100 registry used by the pipeline (resolved relative to the project root).
REGISTRY_NAME = "Коды систем Вертолета.txt"

_OUTPUT_SUFFIXES = (".pdf", ".json", ".report.txt")


def _pick_folder(title: str, initial: str) -> str:
    """Ask for a directory with a native folder picker; '' when cancelled."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(
            title=title, initialdir=initial, mustexist=True
        ) or ""
    finally:
        root.destroy()


def _show_info(title: str, text: str) -> None:
    """Show a native message box (used only in interactive runs)."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        messagebox.showinfo(title, text)
    finally:
        root.destroy()


def _choose_folders() -> tuple[str, str] | None:
    """Ask for (source, destination); None when the source dialog was cancelled."""
    src = _pick_folder("Выберите папку с PDF-файлами", PROJECT_ROOT)
    if not src:
        return None
    dst = _pick_folder(
        "Куда сохранить результат (Отмена = папка out)",
        DEFAULT_DST if os.path.isdir(DEFAULT_DST) else PROJECT_ROOT,
    )
    return src, (dst or DEFAULT_DST)


def _count_pdfs(folder: str) -> int:
    return sum(
        1
        for name in os.listdir(folder)
        if name.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder, name))
    )


def _produced(dst: str, started: float) -> list[str]:
    """Files written into ``dst`` since ``started``, name-sorted."""
    if not os.path.isdir(dst):
        return []
    names = []
    for name in os.listdir(dst):
        path = os.path.join(dst, name)
        if not os.path.isfile(path) or not name.lower().endswith(_OUTPUT_SUFFIXES):
            continue
        if os.path.getmtime(path) >= started:
            names.append(name)
    return sorted(names)


def _run_pipeline(src: str, dst: str) -> int:
    """Run ``python -m pdfprepare <src> <dst>`` from the project root."""
    os.makedirs(dst, exist_ok=True)
    cmd = [sys.executable, "-m", "pdfprepare", src, dst]
    print("Папка с PDF :", src)
    print("Куда сложить:", dst)
    print("Запускаю    :", " ".join(cmd))
    print("-" * 70)
    completed = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return completed.returncode


def main(argv: list[str]) -> int:
    interactive = not argv

    if interactive:
        if not os.path.isfile(os.path.join(PROJECT_ROOT, REGISTRY_NAME)):
            print(f"внимание: справочник не найден: {REGISTRY_NAME}")
        picked = _choose_folders()
        if picked is None:
            print("Отменено — папка не выбрана.")
            return 1
        src, dst = picked
    elif len(argv) == 2:
        src, dst = argv
    else:
        print(
            "Использование:\n"
            "  pdfprepare_launcher.py                  (диалоги выбора папок)\n"
            "  pdfprepare_launcher.py <src> <dst>      (без диалогов)",
            file=sys.stderr,
        )
        return 2

    if not os.path.isdir(src):
        text = f"Папка не найдена:\n{src}"
        print("ошибка:", text)
        if interactive:
            _show_info("Ошибка", text)
        return 1

    if _count_pdfs(src) == 0:
        text = f"В выбранной папке нет PDF-файлов:\n{src}"
        print("ошибка:", text)
        if interactive:
            _show_info("Нет файлов", text)
        return 1

    started = time.time()
    code = _run_pipeline(src, dst)
    produced = _produced(dst, started)

    # A `.report.txt` holds one line per page that needs a re-scan; an empty
    # report is the good outcome and must not look like a failure.
    empty_reports = [
        name
        for name in produced
        if name.lower().endswith(".report.txt")
        and os.path.getsize(os.path.join(dst, name)) == 0
    ]

    print("-" * 70)
    if code != 0:
        print("Завершено с ошибками. Подробности выше в этом окне.")
    else:
        print("Готово. Создано файлов:", len(produced))
    for name in produced:
        print("   ", name)
    if empty_reports:
        print(
            "Отчёт о качестве пуст — это хорошо: страниц, требующих "
            "пересканирования, не найдено."
        )
    print("Папка с результатами:", dst)

    if interactive:
        if code != 0:
            head = "Обработка завершилась с ошибками — подробности в окне консоли."
        else:
            head = "Обработка завершена."
        listing = "\n".join(produced[:15]) if produced else "файлы не созданы"
        if len(produced) > 15:
            listing += f"\n… и ещё {len(produced) - 15}"
        note = ""
        if empty_reports:
            note = (
                "\n\nОтчёт о качестве пуст — это хорошо: страниц, "
                "требующих пересканирования, не найдено."
            )
        _show_info(
            "Подготовка PDF",
            f"{head}\n\nСоздано файлов: {len(produced)}\n{listing}{note}\n\nПапка:\n{dst}",
        )
        try:
            os.startfile(dst)  # noqa: S606 - opening the operator's result folder
        except OSError:
            pass

    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
