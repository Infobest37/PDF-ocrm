# -*- coding: utf-8 -*-
"""Ф1.3б: гейты отказа по конкретике вопроса.

Факт Ф1.3а (раздел 10 .clinerules): сигналы близости текста
(idf_coverage, редкий термин, type_match, ata_match, max_cosine)
классы полигона не разделяют — негативы отвечают соседними листами с
подтверждёнными цитатами. Гейты Ф1.3б проверяют не близость, а
конкретику: есть ли на листе-лидере выдачи те числа, обозначения и тип
техники, о которых спросили.

Четыре гейта (:data:`GATE_NAMES`), каждый — отдельная функция:
  ``numbers``       — значимые числа вопроса (вне обозначений техники)
                      должны быть на листе-лидере;
  ``designators``   — обозначения агрегатов, систем и числовых кодов
                      разделов из вопроса должны быть на лидере или в
                      окне ±1 лист того же документа;
  ``aircraft_type`` — названный в вопросе тип ВС обязан совпадать с
                      типом листа-лидера;
  ``rare_term``     — термин вопроса с df ниже :data:`RARE_DF_THRESHOLD`
                      обязан быть на листе-лидере.

Вердикты гейтов: ``"fail"`` — провален; ``"pass"`` — пройден; ``"na"``
— не применим (в вопросе нет такой конкретики или проверять не на чем).

Отказ — конъюнкция включённых гейтов (:data:`ENABLED_GATES`): провален
хотя бы один включённый — отказ с сообщением, какой именно гейт и
почему (:func:`decide_refusal`). Гейты не влияют на поиск, слияние и
проверку цитат (:func:`pdfgraph.verify.verify_citations` работает как
раньше); решение принимается после того, как выдача собрана.

Замер по полигону карточки Ф1.3б (23 видимых корпусных + 20 негативов)
— ``tools/gates_polygon_f13b.py``. Порогов на скорости текста нет:
гейты только о конкретике, без ``if score < X``.
"""

from __future__ import annotations

import logging
import re

from pdfprepare.normalize import normalize_for_search
from pdfgraph.store import Store

logger = logging.getLogger(__name__)

#: Вердикты гейтов.
VERDICT_FAIL = "fail"
VERDICT_PASS = "pass"
VERDICT_NA = "na"
#: Парный тип вердикта: (вердикт, пояснение).
GateVerdict = tuple[str, str]

#: Все гейты в фиксированном порядке (устойчивый вывод замера).
GATE_NAMES: tuple[str, ...] = (
    "numbers", "designators", "aircraft_type", "rare_term",
)

#: Порог «редкого термина» — согласован с замером Ф1.3а
#: (tools/refusal_signals.py, RARE_DF_THRESHOLD = 1000).
RARE_DF_THRESHOLD = 1000
#: Минимальная длина значимого термина после нормализации поиска.
MIN_TERM_LEN = 3

#: Включённые гейты (конъюнкция отказа). Состав — по замеру полигона
#: Ф1.3б 2026-10-31 (tools/gates_polygon_f13b.py, отчёт
#: reports/gates_polygon_f13b.md): включены только гейты без задетых
#: видимых корпусных — numbers (3/20 негативов, 0 корпусных) и
#: aircraft_type (7/20, 0 корпусных). designators не включён: задевает
#: e04 и e06 (промахи ранжирования без обозначения на лиде ±1);
#: rare_term не включён: задевает 17 корпусных из 23. Приёмка карточки
#: в этой конфигурации: refusal_recall 0.50 при требуемых ≥ 0.80 —
#: честно в отчёт (достигать 0.80 с этими гейтами ценой корпусных
#: ложных нельзя).
ENABLED_GATES: tuple[str, ...] = ("numbers", "aircraft_type")
# --- Разбор вопроса: обозначения, числа, типы, термины -------------------

#: Токен текста: слитная цепочка букв и цифр с разделителями «-», «.», «/»
#: («ТВ3-117», «Ми-8МТВ-1», «065.50», «78.00.5700», «118.00-F9», «ГТ40ПЧ8»).
_TOKEN_RE = re.compile(r"[а-яёА-ЯЁa-zA-Z0-9]+(?:[-./][а-яёА-ЯЁa-zA-Z0-9]+)*")
_LETTER_RE = re.compile(r"[а-яёА-ЯЁa-zA-Z]")
_DIGIT_RE = re.compile(r"\d")
#: Числовые коды разделов из многогрупповых чисел: «065.50», «118.00».
_DOTTED_CODE_RE = re.compile(r"\d+(?:[.]\d+)+")
#: Числа: целые и десятичные (запятая и точка эквивалентны).
_NUMBER_RE = re.compile(r"\d+(?:[.]\d+)?")
#: Типы ВС в обозначениях: «Ми-8», «Ту-154», «Ка-32», «МиГ-29»; только
#: через дефис (вопросы пишут типы с дефисом; свободная форма «ан 2»
#: ловит обычные слова, поэтому не поддерживается сознательно).
_AIRCRAFT_TYPE_RE = re.compile(
    r"(?<![а-яёa-z])(миг|ми|ка|ан|ту|ил|як|су)-(\d{1,3})", re.IGNORECASE)
