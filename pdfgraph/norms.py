# -*- coding: utf-8 -*-
"""Ф1.24: канал норм — лист со значением, а не с описанием.

Эталон проигрывает листам, где «масло» повторяется много раз, но
числа-нормы нет. Канал ищет листы, где рядом с параметром стоит число
с единицей и нормативная конструкция. Кандидаты — prefix-AND по основам
параметра и объекта (:func:`pdfgraph.query._fts_prefix_expr`); точность
даёт не FTS, а проверка окна в Python: (а) объект в окне, (б) число с
совместимой единицей (словарь единиц по параметру, с OCR-вариантами
«кгс/см?»), (в) нормативная конструкция («не менее», «не более»,
«допускается», «в пределах», диапазон, «±»; с Ф1.31 — «минимально/
максимально/предельно допустимая», «допустимый», «должна быть»,
«равна», «составляет»), (г) нет процедурных маркеров («отвернуть»,
«проверьте») в том же окне — маркер в другом месте страницы не мешает.

Границы: ``pages.text`` и цитаты не меняются — сниппет фрагмента
дословный срез ``pages.text`` вокруг первого вхождения параметра
(канал — сигнал ранжирования; окно валидации считается по очищенному
нормализованному тексту, координаты которого могут отличаться от
исходника). Выключатель ``PDFGRAPH_NORMS`` — по умолчанию выключен
(стиль Ф1.16/Ф1.19/Ф1.23а).
"""

from __future__ import annotations

import logging
import os
import re

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfgraph.textclean import clean_search_text
from pdfprepare.normalize import normalize_for_search

#: Выключатель; читается при каждом вызове, без кэша.
NORMS_ENV = "PDFGRAPH_NORMS"
_ON_VALUES = {"1", "true", "yes", "on"}

#: Вес канала норм в RRF-слиянии (карточка Ф1.24): не множитель к
#: ``Fragment.score`` — скор фрагмента используется гейтами.
NORMS_WEIGHT = 2.0

#: Параметры вопроса: имя → основа для поиска в вопросе и на листе.
_PARAMETERS: dict[str, str] = {
    "давление": "давлен",
    "температура": "температур",
    "расход": "расход",
    "время": "врем",
    "ресурс": "ресурс",
    "момент": "момент",
    "зазор": "зазор",
    "частота": "частот",
    "напряжение": "напряжен",
    # Ф1.26: живой вопрос по щёткам МВ-1200 (Регламент ч.2, лист 141).
    "высота": "высот",
    "износ": "износ",
    "толщина": "толщин",
    "натяг": "натяг",
    # Ф1.29: геометрические параметры вопроса-значения («какой
    # минимальный размер щётки на МВ-1200»).
    "размер": "размер",
    "величина": "величин",
    "длина": "длин",
    "диаметр": "диаметр",
    "сечение": "сечен",
}

#: Единицы по параметру (regex по нормализованному тексту листа),
#: включая OCR-варианты («кгс/см?» — распознанный ²).
_UNITS: dict[str, str] = {
    "давлен": r"кгс\s*/\s*см\s*[2²?]|кго\s*/\s*см|кге\s*/\s*см|кгс|мпа|кпа|мм\s*рт",
    "температур": r"°\s*[сс]|град",
    "расход": r"л\s*/\s*ч|г\s*/\s*ч",
    "врем": r"\bмин\b|\bсек\b|\bс\b|\bч\b|\bчас\b",
    "ресурс": r"\bч\b|\bчас\b|\bцикл",
    "момент": r"кгс\s*·?\s*м|н\s*·?\s*м",
    "зазор": r"\bмм\b|\bсм\b|\bмкм\b",
    "частот": r"%|об/мин|гц|герц",
    "напряжен": r"\bв\b|\bвольт\b|\bкв\b",
    # Ф1.26: линейные износы/зазоры/натяги — мм и мкм.
    "высот": r"\bмм\b|\bмкм\b|\bсм\b",
    "износ": r"\bмм\b|\bмкм\b|\bсм\b",
    "толщин": r"\bмм\b|\bмкм\b|\bсм\b",
    "натяг": r"\bмм\b|\bмкм\b",
    # Ф1.29: те же линейные единицы для геометрических параметров.
    "размер": r"\bмм\b|\bмкм\b|\bсм\b",
    "величин": r"\bмм\b|\bмкм\b|\bсм\b",
    "длин": r"\bмм\b|\bмкм\b|\bсм\b",
    "диаметр": r"\bмм\b|\bмкм\b|\bсм\b",
    "сечен": r"\bмм\b|\bмкм\b|\bмм2\b|\bсм2\b",
}
#: Общие единицы — для вопросов без параметра из словаря.
_COMMON_UNITS = r"\bмм\b|\bсм\b|\bм\b|\bкг\b|\bг\b|\bл\b|\bч\b|\bс\b|\bмин\b"

#: Маркеры значения (проверка по префиксу стема токена вопроса).
_VALUE_MARKER_STEMS = ("как", "каков", "скольк", "минимал", "максимал",
                       "предел")

