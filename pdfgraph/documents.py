# -*- coding: utf-8 -*-
"""Ф0.3б: таблица documents — слои и применимость документов.

Данные уровня документа поверх ``pages``: слой (base / supplement /
change / unknown), цель дополнения, применимость (scope all / serials /
equipment + список бортов), ревизия, supersedes. Поиск и ранжирование
этот модуль не трогает; таблица создана миграцией v6
(``pdfgraph/store.py``, SCHEMA_VERSION = 6).

Правила разбора имён — проверенные замером Ф0.3а
(``tools/applicability_audit.py``, прогон 02.11.2026); скопированы с
пометкой, а не импортированы: pdfgraph не зависит от tools.

Правило наследования (карточка Ф0.3б, п. 3 — :func:`scope_allows`):

- ``all`` — документ применим ко всем бортам типа (владелец, 02.11.2026:
  applicability_source = none у слоя base означает «все борта типа»,
  а не пробел в данных);
- ``serials`` — только к перечисленным бортам
  (``applicability_serials``, запятая-разделитель);
- ``equipment`` — к бортам с указанной опцией; список бортов
  проставляет человек, до этого :func:`scope_allows` возвращает False
  (fail-closed).

Идемпотентность: :func:`fill_missing` вставляет только документы,
отсутствующие в ``documents``; существующие строки (в т.ч. ручные
правки docs-set) не перезаписывает.
"""

from __future__ import annotations

import re
from typing import Any

from pdfgraph.store import Store

#: Семейство по умолчанию: в базе пока один тип ВС (карточка Ф0.3б).
FAMILY_DEFAULT = "Ми-8"
#: Сколько первых листов читается на классификацию (правила Ф0.3а).
FIRST_PAGES = 3

#: Решение владельца Ф0.3б (02.11.2026) по равнодатной паре кн. 7:
#: «изм. от» — редакция документа, «файл от» — только дата файла;
#: действующая редакция заменяет файловую. Согласовано с
#: document_aliases (канон дедупликации 29.10.2026 — та же изм-редакция).
OWNER_SUPersedes: dict[str, dict[str, str]] = {
    "РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf": {
        "supersedes": "Руководство по технической эксплуатации "
                      "РТЭ Ми-8МТВ-1 кн. 7 (файл от 24.08.2023).pdf",
        "notes": "решение владельца Ф0.3б (02.11.2026): действующая "
                 "редакция — «изм. от»; «файл от» — дата файла",
    },
}

# --- Правила Ф0.3а (копия из tools/applicability_audit.py) ------------------

_REVISION_RES = (
    re.compile(r"изм\.\s*(\d+)\s*от\s*(\d{2}\.\d{2}\.\d{4})"),
    re.compile(r"изм\.\s*от\s*(\d{2}\.\d{2}\.\d{4})"),
    re.compile(r"файл\s*(?:от)?\s*(\d{2}\.\d{2}\.\d{4})"),
    re.compile(r"изд\.\s*(\d{2}\.\d{2}\.\d{4})"),
)
_KIT_RE = re.compile(r"Д-(\d{3,4})")
_BOOK_RES = (re.compile(r"(?:кн|к)\.\s*(\d+)"),
             re.compile(r"ч\.\s*(\d+)"),
             re.compile(r"[Чч]асть\s+(\d+)"))
_SERIAL_RE = re.compile(r"(?<![0-9-])(?:2\d-\d{3}|2\d\d{3,4})(?![0-9-])")
_TYPE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("дополнение", ("доп. ", "доп.", "дополнение")),
    ("каталог дефектов", ("каталог дефектов",)),
    ("каталог деталей", ("каталок деталей", "каталог деталей", "кдэ",
                         "каталог ")),
    ("регламент ТО", ("регламент технического обслуживания",)),
    ("РТЭ", ("ртэ", "руководство по технической эксплуатации")),
    ("РЭ", ("рэ.ро", "рэ и то", " рэ", "рэ")),
    ("изменение", ("изменение",)),
)
#: Тип документа → слой documents (карточка Ф0.3б).
_LAYER_BY_TYPE = {"дополнение": "supplement", "изменение": "change",
                  "прочее": "unknown"}


def parse_name(name: str) -> dict[str, Any]:
    """Ревизия, дата (ISO), год, комплект, книга/часть, борта из имени.

    Копия правил Ф0.3а; ``revision_date`` конвертируется в ISO.
    """
    revision_no = None
    revision_date = ""
    for pattern in _REVISION_RES:
        found = pattern.search(name)
        if found:
            groups = found.groups()
            if pattern is _REVISION_RES[0]:
                revision_no = int(groups[0])
                revision_date = groups[1]
            else:
                revision_date = groups[-1]
            break
    book = None
    for pattern in _BOOK_RES:
        found = pattern.search(name)
        if found:
            book = int(found.group(1))
            break
    kit = _KIT_RE.search(name)
    iso_date = ""
    if revision_date:
        day, month, year = revision_date.split(".")
        iso_date = f"{year}-{month}-{day}"
    return {
        "revision_no": revision_no,
        "revision_date": iso_date,
        "kit_id": f"Д-{kit.group(1)}" if kit else "",
        "book_or_part": book,
        "serials_in_name": sorted(set(_SERIAL_RE.findall(name))),
    }


