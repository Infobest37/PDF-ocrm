"""Search over the knowledge graph: code, part-number and BM25 text branches.

Three deterministic branches, tried in order:

1. **Code query** — a query that survives :func:`pdfprepare.codes.normalize_code`
   returns every sheet stored under that ``system_code``/``subsystem_code``
   (``kind="code"``, ``score=1.0``).
2. **Part-number query** — a query whose :func:`normalize_part_number` key
   matches an existing ``part`` node returns the sheets whose tables carry it
   (``kind="part"``, ``score=1.0``).
3. **Free text** — FTS5 ``MATCH`` over the stored search text with ``bm25()``
   ranking (``kind="text"``). The query is tried as all-tokens AND, then OR,
   then all-tokens prefix queries using FTS5 ``*``. If those miss, a trigram
   FTS5 index performs indexed substring searches with one-character typo
   tolerance. The score is normalised to the best hit of the branch:
   ``score = penalty * |rank| / max(|rank|)`` — the branch leader carries the
   full weight (1.0 for AND, 0.8 for OR, 0.6 for prefix, 0.5 for trigram) and
   weaker hits scale down proportionally. Stage 2: the previous absolute scale
   (``penalty / (1 + |bm25|)``) gave ~0.15 for real РЭ pages — every real
   question sat under ``answer.min_score=0.35``, so the graph refused all
   answers («не ищет»); the relative scale makes the threshold meaningful.

4. **Semantic channel (Ф1.0)** — optional, off in the pure-lexical mode:
   :func:`vector_search` runs cosine search over per-page e5-small ONNX
   int8 embeddings (:mod:`pdfgraph.embedding`) and returns ``kind="semantic"``
   fragments whose snippets are verbatim chunk slices and whose scores are
   mapped into the exclusive band ``(0.9901, 1.0]``, so exact code/part hits
   (``score=1.0``) stay unbeaten while genuinely weak lexical hits can be
   reinforced. :func:`hybrid_search` fuses that channel with the lexical
   ranking BY RANK (RRF; ``k`` and the exact-branch boost come from the
   fusion config, Ф1.1) and cuts the top-N only after the fusion,
   according to ``PDFGRAPH_SEARCH_MODE`` (:func:`effective_search_mode`).

Snippet honesty (anti-goal "не искажать документ"): ``Fragment.snippet`` is
always a verbatim slice of the ORIGINAL stored page text —
``store.page_text(document, page)[char_start:char_end] == snippet`` holds for
every hit. Because FTS matches normalized text, a position-mapped
normalization (:func:`_search_index`) translates normalized-query offsets
back onto original-text offsets before the window is taken.
"""

import logging
import os
import re
from dataclasses import dataclass, replace

from pdfprepare.codes import normalize_code
from pdfprepare.normalize import normalize_for_search, normalize_part_number
from pdfgraph.gates import card_suffix_occurrences
from pdfgraph.store import Store

#: Snippet window length in characters of original page text.
SNIPPET_CHARS = 200
#: Preferred number of original-text characters kept before the match.
SNIPPET_LEAD = 60
#: Score multiplier for the tolerant OR retry of the text branch (fix round 1).
OR_PENALTY = 0.8
#: Score multiplier for the broader FTS5 token-prefix retry.
PREFIX_PENALTY = 0.6
#: Score multiplier for the trigram-index fallback.
TRIGRAM_PENALTY = 0.5
#: Environment variable selecting the hybrid search mode; read on every
#: :func:`effective_search_mode` call, never cached.
SEARCH_MODE_ENV = "PDFGRAPH_SEARCH_MODE"
#: Default mode: the vector channel joins in only when the lexical branches
#: found something (see :func:`hybrid_search`).
DEFAULT_SEARCH_MODE = "hybrid_gated"
_SEARCH_MODES = ("lexical", "hybrid_gated", "hybrid_full")
_TECHCARD_QUERY_RE = re.compile(
    r"^(?:\d{2,3}\.\d{2}\.\d{2}\s*[a-zа-я]\d?|№?\s*\d{2,4})$",
    re.IGNORECASE,
)

# --- Ф1.9: поиск по номерам технологических карт ----------------------------

