# -*- coding: utf-8 -*-
"""Ф2.3: ответ по-человечески — чистка лида и выбор предложений.

Модуль превращает текст листа-лидера в начало ответа:

1. ``clean_page_lines`` — удаляет строки-мусор: короче 25 символов;
   только из цифр/дат/кодов («Стр. 213», «Авг 13/13», «022.10.00»);
   шапки из списка карточки («ПУЛЬТАЦИИ», «К РО», «На страницах»,
   «ГРУЗОПОДЪЕМНОСТЬ»); колонтитулы — первые две строки, совпадающие
   с первыми двумя строками других листов того же документа. ИСКЛЮЧЕНИЯ
   (решение владельца Ф2.3): порог 25 символов не удаляет заголовки —
   строки с нумерацией (``1.1.``, ``2.3.16.``) или начинающиеся с
   «Раздел/Глава/Рис./Табл./Таблица/Приложение» — и короткие строки,
   содержащие значимое слово вопроса.
2. ``select_sentences`` — до трёх предложений с максимальным покрытием
   значимых слов вопроса (те же термины, что в гейтах,
   ``pdfgraph.gates.question_terms``); порядок — как в тексте,
   предложения дословные.
3. ``lead_paragraph`` — точка входа для build_answer: абзац для
   ``Claim.answer_text``; если предложений с покрытием нет — первый
   содержательный абзац очищенного текста (fallback, помечается
   «фрагмент листа» на отображении).

Цитата (Claim.text) не меняется: verify и дословность прежние.
"""

from __future__ import annotations

import re
import sqlite3

from pdfgraph.gates import question_terms
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

#: Порог «не несущей смысла» строки (карточка Ф2.3).
MIN_MEANINGFUL_CHARS = 25
#: Сколько листов документа сэмплируется для поиска колонтитулов.
HEADER_SAMPLE_PAGES = 30
#: Сколько предложений максимум попадает в абзац.
MAX_SENTENCES = 3
#: Заголовочные фразы (карточка Ф2.3); сопоставление без регистра.
#: «ТЕХНОЛОГИЧЕСКАЯ КАРТА» не в списке (решение владельца): это название
#: документа, а не колонтитул.
_HEADER_PHRASES = ("пультации", "к ро", "на страницах",
                   "грузоподъемность", "грузоподъёмность")
#: Начала заголовков, которые порог 25 символов не удаляет.
_HEADING_START_RE = re.compile(
    r"^(\d+(\.\d+)*\.?\s|раздел|глава|рис\.|табл\.|таблица|приложение)",
    re.IGNORECASE)
#: Строка только из цифр, дат, кодов, разделителей.
_DIGITS_ONLY_RE = re.compile(r"^[\d\s.,:;/№()-]+$")
#: Код вида 022.10.00.
_CODE_RE = re.compile(r"\d{2,3}\.\d{2}\.\d{2}")
#: Дата «Авг 13/13».
_DATE_SLASH_RE = re.compile(r"^[а-яё]{3,4}\s+\d{1,2}/\d{1,2}$", re.I)
#: «Стр. 213».
_PAGE_REF_RE = re.compile(r"^стр\.?\s*\d+", re.I)


def _contains_term(haystack: str, term: str) -> bool:
    """Нормализованное вхождение термина (правило Ф1.3а)."""
    return term in normalize_for_search(haystack)


