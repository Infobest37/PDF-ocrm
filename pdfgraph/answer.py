"""Build answers grounded in verbatim snippets from the knowledge graph.

Every answerer, including any future LLM-backed answerer, must pass its
citations through :func:`pdfgraph.verify.verify_citations` before the answer
is shown to a user.
"""

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Protocol

from pdfgraph.query import (
    SNIPPET_CHARS,
    Fragment,
    effective_search_mode,
    expand_continuations,
    search,
    techcard_missing_suffixes,
    techcard_query,
    techcard_section_more,
    vector_search,
)
from pdfgraph.techcard_card import TechCard, collect_techcards
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

#: Stage-2 diagnostics: the question→branch→SQL→count chain is logged in
#: query.search/_by_fts and store.search_pages; here we log the verdict.
logger = logging.getLogger(__name__)


@dataclass
class Claim:
    """A displayed claim, evidence indexes, and optional citation text.

    ``text`` remains the verbatim evidence text used by citation verification.
    For a synthesized answer, ``answer_text`` carries the user-facing prose;
    this separation lets a paraphrase be displayed without weakening exact
    source-snippet verification.
    """

    text: str
    evidence: list[int]
    answer_text: str | None = None


@dataclass
class Answer:
    """A response, its claims, and the fragments offered as evidence.

    ``neighbors`` (Ф1.7) — окно соседних листов к топ-5: отдельный список
    ссылок ``{"document", "page", "label"}`` без цитат; в ранжирование,
    claims и проверку цитат не попадает.
    ``techcard_missing`` (Ф1.9) — запрошенные карты (номер с суффиксом),
    которых нет в руководствах базы: отдельный список строк «035.10.00e»
    без цитат; в ранжирование, claims и проверку цитат не попадает.
    ``techcard_more`` (Ф1.9б) — сколько карт раздела не попало в выдачу
    после лимита 10; подпись «ещё N карт раздела», не цитата.
    ``applicability_warning`` (Ф0.3в) — пометка «документ применим к
    бортам …; ваш борт не указан», когда борт из вопроса не входит в
    применимость документа-лидера; ответ при этом выдаётся как раньше.
    """

    question: str
    claims: list[Claim]
    fragments: list[Fragment]
    refusal: bool = False
    reason: str = ""
    clarification: dict | None = None
    neighbors: list[dict] = field(default_factory=list)
    techcard_missing: list[str] = field(default_factory=list)
    techcard_more: int = 0
    #: Ф2.5: карточки карт для отображения (по одной на запрошенный
    #: суффикс); в ранжирование, claims и проверку цитат не попадает.
    techcard: list[TechCard] = field(default_factory=list)
    applicability_warning: str = ""
    #: Ф2.10: статус «нормы не нашёл» для вопроса о параметре (давление,
    #: температура, …): где искал и что нашлось вместо нормы. Строка,
    #: не цитата — в claims, verify и проверку цитат не попадает.
    param_norm_missing: str = ""
    #: Ф1.29: состояние ответа по вопросу-значению — «full» (лидер
    #: имеет валидное окно нормы: был первым или поднят в топ),
    #: «mention» (валидного окна нет ни у кого из топ-10: лидер —
    #: лист с упоминанием параметра, честная фраза в
    #: ``param_norm_missing``), «» (вопрос не о параметре или отказ).
    answer_state: str = ""


class Answerer(Protocol):
    """An implementation that produces an answer from retrieved fragments."""

    def answer(self, question: str, fragments: list[Fragment]) -> Answer:
        """Answer ``question`` using only ``fragments``."""
        ...


class ExtractiveAnswerer:
    """Return up to ``max_claims`` verbatim fragment snippets."""

    def __init__(self, max_claims: int = 3):
        self.max_claims = max_claims

    def answer(self, question: str, fragments: list[Fragment]) -> Answer:
        claims = [
            Claim(text=fragment.snippet, evidence=[index])
            for index, fragment in enumerate(fragments[:self.max_claims])
        ]
        return Answer(question=question, claims=claims, fragments=fragments)


# --- Ф1.7: окно соседних листов ---------------------------------------------

#: Флаг окна соседних листов; читается при каждом вызове (без кэша),
#: как SPECIFICITY_WEIGHT_ENV в rag_fusion. Значения «0»/«false» отключают.
NEIGHBORS_ENV = "PDFGRAPH_NEIGHBORS"
#: Метка соседа «лист −1».
NEIGHBOR_LABEL_PREV = "предыдущий лист"
#: Метка соседа «лист +1»: продолжение не гарантируется.
NEIGHBOR_LABEL_NEXT = "следующий лист, возможно продолжение"


def neighbors_enabled() -> bool:
    """Окно соседей включено: PDFGRAPH_NEIGHBORS не равен 0/false."""
    return os.environ.get(NEIGHBORS_ENV, "").strip().lower() not in {
        "0", "false"}


def _page_exists(store: Store, document: str, page: int) -> bool:
    """Лист существует в документе (границы документа не переходить)."""
    row = store._connection.execute(
        "SELECT 1 FROM pages WHERE document = ? AND page = ?",
        (str(document), int(page))).fetchone()
    return row is not None


def _page_flagged(store: Store, document: str, page: int) -> bool:
    """Лист помечен needs_rescan или is_service — в соседи не брать."""
    row = store._connection.execute(
        "SELECT needs_rescan, is_service FROM pages "
        "WHERE document = ? AND page = ?",
        (str(document), int(page))).fetchone()
    if row is None:
        return True
    return bool(row[0]) or bool(row[1])