#: Кириллические двойники букв суффикса (поиск не переписывает документы:
#: приведение к латинице — только ключ запроса, не поле документа).
#: Обязана совпадать с _SUFFIX_HOMOGLYPHS в pdfgraph.gates (Ф1.9в) —
#: рассинхрон ловит тест test_homoglyph_tables_in_sync (Ф1.9д: без
#: к/м/т/в/н search не находил карты, которые видит гейт).
_TECHCARD_HOMOGLYPHS = {"а": "a", "с": "c", "е": "e", "о": "o", "р": "p",
                        "х": "x", "у": "y", "к": "k", "м": "m", "т": "t",
                        "в": "b", "н": "h"}
#: Запрос карты: необязательное слово «(технологическая) карта(-ы)», номер
#: NNN.NN.NN (пробелы и точки-разделители не значимы: 035.10.00e,
#: 035.10.00 e и 0351000e — один номер), суффиксы через запятую. После
#: буквы суффикса допустим индекс — одна цифра (Ф2.8: «033.10.00а1»);
#: «035.10е.00» (буква внутри номера) картой не считается.
_TECHCARD_EXPR_RE = re.compile(
    r"(?:(?:технологическая\s+)?карт[аы]\s+)?"
    r"(\d{3})[\s.]*(\d{2})[\s.]*(\d{2})\s*"
    r"([a-zа-яё]\d?(?:\s*,\s*[a-zа-яё]\d?)*)?",
    re.IGNORECASE,
)

# --- Ф1.9б: карты на виду, роль документа -----------------------------------

#: Роль документа результата: руководство («карта, как выполняется») или
#: регламент («когда выполняется»). Определяется по имени файла:
#: documents.family хранит семейство ВС, а не роль (см. docs/PITFALLS.md).
ROLE_MANUAL = "manual"
ROLE_REGLAMENT = "reglament"
#: Подписи ролей результата (Ф1.9б).
ROLE_LABELS = {
    ROLE_MANUAL: "карта, как выполняется",
    ROLE_REGLAMENT: "когда выполняется",
}
#: Сколько карт раздела попадает в выдачу при запросе без буквы (Ф1.9б);
#: остаток — :func:`techcard_section_more` → ``Answer.techcard_more``.
TECHCARD_SECTION_LIMIT = 10
_ROLE_REGLEMENT_RE = re.compile(r"регламент", re.IGNORECASE)
#: Кэш роли по (id(store), документ) — тот же приём, что _CORPUS_TYPE_CACHE
#: в pdfgraph.gates.
_DOCUMENT_ROLE_CACHE: dict[tuple[int, str], str] = {}
_TECHCARD_CODE_WITH_SUFFIX_RE = re.compile(
    r"^\d{2,3}\.\d{2}\.\d{2}\s*[a-zа-я]\d?$", re.IGNORECASE,
)
_TECHCARD_NUMBER_RE = re.compile(r"^№?\s*\d{2,4}$")
_REGULATION_DOCUMENT_TERMS = ("Регламент", "РЕГЛАМЕНТ", "регламент")
_TECHNICAL_MANUAL_DOCUMENT_TERMS = (
    "РТЭ", "ртэ", "Руководство", "РУКОВОДСТВО", "руководство",
)

#: Stage-2 diagnostics: question → branch → SQL → count is logged at DEBUG
#: (visible with ``pdfgraph ask --debug``).
logger = logging.getLogger(__name__)


@dataclass
class Fragment:
    """One search hit: where it came from and the verbatim quote."""

    document: str
    page: int
    system_code: str
    subsystem_code: str
    snippet: str
    char_start: int
    char_end: int
    score: float
    kind: str
    #: Ф1.9б: роль документа результата («manual»/«reglament»), только
    #: для карт; подпись — :data:`ROLE_LABELS`. Аддитивно, по умолчанию ''.
    role: str = ""