def is_header_line(line: str, question_words: set[str] | None = None
                   ) -> bool:
    """Строка-мусор? Вопросные слова и заголовки спасают строку."""
    stripped = line.strip()
    if not stripped:
        return True
    # Заголовки с нумерацией и «Раздел/Рис./Табл.…» — не мусор.
    if _HEADING_START_RE.match(stripped):
        return False
    # OCR-«гребёнка» и повторы символов — мусор ДАЖЕ если в строке есть
    # слово вопроса: OCR вклеивает мусор и текст в одну строку
    # (урок прогона Ф2.3, «ннннненннн…+150 °С Давление масла…»).
    if re.match(r"(.)\1{4,}", stripped):
        return True
    if len(stripped) >= 10:
        letters = [ch for ch in stripped.lower() if ch.isalpha()]
        if letters:
            most = max(letters.count(ch) for ch in set(letters))
            if most / len(letters) > 0.6:
                return True
    # Значимое слово вопроса в короткой строке — не мусор (подпись
    # к таблице, название параметра; решение владельца Ф2.3).
    if question_words:
        for word in question_words:
            if _contains_term(stripped, word):
                return False
    if _PAGE_REF_RE.match(stripped):
        return True
    if _DATE_SLASH_RE.match(stripped):
        return True
    lowered = stripped.lower()
    if any(phrase in lowered for phrase in _HEADER_PHRASES):
        return True
    if len(stripped) < MIN_MEANINGFUL_CHARS:
        return True
    if _CODE_RE.search(stripped) and _DIGITS_ONLY_RE.search(
            stripped.replace(" ", "")):
        return True
    if _DIGITS_ONLY_RE.match(stripped) and _CODE_RE.search(stripped):
        return True
    return False


def _header_key(lines: list[str]) -> str:
    """Ключ колонтитула: первые две строки листа."""
    return "|".join(line.strip() for line in lines[:2])