def collect_neighbors(store: Store, fragments: list[Fragment]) -> list[dict]:
    """Соседи листов топ-5: −1 и +1 того же документа, без цитат.

    Правила карточки Ф1.7: не больше двух на фрагмент; дубликаты не
    повторяются (в том числе листы самой выдачи); needs_rescan и
    is_service не берутся; за границы документа не переходить. Порядок
    основной выдачи не меняется — соседи живут отдельным списком.
    """
    if not fragments or not neighbors_enabled():
        return []
    # Дедупликация — против топ-5 (карточка Ф1.7: соседи строятся ОТ
    # топ-5; лист глубже топ-5, соседний к нему, остаётся соседом —
    # кейс e18 золотого набора) и против уже добавленных соседей.
    seen: set[tuple[str, int]] = {
        (fragment.document, fragment.page) for fragment in fragments[:5]}
    neighbors: list[dict] = []
    for fragment in fragments[:5]:
        document, page = fragment.document, fragment.page
        for offset, label in ((-1, NEIGHBOR_LABEL_PREV),
                              (1, NEIGHBOR_LABEL_NEXT)):
            candidate = page + offset
            if candidate < 1:
                continue
            key = (document, candidate)
            if key in seen:
                continue
            if not _page_exists(store, document, candidate):
                continue
            if _page_flagged(store, document, candidate):
                continue
            seen.add(key)
            neighbors.append({"document": document, "page": candidate,
                              "label": label})
    return neighbors


# --- Ф0.3в: применимость документов ------------------------------------------

#: Бортовой номер в вопросе — копия правила Ф0.3а
#: (pdfgraph/documents._SERIAL_RE; documents.py не импортируется).
_APPLICABILITY_SERIAL_RE = re.compile(
    r"(?<![0-9-])(?:2\d-\d{3}|2\d\d{3,4})(?![0-9-])")


def _documents_rows(store: Store) -> dict[str, dict] | None:
    """Строки documents {документ: {layer, scope, serials, …}} или None.

    None — таблицы нет (старые базы/фикстуры): поведение прежнее,
    отсутствие таблицы не глушится молча — пишется в лог.
    """
    try:
        rows = store._connection.execute(
            "SELECT document, layer, applicability_scope, "
            "applicability_serials, revision_no, revision_date "
            "FROM documents").fetchall()
        return {
            row[0]: {"layer": row[1], "scope": row[2], "serials": row[3],
                     "revision_no": row[4], "revision_date": row[5]}
            for row in rows}
    except (sqlite3.OperationalError, TypeError, AttributeError) as error:
        # Подделки store в тестах (Mock-connection) тоже означают «нет
        # данных»: поведение прежнее, отсутствие не глушится молча.
        logger.warning("documents table unavailable: %s", error)
        return None


def applicability_verdict(store: Store, question: str, fragments: list
                          ) -> tuple[str | None, str]:
    """Ф0.3в: (причина отказа | None, предупреждение) по борту вопроса.

    Отказ (п. 4 карточки): борт в вопросе есть, но в documents нет ни
    одного документа scope=all и ни одного scope=serials с этим бортом —
    «документации по борту N в базе нет». Предупреждение (п. 3): борт
    есть, лидер — документ scope=serials без этого борта, но отказ не
    сработал (есть базовые или подходящие serials) — ответ выдаётся с
    пометкой. Без борта в вопросе или без таблицы — (None, "").
    """
    rows = _documents_rows(store)
    if rows is None:
        return None, ""
    serials_q = sorted(set(_APPLICABILITY_SERIAL_RE.findall(question)))
    if not serials_q:
        return None, ""
    serial = serials_q[0]
    has_base = any(row["scope"] == "all" for row in rows.values())
    has_serial = any(
        row["scope"] == "serials" and serial in {
            part.strip() for part in (row["serials"] or "").split(",")}
        for row in rows.values())
    if not has_base and not has_serial:
        return f"документации по борту {serial} в базе нет", ""
    leader = fragments[0] if fragments else None
    leader_row = rows.get(leader.document) if leader else None
    if (leader_row and leader_row["scope"] == "serials"
            and serial not in {part.strip() for part in
                               (leader_row["serials"] or "").split(",")}):
        return None, (f"документ применим к бортам "
                      f"{leader_row['serials']}; ваш борт не указан")
    return None, ""


# --- Ф2.10: вопрос о параметре — норма с числом и единицей --------------------

#: Число (в т.ч. диапазон «12–16» и допуск «±0,5») перед единицей.
_PARAM_NUM = r"\d+(?:[.,]\d+)?(?:\s*[±–—-]\s*\d+(?:[.,]\d+)?)?"

#: Регэкспы «число + единица» по классам величины (п. 2 карточки Ф2.10);
#: единица — во 2-й группе, берётся с листа дословно, не додумывается.
#: Число необязательно: в корпусе норма часто стоит в шапке таблицы
#: («Давление масла в магистрали, кгс/см2») — числа в ячейках. Единица
#: класса на листе — «норма на листе есть» (безопасная сторона: меньше
#: ложных отказов); чужой узел отсекается исходом 3.
#: Замена знака ² у OCR: «кгс/см2», «кгс/см?», «кгс/см*», «кгс/см”»,
#: «кго/см?» (кгс→кго). Один символ-мусор после «см» допускается.
_PARAM_SM2 = r"[²*?.”\"'2]"

_PARAM_PRESSURE_RES = (
    re.compile(
        rf"(?:({_PARAM_NUM})\s*)?"
        rf"((?:кгс|кго|кг)\s*/\s*см\s*{_PARAM_SM2})"),
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(МПа)"),
)
_PARAM_TEMP_RES = (re.compile(rf"(?:({_PARAM_NUM})\s*)?(°\s*[CС])"),)
_PARAM_MM_RES = (re.compile(rf"(?:({_PARAM_NUM})\s*)?(мм)(?![а-яё])"),)
_PARAM_TORQUE_RES = (
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(Н\s*[·.]\s*м)"),
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(Нм)(?![а-яё])"),
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(кгс\s*[·.]\s*м)"),
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(кгс\s+м)(?![а-яё])"),
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(кгсм)(?![а-яё])"),
)
#: Расход в корпусе и в «л/мин», и в «л/ч»/«л/час» (л. 38 РЭ1: «Не
#: более 0,3 л/ч»; л. 21 ВР-14: «Расход масла, л/час»).
_PARAM_FLOW_RES = (
    re.compile(rf"(?:({_PARAM_NUM})\s*)?(л\s*/\s*(?:мин|час|ч))(?![а-яё])"),)