#: Процедурное намерение в вопросе — канал не срабатывает.
_PROCEDURAL_RE = re.compile(
    r"\bкак\b[^.]*\b(?:регулир|замен|провер|выполн|запуск|настрой)"
    r"|\bпорядок\b|\bрегулировк|\bпроверк")

#: Служебные слова вопроса (стоп-лист, карточка Ф1.23б).
_STOPWORDS = ("что", "как", "какой", "какое", "какие", "где", "почему",
              "при", "для", "на", "в", "и", "не", "ли", "же", "сколько",
              "о", "об", "из", "за", "до", "по", "каков", "какова",
              "минимальное", "максимальное", "минимальный",
              "максимальный", "пределах", "пределы", "должно", "быть")

#: Нормативная конструкция в окне. Ф1.31: добавлены формулировки
#: «минимально/максимально/предельно допустимая», «допустимый»,
#: «должна/должен быть», «равна/равен/равно», «составляет» — на реальном
#: листе 64 РТЭ кн.3 норма записана как «Минимально допустимая высота
#: щеток равна 13,5 мм», и прежний регэксп окно не признавал.
_NORM_RE = re.compile(
    r"не\s+менее|не\s+более|допускается|в\s+пределах|номинал|±"
    r"|минимально\s+допустим\w*|максимально\s+допустим\w*"
    r"|предельно\s+допустим\w*|допустим\w*"
    r"|должн\w*\s+быть|равн[аое]|равен|составля\w*"
    r"|\d+(?:[.,]\d+)?\s*(?:до|…|\.\.)\s*\d")

#: Процедурные маркеры ВНУТРИ окна — окно отбрасывается.
_PROC_WINDOW_RE = re.compile(
    r"отвернут|завернут|проверьте|выполните|запустите|регулир|снимите")

#: Число рядом с единицей (<=3 символов между).
_NUMBER_BEFORE = r"\d+(?:[.,]\d+)?\s{0,3}"

#: Окно валидации: символов назад/вперёд от вхождения параметра.
WINDOW_BACK = 40
WINDOW_FORWARD = 200
#: Доп.symbols вперёд для проверки объекта («или рядом»).
WINDOW_OBJECT_EXT = 40

#: Логика скора: 1.0 за первое валидное окно + 0.1 за каждое
#: дополнительное, кап 1.5.
SCORE_STEP = 0.1
SCORE_CAP = 1.5

logger = logging.getLogger(__name__)


def effective_norms_enabled() -> bool:
    """Выключатель Ф1.24: ``PDFGRAPH_NORMS``; по умолчанию выключен."""
    raw = os.environ.get(NORMS_ENV, "").strip().casefold()
    return raw in _ON_VALUES


def _stem(word: str) -> str:
    """Основа слова: срезать конечные гласные (а, е, и, о, у, ы, э, ю, я).

    Детерминированно и идемпотентно; согласные окончания не трогаются
    («запусков» остаётся «запусков»). Приём Ф1.23б.
    """
    return word.rstrip("аеиоыэуюя")


def _unit_regex(parameter_stem: str) -> str:
    """Regex единицы: по параметру, иначе общий словарь."""
    return _UNITS.get(parameter_stem, _COMMON_UNITS)


def is_value_question(question: str) -> tuple[bool, str, list[str]]:
    """Вопрос-значение? → (да/нет, параметр, основы объектов).

    Маркеры значения — «какое», «каков», «сколько», «в каких пределах»,
    «минимальное/минимальный», «максимальное/максимальный» (по префиксу
    стема токена). Процедурное намерение («как регулировать», «порядок
    проверки», «как заменить», «как проверить») отключает канал.
    Параметр — существительное из словаря :data:`_PARAMETERS`; объекты —
    основы остальных значимых слов (минус служебные и параметр).
    """
    normalized = normalize_for_search(question)
    if _PROCEDURAL_RE.search(normalized):
        return False, "", []
    tokens = normalized.split()
    marker = any(token.startswith(_VALUE_MARKER_STEMS) for token in tokens)
    if not marker:
        return False, "", []
    parameter = ""
    parameter_stem = ""
    objects: list[str] = []
    for token in tokens:
        if len(token) < 4:
            continue
        if token.startswith(_VALUE_MARKER_STEMS):
            continue
        if token in _STOPWORDS:
            continue
        if parameter == "":
            for name, stem in _PARAMETERS.items():
                if token.startswith(stem):
                    parameter = name
                    parameter_stem = stem
                    break
            if parameter:
                continue
        if token.isdigit() or "-" in token:
            objects.append(token)          # обозначение — как есть
            continue
        objects.append(_stem(token))
    return True, parameter, objects[:2]