def _running_headers(store: Store, document: str, page: int
                     ) -> set[str]:
    """Колонтитулы документа: первые две строки других листов (сэмпл)."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        rows = store._connection.execute(
            "SELECT text FROM pages WHERE document = ? AND page <> ? "
            "AND TRIM(COALESCE(text, '')) <> '' LIMIT ?",
            (document, page, HEADER_SAMPLE_PAGES)).fetchall()
        headers: set[str] = set()
        for (text,) in rows:
            lines = [line for line in (text or "").splitlines()
                     if line.strip()]
            if lines:
                headers.add(_header_key(lines))
        return headers
    except (sqlite3.Error, TypeError, AttributeError) as error:
        # Подделки store в тестах (Mock-connection) — колонтитулы
        # недоступны: чистка работает без них, отсутствие не глушится
        # молча.
        logger.warning("running headers unavailable: %s", error)
        return set()


_DOT_LEADER_RE = re.compile(r"\.{6,}")


def _strip_leaders(line: str) -> str:
    """Точечные лидеры оглавления («........нннннннннннн») → « … ».

    OCR превращает точки-заполнители между параметром и значением в
    цепочки «ннннн…» длиной 10+ из ≤3 уникальных букв без пробелов —
    вырезается и это (урок прогона Ф2.3 на листе 38 РЭ1 кн. 1).
    """
    line = _DOT_LEADER_RE.sub(" ", line)
    result: list[str] = []
    for run in re.findall(r"\S+|\s+", line):
        if len(run) >= 10 and not run.strip().isdigit():
            letters = [ch for ch in run.lower() if ch.isalpha()]
            if letters and len(set(letters)) <= 3:
                continue
        result.append(run)
    return "".join(result)


def clean_page_lines(lines: list[str], header_lines: set[str],
                     question_words: set[str] | None = None
                     ) -> list[str]:
    """Удалить строки-мусор (карточка Ф2.3, п. 1; исключения — см. выше).

    ``header_lines`` — ключи колонтитулов других листов
    (:func:`_header_key` от первых двух строк).
    """
    # Колонтитул — по определению первые две строки листа: если их пара
    # совпала с другими листами, срезаем обе разом.
    if len(lines) >= 2 and _header_key(lines[:2]) in header_lines:
        lines = lines[2:]
    cleaned: list[str] = []
    for raw in lines:
        # OCR-«гребёнка» часто вклеена в НАЧАЛО строки перед полезным
        # текстом: срезаем повторяющийся префикс, а не строку целиком.
        line = re.sub(r"^(.)\1{4,}\s*", "", _strip_leaders(raw.strip()))
        if not line:
            continue
        if not is_header_line(line, question_words):
            cleaned.append(line)
    return cleaned


def _sentences(text: str) -> list[tuple[int, str]]:
    """Предложения с позициями в тексте (разделители . ! ?)."""
    result: list[tuple[int, str]] = []
    start = 0
    for match in re.finditer(r"[.!?]", text):
        sentence = text[start:match.end()].strip()
        if sentence:
            result.append((start, sentence))
        start = match.end()
    tail = text[start:].strip()
    if tail:
        result.append((start, tail))
    return result


def select_sentences(question: str, text: str, limit: int = MAX_SENTENCES
                     ) -> list[str]:
    """До ``limit`` предложений с максимальным покрытием терминов вопроса.

    Термины — ``pdfgraph.gates.question_terms``; порядок выбранных —
    как в тексте; предложения дословные. Нет совпадений — пустой список.
    """
    terms = question_terms(question)
    if not terms:
        return []
    # Порог: одно совпадение — шум («Температура масла…» при вопросе про
    # давление); для вопроса с одним термином достаточно одного.
    threshold = 1 if len(terms) == 1 else 2
    scored: list[tuple[float, int, str]] = []
    for position, sentence in _sentences(text):
        covered = sum(1 for term in terms
                      if _contains_term(sentence, term))
        if covered >= threshold:
            scored.append((covered / len(terms), position, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    picked = scored[:limit]
    picked.sort(key=lambda item: item[1])
    return [sentence for _score, _position, sentence in picked]


# --- Г1: чёткий ответ и страницы (слой показа; Claim.text не трогается) -----

#: Показ-мусор (Г1 п.2): штампы выгрузки, ГОСТы, номера страниц, даты.
_DISPLAY_JUNK_RES = (
    re.compile(r"\{?\s*Копия документа[^\n]*", re.I),
    re.compile(r"Контрольная копия хранится[^\n]*", re.I),
    re.compile(r"Подлинник хранится[^\n]*", re.I),
    re.compile(r"Распечатал[^\n]*", re.I),
    re.compile(r"Контрольная сумма:?\s*[0-9A-Fa-f{}]+\}?"),
    re.compile(r"\bГОСТ\s*\d+(?:-\d+)*", re.I),
    re.compile(r"\bстр\.?\s*\d+(?:\s*/\s*\d+)?", re.I),
    re.compile(r"\b[А-Яа-яё]{3,8}\s+\d{1,2}/\d{1,2}\b"),
    # Коды систем ATA-100 в шапке («…ВСЕ 072.00.00») — в показ не идут.
    re.compile(r"\b\d{2,3}\.\d{2}\.\d{2}\b"),
)
#: Показ-нормализация единиц (Г1 п.3): OCR-гомоглифы и сноски.
_UNIT_SUBS = (
    # «кгс/см?2» — реальный OCR-вариант л.38 (вопросительный знак + «2»).
    (re.compile(r"кг[сcоo0]/см\??2?", re.I), "кгс/см²"),
    (re.compile(r"(?<=\d)\s*[оo0][сcC]\b", re.I), " °C"),
)
#: Число с единицей (любой параметр) — признак нормы в предложении.
#: Хвостовой класс с «)» пропускает «(3,5±0,5) кгс/см²».
_VALUE_UNIT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*[\)\(]?[а-яa-z/²%°?]{1,12}", re.I)
#: Перечень («1. Бензин») — не норма; заголовок раздела («2.3.17.»)
#: отличается двузначностью и перечнем не считается.
_ENUM_RE = re.compile(r"^\d+[.)]\s")
#: Печатная страница в колонтитуле: «Стр. 219/220», «Стр. 9».
_PAGE_MARK_RE = re.compile(r"\bстр\.?\s*(\d+(?:\s*/\s*\d+)?)", re.I)
#: «(изм. от …)» в имени файла — в показ не идёт.
_REVISION_NAME_RE = re.compile(r"\s*\([^)]*изм[^)]*\)", re.I)


def clean_display_text(text: str) -> str:
    """Вырезать не относящееся к показу (Г1 п.2); только отображение.

    Штампы выгрузки, ГОСТы, «стр. N», даты колонтитула, точечные лидеры
    и OCR-«гребёнка». Дословность обязаны держать Claim.text и verify —
    этот текст в проверку не попадает.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw
        for pattern in _DISPLAY_JUNK_RES:
            line = pattern.sub(" ", line)
        line = re.sub(r"^(.)\1{4,}\s*", "",
                      _strip_leaders(line.strip()))
        line = _DOT_LEADER_RE.sub(" ", line)
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def _unglue(text: str, question: str) -> str:
    """Склейки слов («Давлениемасла») — разлепить по парам слов вопроса."""
    words = re.findall(r"[а-яё]{4,}", question.lower())
    for first, second in zip(words, words[1:]):
        pattern = re.compile(re.escape(first) + re.escape(second),
                             re.IGNORECASE)

        def _keep_case(match: re.Match[str]) -> str:
            head = first.capitalize() if match.group(0)[0].isupper() else first
            return f"{head} {second}"

        text = pattern.sub(_keep_case, text)
    return text


