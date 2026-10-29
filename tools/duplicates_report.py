"""Инвентаризация дублей документов — карточка 0.1б, Шаг 1 (read-only).

Печатает и пишет ``reports/duplicates.md``: группы документов с одинаковым
«обозначением» (после вырезания скобок с ревизией и синонимного префикса
«руководство по технической эксплуатации»), число листов, порядок постройки
листов в графе (MIN(pages.rowid)), размер исходных PDF, ready-вопросы
golden-набора с их листами и попадания в топ-20 по трём контрольным
запросам — дословные формулировки берутся из ``tests/golden/questions.toml``
по id (:data:`CONTROL_IDS`), чтобы отчёт всегда согласовывался с набором.

Рабочая БД не открывается на запись: сначала ``graph.db`` копируется
в TEMP (sqlite ``backup()`` поверх read-only подключения — тот же приём,
что в ``tests/golden/metrics.py``); все поисковые прогоны идут по копии.

Канон скрипт НЕ выбирает — только предлагает кандидата по правилам
карточки 0.1б, строго в порядке приоритета:

    1) листы golden set (ready) принадлежат одному из экземпляров — канон он;
    2) иначе более поздняя редакция (дата «от ДД.ММ.ГГ» в имени);
    3) иначе тот, чей исходный PDF реально лежит в папке PDF.

Решение о каноне принимает владелец по отчёту.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import tempfile
import tomllib
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import pathname2url

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_PATH = REPO_ROOT / "graph.db"
DEFAULT_REPORT_PATH = REPO_ROOT / "reports" / "duplicates.md"
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "tests" / "golden" / "questions.toml"

#: Контрольные запросы топ-20 — готовые (ready) вопросы golden-набора;
#: формулировки всегда читаются из questions.toml дословно.
CONTROL_IDS: tuple[str, ...] = ("e03", "e04", "e06")

#: Глубина контрольной выдачи (карточка 0.1б: топ-20).
CONTROL_DEPTH = 20

try:
    from pdfgraph.gui.pdf_opener import _find_pdf_file, resolve_pdf_dir
    from pdfgraph.query import search
    from pdfgraph.store import Store
except ModuleNotFoundError:  # запуск как скрипт вне pytest
    sys.path.insert(0, str(REPO_ROOT))
    from pdfgraph.gui.pdf_opener import _find_pdf_file, resolve_pdf_dir
    from pdfgraph.query import search
    from pdfgraph.store import Store

#: Синонимичное длинное название в именах экземпляров (факт из БД).
_LEAD_PREFIX_RE = re.compile(
    r"^руководство по технической эксплуатации\s*", re.IGNORECASE)

#: Дата редакции в имени: «от ДД.ММ.ГГ»/«от ДД.ММ.ГГГГ».
_DATE_RE = re.compile(r"от\s*(\d{1,2}\.\d{1,2}\.\d{2,4})")


def norm_key(name: str) -> str:
    """Ключ группы: имя без расширения, скобок и синонимного префикса.

    NFKC + casefold для устойчивости к регистру и юникод-формам. Ключ
    используется только для отчёта — имена документов в БД не трогаются.
    """
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    stem = re.sub(r"\([^)]*\)", " ", stem)
    stem = _LEAD_PREFIX_RE.sub("", unicodedata.normalize("NFKC", stem))
    folded = unicodedata.normalize("NFKC", stem).casefold().replace("ё", "е")
    return " ".join(folded.split())


def edition_date(name: str) -> str:
    """Первая дата «от ДД.ММ.ГГ(ГГ)» в имени (для правила «новее редакция»)."""
    match = _DATE_RE.search(name)
    return match.group(1) if match else ""


def _edition_sort_key(name: str) -> tuple[int, int, int] | None:
    """Сортировочный ключ даты редакции; двузначный год: >=30 → 19xx, иначе 20xx."""
    match = _DATE_RE.search(name)
    if not match:
        return None
    day, month, year = match.group(1).split(".")
    value = int(year)
    if len(year) == 2:
        value += 1900 if value >= 30 else 2000
    return value, int(month), int(day)


def copy_readonly(source: str | Path, target: str | Path) -> None:
    """Скопировать рабочую БД: backup() поверх read-only, откат — файловая копия."""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        uri = f"file:{pathname2url(str(source))}?mode=ro"
        src = sqlite3.connect(uri, uri=True)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
                return
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error:
        pass
    shutil.copyfile(source, target)  # откат: только сам файл БД


def load_golden(path: str | Path = DEFAULT_QUESTIONS_PATH
                ) -> list[tuple[str, str, str, str, tuple[int, ...]]]:
    """Ready-вопросы golden-набора: (id, вопрос, тип, документ, листы)."""
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return [
        (item["id"], item["question"], item["aircraft_type"],
         item["expected_doc"], tuple(item["expected_pages"]))
        for item in data.get("question", [])
        if item.get("status") == "ready"
    ]


def golden_map(golden: list[tuple[str, str, str, str, tuple[int, ...]]]
               ) -> dict[str, list[tuple[str, tuple[int, ...]]]]:
    """Индекс: документ → [(id-вопроса, листы)] для ready-вопросов."""
    index: dict[str, list[tuple[str, tuple[int, ...]]]] = {}
    for _qid, _question, _type, expected_doc, pages in golden:
        if expected_doc:
            index.setdefault(expected_doc, []).append((_qid, pages))
    return index


def control_queries(golden: list[tuple[str, str, str, str, tuple[int, ...]]]
                    ) -> dict[str, tuple[str, str]]:
    """(id → (вопрос, aircraft_type)) для контрольных id из golden-набора."""
    by_id = {qid: (question, aircraft_type)
             for qid, question, aircraft_type, _doc, _pages in golden}
    missing = [qid for qid in CONTROL_IDS if qid not in by_id]
    if missing:
        raise ValueError(
            f"контрольных вопросов нет в golden-наборе: {', '.join(missing)}")
    return {qid: by_id[qid] for qid in CONTROL_IDS}


def top20_documents(store: Store, question: str, aircraft_type: str,
                    depth: int = CONTROL_DEPTH) -> dict[str, int]:
    """Сколько листов каждого документа в топ-N выдачи одного запроса."""
    counts: dict[str, int] = defaultdict(int)
    for fragment in search(store, question, aircraft_type, limit=depth):
        counts[fragment.document] += 1
    return dict(counts)


def collect_documents(store: Store) -> list[tuple[str, int, int]]:
    """(документ, листов, порядок-прокси MIN(rowid)) по копии хранилища.

    rowid по построению растёт вместе со вставкой листов, поэтому его
    минимум служит прокси «когда документ строился» — точнее в БД нет.
    """
    rows = store._connection.execute(
        "SELECT document, COUNT(*), MIN(rowid) FROM pages "
        "GROUP BY document ORDER BY document").fetchall()
    return [(row[0], int(row[1]), int(row[2])) for row in rows]


def pdf_dir_info(pdf_dir: Path, documents: list[str]
                 ) -> tuple[dict[str, Path | None], list[str]]:
    """Для каждого документа — найденный PDF; и лишние файлы папки.

    Матчинг тот же, что у GUI (точное имя → ``+ .pdf`` → NFC-casefold);
    «лишние» — PDF на диске, чьи имена не встречаются в графе.
    """
    found: dict[str, Path | None] = {
        name: _find_pdf_file(pdf_dir, name) for name in documents}
    in_graph = {unicodedata.normalize("NFKC", name).casefold()
                for name in documents}
    extra = [entry.name for entry in pdf_dir.glob("*.pdf")
             if unicodedata.normalize("NFKC", entry.name).casefold()
             not in in_graph]
    return found, extra


def suggest_canonical(cluster: list[str],
                      golden_index: dict[str, list[tuple[str, tuple[int, ...]]]],
                      found_pdfs: dict[str, Path | None]
                      ) -> tuple[str | None, str]:
    """Кандидат канона по правилам карточки 0.1б + причина (для отчёта)."""
    with_golden = [name for name in cluster if golden_index.get(name)]
    if len(with_golden) == 1:
        return with_golden[0], "правило 1: листы golden set (ready) только у него"
    if len(with_golden) > 1:
        return None, "правило 1 неоднозначно (golden-листы у нескольких)"
    dated = [(_edition_sort_key(name), name) for name in cluster]
    known = [pair for pair in dated if pair[0] is not None]
    if len(known) == len(cluster) and len({key for key, _ in known}) == len(known):
        newest = max(known)[1]
        other_dates = ", ".join(
            edition_date(name) for _key, name in sorted(known)
            if name != newest)
        return newest, (f"правило 2: более поздняя редакция "
                        f"(прочие: {other_dates})")
    with_file = [name for name in cluster if found_pdfs.get(name) is not None]
    if len(with_file) == 1:
        return with_file[0], "правило 3: только его PDF остался в папке"
    return None, ("автопредложение неоднозначно (правило 1/2 не дали "
                  "явного победителя) — решение владельца")

def _fmt_size(size: int | None) -> str:
    """Человекочитаемый размер PDF (MB) или прочерк."""
    return "—" if size is None else f"{size / (1024 * 1024):.1f} MB"


def _cluster_rows(members: list[str],
                  doc_info: dict[str, tuple[int, int]],
                  golden_index: dict[str, list[tuple[str, tuple[int, ...]]]],
                  found_pdfs: dict[str, Path | None],
                  tops: dict[str, dict[str, int]]) -> list[str]:
    """Строки md-таблицы одного кластера (по одной строке на экземпляр)."""
    header = ("| имя | дата ред. | листов | постр. (rowid) | PDF | размер | "
              "golden ready (вопрос) | "
              + " | ".join(f"топ-{CONTROL_DEPTH} {qid}" for qid in tops) + " |")
    sep = "|---|---|---:|---:|:-:|---|---:|" + "---:|" * len(tops)
    rows = [header, sep]
    for name in sorted(members):
        sheets, row_min = doc_info.get(name, (0, 0))
        pdf = found_pdfs.get(name)
        pdf_mark = "есть" if pdf is not None else "**НЕТ**"
        golden_str = "; ".join(
            f"{qid} ({len(pages)})" for qid, pages in golden_index.get(name, []))
        top_cells = " | ".join(str(tops[qid].get(name, 0)) for qid in tops)
        rows.append(
            f"| {name} | {edition_date(name) or '—'} | {sheets} | {row_min} "
            f"| {pdf_mark} | {_fmt_size(pdf.stat().st_size if pdf else None)} "
            f"| {golden_str or '—'} | {top_cells} |")
    winner, reason = suggest_canonical(members, golden_index, found_pdfs)
    rows.append("")
    rows.append(
        f"**Кандидат в канон:** {winner if winner is not None else '—'} — "
        f"{reason}.")
    return rows


def _render_report(args: argparse.Namespace,
                   documents: list[tuple[str, int, int]],
                   golden_index: dict[str, list[tuple[str, tuple[int, ...]]]],
                   found_pdfs: dict[str, Path | None],
                   pdf_dir: Path | None,
                   extra_files: list[str],
                   clusters: dict[str, list[str]],
                   tops: dict[str, dict[str, int]]) -> list[str]:
    """Собрать строки markdown-отчёта: метаданные, кластеры, сводки."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc_info = {name: (sheets, row_min)
                for name, sheets, row_min in documents}
    lines = [
        "# Инвентаризация дублей документов (карточка 0.1б, Шаг 1)",
        "",
        f"* Дата: {now}",
        f"* Хранилище: ``{args.store}`` — read-only, поиски по копии в TEMP",
        f"* Золотой набор: ``{args.questions}``",
        "* Контрольные запросы — дословно из ready-вопросов "
        + ", ".join(tops) + f", глубина {CONTROL_DEPTH}.",
        f"* Папка PDF: ``{pdf_dir if pdf_dir is not None else 'НЕ НАЙДЕНА'}``",
        "",
        "Скрипт только предлагает кандидата; решение о каноне — владелец. "
        "Из графа ничего не удалено, пометок нет.",
        "",
    ]
    if clusters:
        lines.append(f"## Кластеры кандидатов ({len(clusters)})")
        lines.append("")
        for key in sorted(clusters):
            members = sorted(clusters[key])
            lines.append(f"### ``{key}`` — {len(members)} экз.")
            lines.append("")
            lines.extend(_cluster_rows(members, doc_info, golden_index,
                                       found_pdfs, tops))
            lines.append("")
    else:
        lines += ["## Кластеры кандидатов", "",
                  "Дубли по ключу (скобки и синоним префикса выброшены) "
                  "не найдены.", ""]

    missing = [name for name, pdf in found_pdfs.items() if pdf is None]
    found_count = len(found_pdfs) - len(missing)
    lines += ["## Контрольные запросы (кто реально работает)", ""]
    for qid in tops:
        listing = ", ".join(
            f"{name} — {count}" for name, count
            in sorted(tops[qid].items(), key=lambda kv: (-kv[1], kv[0]))) or "—"
        lines.append(f"* **{qid}**: {listing}")
    lines += [
        "",
        "## Диск и граф",
        "",
        f"* PDF найдены для {found_count}/{len(documents)} документов.",
        "* На диске есть, в графе НЕ участвует: "
        + str(len(extra_files))
        + (": " + ", ".join(f"``{n}``" for n in extra_files)
           if extra_files else ""),
        "* В графе есть, файла НЕТ: " + str(len(missing))
        + (": " + ", ".join(f"``{n}``" for n in missing) if missing else ""),
        "",
        "## Все документы",
        "",
        "| документ | листов | постр. (rowid) | PDF | размер | golden (листов) |",
        "|---|---:|---:|:-:|---|---:|",
    ]
    for name, sheets, row_min in sorted(documents, key=lambda row: row[0]):
        pdf = found_pdfs.get(name)
        golden_pages = sum(len(pages)
                           for _qid, pages in golden_index.get(name, []))
        lines.append(
            f"| {name} | {sheets} | {row_min} | "
            f"{'есть' if pdf is not None else '**НЕТ**'} "
            f"| {_fmt_size(pdf.stat().st_size if pdf else None)} "
            f"| {golden_pages} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Собрать отчёт ``reports/duplicates.md`` и напечатать краткую сводку."""
    parser = argparse.ArgumentParser(
        prog="duplicates_report",
        description="Инвентаризация дублей документов (read-only; "
                    "карточка 0.1б, Шаг 1).")
    parser.add_argument("--store", default=str(DEFAULT_STORE_PATH))
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--pdf-dir", default=None,
                        help="Папка PDF; по умолчанию PDFGRAPH_PDF_DIR, "
                             "затем DEFAULT_PDF_DIR (как в GUI).")
    parser.add_argument("--depth", type=int, default=CONTROL_DEPTH)
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="duplicates_") as tmp:
        store = Store(_copy_to_temp(args.store, tmp))
        try:
            documents = collect_documents(store)
            names = [row[0] for row in documents]
            golden = load_golden(args.questions)
            controls = control_queries(golden)
            tops = {qid: top20_documents(store, question, aircraft_type,
                                         args.depth)
                    for qid, (question, aircraft_type) in controls.items()}
        finally:
            store.close()

    pdf_dir = resolve_pdf_dir(args.pdf_dir)
    found_pdfs: dict[str, Path | None] = {}
    extra_files: list[str] = []
    if pdf_dir is not None:
        found_pdfs, extra_files = pdf_dir_info(pdf_dir, names)
    golden_index = golden_map(golden)
    groups: dict[str, list[str]] = defaultdict(list)
    for name in names:
        groups[norm_key(name)].append(name)
    clusters = {key: members for key, members in groups.items()
                if len(members) > 1}

    lines = _render_report(args, documents, golden_index, found_pdfs,
                           pdf_dir, extra_files, clusters, tops)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Документов: {len(names)}; кластеров-дублей: {len(clusters)}")
    for key in sorted(clusters):
        members = clusters[key]
        winner, reason = suggest_canonical(members, golden_index, found_pdfs)
        print(f"  - {key}: {len(members)} экз. → канон-кандидат: "
              f"{winner if winner is not None else 'не определён'}")
        print(f"      ({reason})")
    print(f"Отчёт: {report_path}")
    return 0


def _copy_to_temp(source: str, tmp: str) -> str:
    """Копия хранилища в TEMP-каталог для read-only прогона."""
    target = Path(tmp) / "graph.db"
    copy_readonly(source, target)
    return str(target)


if __name__ == "__main__":
    raise SystemExit(main())
