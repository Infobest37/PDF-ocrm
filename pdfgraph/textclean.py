"""Ф1.11: чистка поискового текста — заполнители строк и OCR-мусор.

Функция :func:`clean_search_text` принимает ДОСЛОВНЫЙ текст листа
(``pages.text``) и возвращает очищенную копию, из которой затем строится
поисковый индекс (``pages_fts.body`` через ``normalize_for_search``).
Сам ``pages.text`` никогда не перезаписывается: verify обязан
подтверждать цитаты символ в символ по исходному тексту.

Правила (карточка Ф1.11, уточнение владельца):
1. точечные заполнители: последовательности из 4+ точек вырезаются;
2. OCR-«гребёнка»: пробег из 8+ символов, все буквы которого — строчные
   из набора {н, е, м, и, ы}, вырезается целиком (ограничения на число
   уникальных букв нет; реальные слова из этих букв короче восьми —
   «менее» 5, «мнение»/«имение» 6);
3. единицы: «кгс/см?», «кгс/см2», «кго/см?», «кг/см?» приводятся к
   «кгс/см2»; «°С»/«°C» (кириллица/латиница) — к одной форме «°C»;
4. склейки: стык строчной и прописной кириллицы внутри токена
   («ДавлениеМасла») разбивается пробелом по границе регистра.

Правило чистит копию для индекса и никогда не применяется к полям
документа, цитатам и проверке цитат.

Ф1.17: :func:`clean_search_text_mapped` возвращает дополнительно карту
позиций — на каждый символ очищенного текста диапазон ``(start, end)``
в ИСХОДНОМ тексте, из которого он получен. Карта позволяет резать чанки
и сниппеты по очищенному тексту, а срезы брать дословно из исходного —
``pages.text``, цитаты и verify не трогаются.
"""

import re

#: OCR-«гребёнка»: 8+ строчных букв из набора {н, е, м, и, ы}.
_COMB_RE = re.compile(r"[неимы]{8,}")
#: Точечные заполнители строк: 4+ точки подряд.
_DOTS_RE = re.compile(r"\.{4,}")
#: Стык строчной и прописной кириллицы внутри токена.
_CASE_BOUNDARY_RE = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ])")
#: Единицы давления с OCR-мусором → каноничная форма «кгс/см2»;
#: хвостовой символ (?/*/”/2) поглощается — замена идемпотентна.
_PRESSURE_UNIT_RE = re.compile(r"\bкг(?:с|о)?\s*/\s*см\s*[?*”\"2]",
                               re.IGNORECASE)
#: Варианты знака градуса Цельсия → одна форма.
_CELSIUS_RE = re.compile(r"°\s*[CС]", re.IGNORECASE)


def clean_search_text(text: str) -> str:
    """Очищенная копия текста для поискового индекса (Ф1.11).

    Аргумент — дословный текст листа; результат — только для индекса
    (``pages_fts.body``), исходные данные не меняются. Идемпотентно:
    повторная чистка результата даёт тот же результат. Делегирует в
    :func:`clean_search_text_mapped` — строка байт-в-байт та же.
    """
    cleaned, _spans = clean_search_text_mapped(text)
    return cleaned


#: Схлопывание пробельных последовательностей — последний проход чистки.
_WHITESPACE_RE = re.compile(r"\s+")


def _mapped_sub(pattern: re.Pattern, repl: str, text: str,
                spans: list[tuple[int, int]]) -> tuple[str,
                                                       list[tuple[int, int]]]:
    """``re.sub`` с ведением карты: cleaned[i] → (start, end) в исходнике.

    ``spans`` параллелен ``text`` и всегда ссылается на ИСХОДНЫЙ текст
    листа. Символы замены отображаются на весь диапазон совпадения
    (многие-к-одному — при чтении используются только границы).
    Нулевая ширина совпадения (стык регистра Ф1.11) даёт синтетический
    символ с пустым диапазоном в точке стыка исходника.
    """
    out: list[str] = []
    out_spans: list[tuple[int, int]] = []
    pos = 0
    for match in pattern.finditer(text):
        start, end = match.start(), match.end()
        if start > pos:
            out.append(text[pos:start])
            out_spans.extend(spans[pos:start])
        if start == end:  # нулевая ширина: точка между символами исходника
            out_spans.append((spans[start][0], spans[start][0])
                             if start < len(spans) else (len(text), len(text)))
        else:
            out_spans.extend((spans[start][0], spans[end - 1][1])
                             for _ in repl)
        out.append(repl)
        pos = end
    out.append(text[pos:])
    out_spans.extend(spans[pos:])
    return "".join(out), out_spans


def clean_search_text_mapped(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Очищенная копия текста и карта позиций в исходник (Ф1.17).

    Возвращает ``(cleaned, spans)``: ``spans[i]`` — диапазон ``(start, end)``
    в исходном тексте, породивший ``cleaned[i]``. Правила чистки — те же,
    что в :func:`clean_search_text` (Ф1.11), строка результата совпадает
    байт-в-байт. Срез ``text[spans[i][0]:spans[j][1]]`` — дословный фрагмент
    исходника, соответствующий ``cleaned[i:j + 1]``. Идемпотентно.
    """
    cleaned = text
    spans: list[tuple[int, int]] = [(i, i + 1) for i in range(len(text))]
    cleaned, spans = _mapped_sub(_PRESSURE_UNIT_RE, "кгс/см2", cleaned, spans)
    cleaned, spans = _mapped_sub(_CELSIUS_RE, "°C", cleaned, spans)
    cleaned, spans = _mapped_sub(_CASE_BOUNDARY_RE, " ", cleaned, spans)
    cleaned, spans = _mapped_sub(_COMB_RE, " ", cleaned, spans)
    cleaned, spans = _mapped_sub(_DOTS_RE, " ", cleaned, spans)
    cleaned, spans = _mapped_sub(_WHITESPACE_RE, " ", cleaned, spans)
    # Тот же .strip(), что в clean_search_text: края и их диапазоны срезаются.
    while cleaned.startswith(" "):
        cleaned = cleaned[1:]
        spans = spans[1:]
    while cleaned.endswith(" "):
        cleaned = cleaned[:-1]
        spans = spans[:-1]
    return cleaned, spans