def display_normalize(text: str, question: str = "") -> str:
    """Показ-нормализация (Г1 п.3): единицы и склейки; только отображение."""
    for pattern, replacement in _UNIT_SUBS:
        text = pattern.sub(replacement, text)
    return _unglue(text, question)


def printed_page_mark(page_text: str) -> str:
    """Печатная страница из колонтитула (Г1 п.4); нет — пустая строка.

    «Стр. 219/220» → «219/220» (разворот показывается как в книге);
    на техкартах колонтитула нет — не выдумывается.
    """
    match = _PAGE_MARK_RE.search(page_text)
    return re.sub(r"\s", "", match.group(1)) if match else ""


def short_document_name(document: str) -> str:
    """Короткое имя документа для показа: без .pdf и «(изм. от …)»."""
    name = re.sub(r"\.pdf$", "", document, flags=re.I)
    name = _REVISION_NAME_RE.sub("", name)
    # «Руководство по технической эксплуатации РТЭ …» → «РТЭ …» (без дубля).
    name = re.sub(r"^Руководство по технической эксплуатации\s+(?!РТЭ)",
                  "РТЭ ", name, flags=re.I)
    name = re.sub(r"^Руководство по технической эксплуатации\s+", "",
                  name, flags=re.I)
    return re.sub(r"\s{2,}", " ", name).strip()


def no_norm_message(neighbors: list[dict] | None, limit: int = 3) -> str:
    """«Нормы на листе не нашёл. Ближайшие листы: …» без портянки (Г1 п.6)."""
    refs = []
    for item in list(neighbors or [])[:limit]:
        ref = f"лист {item.get('page', '?')}"
        if item.get("label"):
            ref += f" ({item['label']})"
        refs.append(ref)
    tail = "; ".join(refs)
    return ("Нормы на листе не нашёл."
            + (f" Ближайшие листы: {tail}." if tail else ""))


def norm_display_items(store: Store, question: str, document: str,
                       page: int) -> list[str]:
    """Пункты показа: предложения норм из валидных окон (Г1 п.1).

    Отбор по смыслу, не по длине: предложение открывает/продолжает пункт,
    если в нём есть число с единицей; пункт открывается предложением со
    стемом параметра/объекта вопроса. «Осиротевшее» число-предложение
    («не более 4,8 кгс/см²» с отрезанным нарезкой началом) приклеивается
    к текущему пункту — это продолжение той же нормы. Перечни («1.
    Бензин») отсекаются. Окна те же, что у канала норм; дедупликация
    контейнером (почти одинаковые обрезки — остаётся длиннейший).
    """
    from pdfgraph.norms import (is_value_question, norm_window_texts,
                                _PARAMETERS, _stem)

    ok, parameter, object_words = is_value_question(question)
    if not ok:
        return []
    parameter_stem = _PARAMETERS.get(parameter, "")
    object_stems = [
        (_stem(normalize_for_search(word))
         if not word.isdigit() and "-" not in word else word)
        for word in object_words
    ]
    stems = ([parameter_stem] if parameter_stem else []) + object_stems

    def _has_term(text: str) -> bool:
        normalized = normalize_for_search(text)
        return any(stem in normalized for stem in stems)

    def _glue(prev: str, nxt: str) -> str:
        """Стык окон: суффикс prev == префикс nxt — склеить без дубля."""
        for k in range(min(len(prev), len(nxt)), 10, -1):
            if prev[-k:].casefold() == nxt[:k].casefold():
                return prev + nxt[k:]
        return prev + " " + nxt

    items: list[str] = []
    buffer: list[str] = []
    for window in norm_window_texts(store, document, page, question):
        cleaned = display_normalize(clean_display_text(window), question)
        for _pos, sentence in _sentences(cleaned):
            text = re.sub(r"\s{2,}", " ", sentence).strip(" ;,")
            if len(text) < 8 or _ENUM_RE.match(text):
                continue
            has_unit = bool(_VALUE_UNIT_RE.search(text))
            has_term = _has_term(text)
            if has_term and has_unit:
                if buffer:
                    items.append(" ".join(buffer))
                buffer = [text]
            elif buffer and has_unit:
                # Продолжение нормы: число с единицей без повторения
                # параметра («не более 4,8 кгс/см²», «+0,25 кгс/см²»).
                buffer[-1] = _glue(buffer[-1], text)
            elif has_term and has_unit:
                items.append(text)
            elif buffer and has_term and not has_unit:
                # Заголовок/продолжение без числа — не норма; буфер
                # сохраняется, чужой раздел в пункт не попадает.
                continue
            elif buffer:
                # Мусор между нормами (гребёнка, «Стр. 9») закрывает пункт.
                items.append(" ".join(buffer))
                buffer = []
    if buffer:
        items.append(" ".join(buffer))

    deduped: list[str] = []
    for text in items:
        text = text.strip(" ;,")
        # Обрезок пункта («масла по прибору…») со строчной — выбрасывается.
        if not text[:1].isupper() and not text[:1].isdigit():
            continue
        key = text.casefold()
        container = next(
            (existing for existing in deduped
             if key in existing.casefold() or existing.casefold() in key),
            None)
        if container is not None:
            if len(text) > len(container):
                deduped[deduped.index(container)] = text
            continue
        deduped.append(text)
    return deduped