_PARAM_ROT_RES = (re.compile(rf"(?:({_PARAM_NUM})\s*)?(об\s*/\s*мин)"),)
_PARAM_VOLT_RES = (
    re.compile(rf"(?:({_PARAM_NUM})\s*)?([ВB])(?![а-яёa-zA-Z])"),)
_PARAM_CURRENT_RES = (
    re.compile(rf"(?:({_PARAM_NUM})\s*)?([АA])(?![а-яёa-zA-Z])"),)

#: Стемы параметров в вопросе → (каноническое имя, регэкспы единиц).
#: «ток» — отдельно, по границам слова (не «поток», «электроток»).
_PARAM_STEMS: tuple[tuple[str, str, tuple[re.Pattern[str], ...]], ...] = (
    ("давлени", "давление", _PARAM_PRESSURE_RES),
    ("температур", "температура", _PARAM_TEMP_RES),
    ("зазор", "зазор", _PARAM_MM_RES),
    ("люфт", "люфт", _PARAM_MM_RES),
    ("момент затяжк", "момент затяжки", _PARAM_TORQUE_RES),
    ("расход", "расход", _PARAM_FLOW_RES),
    ("напряжени", "напряжение", _PARAM_VOLT_RES),
)
#: «обороты» как частота вращения — только формы мн. числа: «при полном
#: обороте ручки центровки» (ед.ч.) — не вопрос о параметре (золотой e07).
_PARAM_RPM_RE = re.compile(r"оборот(?:ы|ов|ам|ами|ах)(?![а-яё])")
_PARAM_CURRENT_RE = re.compile(r"(?<![а-яё])ток(?:а|у|е|ом|и)?(?![а-яё])")

#: Узел/система вопроса для исхода «число про другую систему»: стем →
#: (именительный для «не про …», винительный для «найдено про …»).
#: Список замкнутый: узла нет в списке — исход 3 не применяется
#: (безопасный недоотказ; слова вроде «каком» узлами не становятся).
_PARAM_SYSTEM_STEMS: tuple[tuple[str, str, str], ...] = (
    ("двигател", "двигатель", "двигатель"),
    ("гидросистем", "гидросистема", "гидросистему"),
    ("маслосистем", "маслосистема", "маслосистему"),
    ("топливн", "топливная система", "топливную систему"),
    ("пневмосистем", "пневмосистема", "пневмосистему"),
    ("электросистем", "электросистема", "электросистему"),
)


#: Вопросительное слово или «?» в конце — гейт срабатывания Ф2.10
#: (решение владельца от 2026-11-05): «минимальное давление масла» и
#: «Продолжение табл. 18 давление масла» — не вопросы о норме, правило
#: на них не срабатывает.
_PARAM_QWORD_RE = re.compile(
    r"(?<![а-яёa-z])(?:"
    r"как(?:ой|ая|ое|ие|ого|ому|им|ими|ую|их|ом)"
    r"|каков(?:а|о)?"
    r"|сколько"
    r"|чему"
    r"|(?:назови|укажи)(?:те)?"
    r")(?![а-яё])")


def _asks_for_value(question: str) -> bool:
    """Вопрос просит значение: вопросительное слово или «?» в конце."""
    return bool(_PARAM_QWORD_RE.search(question.lower())) \
        or question.rstrip().endswith("?")


def _param_question(question: str
                    ) -> tuple[str, tuple[re.Pattern[str], ...]] | None:
    """(имя параметра, регэкспы единиц) либо None — вопрос не о параметре.

    П. 1 карточки Ф2.10: слово параметра (давление, температура, зазор,
    момент затяжки, расход, обороты, напряжение, ток, люфт).
    """
    lowered = question.lower()
    if _PARAM_CURRENT_RE.search(lowered):
        return "ток", _PARAM_CURRENT_RES
    if _PARAM_RPM_RE.search(lowered):
        return "обороты", _PARAM_ROT_RES
    for stem, name, patterns in _PARAM_STEMS:
        if stem in lowered:
            return name, patterns
    return None


def _param_page_text(store: Store, document: str, page: int) -> str:
    """Полный текст листа; недоступен — пустая строка (правило молчит).

    Сниппет фрагмента обрезан SNIPPET_CHARS — единицы искать можно только
    по полному тексту листа (store.page_text).
    """
    try:
        return store.page_text(str(document), int(page)) or ""
    except (sqlite3.Error, TypeError, ValueError, AttributeError) as error:
        logger.warning("param_norm: текст листа %s:%s недоступен: %s",
                       document, page, error)
        return ""


