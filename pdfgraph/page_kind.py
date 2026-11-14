"""Д1а: тип листа по тексту шапки (без обращения к БД).

Классификатор сначала отрезает колонтитул книги — повторяющуюся шапку
(«Ми-8МТВ-1», «РУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ», «Стр. N»,
код раздела ``NNN.NN.NN``, «КАТАЛОГ ДЕТАЛЕЙ...», дату/изменение). Это
шапка книги, а не тип листа; без её отсечения маркеры типа уезжают за
окно анализа. Колонтитул ищется только в первых :data:`HEADER_CHARS`
символах, затем анализируются следующие :data:`HEADER_CHARS` символов:
маркер, стоящий сразу после длинной шапки, снова попадает в окно.
Однострочная книга работает так же — окно режется по символам, а не по
строкам.

Восемь содержательных типов (решение владельца, Д1а):

``purpose``
    описание назначения — «ОПИСАНИЕ И РАБОТА», «ОПИСАНИЕ РАБОТЫ»,
    «РАБОТА СИСТЕМЫ», «ПРИНЦИП РАБОТЫ», «ОБЩИЕ СВЕДЕНИЯ»,
    «НАЗНАЧЕНИЕ», «СОСТАВ», «УСТРОЙСТВО И РАБОТА».
``techcard``
    технологическая карта: бланк («ТЕХНОЛОГИЧЕСКАЯ КАРТА» + «№» или
    «Наименование работы» рядом) и продолжение карты без бланка
    («Содержание операции и технические требования», «Работы,
    выполняемые при отклонениях», «Наименование работы», «Контроль» в
    шапке таблицы карты). Ссылка на карту без номера — упоминание.
``faults``
    отыскание и устранение неисправностей: «ОТЫСКАНИЕ И УСТРАНЕНИЕ»,
    «НЕИСПРАВНОСТЬ», «ВОЗМОЖНАЯ ПРИЧИНА», «СПОСОБ УСТРАНЕНИЯ»,
    «Каталог дефектов», «ДЕФЕКТ».
``contents``
    содержание/оглавление/введение/перечень действующих страниц;
    «Содержание операции» технологической карты сюда не относится.
    «СОДЕРЖАНИЕ»/«ВВЕДЕНИЕ» в первых двух строках после колонтитула
    побеждают ``catalog``.
``table``
    таблица — заголовок «ТАБЛИЦА» в шапке.
``list``
    перечень — заголовок «ПЕРЕЧЕНЬ» (кроме перечня действующих страниц).
``scheme``
    схема/рисунок — «СХЕМА», «СХЕМЫ», «РИСУНОК» в шапке.
``title``
    титульный лист: «УТВЕРЖДЕН», «УТВЕРЖДАЮ», «Лист регистрации
    изменений» (включая «временных»), «Титульный лист» в начале листа.
    «Действительно для вертолетов» — слабый маркер: он часто входит в
    колонтитул, поэтому даёт ``title`` только если после отсечения
    шапки не осталось содержательного текста.
``catalog``
    каталог только по шапке: «КАТАЛОГ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ»,
    шапка таблицы («Номер рисунка» + «Номер позиции» + «Обозначение» —
    все три) или «Рис. N» в начале строки + таблица с «поз.».
    «см. рис. 5» в середине текста и «поз.» в абзаце каталогом не
    считаются.

Штампы «Копия документа», «Контрольная копия хранится», «Подлинник
хранится», «Контрольная сумма», «Распечатал», «Дата печати» вырезаются
из текста до анализа: это служебная отметка на многих листах, тип листа
она не меняет. Если после вырезания текста не осталось — ``service``.

Служебные статусы: ``blank`` — пустой лист или штатная пометка «Эта
страница преднамеренно оставлена пустой» (граница раздела, не мусор);
``other`` — fallback, доля которого контролируется перед Д1б.

При конфликте содержательных типов решает позиция в окне анализа:
побеждает маркер, который встретился раньше. При совпадении позиции —
порядок :data:`ALL_KINDS`.
"""

from __future__ import annotations

import re

#: Окно шапки/анализа: 400 символов. Колонтитул ищется в первых 400,
#: анализ идёт по следующим 400 символам (однострочная книга режется по
#: символам, поэтому маркер в конце такой строки всё равно виден).
HEADER_CHARS = 400