#: Стоп-слова вопроса — копия списка Ф1.3а (tools/refusal_signals.py).
_STOP_WORDS = frozenset({
    "какой", "какая", "какое", "каких", "каким", "какую", "какие",
    "каким", "чтобы", "для", "при", "все", "это", "что", "как", "чем",
    "какова", "каков", "сколько", "есть", "нет", "или", "либо", "если",
    "должно", "должен", "допускается", "допустимо", "максимальное",
    "минимальное", "предел", "пределах",
})


def is_designator(token: str) -> bool:
    """Токен — обозначение техники, агрегата или числовой код раздела?

    Обозначение — токен, где буквы и цифры вместе («ТВ3-117»,
    «Ми-8МТВ-1», «КАУ-115АМ», «АИ-9В», «ГТ40ПЧ8»), либо многогрупповой
    числовой код («065.50», «118.00», «78.00.5700»). Десятичные
    значения («0,3», «3,5») обозначениями не считаются: меньше четырёх
    цифр суммарно — это величина, не код.
    """
    has_letter = _LETTER_RE.search(token) is not None
    has_digit = _DIGIT_RE.search(token) is not None
    if has_letter and has_digit:
        return True
    if has_letter:
        return False
    dotted = _DOTTED_CODE_RE.fullmatch(token)
    if dotted:
        digits = token.replace(".", "")
        return len(digits) >= 4
    return False


def question_designators(question: str) -> list[str]:
    """Обозначения вопроса в порядке появления, без повторов.

    Числовые коды разделов («065.50») и обозначения техники/агрегатов
    («ТВ3-117») сюда входят; простые десятичные значения — нет (это
    числа, их проверяет гейт :func:`gate_numbers`).
    """
    found: list[str] = []
    for token in _TOKEN_RE.findall(question):
        if token in found:
            continue
        if is_designator(token):
            found.append(token)
    return found


def question_significant_numbers(question: str) -> list[str]:
    """Значимые числа вопроса: числа вне обозначений.

    Из вопроса вырезаются обозначения — «8» в «Ми-8МТВ-1», «117» в
    «ТВ3-117», «115» в «КАУ-115АМ» не считаются значимыми (иначе
    числовой гейт пройден всегда и бесполезен); запятая приводится к
    точке («0,3» → «0.3»).
    """
    leftovers = question
    for token in question_designators(question):
        leftovers = leftovers.replace(token, " ")
    normalized = leftovers.replace(",", ".")
    numbers: list[str] = []
    for number in _NUMBER_RE.findall(normalized):
        if number not in numbers:
            numbers.append(number)
    return numbers


def question_aircraft_types(question: str) -> list[str]:
    """Названные в вопросе типы ВС («Ту-154» → «ту-154»), без повторов."""
    found: list[str] = []
    for family, number in _AIRCRAFT_TYPE_RE.findall(question):
        text = f"{family.lower()}-{number}"
        if text not in found:
            found.append(text)
    return found


def question_terms(question: str) -> list[str]:
    """Значимые термины вопроса — как их видит FTS-канал поиска.

    Согласовано с замером Ф1.3а (tools/refusal_signals.question_terms):
    слова после :func:`pdfprepare.normalize.normalize_for_search`, без
    стоп-слов и слов короче :data:`MIN_TERM_LEN`.
    """
    words = re.findall(r"[а-яёa-z]+", question.lower())
    unique: list[str] = []
    for word in words:
        if word in _STOP_WORDS or len(word) < MIN_TERM_LEN:
            continue
        stem = normalize_for_search(word)
        if stem and stem not in unique:
            unique.append(stem)
    return unique
# --- Поиск конкретики в тексте -------------------------------------------

def _number_in_text(text: str, number: str) -> bool:
    """Число на границе цифр в тексте (запятая эквивалентна точке).

    Граница цифр: «115» не считается вхождением «1153» — частичное
    совпадение внутри более длинного числа вхождением не считается.
    """
    if not text:
        return False
    pattern = r"(?<!\d)" + re.escape(number) + r"(?!\d)"
    for variant in (text, text.replace(",", ".")):
        if re.search(pattern, variant):
            return True
    return False


