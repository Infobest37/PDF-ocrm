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

Ф1.4 добавляет третий исход — ``clarify`` (:func:`decide_outcome`): на
провале гейтов конкретики, если тема найдена (вариантов не меньше
:data:`CLARIFY_MIN_OPTIONS`) и вопрос не обзорный. Правка Ф1.4.1
(решение владельца): уточнение по форме вопроса убрано — замер полигона
Ф1.4 показал, что маркеры формы («как», «почему», «сколько»...) не
отличают полный вопрос от неполного; уточнение по смыслу — задача слоя
с моделью, а не детерминированных правил (раздел 11 .clinerules).

Ф1.5.1 (решение владельца): на провале гейтов конкретики, когда
обозначение из вопроса не встречается ни на одном листе корпуса
(:func:`designator_in_corpus`), исход — отказ («обозначение …
в документации не встречается»), а не уточнение; обозначение, которое
в корпусе есть, но не рядом с листом-лидером, остаётся уточнением.
Числа правила не затрагивают.

Ф1.5.2 (решение владельца): тип ВС в вопросе проверяется по метке
листа, а не по вхождению строки: если в базе нет ни одного листа,
где названный тип — основной меткой aircraft_type
(:func:`aircraft_types_without_primary`, :func:`primary_aircraft_type_keys`),
исход — отказ «документации по типу … в базе нет». Упоминание типа в
тексте чужого документа (примечание, сравнение) не основание ни
отказа, ни уточнения — типы исключены из строковой проверки
:func:`corpus_absent_designators`. Правило обозначений агрегатов
(Ф1.5.1) сохранено.

Замер по полигону карточки Ф1.3б (23 видимых корпусных + 20 негативов)
— ``tools/gates_polygon_f13b.py``. Порогов на скорости текста нет:
гейты только о конкретике, без ``if score < X``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

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

#: Включённые гейты (конъюнкция отказа). База — замер полигона
#: Ф1.3б 2026-10-31 (tools/gates_polygon_f13b.py, отчёт
#: reports/gates_polygon_f13b.md): numbers (3/20 негативов, 0 корпусных)
#: и aircraft_type (7/20, 0 корпусных) — без задетых видимых корпусных.
#: designators добавлен решением владельца после приёмки (Ф1.3б.1):
#: два задетых (e04, e06) — промахи ранжирования, правильного листа в
#: топ-5 нет, обозначения на лиде ±1 действительно отсутствуют, гейт
#: отказал верно; refusal_false_on_found (отказ при найденном правильном
#: листе) = 0.00. rare_term не включён: задевает 17 корпусных из 23 —
#: рубящий корпусные гейт. Приёмка Ф1.3б.1 (замер отчёта
#: gates_polygon_f13b_{,acceptance}): refusal_recall 0.70 (14/20),
#: требование карточки 0.80 не достигнуто и не подгоняется — остаток
#: составляют вопросы без конкретики (p11 и подобные): это потолок
#: детерминированных гейтов, предпосылка для опционального слоя
#: «модель-верификатор» (отдельное решение владельца).
ENABLED_GATES: tuple[str, ...] = ("numbers", "designators", "aircraft_type")
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


# --- Ф1.9в: двойники суффикса номера карты ----------------------------------

#: Номер карты с суффиксом в обозначении: NNN[.\s]?NN[.\s]?NN + буква
#: (+ необязательный индекс 1, Ф2.8). «033.10.00а» и «033.10.00а1» —
#: разные ключи.
_TECHCARD_DESIGNATOR_RE = re.compile(
    r"^(\d{3})[.\s]?(\d{2})[.\s]?(\d{2})\s*([a-zа-яё])([1;₁])?$",
    re.IGNORECASE)
#: Кириллические двойники букв суффикса карты (Ф1.9в). Применяются ТОЛЬКО
#: в проверке суффикса карты: агрегатные обозначения (ВР-14, Д-1638) и
#: числа гейта numbers не затронуты.
_SUFFIX_HOMOGLYPHS = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
                      "х": "x", "у": "y", "к": "k", "м": "m", "т": "t",
                      "в": "b", "н": "h"}