#: Содержательные типы листа.
PAGE_KINDS: tuple[str, ...] = (
    "purpose",
    "techcard",
    "faults",
    "contents",
    "table",
    "list",
    "scheme",
    "title",
    "catalog",
)

#: Служебные статусы: пустой лист, только штамп копии и fallback.
SPECIAL_KINDS: tuple[str, ...] = ("blank", "service", "other")

#: Все значения, которые может принимать ``pages.page_kind``.
ALL_KINDS: tuple[str, ...] = PAGE_KINDS + SPECIAL_KINDS

#: Человекочитаемые подписи для отчёта и будущей фразы отказа Д1б.
PAGE_KIND_LABELS: dict[str, str] = {
    "purpose": "описание назначения",
    "techcard": "технологическая карта",
    "faults": "отыскание и устранение неисправностей",
    "contents": "содержание",
    "table": "таблица",
    "list": "перечень",
    "scheme": "схема",
    "title": "титульный лист",
    "catalog": "каталог",
    "blank": "пустой лист",
    "service": "только служебный штамп",
    "other": "прочее",
}

#: Приоритет при совпадении позиции маркеров (позиция важнее приоритета).
_KIND_ORDER = {kind: index for index, kind in enumerate(ALL_KINDS)}

#: Маркеры содержательных типов. Порядок внутри типа не важен: выбирается
#: минимальная позиция среди всех совпадений.
_CONTENT_MARKERS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("purpose", (
        re.compile(r"ОПИСАНИЕ\s+И\s+РАБОТА", re.I),
        re.compile(r"ОПИСАНИЕ\s+РАБОТЫ", re.I),
        re.compile(r"РАБОТА\s+СИСТЕМЫ", re.I),
        re.compile(r"ПРИНЦИП\s+РАБОТЫ", re.I),
        re.compile(r"ОБЩИЕ\s+СВЕДЕНИЯ", re.I),
        re.compile(r"\bНАЗНАЧЕНИЕ\b", re.I),
        re.compile(r"\bСОСТАВ\b", re.I),
        re.compile(r"УСТРОЙСТВО\s+И\s+РАБОТА", re.I),
    )),
    ("techcard", (
        re.compile(r"ТЕХНОЛОГ\w*\s+КАРТ\w*", re.I),
        re.compile(r"ТЕХНОЛОГ\s+ИЧЕСКАЯ\s+КАРТ\w*", re.I),
        re.compile(
            r"СОДЕРЖАНИЕ\s+ОПЕРАЦИИ\s+И\s+ТЕХНИЧЕСКИЕ\s+ТРЕБОВАНИЯ",
            re.I),
        re.compile(r"РАБОТЫ,?\s+ВЫПОЛНЯЕМЫЕ\s+ПРИ\s+ОТКЛОНЕНИЯХ", re.I),
        re.compile(r"НАИМЕНОВАНИЕ\s+РАБОТЫ", re.I),
        re.compile(r"\bКОНТ-?РОЛЬ\b", re.I),
    )),
    ("faults", (
        re.compile(r"ОТЫСКАНИЕ\s+И\s+УСТРАНЕНИЕ", re.I),
        re.compile(r"НЕИСПРАВНОСТЬ", re.I),
        re.compile(r"ВОЗМОЖНАЯ\s+ПРИЧИНА", re.I),
        re.compile(r"СПОСОБ\s+УСТРАНЕНИЯ", re.I),
        re.compile(r"КАТАЛОГ\s+ДЕФЕКТОВ", re.I),
        re.compile(r"\bДЕФЕКТ", re.I),
        # Карточка дефекта в каталоге: симптом без слова «дефект».
        re.compile(r"\bТРЕЩИН", re.I),
    )),
    ("contents", (
        # «Содержание операции» / «Содержание работы» — это шапка таблицы
        # технологической карты, а не оглавление.
        re.compile(
            r"\bСОДЕРЖАНИЕ\b"
            r"(?!\s+(?:ОПЕРАЦИИ|РАБОТЫ|И\s+ТЕХНИЧЕСКИЕ))",
            re.I,
        ),
        re.compile(r"\bОГЛАВЛЕНИЕ\b", re.I),
        re.compile(r"\bВВЕДЕНИЕ\b", re.I),
        re.compile(r"ПЕРЕЧЕНЬ\s+ДЕЙСТВУЮЩИХ\s+СТРАНИЦ", re.I),
    )),
    ("table", (
        re.compile(r"\bТАБЛИЦА\b", re.I),
    )),
    ("list", (
        re.compile(
            r"\bПЕРЕЧЕНЬ\b(?!\s+ДЕЙСТВУЮЩИХ\s+СТРАНИЦ)", re.I),
    )),
    ("scheme", (
        re.compile(r"\bСХЕМ[АЫ]\b", re.I),
        re.compile(r"\bРИСУНОК\b", re.I),
    )),
)