def param_norm_verdict(store: Store, question: str, fragments: list) -> str:
    """Ф2.10: «нормы не нашёл» для вопроса о параметре; иначе пустая строка.

    Гейт срабатывания (решение владельца от 2026-11-05): в вопросе есть
    вопросительное слово (какой/каков/сколько/чему/назови/укажи) или «?»
    в конце — «минимальное давление масла» и «Продолжение табл. 18
    давление масла» не вопросы о норме, правило на них молчит.

    На лидере и в топ-3 выдачи ищется единица класса параметра, по
    возможности с числом рядом (давление → кгс/см², кг/см², МПа;
    температура → °C; зазор/люфт → мм; момент затяжки → Н·м, кгс·м;
    расход → л/мин; обороты → об/мин; напряжение → В; ток → А). В корпусе
    норма часто стоит в шапке таблицы («Давление …, кгс/см2») — единица
    без числа рядом тоже считается «норма на листе есть»: безопасная
    сторона (меньше ложных отказов), чужой узел отсекается исходом 3.
    Три исхода (п. 3 карточки): единица класса есть и лист упоминает узел
    из вопроса → обычный ответ (пустая строка); единицы нет ни на одном
    листе → статус «нормы не нашёл» с перечнем листов и документов;
    единица есть, но узла из вопроса на этих листах нет → тот же статус
    с пометкой «найдено про …, не про …». Правило работает поверх готовой
    выдачи: поиск, гейты, verify и веса не затрагиваются. Текст ни одного
    листа недоступен — правило молчит (безопасный недоотказ).
    """
    detected = _param_question(question)
    if not detected or not fragments or not _asks_for_value(question):
        return ""
    param_name, patterns = detected
    searched = fragments[:3]
    pages_str = ", ".join(
        f"{fragment.document or '?'} л. {fragment.page}"
        for fragment in searched)
    docs = sorted({fragment.document for fragment in searched
                   if fragment.document})
    texts = {index: _param_page_text(store, fragment.document,
                                     fragment.page)
             for index, fragment in enumerate(searched)}
    if not any(texts.values()):
        return ""  # ни один лист не удалось проверить — не отказывать
    hits: list[tuple[int, str]] = []  # (индекс листа, единица с листа)
    for index, text in texts.items():
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                hits.append((index, match.group(2)))
                break
    if not hits:
        status = (f"нормы не нашёл: искал на листах {pages_str}; "
                  f"нормы «{param_name}» (число с единицей) там нет")
        if docs:
            status += f"; документы: {', '.join(docs)}"
        return status
    # Исход 3: узел(ы) из вопроса не упомянуты ни на одном листе с числом.
    lowered = question.lower()
    wanted = [(stem, nom) for stem, nom, _acc in _PARAM_SYSTEM_STEMS
              if stem in lowered]
    if not wanted:
        return ""
    for index, _unit in hits:
        page_lower = texts[index].lower()
        if all(stem in page_lower for stem, _nom in wanted):
            return ""  # число относится к узлу из вопроса — обычный ответ
    hit_pages = ", ".join(
        f"{searched[index].document or '?'} л. {searched[index].page}"
        for index, _unit in hits)
    hit_text = " ".join(texts[index].lower() for index, _unit in hits)
    wanted_stems = {stem for stem, _nom in wanted}
    found = "другим узлом"
    for stem, _nom, acc in _PARAM_SYSTEM_STEMS:
        if stem not in wanted_stems and stem in hit_text:
            found = acc
            break
    wanted_str = " / ".join(nom for _stem, nom in wanted)
    return (f"нормы не нашёл: искал на листах {pages_str}; "
            f"на листах {hit_pages} есть {param_name} "
            f"({hits[0][1]} — единица с листа), но найдено про {found}, "
            f"не про {wanted_str}")


#: Ф1.29: честная фраза статуса «mention» — в ``param_norm_missing``
#: (строка-статус, не цитата; CLI и веб её уже печатают).
MENTION_NOTE_TEMPLATE = ("Найден лист с упоминанием {what}, "
                         "но числовое значение на нём не указано")

#: Ф1.34: глубина перебора листов в поиске валидного окна нормы.
#: Карточка Ф1.29 говорила «до 10-го», но для вопроса «размер щётки»
#: лист со значением («высота щеток…13,5 мм») стоит глубже десятого —
#: разные слова об одном узле хуже ранжируются. Отступление от буквы
#: Ф1.29 задокументировано; замер golden обязателен.
VALUE_SCAN_LIMIT = 20


def _mention_note(question: str, parameter: str) -> str:
    """Честная фраза «mention»: кого упомянули, значения нет.

    «Что» — обозначение техники из исходного вопроса (первый токен
    с цифрой, исходный регистр), иначе имя параметра; без конкретики
    — «узла».
    """
    designator = next(iter(re.findall(
        r"[A-Za-zА-Яа-яЁё0-9-]*\d[A-Za-zА-Яа-яЁё0-9-]*", question)), "")
    what = designator or parameter or "узла"
    return MENTION_NOTE_TEMPLATE.format(what=what)