def classify_document(name: str, first_pages_text: str) -> str:
    """Тип документа по имени; при неоднозначности — по первым листам.

    Маркер «изменение» применяется только к имени: «Перечень изменений»
    есть почти в любом руководстве (урок прогона Ф0.3а).
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


# --- Вычисление строки documents --------------------------------------------


def _pages_head(store: Store, document: str) -> tuple[str, str]:
    """Текст первых листов и метка типа (pages.aircraft_type)."""
    rows = store._connection.execute(
        "SELECT aircraft_type, text FROM pages WHERE document = ? "
        "ORDER BY page LIMIT ?", (document, FIRST_PAGES)).fetchall()
    texts = [row[1] or "" for row in rows]
    aircraft = next((row[0] for row in rows if (row[0] or "").strip()), "")
    return "\n".join(texts), aircraft


def compute_row(store: Store, document: str) -> dict[str, Any]:
    """Поля documents для одного документа по правилам Ф0.3а.

    ``applicability_source``: name — борта в имени; text — борта в
    тексте первых листов; manual — решение владельца (none у base
    означает «все борта типа», трактовка 02.11.2026).
    """
    head, aircraft = _pages_head(store, document)
    parsed = parse_name(document)
    doc_type = classify_document(document, head)
    layer = _LAYER_BY_TYPE.get(doc_type, "base")
    serials_name = parsed["serials_in_name"]
    serials_text = sorted(set(_SERIAL_RE.findall(head)))
    if serials_name:
        source, serials = "name", serials_name
    elif serials_text:
        source, serials = "text", serials_text
    else:
        source, serials = "manual", []
    if layer == "supplement":
        scope = "serials" if serials else "equipment"
    else:
        scope = "all"
    needs_human = layer == "unknown" or scope == "equipment"
    target = parsed["kit_id"] if layer == "supplement" else ""
    return {
        "document": document,
        "family": FAMILY_DEFAULT,
        "aircraft_type": aircraft,
        "layer": layer,
        "target": target,
        "applicability_serials": ", ".join(serials),
        "applicability_scope": scope,
        "applicability_source": source,
        "revision_no": parsed["revision_no"],
        "revision_date": parsed["revision_date"],
        "supersedes": "",
        "review_status": "needs_human" if needs_human else "ok",
        "notes": "",
    }


# --- Заполнение ---------------------------------------------------------------


def _insert_row(store: Store, row: dict[str, Any]) -> None:
    """Вставить строку documents (только для отсутствующего документа)."""
    store._connection.execute(
        "INSERT INTO documents (document, family, aircraft_type, layer, "
        "target, applicability_serials, applicability_scope, "
        "applicability_source, revision_no, revision_date, supersedes, "
        "review_status, notes) VALUES (:document, :family, :aircraft_type, "
        ":layer, :target, :applicability_serials, :applicability_scope, "
        ":applicability_source, :revision_no, :revision_date, :supersedes, "
        ":review_status, :notes)", row)


def _apply_owner_supersedes(store: Store, document: str) -> None:
    """Решение владельца Ф0.3б для кн. 7 (равнодатная пара)."""
    override = OWNER_SUPersedes.get(document)
    if not override:
        return
    store._connection.execute(
        "UPDATE documents SET supersedes = ?, applicability_source = "
        "'manual', review_status = 'ok', notes = ? WHERE document = ?",
        (override["supersedes"], override["notes"], document))
    replaced = override["supersedes"]
    store._connection.execute(
        "UPDATE documents SET notes = ? WHERE document = ? "
        "AND notes = ''",
        (f"заменена решением владельца Ф0.3б (02.11.2026) документом "
         f"{document}", replaced))


def _apply_auto_supersedes(store: Store) -> int:
    """Авто-supersedes для пар базовых книг с разными датами (кн. 6).

    Группа — базовые документы с одинаковой книгой/частью; если в группе
    две строки с непустыми и разными ревизионными датами, более новая
    получает supersedes = имя более старой. Ручные строки (source=manual)
    и уже заполненные supersedes не трогаются.
    """
    rows = store._connection.execute(
        "SELECT document, book_key, revision_date FROM ("
        "  SELECT document, revision_date,"
        "   CASE WHEN document LIKE '%кн. %' THEN"
        "     CAST(SUBSTR(document, INSTR(document, 'кн. ') + 4) AS INTEGER)"
        "    WHEN document LIKE '%часть %' THEN"
        "     CAST(SUBSTR(document, INSTR(LOWER(document), 'часть ') + 6) "
        "          AS INTEGER)"
        "    ELSE NULL END AS book_key"
        "  FROM documents WHERE layer = 'base' AND revision_date <> '' "
        "  AND supersedes = '')"
    ).fetchall()
    # Группа = (книга, КЛАСС документа): «РТЭ кн. 1» и «Каталог дефектов
    # кн. 1» — разные руководства с совпадающим номером книги; без класса
    # авто-правило склеивало их (урок прогона Ф0.3б на рабочей базе).
    groups: dict[tuple[int, str], list[tuple[str, str]]] = {}
    for document, book_key, date in rows:
        if book_key is None:
            continue
        doc_class = classify_document(document, "")
        groups.setdefault((int(book_key), doc_class),
                          []).append((document, date))
    changed = 0
    for members in groups.values():
        if len(members) != 2:
            continue
        members.sort(key=lambda item: item[1])
        older, newer = members[0], members[1]
        if older[1] == newer[1]:
            continue  # равные даты автоматически не решаются (кн. 7)
        store._connection.execute(
            "UPDATE documents SET supersedes = ? WHERE document = ?",
            (older[0], newer[0]))
        changed += 1
    return changed


def fill_missing(store: Store) -> dict[str, int]:
    """Идемпотентное заполнение documents недостающими документами.

    Существующие строки (в т.ч. ручные правки docs-set) не
    перезаписываются; решения владельца (кн. 7) применяются к своим
    документам. Возвращает счётчики для CLI и тестов.
    """
    known = {row[0] for row in store._connection.execute(
        "SELECT document FROM documents")}
    pages_docs = [row[0] for row in store._connection.execute(
        "SELECT DISTINCT document FROM pages ORDER BY document")]
    fresh = [document for document in pages_docs if document not in known]
    for document in fresh:
        _insert_row(store, compute_row(store, document))
    owner_applied = 0
    for document in fresh:
        if document in OWNER_SUPersedes:
            _apply_owner_supersedes(store, document)
            owner_applied += 1
    auto_supersedes = _apply_auto_supersedes(store)
    store._connection.commit()
    return {"filled": len(fresh), "owner_supersedes": owner_applied,
            "auto_supersedes": auto_supersedes,
            "pages_documents": len(pages_docs)}


# --- Правки и правило наследования -------------------------------------------


def scope_allows(row: dict[str, Any] | None, serial: str) -> bool:
    """Правило наследования применимости (карточка Ф0.3б, п. 3).

    all — любой борт типа; serials — только перечисленные;
    equipment — False до ручного заполнения списка бортов
    (fail-closed). Строка без данных (None) — False.
    """
    if not row:
        return False
    scope = row.get("applicability_scope")
    if scope == "all":
        return True
    if scope == "serials":
        listed = {part.strip() for part in
                  (row.get("applicability_serials") or "").split(",")}
        return serial.strip() in listed
    return False  # equipment и неизвестные scope — только человек


def set_fields(store: Store, document: str, layer: str | None = None,
               serials: str | None = None, scope: str | None = None,
               target: str | None = None, notes: str | None = None
               ) -> dict[str, Any]:
    """Ручная правка (docs-set): поля + source=manual + status=ok."""
    if store._connection.execute(
            "SELECT 1 FROM documents WHERE document = ?",
            (document,)).fetchone() is None:
        _insert_row(store, compute_row(store, document))
    updates: dict[str, Any] = {}
    if layer is not None:
        updates["layer"] = layer
    if serials is not None:
        updates["applicability_serials"] = serials
    if scope is not None:
        updates["applicability_scope"] = scope
    if target is not None:
        updates["target"] = target
    if notes is not None:
        updates["notes"] = notes
    if updates:
        assigns = ", ".join(f"{column} = :{column}"
                            for column in updates)
        updates["document"] = document
        store._connection.execute(
            f"UPDATE documents SET {assigns}, applicability_source = "
            f"'manual', review_status = 'ok' WHERE document = :document",
            updates)
        store._connection.commit()
    return get_row(store, document)


def unset_document(store: Store, document: str) -> dict[str, Any]:
    """Откат правки (docs-unset): строка пересчитывается авто-правилами."""
    store._connection.execute(
        "DELETE FROM documents WHERE document = ?", (document,))
    _insert_row(store, compute_row(store, document))
    _apply_owner_supersedes(store, document)
    _apply_auto_supersedes(store)
    store._connection.commit()
    return get_row(store, document)


def get_row(store: Store, document: str) -> dict[str, Any] | None:
    """Строка documents как словарь; None, если документа нет."""
    import sqlite3

    cursor = store._connection.execute(
        "SELECT * FROM documents WHERE document = ?", (document,))
    cursor.row_factory = sqlite3.Row
    found = cursor.fetchone()
    return dict(found) if found else None
