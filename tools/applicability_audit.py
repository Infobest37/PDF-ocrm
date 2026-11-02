# -*- coding: utf-8 -*-
"""Ф0.3а: инвентаризация применимости документов (read-only аудит).

Read-only: работает на КОПИИ graph.db в темп-каталоге (copy_store из
tests/golden/metrics.py); ни база, ни продакшн-код не изменяются.
База читается одним проходом ``SELECT document, page, needs_rescan,
is_service, text FROM pages`` — группировка по документам в памяти.

По каждому документу (карточка Ф0.3а):

1. имя файла, число листов (в т.ч. needs_rescan/is_service);
2. год и редакция из имени («изм. N от …», «изм. от …», «файл от …»,
   «изд. …»);
3. тип по содержанию (имя, при неоднозначности текст первых листов):
   базовый РЭ/РТЭ, регламент ТО, каталог деталей, каталог дефектов,
   дополнение, изменение, прочее;
4. применимость: номера бортов в вариантах «22217», «22-217»,
   «№ 22217», «борт 22217» — в имени и/или в тексте первых трёх
   листов, с фразой-контекстом; источник: name / text / none;
5. упоминания других типов ВС (Ми-26, Ка-32, Ми-2, Ми-17) по ВСЕМУ
   тексту листов — с границей слова (Ми-17 не ловится внутри Ми-171,
   Ми-2 — внутри Ми-26; проверено на примерах до полного прогона),
   где (лист) и в каком контексте.

Сводная таблица: счёт по типам, применимость явно/нет; отдельный список
документов без явной применимости — решение принимает человек.

Отчёты: reports/applicability_audit.json, reports/applicability_audit.md
и раздел docs/plans/2026-11-02-Ф0.3а-applicability-audit.md (сводка,
документы для ручного решения, предложение схемы — не реализация).

Запуск: venv\\Scripts\\python.exe -X utf8 tools\\applicability_audit.py
[путь_БД]. Коммит — только этот скрипт и docs-раздел; reports/ вне git.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.golden.metrics import copy_store  # noqa: E402

DEFAULT_STORE = REPO_ROOT / "graph.db"
JSON_REPORT = REPO_ROOT / "reports" / "applicability_audit.json"
MD_REPORT = REPO_ROOT / "reports" / "applicability_audit.md"
DOCS_REPORT = (REPO_ROOT / "docs" / "plans"
               / "2026-11-02-Ф0.3а-applicability-audit.md")
#: Сколько первых листов читается на применимость (карточка Ф0.3а).
FIRST_PAGES = 3
#: Контекстов упоминания другого типа ВС на документ в отчёте.
MAX_CONTEXTS = 3
#: Окно контекста вокруг совпадения, символов.
CONTEXT_CHARS = 60
#: Другие типы ВС (карточка Ф0.3а).
OTHER_TYPES: tuple[str, ...] = ("Ми-26", "Ка-32", "Ми-2", "Ми-17")
#: Граница слова для типа ВС: не буква/цифра/дефис до, не цифра после —
#: «Ми-171» не считается «Ми-17», «Ми-26» не считается «Ми-2».
_OTHER_TYPE_RE = {
    t: re.compile(r"(?<![A-Za-zА-ЯЁа-я0-9-])" + re.escape(t) + r"(?![0-9])")
    for t in OTHER_TYPES}
#: Бортовой номер: «22217», «22-217», «№ 22217», «борт 22217»;
#: 5–6 цифр с началом «2X» либо split «2X-XXX». Split через ПРОБЕЛ
#: отсечён: в текстах он ловит пары чисел вида «стр. 20 207»
#: (проверено прогоном Ф0.3а на копии БД).
_SERIAL_RE = re.compile(r"(?<![0-9-])(?:2\d-\d{3}|2\d\d{3,4})(?![0-9-])")
#: Маркеры применимости в тексте (первые листы).
_APPLICABILITY_RE = re.compile(
    r"действител\w+ для|вертолето?[ав]\s*№|борт\w*\s*№|№№?\s*2\d", re.I)
#: Ревизия/дата из имени (четыре реальные формы имён базы).
_REVISION_RES = (
    re.compile(r"изм\.\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})"),
    re.compile(r"изм\.\s*от\s*(\d{2}\.\d{2}\.\d{4})"),
    re.compile(r"файл\s*(?:от)?\s*(\d{2}\.\d{2}\.\d{4})"),
    re.compile(r"изд\.\s*(\d{2}\.\d{2}\.\d{4})"),
)
#: Идентификатор комплекта дополнения (Д-1594 и подобные) и книга/часть.
_KIT_RE = re.compile(r"Д-(\d{3,4})")
_BOOK_RES = (re.compile(r"(?:кн|к)\.\s*(\d+)"), re.compile(r"ч\.\s*(\d+)"),
             re.compile(r"[Чч]асть\s+(\d+)"))
#: Тип документа: порядок проверки важен (от частного к общему).
_TYPE_MARKERS: tuple[tuple[str, str], ...] = (
    ("дополнение", ("доп. ", "доп.", "дополнение")),
    ("каталог дефектов", ("каталог дефектов",)),
    ("каталог деталей", ("каталок деталей", "каталог деталей", "кдэ",
                         "каталог ")),
    ("регламент ТО", ("регламент технического обслуживания",)),
    ("РТЭ", ("ртэ", "руководство по технической эксплуатации")),
    ("РЭ", ("рэ.ро", "рэ и то", " рэ", "рэ")),
    ("изменение", ("изменение",)),
)


# --- Разбор имени ----------------------------------------------------------


def parse_name(name: str) -> dict:
    """Редакция, дата, год, комплект, книга/часть, борта из имени."""
    revision_no = None
    revision_date = None
    for pattern in _REVISION_RES:
        found = pattern.search(name)
        if found:
            groups = found.groups()
            if pattern is _REVISION_RES[0]:
                revision_no, revision_date = int(groups[0]), groups[1]
            else:
                revision_date = groups[-1]
            break
    file_dates = _REVISION_RES[2].findall(name)
    year = None
    for date in ([revision_date] if revision_date else []) + file_dates:
        if date:
            year = int(date.split(".")[2])
            break
    book = None
    for pattern in _BOOK_RES:
        found = pattern.search(name)
        if found:
            book = int(found.group(1))
            break
    kit = _KIT_RE.search(name)
    return {
        "revision_no": revision_no,
        "revision_date": revision_date,
        "year": year,
        "kit_id": f"Д-{kit.group(1)}" if kit else None,
        "book_or_part": book,
        "serials_in_name": sorted(set(_SERIAL_RE.findall(name))),
    }


def classify_document(name: str, first_pages_text: str) -> str:
    """Тип документа по имени; при неоднозначности — по первым листам.

    Маркер «изменение» применяется ТОЛЬКО к имени: в текстах первых
    листов «Перечень изменений» есть почти у любого руководства и
    валит тип (прогон Ф0.3а: SafirPG 05-01 был «изменением»).
    """
    lowered = name.lower()
    for label, markers in _TYPE_MARKERS:
        if any(marker in lowered for marker in markers):
            return label
    lowered_text = first_pages_text.lower()
    for label, markers in _TYPE_MARKERS:
        if label == "изменение":
            continue
        if any(marker in lowered_text for marker in markers):
            return label
    return "прочее"


# --- Текст листов -----------------------------------------------------------


def _clip(text: str, start: int, end: int) -> str:
    """Окно ±CONTEXT_CHARS вокруг [start:end) с заменой переводов строк."""
    lo = max(0, start - CONTEXT_CHARS)
    hi = min(len(text), end + CONTEXT_CHARS)
    return " ".join(text[lo:hi].split())


def applicability_contexts(text: str) -> list[str]:
    """Фразы-контексты применимости в тексте первых листов (до 3)."""
    contexts: list[str] = []
    for match in _APPLICABILITY_RE.finditer(text):
        contexts.append(_clip(text, match.start(), match.end()))
        if len(contexts) >= MAX_CONTEXTS:
            break
    return contexts


def find_serials(text: str) -> list[str]:
    """Бортовые номера в тексте (уникальные, отсортированные)."""
    return sorted(set(_SERIAL_RE.findall(text)))


def collect_mentions(pages: list[tuple[int, str]]) -> dict[str, dict]:
    """Упоминания других типов ВС по всем листам документа.

    ``pages`` — [(page, text)] одного документа. Возвращает
    {тип: {"pages": N, "contexts": ["лист N: …", …]}}; документы без
    упоминаний в итог не попадают.
    """
    out: dict[str, dict] = {}
    for type_name, pattern in _OTHER_TYPE_RE.items():
        hit_pages = 0
        contexts: list[str] = []
        for page, text in pages:
            if not text:
                continue
            match = pattern.search(text)
            if not match:
                continue
            hit_pages += 1
            if len(contexts) < MAX_CONTEXTS:
                contexts.append(
                    f"лист {page}: {_clip(text, match.start(), match.end())}")
        if hit_pages:
            out[type_name] = {"pages": hit_pages, "contexts": contexts}
    return out


# --- Основной сбор -----------------------------------------------------------


def audit(source: Path) -> dict:
    """Полный аудит на копии БД; возвращает данные для всех отчётов."""
    rows: dict[str, list[tuple[int, int, int, str]]] = {}
    with tempfile.TemporaryDirectory(prefix="applicability_f03a_") as tmp:
        copy_path = Path(tmp) / "graph.db"
        copy_store(source, copy_path)
        import sqlite3

        connection = sqlite3.connect(str(copy_path))
        try:
            for document, page, rescan, service, text in connection.execute(
                    "SELECT document, page, needs_rescan, is_service, text "
                    "FROM pages ORDER BY document, page"):
                rows.setdefault(document, []).append(
                    (page, rescan, service, text or ""))
        finally:
            connection.close()
    documents: list[dict] = []
    for document, page_rows in sorted(rows.items()):
        page_rows.sort(key=lambda row: row[0])
        page_count = len(page_rows)
        rescan = sum(1 for row in page_rows if row[1])
        service = sum(1 for row in page_rows if row[2])
        head_text = "\n".join(text for page, _r, _s, text
                              in page_rows[:FIRST_PAGES])
        parsed = parse_name(document)
        doc_type = classify_document(document, head_text)
        serials_text = find_serials(head_text)
        if parsed["serials_in_name"]:
            applicability_source = "name"
        elif serials_text or applicability_contexts(head_text):
            applicability_source = "text"
        else:
            applicability_source = "none"
        documents.append({
            "document": document,
            "pages": page_count,
            "needs_rescan": rescan,
            "is_service": service,
            "type": doc_type,
            "revision_no": parsed["revision_no"],
            "revision_date": parsed["revision_date"],
            "year": parsed["year"],
            "kit_id": parsed["kit_id"],
            "book_or_part": parsed["book_or_part"],
            "serials_in_name": parsed["serials_in_name"],
            "serials_in_text": serials_text,
            "applicability_source": applicability_source,
            "applicability_contexts": applicability_contexts(head_text),
            "other_types": collect_mentions(
                [(page, text) for page, _r, _s, text in page_rows]),
        })
    by_type: dict[str, int] = {}
    for doc in documents:
        by_type[doc["type"]] = by_type.get(doc["type"], 0) + 1
    by_source: dict[str, int] = {"name": 0, "text": 0, "none": 0}
    for doc in documents:
        by_source[doc["applicability_source"]] += 1
    return {
        "documents": documents,
        "total": len(documents),
        "by_type": by_type,
        "by_source": by_source,
        "manual_review": [doc["document"] for doc in documents
                          if needs_manual_review(doc)],
        "other_type_totals": {
            type_name: sum(1 for doc in documents
                           if type_name in doc["other_types"])
            for type_name in OTHER_TYPES},
    }


def needs_manual_review(doc: dict) -> bool:
    """Кто реально ждёт человека (трактовка владельца Ф0.3а, 02.11.2026).

    applicability_source = none у документа слоя base означает
    «применим ко всем бортам типа», а НЕ пробел в данных. Ручное
    решение требуется только если слой не определяется однозначно
    (тип «прочее») или это дополнение без указания бортов.
    """
    if doc["type"] == "прочее":
        return True
    if doc["type"] == "дополнение" and not doc["serials_in_name"]:
        return True
    return False


# --- Отчёты -------------------------------------------------------------------


def serials_str(doc: dict) -> str:
    """Борта документа: имя + текст, через запятую; «—» если нет."""
    serials = sorted(set(doc["serials_in_name"]) | set(doc["serials_in_text"]))
    return ", ".join(serials) if serials else "—"


def source_str(doc: dict) -> str:
    """Источник применимости по-русски: имя/текст/нет."""
    return {"name": "имя", "text": "текст", "none": "нет"}[
        doc["applicability_source"]]


def other_types_str(doc: dict) -> str:
    """Краткая строка «тип: N л.» для таблицы; «—» если нет."""
    parts = [f"{type_name}: {data['pages']} л."
             for type_name, data in doc["other_types"].items()]
    return "; ".join(parts) if parts else "—"


def revision_str(doc: dict) -> str:
    """Редакция «изм. N от DD.MM.YYYY» / «файл DD.MM.YYYY» / «—»."""
    if doc["revision_date"]:
        prefix = (f"изм. {doc['revision_no']} от "
                  if doc["revision_no"] is not None else "от ")
        return prefix + doc["revision_date"]
    return f"файл/изд. {doc['year']}" if doc["year"] else "—"


def render_main_md(data: dict) -> str:
    """reports/applicability_audit.md: таблица 49 строк + сводка + список."""
    lines = [
        "# Ф0.3а: инвентаризация применимости документов",
        "",
        f"Прогон: read-only на копии graph.db, "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
        f"Документов: {data['total']}.",
        "",
        "## Сводка",
        "",
        "| показатель | значение |",
        "|---|---|",
    ]
    for type_name, count in sorted(data["by_type"].items(),
                                   key=lambda item: -item[1]):
        lines.append(f"| документов типа «{type_name}» | {count} |")
    lines += [
        f"| применимость в имени | {data['by_source']['name']} |",
        f"| применимость только в тексте | {data['by_source']['text']} |",
        f"| не найдена — базовые: «все борта типа» (трактовка "
        f"владельца), ручное решение: {len(data['manual_review'])} | "
        f"{data['by_source']['none']} |",
    ]
    lines += ["", "Другие типы ВС (документов с упоминанием):"]
    for type_name, count in data["other_type_totals"].items():
        lines.append(f"- {type_name}: {count} документов")
    lines += [
        "",
        "## Таблица по всем документам",
        "",
        "| документ | листов | тип | редакция | год | применимость | "
        "борта | другие ВС |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for doc in data["documents"]:
        lines.append(
            f"| {doc['document']} | {doc['pages']} | {doc['type']} | "
            f"{revision_str(doc)} | {doc['year'] or '—'} | "
            f"{source_str(doc)} | {serials_str(doc)} | "
            f"{other_types_str(doc)} |")
    lines += ["", "## Документы для ручного решения", ""]
    lines.append("Трактовка владельца (02.11.2026): applicability_source = "
                 "none у документа слоя base означает «применим ко всем "
                 "бортам типа», а не пробел в данных. Ручное решение — "
                 "только неоднозначный слой («прочее») и дополнения без "
                 "указания бортов:")
    if data["manual_review"]:
        lines += [f"- {name}" for name in data["manual_review"]]
    else:
        lines.append("- Таких документов нет.")
    lines += ["", "## Контексты применимости и чужих типов", ""]
    for doc in data["documents"]:
        details: list[str] = []
        if doc["applicability_contexts"]:
            details += [f"- применимость: «{context}»" for context
                        in doc["applicability_contexts"]]
        for type_name, mention in doc["other_types"].items():
            details += [f"- {type_name}: {context}"
                        for context in mention["contexts"]]
        if details:
            lines.append(f"### {doc['document']}")
            lines += details
            lines.append("")
    return "\n".join(lines)


def render_docs_md(data: dict) -> str:
    """Раздел docs/: сводка, ручные решения, предложение схемы."""
    lines = [
        "# Ф0.3а: применимость документов — сводка и предложение схемы",
        "",
        f"Дата: {datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
        f"Источник: reports/applicability_audit.md (вне git), скрипт "
        f"tools/applicability_audit.py (read-only на копии БД).",
        "",
        "## Факты",
        "",
        f"- Документов: {data['total']}; по типам: "
        + "; ".join(f"«{t}» — {n}" for t, n in sorted(
            data["by_type"].items(), key=lambda item: -item[1])) + ".",
        f"- Применимость: в имени — {data['by_source']['name']}, "
        f"только в тексте — {data['by_source']['text']}, не найдена — "
        f"{data['by_source']['none']}; по трактовке владельца none у "
        f"слоя base означает «применим ко всем бортам типа», ручного "
        f"решения требуют {len(data['manual_review'])} документов "
        f"(неоднозначный слой и дополнения без бортов).",
        f"- Дополнений (слой supplement): {data['by_type'].get('дополнение', 0)}"
        f", у каждого борта перечислены прямо в имени («для вертолетов "
        f"22810 228811 24543»).",
        "- Другие типы ВС по всему тексту: "
        + "; ".join(f"{t} — {n} документов" for t, n
                    in data["other_type_totals"].items()) + ".",
        "- Формат бортовых номеров не однороден: кроме «2xxxxx» в штампах "
        "встречаются номера вне формата (№96555 в РЭ.РО Д-1263) — поле "
        "applicability_serials требует ручного подтверждения, автомат "
        "ловит только «2xxxxx» и «2X-XXX» (split с пробелом отсечён как "
        "ложный «стр. 20 207»).",
        "",
        "## Документы для ручного решения",
        "",
    ]
    if data["manual_review"]:
        lines += [f"- {name}" for name in data["manual_review"]]
    else:
        lines.append("- Таких нет.")
    lines += [
        "",
        "## Предложение схемы (не реализация)",
        "",
        "Поля уровня документа (таблица-кандидат documents_applicability,",
        "миграция отдельно по §3.7 .clinerules):",
        "",
        "| поле | тип | обоснование от фактов |",
        "|---|---|---|",
        "| family | TEXT | РЭ/РТЭ/РО Ми-8МТВ-1 существует в трёх нумерациях "
        "(кн. 6 и кн. 7 дублируются «изм. от» и «файл от») — семейство "
        "склеивает издания одной книги |",
        "| layer | TEXT (base/supplement/change) | в базе "
        f"{data['by_type'].get('дополнение', 0)} дополнений к РТЭ/РО и "
        "базовые руководства; слой нельзя вывести из типа надёжно "
        "(«Дополнение … СДК-8» — supplement к двум документам сразу) |",
        "| target | TEXT (документ-родитель или агрегат) | «Доп. к РТЭ "
        "Д-1594» ссылается на комплект Д-1594, а не на книгу РТЭ — связь "
        "дополнение→база нужна для отбрасывания неприменимых слоёв |",
        "| applicability_serials | TEXT (JSON-список) | борта «22810 228811 "
        "24543» встречаются в именах 17 документов; «для вертолета 25555» — "
        "у РЭ.РО Д-1263/Д-1411 |",
        "| applicability_source | TEXT (name/text/none) | администратор при "
        "загрузке нового типа должен видеть, что система распознала сама, "
        f"а что требует человека ({data['by_source']['none']} документов "
        "сейчас) — решение владельца Ф0.3а |",
        "| revision_no / revision_date | INTEGER/TEXT | «изм. 63 от "
        "04.04.2025» и «файл от 19.06.2023» — две формы ревизии в именах; "
        "нужны для «что новее» при нескольких изданиях одного семейства |",
        "| supersedes | TEXT | кн. 7 существует в двух редакциях (изм. от "
        "24.08.2023 и файл от 24.08.2023) — без поля не отличить "
        "действующую редакцию от снятой |",
        "",
        "Поля уровня листа: существующий `pages.aircraft_type` остаётся "
        "меткой; слой и применимость наследуются от документа, листового "
        "переопределения в фактах базы не обнаружено (проверка — при "
        "реализации, не сейчас).",
        "",
        "Ни одно поле этой схемы не внедряется этой карточкой: аудит "
        "read-only, миграция — отдельная задача по §3.7.",
    ]
    return "\n".join(lines)


def print_summary(data: dict) -> None:
    """Сводка в консоль (приёмка карточки)."""
    print(f"Ф0.3а: документов {data['total']}")
    for type_name, count in sorted(data["by_type"].items(),
                                   key=lambda item: -item[1]):
        print(f"  «{type_name}»: {count}")
    print(f"  применимость: имя {data['by_source']['name']}, "
          f"текст {data['by_source']['text']}, "
          f"нет {data['by_source']['none']}")
    for type_name, count in data["other_type_totals"].items():
        print(f"  чужой тип {type_name}: {count} документов")
    print(f"  ручное решение: {len(data['manual_review'])} документов "
          f"({', '.join(data['manual_review']) or '—'})")


def main(argv: list[str] | None = None) -> int:
    """Аудит целиком на копии БД; json + md + docs-раздел."""
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(argv[0]) if argv else DEFAULT_STORE
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    data = audit(source)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    JSON_REPORT.parent.mkdir(parents=True, exist_ok=True)
    JSON_REPORT.write_text(json.dumps(
        {"meta": {"generated_at": generated, "store": str(source.resolve()),
                  "first_pages": FIRST_PAGES}, **data},
        ensure_ascii=False, indent=2), encoding="utf-8")
    MD_REPORT.write_text(render_main_md(data), encoding="utf-8")
    DOCS_REPORT.write_text(render_docs_md(data), encoding="utf-8")
    print_summary(data)
    print(f"Отчёты: {JSON_REPORT}, {MD_REPORT}, {DOCS_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
