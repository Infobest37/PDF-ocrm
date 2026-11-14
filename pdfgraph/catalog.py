# -*- coding: utf-8 -*-
"""Д1б: поиск по каталогу деталей — обозначение ↔ наименование.

Каталог в корпусе — это лист типа ``catalog`` (Д1а) с таблицей
«Номер рисунка / Номер позиции / Обозначение / Наименование сборочной
единицы / Колич.». OCR отдаёт её построчно, по ячейке на строку:

    8АТ.7714.150-01
    .Установка блока БСПИ-4-2
    (см. 142.10.00, рис.3)
    1

Модуль разбирает такие листы в пары «обозначение — наименование» и
ищет по ним в обе стороны: «что за деталь 8АТ.7714.150-01» (обозначение
→ имя) и «какой номер у крышки» (имя → обозначение).

Поиск идёт только по листам ``pages.page_kind = 'catalog'``; текст
листов не меняется, цитата — дословный срез ``pages.text``. Не найдено —
честная причина «в каталоге не нашёл»: выдумывать номер нельзя.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field

from pdfgraph.page_kind import _STANDARD_RE, is_designator
from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

logger = logging.getLogger(__name__)

#: Сколько строк наименования склеивать подряд (каталог переносит имя).
MAX_NAME_LINES = 3
#: Служебные слова шапки таблицы каталога — не наименование.
_HEADER_WORDS = frozenset({
    "номер", "рисунка", "позиции", "обозначение", "колич.", "колич",
    "наименование", "сборочной", "единицы", "сведения", "о",
    "взаимозаменяемости", "конструктивных", "изменениях",
})
#: Максимальная длина строки-обозначения (защита от абзаца).
MAX_DESIGNATOR_CHARS = 60


@dataclass(frozen=True)
class CatalogPair:
    """Пара каталога: обозначение и наименование с одного листа."""

    designator: str
    name: str


@dataclass
class CatalogLookup:
    """Итог поиска по каталогу.

    ``designator``/``name`` — что именно спросили (для отчёта и фразы
    ответа); ``pairs`` — найденные пары; ``snippet`` — дословный срез
    листа от строки обозначения; ``reason`` — причина, если не найдено.
    """

    question: str
    designator: str = ""
    name: str = ""
    pairs: list[CatalogPair] = field(default_factory=list)
    document: str = ""
    page: int = 0
    snippet: str = ""
    reason: str = ""

    @property
    def found(self) -> bool:
        """Нашлась ли хоть одна пара."""
        return bool(self.pairs)


def _is_designator_line(line: str) -> bool:
    """Строка-обозначение каталога (чертёжный номер или стандарт)."""
    token = line.strip().strip(".,;:")
    if not token or len(token) > MAX_DESIGNATOR_CHARS:
        return False
    if is_designator(token):
        return True
    return (_STANDARD_RE.search(token) is not None
            and re.search(r"\d", token) is not None)


def _is_header_line(line: str) -> bool:
    """Строка шапки таблицы каталога — не наименование."""
    lowered = line.strip().lower()
    if not lowered:
        return True
    if lowered in _HEADER_WORDS:
        return True
    return lowered.startswith(("сведения о", "взаимозаменяе"))


def catalog_pairs(text: str) -> list[CatalogPair]:
    """Разобрать текст одного листа каталога в пары «обозначение — имя».

    OCR каталога идёт по ячейке на строку: обозначение, затем 1–3 строки
    наименования (часто с ведущей точкой), затем «(см. …)» и количество.
    Наименование склеивается из подряд идущих строк до количества,
    ссылки, следующего обозначения или шапки таблицы.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    pairs: list[CatalogPair] = []
    index = 0
    while index < len(lines):
        if not _is_designator_line(lines[index]):
            index += 1
            continue
        designator = lines[index].strip().strip(".,;:")
        name_parts: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and len(name_parts) < MAX_NAME_LINES:
            candidate = lines[cursor].strip()
            if (not candidate or candidate.isdigit()
                    or candidate.startswith("(")
                    or _is_designator_line(candidate)
                    or _is_header_line(candidate)):
                break
            name_parts.append(candidate.lstrip(". ").strip())
            cursor += 1
        name = " ".join(part for part in name_parts if part).strip(" .")
        name = re.sub(r"\s+", " ", name)
        if name:
            pairs.append(CatalogPair(designator=designator, name=name))
        index = max(cursor, index + 1)
    return pairs


def _token_present(token: str, normalized_text: str) -> bool:
    """Есть ли слово вопроса в наименовании (простое усечение основы)."""
    if not token:
        return False
    if token in normalized_text:
        return True
    # «крышки» → «крыш»: каталог хранит именительный падеж.
    return len(token) >= 4 and token[:4] in normalized_text


