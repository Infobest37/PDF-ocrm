# -*- coding: utf-8 -*-
"""Ф2.5: карточка технологической карты — короткий структурированный блок.

Модуль без побочных эффектов: только читает текст листа и собирает
структуру для отображения. Цитаты (Claim.text) и verify не затрагивает.

Поля карточки:

- ``number`` — номер карты в канонической форме («035.10.00e»);
- ``title``  — наименование работы: текст после маркера «Наименование
  работы» (регистр и ё/е не важны, двоеточие опционально) до следующего
  раздела листа; переносы строк OCR схлопываются — содержимое дословное;
  маркера нет — пустая строка, не выдумывается;
- ``labor``  — трудоёмкость: строка «Трудоемкость/Трудоёмкость», из неё
  «N,N чел.ч»; маркера или числа нет — пустая строка;
- ``sheets`` — листы карты: документ, лист, роль-подпись
  («как выполняется» / «когда выполняется», Ф1.9б).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pdfgraph.gates import _designator_in_text
from pdfgraph.query import parse_techcard_query
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


def build_card(number: str, store: Store, fragments: list) -> TechCard:
    """Карточка одного номера карты из techcard-фрагментов выдачи.

    ``fragments`` — фрагменты (kind="techcard"), чей лист относится к этой
    карте (для сводного листа регламента — да, несёт несколько карт);
    порядок фрагментов сохраняется (руководства выше регламента, Ф1.9б).
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
    return TechCard(number=number, title=title, labor=labor, sheets=sheets)


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
        sheets = [fragment for fragment in tech_fragments
                  if _designator_in_text(
                      store.page_text(fragment.document, fragment.page)
                      or "", number)]
        cards.append(build_card(number, store, sheets))
    return cards