def _valid_windows(text_norm: str, anchor_stem: str,
                   object_stems: list[str], parameter_stem: str) -> int:
    """Число валидных окон вокруг вхождений ``anchor_stem``.

    Каждое вхождение даёт окно [pos-40, pos+200] (объект проверяется
    в окне, расширенном на +40 вперёд — «или рядом»).
    """
    if not anchor_stem:
        return 0
    unit_re = re.compile(_NUMBER_BEFORE + "(" + _unit_regex(parameter_stem)
                         + ")")
    procedural_spans = [(match.start(), match.end())
                        for match in _PROC_WINDOW_RE.finditer(text_norm)]
    valid = 0
    for match in re.finditer(re.escape(anchor_stem), text_norm):
        start = max(0, match.start() - WINDOW_BACK)
        end = match.end() + WINDOW_FORWARD
        window = text_norm[start:end]
        window_ext = text_norm[start:end + WINDOW_OBJECT_EXT]
        # (а) объект в окне или рядом
        if object_stems and not any(stem in window_ext
                                    for stem in object_stems):
            continue
        # (б) число с совместимой единицей
        if not unit_re.search(window):
            continue
        # (в) нормативная конструкция
        if not _NORM_RE.search(window):
            continue
        # (г) процедурный маркер ВНУТРИ окна — окно отбрасывается;
        # маркер в другом месте страницы не мешает
        if any(p_start < end and p_end > start
               for p_start, p_end in procedural_spans):
            continue
        valid += 1
    return valid


def norm_candidates(store: Store, question: str, parameter: str,
                    object_words: list[str], limit: int,
                    aircraft_type: str | None = None,
                    ) -> list[tuple[str, int, float]]:
    """Кандидаты канала: (document, page, score), скор ↓, до ``limit``.

    FTS: prefix-AND по основам параметра и объектов (не OR — канал
    валидирует окна сам). Для каждого листа-кандидата окно валидации
    считается по нормализованному очищенному тексту на лету (та же
    чистка, что у индекса). Лист засчитывается при хотя бы одном
    валидном окне; скор = 1.0 + 0.1 за каждое дополнительное (кап 1.5).
    """
    from pdfgraph.query import _fts_prefix_expr

    parameter_stem = _PARAMETERS.get(parameter, "")
    # Основа объекта — после normalize_for_search (ё→е и пр.):
    # «щёток» и «щеток» — одна основа «щет».
    object_stems = [
        (_stem(normalize_for_search(word))
         if not word.isdigit() and "-" not in word else word)
        for word in object_words
    ]
    anchor_stems = ([parameter_stem] if parameter_stem
                    else object_stems[:1])
    if not anchor_stems or not anchor_stems[0]:
        return []
    stems = ([parameter_stem] if parameter_stem else []) + object_stems[:2]
    if not stems:
        stems = anchor_stems
    fetch = 200
    rows = store.search_pages(_fts_prefix_expr(stems, " AND "),
                              aircraft_type, fetch)
    if not rows:
        return []
    scores: list[tuple[str, int, float]] = []
    for document, page, *_rest in rows:
        text = store.page_text(document, page)
        if not text:
            continue
        text_norm = normalize_for_search(clean_search_text(text))
        valid = 0
        for stem in anchor_stems:
            valid += _valid_windows(text_norm, stem, object_stems,
                                    parameter_stem)
        if valid:
            score = min(SCORE_CAP, 1.0 + SCORE_STEP * (valid - 1))
            scores.append((document, page, score))
    scores.sort(key=lambda item: -item[2])
    return scores[:max(limit, 0)]


def norm_search_fragments(store: Store, question: str, limit: int,
                          aircraft_type: str | None = None,
                          ) -> list[Fragment]:
    """Фрагменты канала норм (для RRF-слияния); фрагменты как обычный
    поиск (kind="text"), ``Fragment.score`` — скор канала (вес 2.0
    применяется в слиянии через ``ranking_weights``, не здесь)."""
    ok, parameter, objects = is_value_question(question)
    if not ok:
        return []
    candidates = norm_candidates(store, question, parameter, objects,
                                 limit, aircraft_type)
    parameter_stem = _PARAMETERS.get(parameter, "")
    fragments: list[Fragment] = []
    for document, page, score in candidates:
        text = store.page_text(document, page)
        if not text:
            continue
        # Сниппет — дословный срез исходника вокруг первого вхождения
        # основы параметра (канал — сигнал ранжирования; окно валидации
        # считается по нормализованному тексту и в сниппет не переносится).
        anchor = parameter_stem or (objects[0] if objects else "")
        match = re.search(re.escape(anchor), normalize_for_search(text)) \
            if anchor else None
        if match:
            start = max(0, match.start() - 60)
            snippet = text[start:start + 220]
            char_start = start
            char_end = start + len(snippet)
        else:
            snippet = text[:200]
            char_start, char_end = 0, len(snippet)
        system_code, subsystem_code = "", ""
        row = store._connection.execute(
            "SELECT system_code, subsystem_code FROM pages "
            "WHERE document = ? AND page = ?",
            (document, page)).fetchone()
        if row:
            system_code, subsystem_code = row[0], row[1]
        fragments.append(Fragment(
            document=document, page=page,
            system_code=system_code, subsystem_code=subsystem_code,
            snippet=snippet, char_start=char_start, char_end=char_end,
            score=score, kind="text",
        ))
    return fragments