def value_display(store: Store, answer) -> dict | None:
    """Словарь показа Г1 для вопрос-значений; None — не вопрос-значение.

    Ключи: ``items`` — пункты норм (чистка и нормализация только для
    показа); ``source`` — строка с двумя нумерациями (лист базы + печатная
    страница из колонтитула, если есть); ``source_url`` — роут веб-листа;
    ``quote`` — ДОСЛОВНЫЙ ``Claim.text`` (verify не затрагивается).
    """
    from urllib.parse import quote as url_quote

    from pdfgraph.norms import is_value_question

    if answer.refusal or not answer.fragments:
        return None
    ok, _parameter, _objects = is_value_question(answer.question)
    if not ok:
        return None
    fragment = answer.fragments[0]
    items = norm_display_items(store, answer.question, fragment.document,
                               fragment.page)
    page_text = store.page_text(fragment.document, fragment.page)
    mark = printed_page_mark(page_text)
    source = (f"Источник: {short_document_name(fragment.document)}, "
              f"лист {fragment.page}")
    if mark:
        source += f" (по книге стр. {mark})"
    return {
        "items": items,
        "source": source,
        "source_url": f"/doc/{url_quote(fragment.document)}"
                      f"/page/{fragment.page}",
        "quote": answer.claims[0].text if answer.claims else "",
        "page_mark": mark,
    }


def lead_paragraph(store: Store, question: str,
                   fragment) -> tuple[str, bool]:
    """Абзац для Claim.answer_text; (текст, fallback-флаг).

    Fallback: предложений с покрытием нет — первый содержательный
    абзац очищенного текста (на отображении помечается «фрагмент
    листа»). Текст листа читается store.page_text — дословный.
    """
    text = store.page_text(str(fragment.document), int(fragment.page))
    lines = [line for line in text.splitlines() if line.strip()]
    headers = _running_headers(store, str(fragment.document),
                               int(fragment.page))
    terms = set(question_terms(question))
    cleaned = clean_page_lines(lines, headers, question_words=terms)
    cleaned_text = "\n".join(cleaned)
    sentences = select_sentences(question, cleaned_text)
    if sentences:
        return " ".join(sentences), False
    # Fallback: первый содержательный абзац (куски до пустой строки).
    for block in re.split(r"\n\s*\n", cleaned_text):
        block = block.strip()
        if len(block) >= MIN_MEANINGFUL_CHARS:
            return block, True
    return (cleaned_text.strip()[:500], True) if cleaned_text else ("", True)
