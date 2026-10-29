"""Page-level quality flags and the re-scan report.

After OCR, pages are inspected for two defects that justify a manual re-scan:

* **low confidence** — the mean per-word OCR confidence is below ``MIN_CONF``;
* **low text coverage** — the area covered by recognized words is a tiny
  fraction of the page (below ``MIN_COVERAGE``), usually meaning the page is
  mostly whitespace or the OCR found almost nothing.

Only non-blank, non-native pages are considered: native pages already carry
their text, and blank pages are expected to have none. At most one flag is
emitted per page, with confidence checked first (see the binding ruling).
"""

import re

from pdfprepare.models import DocResult, QualityFlag

#: Mean word confidence below which a page is flagged for re-scan (0..1).
MIN_CONF = 0.60
#: Minimum word-area ÷ page-area ratio before a page is flagged (0..1).
MIN_COVERAGE = 0.02

#: Fallback page area (points) used when a page has no words to bound it.
_DEFAULT_PAGE_AREA = 612.0 * 792.0


def flag_pages(result: DocResult) -> list[QualityFlag]:
    """Return one :class:`QualityFlag` per suspicious page in ``result``.

    For each non-blank, non-native page, ``avg_conf`` is the mean word
    confidence and ``text_coverage`` is the summed word bounding-box area
    divided by the area of the words' union bounding box (or ``612*792`` when
    the page has no words). A page with no words gets ``avg_conf = 0.0`` and
    ``text_coverage = 0.0``.

    Confidence is checked first: a page with both low confidence and low
    coverage emits a single flag with reason ``"low_confidence"``; otherwise a
    low-coverage page emits ``"low_text_coverage"``.
    """
    flags: list[QualityFlag] = []
    for page in result.pages:
        if page.is_blank or page.is_native_text:
            continue

        words = page.words
        if words:
            avg_conf = sum(w.conf for w in words) / len(words)

            min_x = min(w.x0 for w in words)
            min_y = min(w.y0 for w in words)
            max_x = max(w.x1 for w in words)
            max_y = max(w.y1 for w in words)
            page_area = (max_x - min_x) * (max_y - min_y)
            if page_area <= 0:
                page_area = _DEFAULT_PAGE_AREA

            word_area = sum((w.x1 - w.x0) * (w.y1 - w.y0) for w in words)
            text_coverage = word_area / page_area
        else:
            avg_conf = 0.0
            text_coverage = 0.0

        if avg_conf < MIN_CONF:
            flags.append(
                QualityFlag(page.page_index, "low_confidence", avg_conf, text_coverage)
            )
        elif text_coverage < MIN_COVERAGE:
            flags.append(
                QualityFlag(page.page_index, "low_text_coverage", avg_conf, text_coverage)
            )

    return flags


def write_report(flags: list[QualityFlag], out_path: str) -> None:
    """Write ``flags`` to ``out_path`` as UTF-8 text, one line per flag.

    Each line has the form ``page {page_index}: {reason} (conf={avg_conf:.2f},
    coverage={text_coverage:.4f})``.
    """
    with open(out_path, "w", encoding="utf-8") as f:
        for flag in flags:
            f.write(
                f"page {flag.page_index}: {flag.reason} "
                f"(conf={flag.avg_conf:.2f}, coverage={flag.text_coverage:.4f})\n"
            )


# ---------------------------------------------------------------------------
# Пометки листов 0.2 (пересмотр от 29.10.2026): два независимых флага по
# сохранённому тексту листа. Разметка владельца (30 листов) показала, что
# чужого контента в базе нет: bad sheets — либо свой лист с вырожденным
# OCR (needs_rescan), либо служебная страница (is_service).
# ---------------------------------------------------------------------------

#: Токен признаков: буквы (рус./лат.) и цифры.
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
#: R1 «обрывочный лист»: минимум токенов для применения порогов.
_RESCAN_MIN_TOKENS = 20
#: R1: доля буквенных токенов из 1–2 символов среди буквенных токенов.
_RESCAN_FRAG = 0.85
#: R1: доля однобуквенных токенов среди всех токенов.
_RESCAN_SINGLE = 0.36
#: R2 «вырожденный короткий лист» — верхняя граница числа токенов.
_RESCAN_SHORT_MAX_TOKENS = 15
#: R2: максимум длинных (3+ букв) буквенных токенов.
_RESCAN_SHORT_MAX_LONG = 5
#: R2: доля обрывков среди буквенных токенов.
_RESCAN_SHORT_FRAG = 0.60
#: Титул каталога — служебный флаг при таком числе токенов и подстроке ниже.
_SERVICE_TITLE_MAX_TOKENS = 30
#: Заглушка преднамеренно пустой страницы.
_BLANK_STUB_RE = re.compile(
    r"преднамеренно\s+оставлена\s+пустой|intentionally\s+left\s+blank",
    re.IGNORECASE,
)
#: Разделители-заголовки (сравнение после strip/lower/снятия BOM).
_DIVIDER_HEADINGS = frozenset(
    {"приложения", "приложение", "содержание", "оглавление"})