#: Признак бланка технологической карты рядом с её заголовком.
_TECHCARD_FORM = re.compile(r"№|НАИМЕНОВАНИЕ\s+РАБОТ", re.I)
#: Контекст, в котором «Контроль» — шапка таблицы карты, а не слово
#: в обычном предложении.
_TECHCARD_CONTEXT = re.compile(
    r"СОДЕРЖАНИЕ\s+ОПЕРАЦИИ|ТЕХНИЧЕСКИЕ\s+ТРЕБОВАНИЯ|"
    r"РАБОТЫ,?\s+ВЫПОЛНЯЕМЫЕ|НАИМЕНОВАНИЕ\s+РАБОТЫ",
    re.I,
)
#: Шапка таблицы каталога.
_CATALOG_HEADER = re.compile(
    r"(?:ЭКСПЛУАТАЦИОННЫЙ\s+)?"
    r"КАТАЛОГ\s+ДЕТАЛЕЙ\s+И\s+СБОРОЧНЫХ\s+ЕДИНИЦ",
    re.I,
)
_CATALOG_NUMBER_FIGURE = re.compile(r"НОМЕР\s+РИСУНКА", re.I)
_CATALOG_NUMBER_POSITION = re.compile(r"НОМЕР\s+ПОЗИЦИИ", re.I)
_CATALOG_DESIGNATION = re.compile(r"ОБОЗНАЧЕНИЕ", re.I)
_CATALOG_POSITION = re.compile(r"\bПОЗ\.?\s*\d+", re.I)
#: «Рис. N» в позиции заголовка — только начало строки.
_CATALOG_FIGURE_HEADING = re.compile(r"^\s*РИС\.?\s*\d", re.I | re.M)

#: Сильные маркеры титульного листа: побеждают остальное.
_STRONG_TITLE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bУТВЕРЖДЕН\b", re.I),
    re.compile(r"\bУТВЕРЖДАЮ\b", re.I),
    re.compile(
        r"ЛИСТ\s+РЕГИСТРАЦИИ\s+(?:ВРЕМЕННЫХ\s+)?ИЗМЕНЕНИЙ", re.I),
)
#: «Титульный лист» — только в начале листа, не в перечне страниц.
_TITLE_HEADING = re.compile(r"^\s*ТИТУЛЬНЫЙ\s+ЛИСТ", re.I)
#: Слабый маркер: часто входит в колонтитул, поэтому только fallback.
_WEAK_TITLE = re.compile(r"ДЕЙСТВИТЕЛЬНО\s+ДЛЯ\s+ВЕРТОЛЕТОВ", re.I)

#: Штатная пометка пустого листа (не мусор, а граница раздела).
_BLANK_MARK = re.compile(
    r"ПРЕДНАМЕРЕННО\s+ОСТАВЛЕНА\s+ПУСТОЙ", re.I)

#: Служебный штамп копии: вырезается из текста до анализа.
_SERVICE_STAMP = re.compile(
    r"КОПИЯ\s+ДОКУМЕНТА|"
    r"КОНТРОЛЬНАЯ\s+КОПИЯ\s+ХРАНИТСЯ|"
    r"ПОДЛИННИК\s+ХРАНИТСЯ|"
    r"КОНТРОЛЬНАЯ\s+СУММА|"
    r"РАСПЕЧАТАЛ|"
    r"ДАТА\s+ПЕЧАТИ",
    re.I,
)

#: «СОДЕРЖАНИЕ»/«ВВЕДЕНИЕ» в первых двух строках после колонтитула
#: побеждают ``catalog``: это страница содержания/введения, а не каталог.
_CONTENTS_HEAD = re.compile(
    r"\bСОДЕРЖАНИЕ\b"
    r"(?!\s+(?:ОПЕРАЦИИ|РАБОТЫ|И\s+ТЕХНИЧЕСКИЕ))|"
    r"\bОГЛАВЛЕНИЕ\b|"
    r"\bВВЕДЕНИЕ\b",
    re.I,
)