def _search_index(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize ``text`` for search, keeping a per-character position map.

    Mirrors :func:`pdfprepare.normalize_for_search` character by character,
    so ``spans[i]`` is the ``(start, end)`` slice of ``text`` that produced
    normalized character ``i``. This is what lets a match found in the
    normalized text be reported as an exact slice of the original.
    """
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            out.append(" ")
            spans.append((i, j))
            i = j
            continue
        for mapped in normalize_for_search(ch):
            out.append(mapped)
            spans.append((i, i + 1))
        i += 1
    # Same .strip() the public normalizer applies (runs collapsed to " ").
    while out and out[0] == " ":
        out.pop(0)
        spans.pop(0)
    while out and out[-1] == " ":
        out.pop()
        spans.pop()
    return "".join(out), spans


def _match_span(norm: str, spans: list[tuple[int, int]], query: str) -> tuple[int, int] | None:
    """Locate ``query`` (normalized) in the mapped index; return an original-text span.

    Prefers the whole query; failing that, the first token (in query order)
    that occurs, so the reported window can be centered on what matched.
    """
    candidates = [query] + query.split()
    for needle in candidates:
        if not needle:
            continue
        pos = norm.find(needle)
        if pos != -1:
            return spans[pos][0], spans[pos + len(needle) - 1][1]
    return None


def _window(text: str, match: tuple[int, int] | None) -> tuple[str, int, int]:
    """200-character slice of ``text`` centered on ``match`` (or from 0)."""
    if match is None:
        start = 0
    else:
        start = max(0, min(match[0] - SNIPPET_LEAD, max(0, len(text) - SNIPPET_CHARS)))
    end = min(start + SNIPPET_CHARS, len(text))
    return text[start:end], start, end


def _fragment(store: Store, document: str, page: int, system_code: str,
              subsystem_code: str, score: float, kind: str, needle: str,
              role: str = "") -> Fragment:
    text = store.page_text(document, page)
    norm, spans = _search_index(text)
    match = _match_span(norm, spans, needle) if norm else None
    snippet, char_start, char_end = _window(text, match)
    return Fragment(
        document=document, page=page, system_code=system_code,
        subsystem_code=subsystem_code, snippet=snippet,
        char_start=char_start, char_end=char_end, score=score, kind=kind,
        role=role,
    )


def _fts_expr(tokens: list[str], joiner: str) -> str:
    """Join search tokens into an FTS5 MATCH expression of quoted phrases."""
    return joiner.join('"{}"'.format(token.replace('"', '""')) for token in tokens)


def _fts_prefix_expr(tokens: list[str], joiner: str) -> str:
    """Join tokens as FTS5 prefix queries."""
    return joiner.join(
        '"{}"*'.format(token.replace('"', '""')) for token in tokens
    )


def _trigram_patterns(token: str) -> list[str]:
    """Build indexed substring patterns, including one-character typos."""
    if len(token) < 4 or "%" in token or "_" in token:
        return []

    patterns = [f"%{token}%"]
    if len(token) >= 5:
        patterns.extend(
            f"%{token[:i]}_{token[i + 1:]}%"
            for i in range(len(token))
            if max(i, len(token) - i - 1) >= 3
        )
    return patterns


def search(
    store: Store,
    query: str,
    aircraft_type: str | None = None,
    limit: int = 10,
) -> list[Fragment]:
    """Return ranked fragments for ``query`` across the graph.

    Branches: normalized code → sheets of that code; existing part-number key
    → sheets carrying that part; otherwise FTS5/BM25 over the whole query
    (all tokens, AND), a tolerant OR retry, an all-token prefix retry, and
    finally a trigram-indexed substring fallback. Text-branch scores are
    normalised to the best hit of the branch (see :func:`_by_fts`).
    Ф1.9: запрос карты — номер NNN.NN.NN с суффиксами (:func:`parse_techcard_query`)
    — обрабатывается раньше прочих веток (:func:`_search_techcard`).
    ``aircraft_type`` restricts hits to pages of that type. A topic absent
    from the index returns ``[]``.
    """
    stripped = query.strip()
    if not stripped:
        return []

    # Ф1.9: запрос карты — номер (и суффиксы) плюс необязательное слово
    # «карта»; обработка раньше прочих веток.
    techcard = parse_techcard_query(stripped)
    if techcard is not None:
        base, suffixes = techcard
        return _search_techcard(store, base, suffixes, aircraft_type, limit)

    if _TECHCARD_QUERY_RE.fullmatch(stripped):
        number_query = _TECHCARD_NUMBER_RE.fullmatch(stripped) is not None
        suffixed_code = (
            _TECHCARD_CODE_WITH_SUFFIX_RE.fullmatch(stripped) is not None
        )
        # Preserve the established exact-code route when an unsuffixed ATA
        # code already resolves to system-code sheets.
        if not number_query and not suffixed_code:
            ordinary = _search_ordinary(store, stripped, aircraft_type, limit)
            if ordinary:
                return ordinary

        needle = normalize_for_search(stripped)
        regulation = _by_fts(
            store, needle, aircraft_type, limit=1,
            document_terms=_REGULATION_DOCUMENT_TERMS,
        )
        technical_manual = _by_fts(
            store, needle, aircraft_type, limit=1,
            document_terms=_TECHNICAL_MANUAL_DOCUMENT_TERMS,
        )
        techcards = [
            replace(fragment, kind="techcard")
            for fragment in regulation[:1] + technical_manual[:1]
        ]

        # A bare sheet number is ambiguous: keep ordinary matches too.
        if number_query:
            seen = {(fragment.document, fragment.page)
                    for fragment in techcards}
            for fragment in _search_ordinary(
                    store, stripped, aircraft_type, limit):
                key = (fragment.document, fragment.page)
                if key not in seen:
                    seen.add(key)
                    techcards.append(fragment)
        elif not techcards:
            return _search_ordinary(store, stripped, aircraft_type, limit)
        return techcards

    return _search_ordinary(store, stripped, aircraft_type, limit)


def parse_techcard_query(query: str) -> tuple[str, list[str]] | None:
    """Ф1.9: разобрать запрос карты → (база «NNN.NN.NN», суффиксы) | None.

    Пробелы и точки внутри номера не значимы, суффикс — буква с
    необязательным индексом-цифрой (Ф2.8: «033.10.00а1»), список
    суффиксов — через запятую («035.10.00а,b,e»); кириллические двойники
    букв приводятся к латинице. Буква внутри номера («035.10е.00») картой
    не считается — возвращается None. Без суффиксов суффикс-список пуст.
    """
    match = _TECHCARD_EXPR_RE.fullmatch(query.strip())
    if match is None:
        return None
    d1, d2, d3, raw_suffixes = match.groups()
    suffixes: list[str] = []
    if raw_suffixes:
        for chunk in raw_suffixes.split(","):
            token = chunk.strip().lower()
            index = token[-1] if token[-1:].isdigit() else ""
            letter = token[:-1] if index else token
            letter = _TECHCARD_HOMOGLYPHS.get(letter, letter)
            suffix = f"{letter}{index}"
            if suffix and suffix not in suffixes:
                suffixes.append(suffix)
    return f"{d1}.{d2}.{d3}", suffixes


def _techcard_matches(store: Store, base: str, aircraft_type: str | None
                      ) -> list[tuple[str, int, str, str, str]]:
    """Карты базы ``base`` в корпусе: (документ, лист, коды, суффикс).

    Кандидаты — листы, чей нормализованный текст содержит номер в любой из
    форм (с точками, пробелами, слитно); точный суффикс (буква, двойники,
    индекс; «;»/«:» — только в зоне заголовка карты) признаёт
    :func:`pdfgraph.gates.card_suffix_occurrences` по исходному тексту
    листа — в FTS-теле переносы строк схлопнуты, и зону по строкам не
    посчитать (Ф2.8). Дубликаты (суффикс, лист) убираются; мусор и
    документы-дубли скрыты штатными фильтрами хранилища.
    """
    sql = (
        "SELECT pg.document, pg.page, pg.system_code, pg.subsystem_code, "
        "fts.body FROM pages_fts AS fts "
        "JOIN pages AS pg ON pg.document = fts.document "
        "AND pg.page = CAST(fts.page AS INTEGER) "
        "WHERE (fts.body LIKE ? OR fts.body LIKE ? OR fts.body LIKE ?)"
    )
    d1, d2, d3 = base.split(".")
    args: list[object] = [f"%{d1}.{d2}.{d3}%", f"%{d1} {d2} {d3}%",
                          f"%{d1}{d2}{d3}%"]
    if aircraft_type is not None:
        sql += " AND pg.aircraft_type = ?"
        args.append(aircraft_type)
    sql += store._dup_filter("pg.document")
    sql += store._garbage_filter("pg.")
    sql += " ORDER BY pg.document, pg.page"
    matches: list[tuple[str, int, str, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for document, page, system_code, subsystem_code, _body \
            in store._connection.execute(sql, args):
        text = store.page_text(document, page) or ""
        for found_base, suffix, _position in card_suffix_occurrences(text):
            if found_base != base:
                continue
            key = (document, page, suffix)
            if key in seen:
                continue
            seen.add(key)
            matches.append((document, page, system_code, subsystem_code,
                            suffix))
    return matches


def _search_techcard(store: Store, base: str, suffixes: list[str],
                     aircraft_type: str | None, limit: int
                     ) -> list[Fragment]:
    """Ф1.9: выдача по номеру карты.

    С суффиксами — каждая запрошенная карта отдельным результатом
    (kind="techcard", score=1.0), ВСЕ листы карты, в порядке суффиксов
    запроса; карта, которой нет в базе, просто не возвращается (её честно
    перечисляет ``Answer.techcard_missing``, Ф1.9). Без суффиксов — раздел
    (Ф1.9б): карты раздела ВЫШЕ листов раздела, больше
    :data:`TECHCARD_SECTION_LIMIT` — первые по суффиксам, остаток в
    ``Answer.techcard_more`` (:func:`techcard_section_more`).
    """
    matches = _techcard_matches(store, base, aircraft_type)
    by_suffix: dict[str, list[tuple[str, int, str, str, str]]] = {}
    for row in matches:
        by_suffix.setdefault(row[4], []).append(row)
    if suffixes:
        # Ф1.9б: внутри карты руководства выше регламента, затем документ/лист.
        fragments: list[Fragment] = []
        for suffix in suffixes:
            for document, page, system_code, subsystem_code, _suffix \
                    in sorted(by_suffix.get(suffix, []),
                              key=lambda row: _role_row_key(store, row)):
                fragments.append(_fragment(
                    store, document, page, system_code, subsystem_code,
                    1.0, "techcard", base,
                    role=document_role(store, document)))
        return fragments[:max(limit, 0)]
    # Лист регламента может нести несколько карт — дедупликация по
    # (суффикс, лист), иначе свод-форма скроет все карты, кроме первой.
    # Ф2.8: базовая карта (без индекса) выше карты-дополнения с индексом.
    cards: list[Fragment] = []
    card_sheets: set[tuple[str, int]] = set()
    for suffix in sorted(by_suffix,
                         key=lambda item: (item[1:] != "", item))[
                :TECHCARD_SECTION_LIMIT]:
        for document, page, system_code, subsystem_code, _suffix \
                in sorted(by_suffix[suffix],
                          key=lambda row: _role_row_key(store, row)):
            cards.append(_fragment(
                store, document, page, system_code, subsystem_code,
                1.0, "techcard", base, role=document_role(store, document)))
            card_sheets.add((document, page))
    section = _search_ordinary(store, base, aircraft_type, limit)
    # Номер мог попасть в партномерную/текстовую ветку — карты перечисляем
    # только при раздел-выдаче (code-ветка) или пустой выдаче.
    if section and not any(f.kind == "code" for f in section):
        return section
    section = [f for f in section if (f.document, f.page) not in card_sheets]
    return cards + section
    return section


def document_role(store: Store, document: str) -> str:
    """Роль документа по имени файла (Ф1.9б): регламент или руководство."""
    key = (id(store), document)
    role = _DOCUMENT_ROLE_CACHE.get(key)
    if role is None:
        role = (ROLE_REGLAMENT if _ROLE_REGLEMENT_RE.search(document)
                else ROLE_MANUAL)
        _DOCUMENT_ROLE_CACHE[key] = role
    return role


def _role_row_key(store: Store, row: tuple[str, int, str, str, str]
                  ) -> tuple[int, str, int]:
    """Порядок листов карты: руководства выше регламента, затем документ/лист."""
    role = document_role(store, row[0])
    return (0 if role == ROLE_MANUAL else 1, row[0], row[1])


def techcard_query(question: str) -> bool:
    """Ф1.9/Ф1.9б: вопрос — точный номер карты (с суффиксами или без)?

    Такие запросы идут только по лексической ветке (см.
    :func:`hybrid_search` и слияние в ``answer._build_answer_core``):
    для точного номера семантика — шум, а слагаемое специфичности
    зависит от написания номера и перемешивает роли и листы.
    """
    return parse_techcard_query(question) is not None


def techcard_missing_suffixes(store: Store, question: str,
                              aircraft_type: str | None = None
                              ) -> list[str]:
    """Ф1.9: запрошенные карты, которых нет в базе («035.10.00e»).

    Пустой список — вопрос не про карты, суффиксов нет или весь комплект
    найден. Заполняется в :func:`pdfgraph.answer.build_answer` в
    ``Answer.techcard_missing``; verify и цитаты не затрагивает.
    """
    parsed = parse_techcard_query(question)
    if parsed is None:
        return []
    base, suffixes = parsed
    if not suffixes:
        return []
    # Ф1.9б: карта считается найденной, только когда она есть в руководстве
    # («как выполняется»); версия только в регламенте (периодичность) —
    # карта отсутствует, листы регламента при этом показываются (п. 4).
    found = {row[4] for row in _techcard_matches(store, base, aircraft_type)
             if document_role(store, row[0]) == ROLE_MANUAL}
    return [f"{base}{suffix}" for suffix in suffixes if suffix not in found]


def techcard_section_more(store: Store, question: str,
                          aircraft_type: str | None = None) -> int:
    """Ф1.9б: сколько карт раздела не попало в выдачу (после лимита 10).

    Не ноль — только для запроса раздела (без суффиксов); в
    :func:`pdfgraph.answer.build_answer` идёт в ``Answer.techcard_more``.
    """
    parsed = parse_techcard_query(question)
    if parsed is None or parsed[1]:
        return 0
    cards = {row[4]
             for row in _techcard_matches(store, parsed[0], aircraft_type)}
    return max(0, len(cards) - TECHCARD_SECTION_LIMIT)


def _search_ordinary(store: Store, stripped: str,
                     aircraft_type: str | None, limit: int) -> list[Fragment]:
    code = normalize_code(stripped)
    if code:
        logger.debug("branch=code %r (question=%r)", code, stripped)
        return _by_code(store, code, aircraft_type)

    part_key = normalize_part_number(stripped)
    if part_key and store.has_node("part", part_key):
        logger.debug("branch=part %r (question=%r)", part_key, stripped)
        return _by_part(store, part_key, aircraft_type)

    needle = normalize_for_search(stripped)
    if not needle:
        return []
    logger.debug("branch=fts (question=%r)", stripped)
    return _by_fts(store, needle, aircraft_type, limit)


def _by_code(store: Store, code: str,
             aircraft_type: str | None) -> list[Fragment]:
    needle = normalize_for_search(code)
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  1.0, "code", needle)
        for document, page, system_code, subsystem_code
        in store.sheets_by_code(code, aircraft_type)
    ]


def _by_part(store: Store, part_key: str,
             aircraft_type: str | None) -> list[Fragment]:
    needle = normalize_for_search(part_key)
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  1.0, "part", needle)
        for document, page, system_code, subsystem_code
        in store.sheets_with_part(part_key, aircraft_type)
    ]


def _by_fts(store: Store, needle: str, aircraft_type: str | None,
            limit: int,
            document_terms: tuple[str, ...] | None = None
            ) -> list[Fragment]:
    if limit <= 0:
        return []
    tokens = needle.split()
    rows = store.search_pages(
        _fts_expr(tokens, " AND "), aircraft_type, limit,
        document_terms=document_terms,
    )
    penalty = 1.0
    if not rows:
        # Ф1.12 (этап 1, решение владельца): префиксная ветка раньше OR —
        # та же точность, что AND, но с учётом словоформ; жертвовать ею
        # ради «хоть что-нибудь» нельзя. Штрафы и текст не менялись.
        rows = store.search_pages(
            _fts_prefix_expr(tokens, " AND "), aircraft_type, limit,
            document_terms=document_terms,
        )
        penalty = PREFIX_PENALTY
    if not rows:
        # Tolerant retry (fix round 3): a conversational query ("рисунок 12")
        # must still find documents phrased differently ("Рис. 12").
        rows = store.search_pages(
            _fts_expr(tokens, " OR "), aircraft_type, limit,
            document_terms=document_terms,
        )
        penalty = OR_PENALTY
    if not rows:
        trigram_hits: dict[tuple[str, int], tuple[str, int, str, str]] = {}
        for token in tokens:
            for pattern in _trigram_patterns(token):
                for row in store.search_trigrams(
                        pattern, aircraft_type,
                        document_terms=document_terms):
                    trigram_hits.setdefault((row[0], row[1]), row)
                    if len(trigram_hits) >= limit:
                        break
                if len(trigram_hits) >= limit:
                    break
            if len(trigram_hits) >= limit:
                break
        rows = [(*row, -1.0) for row in trigram_hits.values()]
        penalty = TRIGRAM_PENALTY
    logger.debug("fts: needle=%r tokens=%d penalty=%.1f hits=%d",
                 needle, len(tokens), penalty, len(rows))
    if not rows:
        return []
    # Stage 2: normalise the score to the best hit of the branch. bm25() is
    # negative and smaller means better, so |rank| is the raw relevance;
    # scaling by the branch maximum keeps the leader at the full weight
    # (1.0 for AND, the retry penalty, or the fixed trigram penalty) and turns
    # answer.build_answer's min_score into a real relative threshold instead
    # of a filter that removed every long real-world page hit.
    best_rank = max(abs(rank) for _, _, _, _, rank in rows)
    if best_rank <= 0.0:
        best_rank = 1.0
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  penalty * (abs(rank) / best_rank), "text", needle)
        for document, page, system_code, subsystem_code, rank in rows
    ]


# ---------------------------------------------------------------------------
# Ф1.0: гибридный поиск — три лексические ветки плюс локальный векторный канал


def effective_search_mode() -> str:
    """Текущий режим гибридного поиска: ``PDFGRAPH_SEARCH_MODE`` или дефолт.

    Читается при каждом вызове (без кэша). Неизвестное значение считается
    ``DEFAULT_SEARCH_MODE``, чтобы опечатка в переменной окружения не
    отключала отказ-инвариант и мусор-фильтр.
    """
    mode = os.environ.get(SEARCH_MODE_ENV, DEFAULT_SEARCH_MODE)
    return mode if mode in _SEARCH_MODES else DEFAULT_SEARCH_MODE


def _page_codes(store: Store, document: str, page: int) -> tuple[str, str]:
    """Коды системы/подсистемы листа; записи нет — ('', '')."""
    # Прямое чтение колонок pages — как в embedding.py (единый источник).
    row = store._connection.execute(
        "SELECT system_code, subsystem_code FROM pages "
        "WHERE document = ? AND page = ?",
        (document, page),
    ).fetchone()
    return ("", "") if row is None else (row[0], row[1])


def vector_search(store: Store, question: str, limit: int = 10,
                  model_dir: str | None = None,
                  aircraft_type: str | None = None) -> list[Fragment]:
    """Семантические фрагменты «вопрос — лист» поверх чанк-векторов.

    Каждый хит — дословный срез текста одного листа (границы даёт
    :func:`pdfgraph.embedding.chunk_span`); скор отображается в полосу
    ``(SEMANTIC_BASE_LOW, SEMANTIC_BASE_HIGH]``, чтобы точные ветки
    (код, номер листа) оставались впереди. Хранилище без индекса даёт
    ``[]`` без вызова модели: канал вспомогательный и отказ не поднимает.

    Ф0.3в: с заданным ``aircraft_type`` листы других типов не попадают
    в канал вовсе (та же семантика, что у лексических ветвей); пустая
    метка при заданном фильтре тоже отсекается (замер Ф0.3в: пустых
    меток 0 из 13 700).
    """
    from pdfgraph import embedding

    hits = embedding.search_chunks(store, question, model_dir=model_dir,
                                   limit=limit)
    if not hits:
        return []
    allowed = set(store.searchable_pages())
    if aircraft_type is not None:
        typed = {(row[0], row[1]) for row in store._connection.execute(
            "SELECT document, page FROM pages WHERE aircraft_type = ?",
            (aircraft_type,))}
        allowed &= typed
    fragments: list[Fragment] = []
    seen: set[tuple[str, int]] = set()
    for (document, page, chunk_ix), cosine in hits:
        if (document, page) not in allowed or (document, page) in seen:
            continue
        seen.add((document, page))
        text = store.page_text(document, page)
        stripped = text.strip()
        offset = text.find(stripped) if stripped else -1
        if offset < 0:
            continue
        start, end = embedding.chunk_span(stripped, chunk_ix)
        system_code, subsystem_code = _page_codes(store, document, page)
        fragments.append(Fragment(
            document=document, page=page,
            system_code=system_code, subsystem_code=subsystem_code,
            snippet=text[offset + start:offset + end],
            char_start=offset + start, char_end=offset + end,
            score=embedding.SEMANTIC_BASE_LOW
            + (embedding.SEMANTIC_BASE_HIGH - embedding.SEMANTIC_BASE_LOW)
            * max(0.0, min(1.0, cosine)),
            kind="semantic",
        ))
    return fragments


def hybrid_search(store: Store, question: str,
                  aircraft_type: str | None = None, limit: int = 10,
                  model_dir: str | None = None,
                  search_mode: str | None = None) -> list[Fragment]:
    """Каналы: лексика (:func:`search`) и вектор (:func:`vector_search`).

    Слияние по рангам через :func:`pdfgraph.rag_fusion.fuse_hybrid`
    (карточка Ф1.1: RRF, k и буст точных веток из конфига; отсечение
    топ-``limit`` — только после слияния, поэтому лист, который нашла
    только семантика, попадает в топ-5). ``lexical`` — только лексика,
    без слияния (контроль A); ``hybrid_gated`` (дефолт) — векторный канал
    вызывается только при непустой лексике (пустая лексика ведёт к ``[]``
    без вызова модели — отказ-инвариант); ``hybrid_full`` — вектор
    работает и на пустой лексике. Каждый канал отдаёт до
    ``max(limit, FUSION_CHANNEL_DEPTH)`` листов, поэтому ранги глубже
    топ-N участвуют в слиянии.
    """
    mode = (search_mode if search_mode in _SEARCH_MODES
            else effective_search_mode())
    if mode == "lexical" or techcard_query(question):
        # Ф1.9/Ф1.9б: точный номер карты — чисто лексическая ветка, без
        # слияния с семантикой: семантика на номере даёт только шум, а
        # слагаемое специфичности зависит от написания номера
        # («035.10.00e» ≠ «035.10.00 e») и перемешивает роли и листы.
        return search(store, question, aircraft_type, limit)
    # Импорт по месту: rag_fusion импортирует query наверху, а чисто
    # лексический путь не должен тянуть его на старте (замыкание цикла).
    from pdfgraph.rag_fusion import (FUSION_CHANNEL_DEPTH, fuse_hybrid,
                                     make_specificity_fn)

    depth = max(limit, FUSION_CHANNEL_DEPTH)
    lexical_ranking = search(store, question, aircraft_type, depth)
    if not lexical_ranking and mode == "hybrid_gated":
        return []  # гейт: векторный канал на пустой лексике не вызывается
    vector_ranking = vector_search(store, question, limit=depth,
                                   model_dir=model_dir,
                                   aircraft_type=aircraft_type)
    return fuse_hybrid(lexical_ranking, vector_ranking, limit,
                       spec_fn=make_specificity_fn(store, question))


def expand_continuations(store: Store,
                         fragments: list[Fragment]) -> list[Fragment]:
    """Дословный предыдущий лист для листов-продолжений таблиц.

    Лист с ``is_continuation`` («Продолжение табл.») получает в хвост
    выдачи фрагмент предыдущего листа (``kind="continuation"``, первый
    срез исходного текста листа); порядок исходной выдачи не меняется.
    """
    merged = list(fragments)
    seen = {(fragment.document, fragment.page) for fragment in merged}
    for fragment in list(fragments):
        document, page = fragment.document, fragment.page
        if page <= 1 or (document, page - 1) in seen:
            continue
        if not store.is_continuation(document, page):
            continue
        text = store.page_text(document, page - 1)
        snippet = text.strip()[:SNIPPET_CHARS]
        if not snippet:
            continue
        char_start = text.find(snippet)
        system_code, subsystem_code = _page_codes(store, document, page - 1)
        merged.append(Fragment(
            document=document, page=page - 1,
            system_code=system_code, subsystem_code=subsystem_code,
            snippet=snippet, char_start=char_start,
            char_end=char_start + len(snippet), score=fragment.score,
            kind="continuation",
        ))
        seen.add((document, page - 1))
    return merged
