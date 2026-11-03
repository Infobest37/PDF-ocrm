# -*- coding: utf-8 -*-
"""Ф2.5: карточка технологической карты — короткий структурированный блок.

Модуль без побочных эффектов: только читает текст листа и собирает
структуру для отображения. Цитаты (Claim.text) и verify не затрагивает.

Поля карточки:

- ``number`` — номер карты в канонической форме («035.10.00e», с индексом
  «033.10.00а1» — Ф2.8);
- ``title``  — наименование работы: текст после маркера «Наименование
  работы» (регистр и ё/е не важны, двоеточие опционально) до следующего
  раздела листа; переносы строк OCR схлопываются — содержимое дословное;
  маркера нет — пустая строка, не выдумывается;
- ``labor``  — трудоёмкость: строка «Трудоемкость/Трудоёмкость», из неё
  «N,N чел.ч»; маркера или числа нет — пустая строка;
- ``sheets`` — листы карты: документ, лист, роль-подпись
  («как выполняется» / «когда выполняется», Ф1.9б);
- ``applicability`` — пометка применимости (Ф2.8): карта найдена только
  в дополнении — «карта из дополнения Д-1697; применима к бортам …;
  для остальных бортов — базовая карта в …»; иначе пустая строка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pdfgraph import documents as documents_table
from pdfgraph.gates import _card_suffix_in_text
from pdfgraph.query import (ROLE_MANUAL, _techcard_matches, document_role,
                            parse_techcard_query)
from pdfgraph.store import Store

#: Маркер наименования работы: регистр не важен, ё/е эквивалентны,
#: двоеточие после маркера опционально (OCR его теряет и добавляет).
_TITLE_MARKER_RE = re.compile(r"наименование\s+работы\s*:?\s*", re.IGNORECASE)
#: Маркеры трудоёмкости: OCR даёт «Трудоемкость» через «е» и с запятой.
_LABOR_MARKER_RE = re.compile(r"трудое?мкость\s*:?[,\s]*", re.IGNORECASE)
#: Начала следующих разделов листа — на них срез заголовка заканчивается.
_SECTION_MARKS = (
    "трудое?мкость",
    "содержание операции",
    "контрольно-проверочн",
    "работы, выполняемые",
    "работы, выполняемые при отклонениях",
    "примечание\\.",
)
_SECTION_MARK_RE = re.compile(
    "|".join(_SECTION_MARKS), re.IGNORECASE)
#: Трудоёмкость: число + «чел.ч»/«чел.-ч»/«чел ч» (точка/дефис от OCR).
_LABOR_VALUE_RE = re.compile(r"\d+[.,]\d+\s*чел[.\s-]*ч", re.IGNORECASE)
#: Подпись роли листа в карточке: короткая форма (без «карта,») —
#: роли в выдаче описывает ROLE_LABELS (Ф1.9б).
_SHEET_ROLES = {
    "manual": "как выполняется",
    "reglament": "когда выполняется",
}


@dataclass
class CardSheet:
    """Лист карты в карточке: документ, лист, подпись роли."""

    document: str
    page: int
    role: str


@dataclass
class TechCard:
    """Карточка карты: номер, наименование, трудоёмкость, листы."""

    number: str
    title: str = ""
    labor: str = ""
    sheets: list[CardSheet] = field(default_factory=list)
    #: Ф2.8: пометка применимости для карты, найденной только в
    #: дополнении; пустая строка — обычная карта основной книги.
    applicability: str = ""


def _segment_after_marker(text: str, marker: re.Pattern[str]) -> str:
    """Текст после маркера до следующего раздела листа; переносы схлопнуты.

    Ищет маркер в тексте с ё→е (регистр не важен); возвращает часть между
    маркером и ближайшим началом следующего раздела. Маркера нет — ''.
    """
    flat = text.replace("ё", "е").replace("Ё", "Е")
    match = marker.search(flat)
    if match is None:
        return ""
    start = match.end()
    tail = flat[start:]
    section = _SECTION_MARK_RE.search(tail)
    segment = tail if section is None else tail[:section.start()]
    return " ".join(segment.split())


def extract_title(page_text: str) -> str:
    """Наименование работы с листа; маркера нет — пусто."""
    return _segment_after_marker(page_text, _TITLE_MARKER_RE)


def extract_labor(page_text: str) -> str:
    """Трудоёмкость («0,01 чел.ч») с листа; маркера/числа нет — пусто."""
    segment = _segment_after_marker(page_text, _LABOR_MARKER_RE)
    match = _LABOR_VALUE_RE.search(segment)
    if match is None:
        return ""
    return match.group(0).strip().rstrip(".")


def _base_card_book(store: Store, base: str, letter: str) -> str:
    """Краткое имя руководства с базовой картой (без индекса), Ф2.8.

    Первый по порядку документ-руководство, где встречается карта базы
    ``base`` с буквой ``letter`` без индекса; имя без «.pdf» и ревизии в
    скобках («РТЭ Ми-8МТВ-1 кн. 7»). Базовой карты в корпусе нет — ''.
    """
    for document, _page, _code1, _code2, suffix \
            in _techcard_matches(store, base, None):
        if suffix != letter or document_role(store, document) != ROLE_MANUAL:
            continue
        name = document[:-4] if document.lower().endswith(".pdf") else document
        return re.sub(r"\s*\([^)]*\)", "", name).strip()
    return ""


def _applicability_note(store: Store, base: str, suffix: str,
                        sheets: list[CardSheet]) -> str:
    """Пометка применимости: карта найдена только в дополнении (Ф2.8, п. 4).

    Хотя бы один лист карты из основной книги — '' (карта общая). Все
    листы из дополнений — строка по таблице documents: «карта из
    дополнения <target>; применима к бортам <serials>; для остальных
    бортов — базовая карта в <книга>». Без target/бортов в documents
    (scope equipment до ручной правки) пометка не строится — fail-closed.
    """
    if not sheets:
        return ""
    rows = []
    for document in sorted({sheet.document for sheet in sheets}):
        row = documents_table.get_row(store, document)
        if row is None or row.get("layer") != "supplement":
            return ""
        rows.append(row)
    targets = sorted({row["target"] for row in rows if row.get("target")})
    serials = sorted({part.strip() for row in rows
                      for part in (row.get("applicability_serials")
                                   or "").split(",") if part.strip()})
    if not targets or not serials:
        return ""
    note = (f"карта из дополнения {', '.join(targets)}; "
            f"применима к бортам {', '.join(serials)}")
    book = _base_card_book(store, base, suffix[0])
    if book:
        note += f"; для остальных бортов — базовая карта в {book}"
    return note


def build_card(number: str, store: Store, fragments: list,
               applicability: str = "") -> TechCard:
    """Карточка одного номера карты из techcard-фрагментов выдачи.

    ``fragments`` — фрагменты (kind="techcard"), чей лист относится к этой
    карте (для сводного листа регламента — да, несёт несколько карт);
    порядок фрагментов сохраняется (руководства выше регламента, Ф1.9б).
    ``applicability`` — пометка применимости (Ф2.8), вычисляет
    :func:`collect_techcards`.
    """
    sheets = [
        CardSheet(document=fragment.document, page=fragment.page,
                  role=_SHEET_ROLES.get(fragment.role, fragment.role))
        for fragment in fragments
    ]
    # Название и трудоёмкость — с первого листа-руководства, где они есть;
    # сводный лист регламента маркеров не содержит (Ф2.5: пусто, не выдумывать).
    title = ""
    labor = ""
    for fragment in fragments:
        if fragment.role != "manual":
            continue
        text = store.page_text(fragment.document, fragment.page)
        title = title or extract_title(text)
        labor = labor or extract_labor(text)
        if title and labor:
            break
    return TechCard(number=number, title=title, labor=labor, sheets=sheets,
                    applicability=applicability)


def collect_techcards(store: Store, question: str, fragments: list
                      ) -> list[TechCard]:
    """Карточки по запрошенным суффиксам, в порядке запроса (Ф2.5).

    Пустой список — вопрос не про карту с суффиксом или в выдаче нет
    карточных фрагментов (раздел-запрос идёт прежним путём).
    """
    parsed = parse_techcard_query(question)
    if parsed is None or not parsed[1] or not fragments:
        return []
    base, suffixes = parsed
    tech_fragments = [fragment for fragment in fragments
                      if fragment.kind == "techcard"]
    cards: list[TechCard] = []
    for suffix in suffixes:
        number = f"{base}{suffix}"
        # Ф2.8: точное совпадение суффикса — базовая карта не собирает
        # листы карты-дополнения с индексом и наоборот.
        sheets = [fragment for fragment in tech_fragments
                  if _card_suffix_in_text(
                      (store.page_text(fragment.document, fragment.page)
                       or "").lower(), number)]
        cards.append(build_card(
            number, store, sheets,
            applicability=_applicability_note(store, base, suffix, sheets)))
    return cards