def rescan_features(text: str) -> dict[str, float]:
    """Текстовые признаки нечитаемости по сохранённому (сырому) тексту.

    ``tokens`` — все буквенно-цифровые токены; ``long_words`` — буквенные
    токены из 3+ символов; ``frag_share`` — доля буквенных токенов из 1–2
    символов среди буквенных токенов; ``single_share`` — доля
    однобуквенных токенов среди всех токенов. Числа округляются до 0.01 —
    те же значения, что в калибровке 29.10.
    """
    tokens = _TOKEN_RE.findall(text)
    letter_tokens = [t for t in tokens if any(c.isalpha() for c in t)]
    short_alpha = [t for t in letter_tokens if len(t) <= 2]
    long_alpha = [t for t in letter_tokens if len(t) >= 3]
    single = [t for t in tokens if len(t) == 1 and t.isalpha()]
    if not tokens:
        return {"tokens": 0, "long_words": 0, "frag_share": 0.0,
                "single_share": 0.0}
    return {
        "tokens": len(tokens),
        "long_words": len(long_alpha),
        "frag_share": round(len(short_alpha) / max(1, len(letter_tokens)), 2),
        "single_share": round(len(single) / len(tokens), 2),
    }


def rescan_component(text: str) -> tuple[str, float] | None:
    """Признак ``needs_rescan`` по сохранённому тексту листа.

    Возвращает ``(причина, score)`` или ``None``. Правила из калибровки:
    R1 «обрывочный лист» — длинный текст (>=``_RESCAN_MIN_TOKENS`` токенов),
    где почти всё — обрывки из 1–2 букв (frag >=``_RESCAN_FRAG`` либо доля
    однобуквенных >=``_RESCAN_SINGLE``); R2 «вырожденный короткий лист» —
    мало токенов (1..``_RESCAN_SHORT_MAX_TOKENS``), среди буквенных почти
    нет длинных слов и доля обрывков >=``_RESCAN_SHORT_FRAG``. Score —
    мера выраженности признака (max(frag, single) / frag для R1/R2).
    Осмысленный текст, артикулы-латиница и слово «SAFIR» не ловятся.
    """
    features = rescan_features(text)
    tokens = features["tokens"]
    frag = features["frag_share"]
    single = features["single_share"]
    if tokens >= _RESCAN_MIN_TOKENS and (
            frag >= _RESCAN_FRAG or single >= _RESCAN_SINGLE):
        return "обрывочный лист", max(frag, single)
    if (1 <= tokens <= _RESCAN_SHORT_MAX_TOKENS
            and features["long_words"] <= _RESCAN_SHORT_MAX_LONG
            and frag >= _RESCAN_SHORT_FRAG):
        return "вырожденный короткий лист", frag
    return None


def service_component(text: str, is_blank: bool = False) -> tuple[str, float]:
    """Признак ``is_service`` по сохранённому тексту листа; score = 1.0.

    Возвращает ``(причина, 1.0)`` или ``None``. Причины: «пустой лист»
    (``is_blank`` или нет токенов), «заглушка <страница оставлена
    пустой>», «разделитель-заголовок» (текст целиком — ПРИЛОЖЕНИЯ/
    ПРИЛОЖЕНИЕ/СОДЕРЖАНИЕ/ОГЛАВЛЕНИЕ, BOM и пробелы по краям игнорируются),
    «титул каталога» (<=``_SERVICE_TITLE_MAX_TOKENS`` токенов и подстрока
    «дополнение к каталогу»). Штампы копий и «Изменение №» НЕ являются
    признаками: они стоят и на содержательных листах
    (решение владельца по разметке 29.10).
    """
    tokens = _TOKEN_RE.findall(text)
    if is_blank or not tokens:
        return "пустой лист", 1.0
    if _BLANK_STUB_RE.search(text):
        return "заглушка «страница оставлена пустой»", 1.0
    normalized = text.strip().lstrip("﻿").strip().lower()
    if normalized in _DIVIDER_HEADINGS:
        return "разделитель-заголовок", 1.0
    if (len(tokens) <= _SERVICE_TITLE_MAX_TOKENS
            and "дополнение к каталогу" in normalized):
        return "титул каталога", 1.0
    return None