#: Индекс после буквы суффикса (Ф2.8): цифра 1, нижний индекс ₁ и его
#: OCR-варианты «;» и «:» (замер ДО правки: в корпусе 103 вхождения
#: «NNN.NN.NN + буква + ;», индекс ₁ NFKC-нормализацией тела даёт «1»).
_CARD_INDEX_CHARS = "1;₁:"
#: Строки зоны заголовка карты (Ф2.8, решение владельца): «Пункт РО»
#: (включая слитное OCR «пунктро» и латинское «PO») и «ТЕХНОЛОГИЧЕСКАЯ
#: КАРТА». «;»/«:» после буквы признаётся индексом только в этой зоне,
#: вне её — пунктуация («см. карту 033.10.00 а; далее…»).
_CARD_HEADER_RE = re.compile(r"пункт\s*[рp][оo]|технологическая\s+карта",
                             re.IGNORECASE)
#: Окно зоны заголовка: ±N строк от строки-заголовка.
CARD_HEADER_WINDOW = 6
#: Вхождение карты в текст листа: база + буква (+ необязательный индекс).
#: Без индекса буква не продолжается в букву/цифру/индекс (иначе
#: «035.10.00 кислородное» считалось бы картой «…к», а «033.10.00 a;» —
#: базовой «…а»); с индексом — ровно один символ индекса.
_CARD_IN_TEXT_RE = re.compile(
    r"(?<![\da-zа-яё.])(\d{3})[.\s]*(\d{2})[.\s]*(\d{2})[.\s]*"
    rf"([a-zа-яё])(?:([{re.escape(_CARD_INDEX_CHARS)}])(?![a-zа-яё\d])"
    rf"|(?![a-zа-яё\d{re.escape(_CARD_INDEX_CHARS)}]))",
    re.IGNORECASE)


def _card_header_lines(text: str) -> list[int]:
    """Номера строк зоны заголовка карты («Пункт РО», «ТЕХНОЛОГИЧЕСКАЯ КАРТА»)."""
    return [number for number, line in enumerate(text.splitlines())
            if _CARD_HEADER_RE.search(line)]


def _index_in_header_zone(text: str, position: int,
                          headers: list[int]) -> bool:
    """«;»/«:» после буквы — индекс только в зоне заголовка (Ф2.8, п. 2)."""
    line_no = text.count("\n", 0, position)
    return any(abs(line_no - number) <= CARD_HEADER_WINDOW
               for number in headers)


def card_suffix_occurrences(text: str) -> list[tuple[str, str, int]]:
    """Карты «NNN.NN.NN + буква (+ индекс)» в тексте листа (Ф1.9в, Ф2.8).

    Возвращает (база «NNN.NN.NN», суффикс, позиция буквы) без повторов:
    суффикс — каноническая латинская буква (двойники приравнены) с
    индексом «1» или без. Точное совпадение суффикса: базовая буква не
    матчит карту-индекс и наоборот («033.10.00а» ≠ «033.10.00а1»).
    «;»/«:» считается индексом 1 только в зоне заголовка карты
    (±:data:`CARD_HEADER_WINDOW` строк от «Пункт РО»/«ТЕХНОЛОГИЧЕСКАЯ
    КАРТА»), вне зоны — пунктуация, вхождение пропускается. Границы:
    перед базой не цифра/буква/точка («1035.10.00e» не совпадает).
    """
    headers = _card_header_lines(text)
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for match in _CARD_IN_TEXT_RE.finditer(text):
        d1, d2, d3, letter, index = match.groups()
        letter = _SUFFIX_HOMOGLYPHS.get(letter.lower(), letter.lower())
        if index is None:
            suffix = letter
        else:
            if index not in "1₁" and not _index_in_header_zone(
                    text, match.start(4), headers):
                continue
            suffix = f"{letter}1"
        key = (f"{d1}.{d2}.{d3}", suffix)
        if key in seen:
            continue
        seen.add(key)
        found.append((*key, match.start(4)))
    return found