def _match_pairs(pairs: list[CatalogPair], designator: str, name: str
                 ) -> list[CatalogPair]:
    """Пары, отвечающие вопросу: сначала точное обозначение, потом имя."""
    if designator:
        target = normalize_for_search(designator)
        exact = [pair for pair in pairs
                 if normalize_for_search(pair.designator) == target]
        if exact:
            return exact
        contained = [pair for pair in pairs
                     if target and target in normalize_for_search(pair.designator)]
        if contained:
            return contained
    if name:
        tokens = [token for token in normalize_for_search(name).split()
                  if len(token) >= 3]
        if tokens:
            return [pair for pair in pairs
                    if all(_token_present(token, normalize_for_search(pair.name))
                           for token in tokens)]
    return []


def _candidate_pages(store: Store, designator: str, name: str,
                     limit: int) -> list[tuple[str, int]]:
    """Листы-каталоги, где встречается обозначение или слова имени.

    Читается ``pages_fts.body`` (нормализованный поисковый текст) с
    фильтром ``page_kind = 'catalog'``; порядок — обозначение первым,
    затем слова имени. Ошибка SQLite — пустой список с записью в лог.
    """
    patterns: list[str] = []
    if designator:
        patterns.append(normalize_for_search(designator))
    if name:
        # Имя в вопросе может стоять в косвенном падеже («крышки»), а в
        # каталоге — в именительном («Крышка»): ищем по усечённой основе.
        for token in normalize_for_search(name).split():
            if len(token) >= 4:
                patterns.append(token[:4])
            elif len(token) >= 3:
                patterns.append(token)
    rows: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    try:
        for pattern in patterns[:4]:
            found = store._connection.execute(
                "SELECT pg.document, pg.page FROM pages_fts "
                "JOIN pages pg ON pg.document = pages_fts.document "
                "AND pg.page = CAST(pages_fts.page AS INTEGER) "
                "WHERE pg.page_kind = 'catalog' "
                "AND pages_fts.body LIKE ? LIMIT ?",
                (f"%{pattern}%", limit),
            ).fetchall()
            for document, page in found:
                key = (str(document), int(page))
                if key not in seen:
                    seen.add(key)
                    rows.append(key)
    except sqlite3.Error as error:
        logger.warning("catalog: поиск по каталогу недоступен: %s", error)
    return rows


def _pair_snippet(text: str, pair: CatalogPair) -> str:
    """Дословный срез листа от обозначения: до 4 строк подряд."""
    position = text.find(pair.designator)
    if position < 0:
        return pair.designator
    lines = text[position:].splitlines(keepends=True)[:4]
    return "".join(lines).strip()


def lookup_catalog(store: Store, question: str, limit: int = 20
                   ) -> CatalogLookup:
    """Найти деталь в каталоге по обозначению или наименованию.

    Обозначение берётся строгим разбором Д1б (:func:`extract_designator`
    в ``page_kind``), имя — из вопроса без служебных слов. Поиск идёт
    только по листам ``page_kind = 'catalog'``; пары извлекаются только
    из найденного листа. Не найдено — ``reason = "в каталоге не нашёл"``.
    """
    from pdfgraph.page_kind import extract_designator, extract_question_name

    designator = extract_designator(question) or ""
    name = extract_question_name(question)
    lookup = CatalogLookup(question=question, designator=designator,
                           name=name)
    if not designator and not name:
        lookup.reason = "в вопросе нет ни обозначения, ни наименования"
        return lookup
    for document, page in _candidate_pages(store, designator, name, limit):
        text = store.page_text(document, page) or ""
        if not text:
            continue
        matched = _match_pairs(catalog_pairs(text), designator, name)
        if not matched:
            continue
        lookup.pairs = matched
        lookup.document = document
        lookup.page = page
        lookup.snippet = _pair_snippet(text, matched[0])
        return lookup
    lookup.reason = "в каталоге не нашёл"
    return lookup


def catalog_fragment(store: Store, lookup: CatalogLookup) -> Fragment:
    """Фрагмент-цитата найденного листа каталога (дословный срез)."""
    text = store.page_text(lookup.document, lookup.page) or ""
    snippet = lookup.snippet or text[:200]
    position = text.find(snippet)
    if position < 0:
        position = 0
    row = store._connection.execute(
        "SELECT system_code, subsystem_code FROM pages "
        "WHERE document = ? AND page = ?",
        (lookup.document, lookup.page),
    ).fetchone()
    system_code = str(row[0] or "") if row is not None else ""
    subsystem_code = str(row[1] or "") if row is not None else ""
    return Fragment(
        document=lookup.document,
        page=lookup.page,
        system_code=system_code,
        subsystem_code=subsystem_code,
        snippet=snippet,
        char_start=position,
        char_end=position + len(snippet),
        score=1.0,
        kind="catalog",
    )


__all__ = [
    "CatalogPair",
    "CatalogLookup",
    "catalog_pairs",
    "lookup_catalog",
    "catalog_fragment",
]