def _designator_in_text(text: str, designator: str) -> bool:
    """Обозначение или его нормализованная форма в тексте.

    Три формы на всякую запись листа: дословно без учёта регистра
    («ТВ3-117ПМ» содержит «ТВ3-117»), с вычищенными разделителями и
    через нормализацию поиска.
    """
    if not text or not designator:
        return False
    lowered = text.lower()
    key = designator.lower()
    if key in lowered:
        return True
    squeezed_text = re.sub(r"[-./\s]", "", lowered)
    squeezed_key = re.sub(r"[-./\s]", "", key)
    if squeezed_key and squeezed_key in squeezed_text:
        return True
    return normalize_for_search(designator) in normalize_for_search(text)


def _contains_term(haystack: str, term: str) -> bool:
    """Нормализованный термин в нормализованном тексте (как в Ф1.3а)."""
    return term in normalize_for_search(haystack)


def _aircraft_type_key(text: str) -> tuple[str, str] | None:
    """Разбор типа ВС: «Ми-8МТВ» → («ми», «8»); неразобранное — None."""
    match = _AIRCRAFT_TYPE_RE.search(text)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


# --- df по БД (согласовано с Ф1.3а) --------------------------------------

#: Кэш df в рамках одного Store: {термин: df}.
_DF_CACHE: dict[int, dict[str, int]] = {}


def document_frequency(store: Store, term: str) -> int:
    """df термина: на скольких досягаемых листах встречается (FTS, body)."""
    cache = _DF_CACHE.setdefault(id(store), {})
    if term not in cache:
        cursor = store._connection.execute(
            "SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH ?",
            (f'body: "{term}"',))
        cache[term] = int(cursor.fetchone()[0])
    return cache[term]


# --- Данные листа-лидера -------------------------------------------------

def leader_page_text(store: Store, leader: object) -> str:
    """Текст листа-лидера; пустая строка — если лидера или листа нет."""
    document = getattr(leader, "document", "")
    page = getattr(leader, "page", None)
    if not document or page is None:
        return ""
    return store.page_text(str(document), int(page))


def leader_aircraft_type(store: Store, leader: object) -> str | None:
    """Тип ВС листа-лидера (pages.aircraft_type); None — не задан.

    У подделок Store в тестах таблицы нет — тип вернётся «na» (лог о
    деградации пишется; у настоящего Store метод всегда доступен).
    """
    document = getattr(leader, "document", "")
    page = getattr(leader, "page", None)
    if not document or page is None:
        return None
    connection = getattr(store, "_connection", None)
    if connection is None:
        logger.debug("gates: store без таблицы страниц — тип лидера «na»")
        return None
    row = connection.execute(
        "SELECT aircraft_type FROM pages WHERE document = ? AND page = ?",
        (str(document), int(page))).fetchone()
    if row is None or not str(row[0] or "").strip():
        return None
    return str(row[0])


# --- Четыре гейта --------------------------------------------------------

def gate_numbers(question: str, leader_text: str) -> GateVerdict:
    """Числовой гейт: значимые числа вопроса должны быть на лидере."""
    numbers = question_significant_numbers(question)
    if not numbers:
        return VERDICT_NA, "значимых чисел нет"
    if not leader_text:
        return VERDICT_NA, "текст лида недоступен"
    missing = [
        number for number in numbers
        if not _number_in_text(leader_text, number)]
    if missing:
        return VERDICT_FAIL, (
            f"числа {', '.join(missing)} отсутствуют на листе-лидере")
    return VERDICT_PASS, f"числа {', '.join(numbers)} есть на лидере"


def gate_designators(
        question: str, leader_text: str, window: list[str] | None = None
) -> GateVerdict:
    """Гейт обозначений: обозначение обязано быть на лиде или в окне ±1."""
    designators = question_designators(question)
    if not designators:
        return VERDICT_NA, "обозначений нет"
    places = [leader_text, *(window or [])]
    if not any(place for place in places):
        return VERDICT_NA, "текст лида и окна недоступен"
    missing = [
        designator for designator in designators
        if not any(_designator_in_text(place, designator)
                   for place in places)]
    if missing:
        return VERDICT_FAIL, (
            f"обозначения {', '.join(missing)} "
            "не найдены на лидере и в окне ±1 лист")
    return VERDICT_PASS, (
        f"обозначения {', '.join(designators)} найдены")