def _card_suffix_in_text(lowered: str, designator: str) -> bool:
    """Точное совпадение суффикса карты (Ф1.9в, Ф2.8).

    Только для обозначений-номеров карт «NNN.NN.NN + буква (+ индекс)»:
    суффикс — латинская буква или любой её кириллический двойник, индекс
    (если запрошен) обязан быть в тексте. Запрос базовой буквы не
    совпадает с картой-индексом и наоборот; суффикс не продолжается в
    слово («035.10.00 кислородное» — не карта «…к»).
    """
    match = _TECHCARD_DESIGNATOR_RE.fullmatch(designator.strip())
    if match is None:
        return False
    d1, d2, d3, suffix, index = match.groups()
    suffix = _SUFFIX_HOMOGLYPHS.get(suffix.lower(), suffix.lower())
    wanted = f"{suffix}1" if index else suffix
    base = f"{d1}.{d2}.{d3}"
    return any(found_base == base and found_suffix == wanted
               for found_base, found_suffix, _position
               in card_suffix_occurrences(lowered))


def _designator_in_text(text: str, designator: str) -> bool:
    """Обозначение или его нормализованная форма в тексте.

    Формы на всякую запись листа: дословно без учёта регистра
    («ТВ3-117ПМ» содержит «ТВ3-117»), с вычищенными разделителями,
    через нормализацию поиска и — только для номеров карт с суффиксом —
    с приравниванием кириллических двойников суффикса (Ф1.9в).
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
    if _card_suffix_in_text(lowered, designator):
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


# --- Корпусная проверка обозначений (Ф1.5.1) ------------------------------

#: Кэш «обозначение встречается в корпусе» в рамках одного Store.
_CORPUS_PRESENCE_CACHE: dict[int, dict[str, bool]] = {}
#: Потолок листов-кандидатов на одну форму trigram-поиска: уникальные
#: листы сортированы по документу и номеру, встречающееся обозначение
#: находится в первых кандидатах; потолок — защита от патологически
#: частого слипшегося шума OCR, а не рабочий фильтр.
CORPUS_CANDIDATE_LIMIT = 200


def designator_in_corpus(store: Store, designator: str) -> bool:
    """Встречается ли обозначение хоть на одном листе корпуса (Ф1.5.1).

    «Встречается» — тем же признанием вхождения, каким гейт designators
    проверяет лист: :func:`_designator_in_text` (дословно, слипше или
    через нормализацию поиска). Кандидатов даёт trigram-индекс
    (:meth:`pdfgraph.store.Store.search_trigrams`) по трём формам
    обозначения (слипшая, с разделителями, с пробелами); текст
    кандидата читается :meth:`pdfgraph.store.Store.page_text`.
    Фильтра по типу ВС нет: обозначение может стоять в чужом по теме
    листе (сравнение, реестр, таблица взаимозаменяемости). Ошибка
    FTS не глушится: как в :func:`gate_rare_term`, она означает
    невалидную среду, а не «не встречается». Результат кэшируется в
    рамках одного Store.
    """
    cache = _CORPUS_PRESENCE_CACHE.setdefault(id(store), {})
    if designator not in cache:
        cache[designator] = _scan_corpus_for_designator(store, designator)
    return cache[designator]


def _scan_corpus_for_designator(store: Store, designator: str) -> bool:
    """Пройти кандидатов trigram-индекса и найти вхождение обозначения."""
    lowered = designator.lower()
    parts = [part for part in re.split(r"[-\.\s]+", lowered) if part]
    variants = {"".join(parts), lowered, " ".join(parts)} - {""}
    # Ф1.9в: суффиксная часть номера карты («00e», «00а») короче четырёх
    # символов и не даёт trigram-кандидатов, а двойники суффикса в тексте
    # не совпадают дословно — ищем и по базе номера; вхождение по-прежнему
    # признаёт _designator_in_text, в том числе карточную форму с двойниками.
    card = _TECHCARD_DESIGNATOR_RE.fullmatch(lowered)
    if card is not None:
        d1, d2, d3, _suffix, _index = card.groups()
        variants |= {f"{d1}.{d2}.{d3}", f"{d1}{d2}{d3}", f"{d1} {d2} {d3}"}
    seen: set[tuple[str, int]] = set()
    for variant in sorted(variants):
        rows = store.search_trigrams(f"%{variant}%")[:CORPUS_CANDIDATE_LIMIT]
        for row in rows:
            key = (str(row[0]), int(row[1]))
            if key in seen:
                continue
            seen.add(key)
            if _designator_in_text(store.page_text(*key), designator):
                return True
    return False


def corpus_absent_designators(store: Store, question: str) -> list[str]:
    """Обозначения вопроса, которых нет ни на одном листе корпуса (Ф1.5.1).

    Порядок — как в вопросе, без повторов (переносит порядок
    :func:`question_designators`). Типы ВС строково не проверяются:
    упоминание типа в тексте чужого документа (примечание, сравнение)
    в правиле корпуса не участвует (Ф1.5.2) — их проверяет
    :func:`aircraft_types_without_primary` по метке листа.
    """
    type_keys = {key for _, key in _question_aircraft_entries(question)}
    return [designator for designator in question_designators(question)
            if _aircraft_type_key(designator) not in type_keys
            and not designator_in_corpus(store, designator)]


# --- Проверка типа ВС по метке листа (Ф1.5.2) ------------------------------

#: Кэш «основные типы корпуса» в рамках одного Store; None — метки
#: недоступны (тестовый дублер), исход не меняется.
_CORPUS_TYPE_CACHE: dict[int, frozenset[str] | None] = {}


def _question_aircraft_entries(
        question: str) -> list[tuple[str, tuple[str, str]]]:
    """Типы ВС вопроса с дословной формой: «Ми-26» → («Ми-26», («ми», «26»)).

    Дословная форма нужна для сообщения отказа; ключ — тот же, каким
    гейт aircraft_type сравнивает листы (:func:`_aircraft_type_key`).
    Порядок — как в вопросе, без повторов ключа.
    """
    entries: list[tuple[str, tuple[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for match in _AIRCRAFT_TYPE_RE.finditer(question):
        key = (match.group(1).lower(), match.group(2))
        if key in seen:
            continue
        seen.add(key)
        entries.append((match.group(0), key))
    return entries


def _fetch_primary_type_keys(connection) -> frozenset[str] | None:
    """Ключи типов из меток листов (SELECT DISTINCT aircraft_type).

    None — соединение не отвечает на запрос (тестовый дублер): проверка
    типа «na», исход не меняется, как у :func:`leader_aircraft_type`.
    Ошибка sqlite не глушится: отсутствие таблицы меток в настоящей
    БД — невалидная среда.
    """
    try:
        rows = connection.execute(
            "SELECT DISTINCT aircraft_type FROM pages").fetchall()
    except (AttributeError, TypeError) as error:
        logger.warning(
            "gates: метки типов листов недоступны — проверка типа «na»: %s",
            error)
        return None
    keys: set[str] = set()
    for row in rows:
        key = _aircraft_type_key(str(row[0] or ""))
        if key is not None:
            keys.add(key)
    return frozenset(keys)


def primary_aircraft_type_keys(store: Store) -> frozenset[str] | None:
    """Ключи типов ВС, встречающихся как метка листа (Ф1.5.2).

    «Основной» тип листа — его метка aircraft_type, записанная при
    build; упоминание типа в тексте листа меткой не является.
    None — метки недоступны (Store без таблицы, тестовый дублер).
    Результат кэшируется в рамках одного Store.
    """
    if id(store) not in _CORPUS_TYPE_CACHE:
        connection = getattr(store, "_connection", None)
        if connection is None:
            logger.debug("gates: store без таблицы страниц — метки «na»")
            _CORPUS_TYPE_CACHE[id(store)] = None
        else:
            _CORPUS_TYPE_CACHE[id(store)] = _fetch_primary_type_keys(
                connection)
    return _CORPUS_TYPE_CACHE[id(store)]


def aircraft_types_without_primary(
        store: Store, question: str) -> list[str]:
    """Названные в вопросе типы ВС без единого листа-основного (Ф1.5.2).

    Тип проверяется по метке aircraft_type страниц, а не по вхождению
    строки в тексты: «Ми-26» в примечании документа Ми-8 — упоминание,
    но метки «Ми-26» в базе нет, значит документации по типу в базе
    нет. Обозначения агрегатов это не затрагивает — их продолжает
    проверять :func:`corpus_absent_designators`. Метки неизвестны
    (Store без таблицы) — проверка «na», пустой список.
    """
    entries = _question_aircraft_entries(question)
    if not entries:
        return []
    keys = primary_aircraft_type_keys(store)
    if keys is None:
        return []
    return [display for display, key in entries if key not in keys]
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


# --- Ф1.4: третий исход — уточняющий вопрос ---------------------------------

#: Сколько верхних фрагментов выдачи уходит в clarify-ответ (лимит Ф1.4).
CLARIFY_TOP_FRAGMENTS = 10
#: Максимум вариантов уточнения (карточка Ф1.4).
CLARIFY_MAX_OPTIONS = 3
#: Минимум вариантов: меньше — тема однородна, уточнять нечем.
CLARIFY_MIN_OPTIONS = 2
#: Шаблон текста уточняющего вопроса.
CLARIFY_QUESTION = "Уточните, какой вариант имеется в виду."

#: Замороженная группа маркеров обзорных формулировок (решение владельца
#: Ф1.4, пин-тест tests/test_refusal_clarify.py
#: ::test_question_overview_markers_frozen) — менять только явным
#: решением владельца. Обзорные формулировки («перечислить состав»)
#: не уточняются НИКОГДА: даже при провале гейта конкретики исход —
#: отказ «уточнение невозможно — обзорная формулировка», а не переспрос;
#: маркеры: «какие», «из чего состоит/состоят», «что входит»,
#: «что включает», «перечисли», «список».
#:
#: Ф1.4.1 (решение владельца): уточнение по форме вопроса убрано —
#: замер полигона Ф1.4 показал, что маркеры формы «как», «почему»,
#: «сколько»... не отличают полный вопрос от неполного (7 корпусных
#: вопрос-действий уточнялись как неполные; формы и слотless-профили
#: полигонных и корпусных вопросов неразличимы). Уточнение по смыслу —
#: задача слоя с моделью, а не детерминированных правил (раздел 11
#: .clinerules).
OVERVIEW_QUESTION_RE = re.compile(
    r"(?i)\b(?:какие|из\s+чего\s+состоит|из\s+чего\s+состоят"
    r"|что\s+входит|что\s+включает|перечисли|список)\b")

#: Словарь чужой западной техники — заморожен пин-тестом Ф1.4
#: (tests/test_refusal_clarify.py): транслитерация и латиница, без
#: хвостового ограничения (вопрос перед поиском приводится к нижнему
#: регистру, поэтому флаг IGNORECASE не нужен).
FOREIGN_AIRCRAFT_RE = re.compile(
    "(?<![а-яёa-z])(боинг|аэробус|эйрбас|сессна|эмбраер|фоккер|локхид|"
    "дуглас|boeing|airbus|cessna|embraer|fokker|lockheed|douglas)")


def question_foreign_aircraft(question: str) -> list[str]:
    """Названия чужой западной техники из вопроса, нижний регистр (Ф1.4)."""
    return [match.group(0)
            for match in FOREIGN_AIRCRAFT_RE.finditer(question.lower())]


def question_overview(question: str) -> bool:
    """Обзорная формулировка («перечислить состав»)? (Ф1.4, решение владельца).

    Обзорные вопросы никогда не уточняются: при найденной теме исход —
    обычный ответ (перечень по листу), при ненайденной — прежний отказ.
    """
    return bool(OVERVIEW_QUESTION_RE.search(question))


@dataclass
class Outcome:
    """Итог классификатора исхода: ответ, отказ или уточняющий вопрос.

    ``decision`` — ``"answer"`` | ``"clarify"`` | ``"refuse"``; для
    ``clarify`` заполнены текст вопроса и варианты; ``fragments`` —
    верхняя часть выдачи (лимит :data:`CLARIFY_TOP_FRAGMENTS`).
    """

    decision: str
    reason: str = ""
    clarify_question: str = ""
    options: list[dict] = field(default_factory=list)
    fragments: list = field(default_factory=list)


@dataclass
class Slot:
    """Слот конкретики: строка вопроса, обязательная на листе-лидере."""

    kind: str
    token: str
    designator: bool = False


def question_slots(question: str) -> list[Slot]:
    """Слоты конкретики вопроса: обозначения, коды и значимые числа (Ф1.4).

    Переиспользует разбор гейтов Ф1.3б: обозначения и числовые коды
    разделов (``designator=True``) и значимые числа-ограничения
    (``designator=False``); порядок — как в вопросе.
    """
    slots: list[Slot] = []
    seen: set[str] = set()
    for token in question_designators(question):
        key = f"designator:{token}"
        if key not in seen:
            slots.append(Slot("designator", token, True))
            seen.add(key)
    for number in question_significant_numbers(question):
        key = f"number:{number}"
        if key not in seen:
            slots.append(Slot("number", number, False))
            seen.add(key)
    return slots


def _group_label(store: Store, fragments: list) -> str:
    """Ярлык группы вариантов: имя документа и раздел (Ф1.4).

    Раздел берётся по коду первого фрагмента группы (подсистема
    предпочитается системе); узла в ``nodes`` нет — «код <ключ>»;
    кода нет вовсе — только имя файла без расширения.
    """
    document = str(getattr(fragments[0], "document", "") or "(без документа)")
    basename = document.rsplit(".", 1)[0] if "." in document else document
    code = ""
    for fragment in fragments:
        candidate = str(getattr(fragment, "subsystem_code", "") or "")
        if not candidate:
            candidate = str(getattr(fragment, "system_code", "") or "")
        if candidate:
            code = candidate
            break
    if not code:
        return basename
    key = code.casefold()
    row = store._connection.execute(
        """
        SELECT label FROM nodes
        WHERE key = ? AND kind = 'subsystem'
        """,
        (key,),
    ).fetchone()
    if row is None or not str(row[0]).strip():
        row = store._connection.execute(
            "SELECT label FROM nodes WHERE key = ? AND kind = 'system'",
            (key,),
        ).fetchone()
    label = str(row[0]).strip() if row is not None else ""
    if not label:
        label = f"код {code}"
    return f"{basename} — {label}"


def clarify_options(store: Store, fragments: list) -> tuple[list, list[dict]]:
    """Разбить выдачу на группы-разделы и построить варианты (Ф1.4).

    Возвращает ``(top, options)``: верхние :data:`CLARIFY_TOP_FRAGMENTS`
    фрагментов и варианты — до :data:`CLARIFY_MAX_OPTIONS`, группы
    (документ, код раздела) в порядке убывания суммарного веса выдачи;
    у варианта ``id`` («1», «2», …), ярлык группы, исходные индексы
    фрагментов и ``query_terms`` (код раздела, «072.10.00»).
    """
    top = list(fragments)[:CLARIFY_TOP_FRAGMENTS]
    groups: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for index, fragment in enumerate(top):
        code = (str(getattr(fragment, "subsystem_code", "") or "")
                or str(getattr(fragment, "system_code", "") or ""))
        key = (str(getattr(fragment, "document", "(без документа)")), code)
        if key not in groups:
            groups[key] = {"fragments": [], "ids": [], "score": 0.0}
            order.append(key)
        entry = groups[key]
        entry["fragments"].append(fragment)
        entry["ids"].append(index)
        entry["score"] += float(getattr(fragment, "score", 0.0) or 0.0)
    ranked = sorted(
        enumerate(order), key=lambda item: (-groups[item[1]]["score"],
                                            item[0]))
    options: list[dict] = []
    for position, (_, key) in enumerate(
            ranked[:CLARIFY_MAX_OPTIONS], start=1):
        entry = groups[key]
        code = key[1]
        options.append({
            "id": str(position),
            "label": _group_label(store, entry["fragments"]),
            "fragment_ids": list(entry["ids"]),
            "query_terms": [code] if code else [],
        })
    return top, options


def decide_outcome(store: Store, question: str, fragments: list) -> Outcome:
    """Классификатор исхода: обычный ответ / отказ / уточняющий вопрос (Ф1.4).

    Порядок решений детерминированный, без модели:
      1. пустая выдача — прежний отказ «нет подтверждений в документах»;
      2. чужая западная техника в вопросе — отказ «чужая техника (...»;
      3. провал гейтов конкретики (:func:`decide_refusal`) —
         3а. названный в вопросе тип ВС, у которого в базе нет ни одного
             листа с этим типом как меткой (метка aircraft_type,
             :func:`aircraft_types_without_primary`) — отказ «документации
             по типу … в базе нет»; упоминание типа в тексте чужого
             документа не считается ни отказом, ни уточнением (Ф1.5.2);
         3б. если обозначение (не тип) из вопроса не встречается нигде в
             корпусе (:func:`corpus_absent_designators`) — прежний отказ
             с причиной «обозначение … в документации не встречается»;
         3в. иначе уточнение, если групп в выдаче хотя бы
             :data:`CLARIFY_MIN_OPTIONS` и вопрос не обзорный
             (:func:`question_overview`), иначе прежний отказ;
      4. в вопросе нет ни одного слота конкретики — гейты конкретики
         не применимы (нечего проверять на листе), исход — обычный ответ.
         Ф1.4.1 (решение владельца): уточнение по форме вопроса убрано —
         замер полигона Ф1.4 показал, что форма не отличает полный
         вопрос от неполного (раздел 11 .clinerules).

    Отказ по чужой технике стоит раньше гейтов: вопрос про другой тип
    ВС не требует проверки листа. Гейты не влияют на поиск и проверку
    цитат — решение принимается на собранной выдаче.
    """
    if not fragments:
        return Outcome("refuse", "нет подтверждений в документах")
    foreign = question_foreign_aircraft(question)
    if foreign:
        return Outcome(
            "refuse",
            "чужая техника (" + ", ".join(foreign)
            + ") — в документах ответа нет",
            fragments=list(fragments))
    top, options = clarify_options(store, fragments)
    gate_failures = decide_refusal(store, question, fragments)
    gate_summary = "гейты конкретики: " + "; ".join(
        f"{failure['gate']} ({failure['detail']})"
        for failure in gate_failures)
    if gate_failures:
        missing_types = aircraft_types_without_primary(store, question)
        if missing_types:
            if len(missing_types) == 1:
                type_note = (
                    f"документации по типу {missing_types[0]} в базе нет")
            else:
                type_note = (f"документации по типам "
                             f"{', '.join(missing_types)} в базе нет")
            return Outcome(
                "refuse", gate_summary + "; " + type_note, fragments=top)
        absent = corpus_absent_designators(store, question)
        if absent:
            if len(absent) == 1:
                corpus_note = (
                    f"обозначение {absent[0]} в документации не встречается")
            else:
                corpus_note = (f"обозначения {', '.join(absent)} "
                               "в документации не встречаются")
            return Outcome(
                "refuse",
                gate_summary + "; " + corpus_note,
                fragments=top)
        if len(options) < CLARIFY_MIN_OPTIONS:
            return Outcome(
                "refuse",
                gate_summary + "; уточнение невозможно — выдача однородна",
                fragments=top)
        if question_overview(question):
            return Outcome(
                "refuse",
                gate_summary
                + "; уточнение невозможно — обзорная формулировка",
                fragments=top)
        return Outcome("clarify", gate_summary, CLARIFY_QUESTION,
                       options, top)
    return Outcome("answer", "", fragments=list(fragments))


__all__ = [
    "GATE_NAMES", "ENABLED_GATES", "RARE_DF_THRESHOLD", "MIN_TERM_LEN",
    "VERDICT_FAIL", "VERDICT_PASS", "VERDICT_NA", "GateVerdict",
    "is_designator", "question_designators", "question_significant_numbers",
    "question_aircraft_types", "question_terms", "document_frequency",
    "leader_page_text", "leader_aircraft_type", "window_texts",
    "gate_numbers", "gate_designators", "gate_aircraft_type",
    "gate_rare_term", "run_all_gates", "decide_refusal",
    "CLARIFY_TOP_FRAGMENTS", "CLARIFY_MAX_OPTIONS",
    "CLARIFY_MIN_OPTIONS", "CLARIFY_QUESTION", "FOREIGN_AIRCRAFT_RE",
    "OVERVIEW_QUESTION_RE",
    "Outcome", "Slot", "question_foreign_aircraft", "question_slots",
    "question_overview",
    "CORPUS_CANDIDATE_LIMIT", "designator_in_corpus",
    "corpus_absent_designators", "primary_aircraft_type_keys",
    "aircraft_types_without_primary",
    "clarify_options", "decide_outcome",
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