def _value_leader_state(store: Store, question: str,
                        fragments: list[Fragment],
                        ) -> tuple[list[Fragment], str, str]:
    """Ф1.29: лидер ответа — лист со значением, не с упоминанием.

    Гейт — :func:`pdfgraph.norms.is_value_question` (вопрос-значение
    с параметром из словаря норм; «высота», «износ», «толщина»,
    «натяг» там есть, в отличие от словаря Ф2.10 — поэтому не
    ``_param_question``). Значение на листе — два уровня (решение
    владельца Ф1.29 от 2026-11-10: свободное «окно значения» убрано —
    на e35 оно признавало значением 1,25 кгс/см² противодавления на
    листе про температуру масла, ложный ответ хуже отказа):

    - ПОЛНОЕ окно нормы — :func:`pdfgraph.norms._valid_windows`:
      объект вопроса в окне, число с единицей, нормативная
      конструкция, нет процедурного маркера.
    - ТАБЛИЧНАЯ НОРМА — только если полного окна нет: в ОДНОЙ строке
      исходного текста (строки таблицы, :func:`str.splitlines`)
      стоят название параметра (с границей слова — «противодавления»
      не считается «давлением»), число с единицей и объектные основы
      вопроса; строка короткая (≤ :data:`TABULAR_LINE_MAX`) и без
      процедурных маркеров. Случай «давление 155 кгс/см²» в шапке
      таблицы без словесной конструкции.

    Объектные основы вопроса обязаны быть в окне/строке — окно «про
    другой объект» отсекается; дизайнаторы техники в окне не
    требуются (на листе с нормой их как раз нет — это суть
    подстановки Ф1.32).

    Возвращает (фрагменты, состояние, фраза-статус): у лидера полное
    окно или табличная норма → список прежний и ``"full"``; у кого-то
    из позиций 2–10 (сначала ищется полное окно, потом табличная
    норма) → он поднимается на первое место (остальные — в прежнем
    порядке) и ``"full"``; значения нет ни у кого → список прежний,
    ``"mention"`` и честная фраза :func:`_mention_note`. Не
    вопрос-значение или пустая выдача → (список, ``""``, ``""``) —
    поведение прежнее. Поиск, гейты, verify и веса слияния не
    затрагиваются: перестановка происходит ПОСЛЕ гейтов
    отказа/уточнения и отсечения по ``min_score``.
    """
    from pdfgraph.norms import (_GENERIC_PARAMS, _NUMBER_BEFORE, _PARAMETERS,
                                _PROC_WINDOW_RE, _stem, _unit_regex,
                                _valid_windows, _class_stems, WINDOW_BACK,
                                WINDOW_FORWARD, WINDOW_OBJECT_EXT)
    from pdfgraph.norms import is_value_question
    from pdfgraph.textclean import clean_search_text

    # Ф1.32-связка: если подстановка алиаса сработала, объектные основы
    # берутся из РАСШИРЕННОГО вопроса — расшифровка («электродвигатель
    # вентилятора…») отличает лист со значением по этому узлу от листа
    # со значением по другому объекту с тем же параметром (живой
    # случай: щётки генераторов л.568 против щёток электродвигателя
    # л.64 при вопросе про МВ-1200). Без подстановки — исходный вопрос.
    from pdfgraph.terms import effective_terms_enabled, expand_question
    expanded = ""
    if effective_terms_enabled():
        expanded = expand_question(question) or ""
    target = expanded or question
    ok, parameter, objects = is_value_question(target)
    if not ok or not fragments:
        return fragments, "", ""
    parameter_stem = _PARAMETERS.get(parameter, "")
    if not parameter_stem:
        # Параметра из словаря норм в вопросе нет: правило «лидер —
        # лист со значением» не применяется (иначе «какой»-маркер
        # без параметра даёт ложные перестановки на процедурах и
        # «какой элемент»-вопросах — подтверждено аудитом Ф1.29).
        return fragments, "", ""
    object_stems = [
        (_stem(normalize_for_search(word))
         if not word.isdigit() and "-" not in word else word)
        for word in objects
    ]
    anchor = parameter_stem
    #: Латинские двойники в символьных классах единиц: OCR часто даёт
    #: «+160 °c» с латинской c (0x63), а словарь единиц norms.py знает
    #: только кириллическую «с» — живые случаи p17 (л.38 ТВ3-117) и
    #: p19 (л.40 SafirPG), нормы температуры без единицы не находились
    #: вовсе. Классы вида «[сс]» расширяются до «[ссcc]»; диапазоны
    #: («[а-яё]») и символы («[2²?]») не трогаются. norms.py не
    #: правится (карточка Ф1.29).
    _LATIN_DUPES = str.maketrans("саеорхувкмнт", "caeorpybkmht")

    def _latinize(match: re.Match) -> str:
        """Расширить символьный класс единиц латинскими двойниками."""
        return "[" + match.group(1) + match.group(1).translate(_LATIN_DUPES) \
            + "]"

    unit_src_latin = re.sub(r"\[([а-яё]+)\]", _latinize,
                            _unit_regex(parameter_stem))
    unit_re = re.compile(_NUMBER_BEFORE + "(" + unit_src_latin + ")")
    # Стемы расшифровки (Ф1.29 + Ф1.32): слова расширенного вопроса,
    # которых нет в исходном («электродвигатель», «вентилятор», …),
    # обязаны присутствовать на ЛИСТЕ-кандидате. Без этого общий
    # объектный стем вопроса («щеток») признаёт валидными окна
    # листов других узлов с тем же параметром (живой случай: щётки
    # генераторов л.568 и преобразователя ПТ-200Ц л.116 при вопросе
    # про щётки МВ-1200). На листе с нормой расшифровка может стоять
    # далеко от окна («Замена щеток электродвигателя…» выше нормы
    # л.64) — поэтому проверка по всему листу, не по окну.
    substitute_stems: set[str] = set()
    if expanded:
        expanded_tokens = set(normalize_for_search(expanded).split())
        original_tokens = set(normalize_for_search(question).split())
        substitute_stems = {
            (_stem(token) if not token.isdigit() and "-" not in token
             else token)
            for token in expanded_tokens - original_tokens
            if len(token) >= 4
        }
    #: Ф1.35: пометка расширения при полном окне, найденном классовым
    #: стемом вместо якоря вопроса («найдено по параметру: высота
    #: (в запросе: размер)»). Только для общих параметров —
    #: частное→частное запрещено.
    generic = parameter_stem in _GENERIC_PARAMS
    param_names = {stem: name for name, stem in _PARAMETERS.items()}

    def _expansion_note(fragment: Fragment) -> str:
        """Пометка, если окно лидера нашлось классовым стемом."""
        try:
            text = store.page_text(fragment.document, fragment.page)
        except (sqlite3.Error, TypeError, ValueError, AttributeError):
            return ""
        if not text:
            return ""
        text_norm = normalize_for_search(clean_search_text(text))
        if re.search(re.escape(anchor), text_norm):
            return ""  # якорь вопроса на листе есть — расширения не было
        for stem in _class_stems(parameter_stem):
            if stem == anchor:
                continue
            if _valid_windows(text_norm, stem, [primary_stem],
                              parameter_stem) > 0:
                name = param_names.get(stem, stem)
                return (f"найдено по параметру: {name} "
                        f"(в запросе: {parameter})")
        return ""

    #: Максимальная длина строки, считающейся строкой таблицы
    #: (карточка Ф1.29: «только в табличном контексте, не в
    #: произвольном тексте»).
    TABULAR_LINE_MAX = 120
    # Название параметра с границей слова: «противодавления» не
    # считается «давлением» (живой случай e35 — лист 589).
    param_word_re = re.compile(r"(?:^|[^а-яё])" + re.escape(anchor))

    #: Порог срабатывания сигнализации / граница запуска в строке —
    #: не рабочая норма (живые случаи: e35 «при снижении в
    #: маслосистеме избыточного давления до величины 2,5 кгс/см²» —
    #: л.102 РЭ Д-1263 ч.3; p17 «запуск всу … при температуре масла
    #: в маслобаке выше минус 40°c» — л.32 SafirPG).
    _THRESHOLD_RE = re.compile(
        r"сигнал|срабатыва|снижени|повышени|уменьшени|увеличени|минус")

    def _tabular_norm(text: str) -> bool:
        """Число с единицей в строке параметра или следующей строке.

        Форматы таблиц: «давление 155 кгс/см²» — параметр и значение
        в одной строке; «2.3.14. температура масла на выходе…:» +
        «максимальная на стоянке +160 °c» — значение на СЛЕДУЮЩЕЙ
        строке. Проверяются обе строки пары (параметр — в первой,
        объект — в любой, единица — в любой), без процедурных и
        пороговых маркеров.
        """
        lines = [raw.strip().lower() for raw in text.splitlines()]
        for index, line in enumerate(lines):
            if not line or len(line) > TABULAR_LINE_MAX:
                continue
            if not param_word_re.search(line):
                continue
            pair = [line]
            if index + 1 < len(lines) and lines[index + 1]:
                pair.append(lines[index + 1])
            if any(len(part) > TABULAR_LINE_MAX for part in pair):
                continue
            joined = " ".join(pair)
            if not primary_stem or primary_stem not in joined:
                continue
            if (_PROC_WINDOW_RE.search(joined)
                    or _THRESHOLD_RE.search(joined)):
                continue
            if unit_re.search(joined):
                return True
        return False

    #: Главный объектный стем вопроса (первый): обязателен в окне/строке.
    #: ANY-семантика по всем объектам признавала значением окно
    #: процедуры проверки прибора с «двигателем» без «масла» (живой
    #: случай e35 — л.232 кн.4 «увеличивайте давление в приемниках…»).
    primary_stem = object_stems[0] if object_stems else ""

    def _has_value(fragment: Fragment) -> bool:
        """Лидер с числом с единицей у параметра (без конструкции).

        Только для ЛИДЕРА (решение владельца Ф1.29: лидер со значением
        не трогается), перебор по этому уровню не ведётся — иначе
        процедуры («увеличивайте давление…») и пороги сигнализации
        вытесняют эталонных лидеров. Латин-фикс единиц — см. выше.
        Ф1.34: если вхождений якоря на листе нет — fallback по стемам
        класса параметра (как в :func:`pdfgraph.norms._valid_windows`).
        """
        try:
            text = store.page_text(fragment.document, fragment.page)
        except (sqlite3.Error, TypeError, ValueError, AttributeError):
            return False
        if not text:
            return False
        text_norm = normalize_for_search(clean_search_text(text))
        if substitute_stems and not any(stem in text_norm
                                        for stem in substitute_stems):
            return False

        def _find(stem: str) -> bool:
            """Число с единицей у параметра в окне вхождения стема."""
            for match in re.finditer(re.escape(stem), text_norm):
                start = max(0, match.start() - WINDOW_BACK)
                end = match.end() + WINDOW_FORWARD
                window_ext = text_norm[start:end + WINDOW_OBJECT_EXT]
                if not primary_stem or primary_stem not in window_ext:
                    continue
                if unit_re.search(text_norm[start:end]):
                    return True
            return False

        if _find(anchor):
            return True
        # Ф1.34: якоря на листе нет — fallback по классу параметра.
        if not any(re.finditer(re.escape(anchor), text_norm)):
            from pdfgraph.norms import _class_stems

            for stem in _class_stems(parameter_stem):
                if stem != anchor and _find(stem):
                    return True
        return False

    def _windows(fragment: Fragment) -> tuple[bool, bool]:
        """(полное окно нормы, табличная норма) на листе фрагмента."""
        try:
            text = store.page_text(fragment.document, fragment.page)
        except (sqlite3.Error, TypeError, ValueError, AttributeError):
            return False, False
        if not text:
            return False, False
        text_norm = normalize_for_search(clean_search_text(text))
        if substitute_stems and not any(stem in text_norm
                                        for stem in substitute_stems):
            return False, False
        if _valid_windows(text_norm, anchor,
                          [primary_stem] if primary_stem else object_stems,
                          parameter_stem) > 0:
            return True, True
        return False, _tabular_norm(text)

    def _first_with(index: int, position: int) -> int | None:
        """Позиция первого фрагмента с ``windows[position]`` истинным."""
        for offset, fragment in enumerate(fragments[index:VALUE_SCAN_LIMIT],
                                          start=index):
            full, tabular = _windows(fragment)
            if (full, tabular)[position]:
                return offset
        return None

    # Лидер: полное окно, табличная норма или значение у параметра →
    # "full" без перестановки (лидер со значением не трогается).
    leader_full, leader_tabular = _windows(fragments[0])
    if leader_full or leader_tabular or _has_value(fragments[0]):
        return fragments, "full", _expansion_note(fragments[0])
    # Перестановка: сначала полный лист нормы, затем табличная норма;
    # по value-уровню перебора нет (см. докстринг _has_value).
    candidate = _first_with(1, 0)
    if candidate is None:
        candidate = _first_with(1, 1)
    if candidate is not None:
        picked = fragments[candidate]
        reordered = ([picked]
                     + [f for f in fragments if f is not picked])
        return reordered, "full", _expansion_note(picked)
    return fragments, "mention", _mention_note(question, parameter)