def gate_aircraft_type(
        question: str, leader_type: str | None
) -> GateVerdict:
    """Гейт типа ВС: чужой для листа тип в вопросе — провал."""
    question_types = question_aircraft_types(question)
    if not question_types:
        return VERDICT_NA, "тип ВС в вопросе не назван"
    if not leader_type:
        return VERDICT_NA, "у листа-лидера тип не задан"
    leader_key = _aircraft_type_key(leader_type)
    if leader_key is None:
        return VERDICT_NA, f"тип лида {leader_type!r} нераспознан"
    mismatched = [
        text for text in question_types
        if _aircraft_type_key(text) != leader_key]
    if mismatched:
        return VERDICT_FAIL, (
            f"тип ВС {', '.join(mismatched)} в вопросе, "
            f"на лидере тип {leader_type}")
    return VERDICT_PASS, f"тип ВС {leader_type} совпадает"


def gate_rare_term(
        store: Store, question: str, leader_text: str
) -> GateVerdict:
    """Гейт редкого термина: термин с df ниже порога обязан быть на лиде.

    Строгая форма замера: каждый редкий термин вопроса (df ниже
    порога) обязан быть на листе-лидере; редких нет — «na». Ошибка
    FTS не глушится: она означает невалидную среду, а не «пройдено».
    """
    term_ids = question_terms(question)
    if not leader_text:
        return VERDICT_NA, "текст лида недоступен"
    rare: list[tuple[str, int]] = []
    for term in term_ids:
        df = document_frequency(store, term)
        if df < RARE_DF_THRESHOLD:
            rare.append((term, df))
    if not rare:
        return VERDICT_NA, "редких терминов нет"
    missing = [
        f"{term}(df={df})" for term, df in rare
        if not _contains_term(leader_text, term)]
    if missing:
        return VERDICT_FAIL, (
            f"редкие термины {', '.join(missing)} "
            f"отсутствуют на листе-лидере (df < {RARE_DF_THRESHOLD})")
    return VERDICT_PASS, (
        "все редкие термины на лидере: "
        + ", ".join(f"{term}(df={df})" for term, df in rare))


def run_all_gates(store: Store, question: str, leader: object
                  ) -> dict[str, GateVerdict]:
    """Все четыре гейта на листе-лидере (для полигона и тестов)."""
    text = leader_page_text(store, leader)
    window = window_texts(store, leader)
    return {
        "numbers": gate_numbers(question, text),
        "designators": gate_designators(question, text, window),
        "aircraft_type": gate_aircraft_type(
            question, leader_aircraft_type(store, leader)),
        "rare_term": gate_rare_term(store, question, text),
    }


def decide_refusal(
        store: Store, question: str, fragments: list,
        enabled: tuple | None = None) -> list[dict]:
    """Конъюнкция включённых гейтов; список проваленных включённых.

    Лидер — первый фрагмент выдачи; пустая выдача не проверяется
    (там уже штатный отказ «нет подтверждений в документах»). Каждый
    провал — ``{"gate": имя, "detail": пояснение}``; пустой список
    означает, что отказывать не нужно.
    """
    enabled = tuple(ENABLED_GATES) if enabled is None else tuple(enabled)
    enabled = [name for name in GATE_NAMES if name in enabled]
    if not enabled or not fragments:
        return []
    verdicts = run_all_gates(store, question, fragments[0])
    failures: list[dict] = []
    for name in enabled:
        verdict, detail = verdicts[name]
        if verdict == VERDICT_FAIL:
            failures.append({"gate": name, "detail": detail})
    return failures


__all__ = [
    "GATE_NAMES", "ENABLED_GATES", "RARE_DF_THRESHOLD", "MIN_TERM_LEN",
    "VERDICT_FAIL", "VERDICT_PASS", "VERDICT_NA", "GateVerdict",
    "is_designator", "question_designators", "question_significant_numbers",
    "question_aircraft_types", "question_terms", "document_frequency",
    "leader_page_text", "leader_aircraft_type", "window_texts",
    "gate_numbers", "gate_designators", "gate_aircraft_type",
    "gate_rare_term", "run_all_gates", "decide_refusal",
]
def window_texts(store: Store, leader: object) -> list[str]:
    """Тексты соседних листов того же документа (лидер.page ± 1)."""
    document = getattr(leader, "document", "")
    page = getattr(leader, "page", None)
    if not document or page is None:
        return []
    texts: list[str] = []
    for shift in (-1, 1):
        text = store.page_text(str(document), int(page) + shift)
        if text:
            texts.append(text)
    return texts