#: Колонтитул книги: повторяющаяся шапка, а не тип листа.
_HEADER_TOKENS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bМИ-8МТВ-1\b", re.I),
    re.compile(r"\bMU-8MTB-1\b", re.I),
    re.compile(r"\bTB3-117\b", re.I),
    re.compile(r"\bТВ3-117\b", re.I),
    re.compile(r"\bSAFIR\b", re.I),
    re.compile(r"\bPG\s*05-\d+\b", re.I),
    re.compile(r"РУКОВОДСТВО\s+ПО\s+ТЕХНИЧЕСКОЙ\s+ЭКСПЛУАТАЦИИ", re.I),
    re.compile(r"РУКОВОДСТВО\s+ПО\s+ЭКСПЛУАТАЦИИ", re.I),
    re.compile(
        r"КАТАЛОГ\s+ДЕТАЛЕЙ\s+И\s+СБОРОЧНЫХ\s+ЕДИНИЦ", re.I),
    re.compile(r"ДОПОЛНЕНИЕ\s+К\s+РУКОВОДСТВУ", re.I),
    re.compile(r"ДЕЙСТВИТЕЛЬНО\s+ДЛЯ\s+ВЕРТОЛЕТОВ", re.I),
    re.compile(r"\bСтр\.?\s*\d+(?:\s*/\s*\d+)?[а-я]?\b", re.I),
    re.compile(r"\b\d{3}\.\d{2}\.\d{2}\b", re.I),
    re.compile(
        r"\b(?:Янв|Фев|Мар|Апр|Май|Июн|Июл|Авг|Сен|Окт|Ноя|Дек)"
        r"[а-я]*\.?\s*\d{2}/\d{2}\b", re.I),
    re.compile(r"\bИзменение\s+№\s*\d+", re.I),
    re.compile(r"\bКНИГА\s*\d+", re.I),
    re.compile(r"\bРЭ\s*\|\s*КНИГА", re.I),
    re.compile(r"\bНЕСЕКРЕТНО\b", re.I),
    re.compile(r"\bОДК\s*\|?\s*КЛИМОВ\b", re.I),
)


def _header_end(text: str) -> int:
    """Конец колонтитула в первых :data:`HEADER_CHARS` символах.

    Учитываются только токены шапки, стоящие до первого содержательного
    маркера: «Стр. 5» в середине текста не должен съедать страницу.
    """
    window = text[:HEADER_CHARS]
    first_content = len(window)
    for _kind, patterns in _CONTENT_MARKERS:
        for pattern in patterns:
            match = pattern.search(window)
            if match is not None and match.start() < first_content:
                first_content = match.start()
    for pattern in _STRONG_TITLE:
        match = pattern.search(window)
        if match is not None and match.start() < first_content:
            first_content = match.start()
    title_heading = _TITLE_HEADING.search(window[:120])
    if title_heading is not None and title_heading.start() < first_content:
        first_content = title_heading.start()
    end = 0
    for pattern in _HEADER_TOKENS:
        for match in pattern.finditer(window):
            if match.end() <= first_content + 1 and match.end() > end:
                end = match.end()
    return min(end, HEADER_CHARS)


def _is_techcard_match(match: re.Match[str], analysis: str) -> bool:
    """Отличить бланк/продолжение карты от упоминания «карта N»."""
    text = match.group(0).upper()
    if "ТЕХНОЛОГ" in text:
        around = analysis[max(0, match.start() - 50):match.end() + 250]
        return bool(_TECHCARD_FORM.search(around))
    if "КОНТ" in text:
        return bool(_TECHCARD_CONTEXT.search(analysis))
    return True


def _first_lines(text: str, count: int = 2) -> list[tuple[int, str]]:
    """Первые ``count`` непустых строк с их смещением в ``text``."""
    lines: list[tuple[int, str]] = []
    for match in re.finditer(r"[^\n]+", text):
        line = match.group(0).strip()
        if not line:
            continue
        lines.append((match.start(), line))
        if len(lines) >= count:
            break
    return lines