def _build_answer_core(
    store: Store,
    question: str,
    aircraft_type: str | None = None,
    answerer: Answerer | None = None,
    min_score: float = 0.35,
    use_rag_fusion: bool = False,
    use_synthesis: bool = False,
    llm_settings=None,
    search_mode: str | None = None,
) -> Answer:
    """Retrieve evidence and construct an answer, refusing unsupported queries.

    Call :func:`pdfgraph.verify.verify_citations` before showing any returned
    answer, regardless of which ``answerer`` implementation produced it.

    With ``use_rag_fusion`` the retrieval goes through
    :func:`pdfgraph.rag_fusion.rag_search` (LLM query expansion + RRF); the
    module is imported lazily so the default path stays free of it. Without
    it, ordinary :func:`pdfgraph.query.search` is used. With a configured
    ``llm_settings`` and ``use_synthesis``, the answer is synthesized from the
    retrieved fragments and each citation's exact snippet is verified. The
    ``min_score`` threshold and existing positional arguments are unchanged.

    ``search_mode`` переопределяет режим гибридного поиска из
    ``PDFGRAPH_SEARCH_MODE`` (:func:`pdfgraph.query.effective_search_mode`):
    в гибридных режимах лексическая выдача и семантические листы
    (:func:`pdfgraph.query.vector_search`) сливаются по рангам через
    :func:`pdfgraph.rag_fusion.fuse_hybrid` (RRF; k и буст точных веток —
    из конфига), и только после слияния отсекается топ-10; пустая
    лексика в режиме по умолчанию по-прежнему даёт отказ без вызова
    модели. Лист,
    помеченный «Продолжение табл.», расширяется предыдущим листом как
    дословным фрагментом ``kind="continuation"`` (источник цитирует
    «листы N–N+1», когда подтверждены оба листа).
    """
    if use_rag_fusion:
        from pdfgraph.rag_fusion import rag_search
        fragments = rag_search(store, question, aircraft_type, limit=10,
                               settings=llm_settings, search_mode=search_mode)
    else:
        # Лексический поиск остаётся патчабельным входом существующих
        # тестов (они подменяют answer_module.search); векторный канал
        # подключается по режиму ВТОРЫМ каналом слияния (Ф1.1): RRF до
        # отсечения, k и буст точных веток — из конфига rag_fusion,
        # топ-10 ответа формируется только после слияния.
        from pdfgraph.rag_fusion import (FUSION_CHANNEL_DEPTH, fuse_hybrid,
                                         make_specificity_fn)
        from pdfgraph.rerank import effective_rerank_enabled

        channel_depth = max(10, FUSION_CHANNEL_DEPTH)
        # Ф1.16: при включённом переранжировщике лексика отдаёт в пул
        # топ-50 (широкий пул карточки); с выключенным — прежние топ-10,
        # порядок байт-в-байт прежний.
        rerank_on = effective_rerank_enabled()
        fragments = search(store, question, aircraft_type, channel_depth)[
            :channel_depth if rerank_on else 10]
        mode_now = (effective_search_mode()
                    if search_mode is None else search_mode)
        # Ф1.9/Ф1.9б: точный номер карты (с суффиксами или без) — без
        # слияния с семантикой (та же причина, что в query.hybrid_search:
        # шум и зависимость специфичности от написания номера).
        if techcard_query(question):
            pass
        elif mode_now != "lexical" and (mode_now == "hybrid_full" or fragments):
            vector_ranking = vector_search(store, question,
                                           limit=channel_depth,
                                           aircraft_type=aircraft_type)
            fragments = fuse_hybrid(fragments, vector_ranking,
                                    limit=10,
                                    spec_fn=make_specificity_fn(
                                        store, question),
                                    store=store, question=question,
                                    aircraft_type=aircraft_type)
    # Лист-продолжение таблицы расширяется предыдущим листом: дословный
    # кусок с kind="continuation" (Ф1.0). Вызывается после любой ветки
    # поиска — к отказу (пустой список) ничего не добавляет.
    fragments = expand_continuations(store, fragments)
    mode = "rag-fusion" if use_rag_fusion else "обычный поиск"
    synthesis_enabled = bool(
        use_synthesis and llm_settings is not None
        and getattr(llm_settings, "api_key", "").strip()
    )
    if synthesis_enabled:
        mode += "+aitunnel"
    best_score = max((fragment.score for fragment in fragments), default=0.0)
    logger.debug("question=%r mode=%s -> %d fragments, best_score=%.4f "
                 "(min_score=%.3f)", question, mode, len(fragments),
                 best_score, min_score)
    if not fragments:
        logger.debug("question=%r -> refusal (%s)", question,
                     "нет фрагментов")
        return Answer(
            question=question,
            claims=[],
            fragments=fragments,
            refusal=True,
            reason="нет подтверждений в документах",
        )

    # Ф1.3б + Ф1.4: гейты отказа по конкретике и третий исход — уточняющий
    # вопрос. Отказ остаётся для чужой техники, провала aircraft_type и
    # гейт-провала на однородной выдаче (уточнять нечем); поиск, слияние
    # и verify_citations не затрагиваются (verify вызывается дальше).
    from pdfgraph.gates import decide_outcome
    outcome = decide_outcome(store, question, fragments)
    if outcome.decision == "refuse":
        logger.debug("question=%r -> refusal (gates): %s", question,
                     outcome.reason)
        return Answer(
            question=question,
            claims=[],
            fragments=fragments,
            refusal=True,
            reason=outcome.reason,
        )
    if outcome.decision == "clarify":
        logger.debug("question=%r -> clarify (%s)", question, outcome.reason)
        return Answer(
            question=question,
            claims=[],
            fragments=outcome.fragments,
            reason=outcome.reason,
            clarification={
                "decision": outcome.decision,
                "question": outcome.clarify_question,
                "reason": outcome.reason,
                "options": list(outcome.options),
            },
        )

    if synthesis_enabled:
        from pdfgraph.rag_fusion import classify_answerability
        classification = classify_answerability(
            llm_settings, question, fragments,
        )
        decision = classification.get("decision")
        if decision == "clarify":
            return Answer(
                question=question,
                claims=[],
                fragments=fragments,
                reason=classification.get("reason", ""),
                clarification=classification,
            )
        if decision == "refuse":
            return Answer(
                question=question,
                claims=[],
                fragments=fragments,
                refusal=True,
                reason=classification.get(
                    "reason", "нет подтверждений в документах"),
            )
        if decision != "answer":
            return Answer(
                question=question,
                claims=[],
                fragments=fragments,
                refusal=True,
                reason="нет подтверждений в документах",
            )

    if best_score < min_score:
        logger.debug("question=%r -> refusal (best_score %.4f < %.3f)",
                     question, best_score, min_score)
        return Answer(
            question=question,
            claims=[],
            fragments=fragments,
            refusal=True,
            reason="нет подтверждений в документах",
        )

    # Ф1.29: лидер ответа — лист со значением, не с упоминанием.
    # ПОСЛЕ гейтов отказа/уточнения и min_score: refusal/clarify метрики
    # не меняются; best_score — max по списку, от порядка не зависит;
    # claims строятся ПОСЛЕ перестановки, Claim.evidence корректен.
    fragments, answer_state, mention_note = _value_leader_state(
        store, question, fragments)
    logger.debug("question=%r -> answer_state=%s", question, answer_state)

    if synthesis_enabled:
        from pdfgraph.rag_fusion import synthesize_answer
        synthesis = synthesize_answer(
            store, question, fragments, llm_settings,
            answerability_checked=True,
        )
        if synthesis.get("refusal"):
            return Answer(
                question=question,
                claims=[],
                fragments=fragments,
                refusal=True,
                reason=synthesis.get("reason", "LLM недоступен"),
            )

        answer_text = str(synthesis.get("answer", "")).strip()
        citations = synthesis.get("citations", [])
        by_location = {
            (fragment.document, fragment.page): index
            for index, fragment in enumerate(fragments)
        }
        claims: list[Claim] = []
        if not citations:
            claims.append(Claim(
                text="",
                evidence=[],
                answer_text=f"[НЕ ПОДТВЕРЖДЕНО] {answer_text}".strip(),
            ))
        else:
            for citation_index, citation in enumerate(citations):
                document = str(citation.get("document", ""))
                snippet = str(citation.get("snippet", ""))
                try:
                    page = int(citation.get("page"))
                except (TypeError, ValueError):
                    page = -1
                fragment_index = by_location.get((document, page), -1)
                claims.append(Claim(
                    text=snippet,
                    evidence=[fragment_index],
                    # Show the synthesis once; following rows are separate
                    # source citations for the same answer.
                    answer_text=answer_text if citation_index == 0 else "",
                ))

        synthesized = Answer(
            question=question,
            claims=claims,
            fragments=fragments,
            answer_state=answer_state,
        )
        # Ф1.29: честная фраза «mention» — строкой-статусом, не цитатой.
        if mention_note:
            synthesized.param_norm_missing = mention_note
        # Verify the source snippets before displaying the LLM prose. The GUI
        # rechecks the same evidence before rendering its verified stamp.
        from pdfgraph.verify import verify_citations
        checks = verify_citations(synthesized, store)
        for claim_index, claim in enumerate(synthesized.claims):
            claim_checks = [check for check in checks
                            if check.claim_index == claim_index]
            if not claim_checks or not all(check.ok for check in claim_checks):
                marker = "[НЕ ПОДТВЕРЖДЕНО]"
                claim.answer_text = (
                    f"{marker} {answer_text}".strip()
                    if claim.answer_text else marker
                )
        return synthesized

    selected_answerer = answerer if answerer is not None else ExtractiveAnswerer()
    answer = selected_answerer.answer(question, fragments)
    answer.answer_state = answer_state
    # Ф1.29: честная фраза «mention» — строкой-статусом, не цитатой
    # (CLI и веб печатают param_norm_missing уже существующим путём).
    if mention_note:
        answer.param_norm_missing = mention_note
    logger.debug("question=%r -> answer with %d claims",
                 question, len(answer.claims))
    return answer