def _catalog_candidates(window: str, analysis: str) -> list[int]:
    """Позиции маркеров каталога только в шапке (первые 2 строки).

    «КАТАЛОГ ДЕТАЛЕЙ...» в колонтитуле книги даёт позицию -1: это шапка
    документа, она важнее любого маркера в тексте. Шапка таблицы
    каталога требует все три слова; «Рис. N» — только в начале строки и
    только если дальше есть «поз.».
    """
    positions: list[int] = []
    for _offset, line in _first_lines(window, 2):
        if _CATALOG_HEADER.search(line):
            positions.append(-1)
            break
    analysis_lines = _first_lines(analysis, 2)
    head = "\n".join(line for _offset, line in analysis_lines)
    for offset, line in analysis_lines:
        header = _CATALOG_HEADER.search(line)
        if header is not None:
            positions.append(offset + header.start())
        figure = _CATALOG_NUMBER_FIGURE.search(line)
        if (figure is not None
                and _CATALOG_NUMBER_POSITION.search(head)
                and _CATALOG_DESIGNATION.search(head)):
            positions.append(offset + figure.start())
        heading = _CATALOG_FIGURE_HEADING.search(line)
        if heading is not None and _CATALOG_POSITION.search(analysis):
            positions.append(offset + heading.start())
    return positions


def _strip_service_stamps(text: str) -> str:
    """Удалить строки служебного штампа копии перед анализом."""
    return "\n".join(
        line for line in text.splitlines()
        if not _SERVICE_STAMP.search(line)
    )


def classify_page(text: str) -> str:
    """Вернуть тип листа по шапке без колонтитула и штампа копии.

    Пустая строка/пробелы и штатная пометка пустого листа дают ``blank``;
    лист, от которого после вырезания штампа копии ничего не осталось —
    ``service``; отсутствие маркеров — ``other``. Сильные маркеры
    титульного листа побеждают остальное; «Действительно для вертолетов»
    — только fallback после отсечения колонтитула. Среди содержательных
    маркеров побеждает самый ранний, при равной позиции — порядок
    :data:`ALL_KINDS`.
    """
    raw = text or ""
    if not raw.strip():
        return "blank"
    text = _strip_service_stamps(raw)
    if not text.strip():
        return "service"
    window = text[:HEADER_CHARS]
    if _BLANK_MARK.search(window):
        return "blank"
    if any(pattern.search(window) for pattern in _STRONG_TITLE):
        return "title"
    if _TITLE_HEADING.search(window[:120]):
        return "title"
    start = _header_end(text)
    analysis = text[start:start + HEADER_CHARS]
    catalog_positions = _catalog_candidates(window, analysis)
    analysis_head = "\n".join(
        line for _offset, line in _first_lines(analysis, 2))
    # «СОДЕРЖАНИЕ»/«ВВЕДЕНИЕ» в шапке страницы каталога — это contents,
    # а не catalog, даже если в колонтитуле стоит «КАТАЛОГ ДЕТАЛЕЙ».
    if catalog_positions and _CONTENTS_HEAD.search(analysis_head):
        return "contents"
    candidates: list[tuple[int, str]] = [
        (position, "catalog") for position in catalog_positions
    ]
    for kind, patterns in _CONTENT_MARKERS:
        for pattern in patterns:
            for match in pattern.finditer(analysis):
                if kind == "techcard" and not _is_techcard_match(
                        match, analysis):
                    continue
                candidates.append((match.start(), kind))
    if candidates:
        candidates.sort(key=lambda item: (item[0], _KIND_ORDER[item[1]]))
        return candidates[0][1]
    # Слабый маркер даёт title только пустому после шапки листу: на
    # содержательных страницах «Действительно для вертолетов» — часть
    # колонтитула, а не тип листа.
    if _WEAK_TITLE.search(window) and len(analysis.strip()) < 40:
        return "title"
    return "other"


def page_kind_label(kind: str) -> str:
    """Подпись типа листа; неизвестный тип возвращается как есть."""
    return PAGE_KIND_LABELS.get(kind, kind)


__all__ = [
    "HEADER_CHARS",
    "PAGE_KINDS",
    "SPECIAL_KINDS",
    "ALL_KINDS",
    "PAGE_KIND_LABELS",
    "classify_page",
    "page_kind_label",
]