def build_answer(
    store: Store,
    question: str,
    aircraft_type: str | None = None,
    answerer: Answerer | None = None,
    min_score: float = 0.35,
    use_rag_fusion: bool = False,
    use_synthesis: bool = False,
    llm_settings=None,
    search_mode: str | None = None,
) -> Answer:
    """Retrieve evidence and construct an answer, refusing unsupported queries.

    Контракт и все параметры прежние (см. :func:`_build_answer_core`).
    Ф1.7: ответ дополняется окном соседних листов ``neighbors`` к топ-5
    (отдельный список ссылок с подписью «может быть продолжение»,
    без цитат); выдача и порядок топ-5 не меняются. Флаг
    ``PDFGRAPH_NEIGHBORS=0`` отключает окно — ответ идентичен прежнему.
    Ф0.3в: применимость по борту вопроса — честный отказ «документации
    по борту N в базе нет» (п. 4) и пометка на ответе, если лидер —
    дополнение к чужим бортам (п. 3); цитаты и verify не затронуты.
    """
    answer = _build_answer_core(
        store, question, aircraft_type, answerer, min_score,
        use_rag_fusion, use_synthesis, llm_settings, search_mode)
    reason, warning = applicability_verdict(store, question,
                                            answer.fragments)
    if reason and not answer.refusal:
        answer = Answer(question=question, claims=[],
                        fragments=answer.fragments, refusal=True,
                        reason=reason)
    # Ф2.10: вопрос о параметре — поверх готовой выдачи (п. 5 карточки:
    # поиск, гейты, verify, веса не тронуты). «Нормы не нашёл» — отказ
    # по решению владельца, как в Ф0.3в; листы выдачи сохраняются во
    # фрагментах, статус печатается CLI и вебом через поле
    # param_norm_missing. Уточнение (rc=3) не переопределяется.
    param_status = param_norm_verdict(store, question, answer.fragments)
    if param_status and not answer.refusal and not answer.clarification:
        answer = Answer(question=question, claims=[],
                        fragments=answer.fragments, refusal=True,
                        reason=param_status, param_norm_missing=param_status)
    answer.neighbors = collect_neighbors(store, answer.fragments)
    answer.applicability_warning = warning
    # Ф1.9: карты из списка суффиксов, которых нет в базе; строкой, не
    # цитатой. Заполняется и при отказе — инженеру полезно знать, какие
    # именно карты не нашлись, даже когда ответа нет.
    answer.techcard_missing = techcard_missing_suffixes(
        store, question, aircraft_type)
    answer.techcard_more = techcard_section_more(
        store, question, aircraft_type)
    # Ф2.5: карточки карт вместо человеческого абзаца; Claim.text
    # (дословный сниппет-цитата) и verify не меняются.
    answer.techcard = collect_techcards(store, question, answer.fragments)
    # Ф2.3: человеческое начало ответа — Claim.answer_text; Claim.text
    # (дословный сниппет-цитата) и verify не меняются. Для карточных
    # ответов абзац не собирается (Ф2.5).
    if not answer.refusal and answer.claims and not answer.techcard:
        from pdfgraph.humanize import lead_paragraph

        leader = answer.fragments[0] if answer.fragments else None
        if leader is not None and answer.claims[0].answer_text is None:
            paragraph, _fallback = lead_paragraph(store, question, leader)
            answer.claims[0].answer_text = paragraph or None
    return answer
