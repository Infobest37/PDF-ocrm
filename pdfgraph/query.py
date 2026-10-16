"""Search over the knowledge graph: code, part-number and BM25 text branches.

Three deterministic branches, tried in order:

1. **Code query** — a query that survives :func:`pdfprepare.codes.normalize_code`
   returns every sheet stored under that ``system_code``/``subsystem_code``
   (``kind="code"``, ``score=1.0``).
2. **Part-number query** — a query whose :func:`normalize_part_number` key
   matches an existing ``part`` node returns the sheets whose tables carry it
   (``kind="part"``, ``score=1.0``).
3. **Free text** — FTS5 ``MATCH`` over the stored search text with ``bm25()``
   ranking, ``score = 1 / (1 + |bm25|)`` (``kind="text"``). The query is first
   tried as all-tokens AND; when nothing matches, a tolerant OR retry runs
   with the score multiplied by ``OR_PENALTY``. A topic absent from the index
   returns no hits.

Snippet honesty (anti-goal "не искажать документ"): ``Fragment.snippet`` is
always a verbatim slice of the ORIGINAL stored page text —
``store.page_text(document, page)[char_start:char_end] == snippet`` holds for
every hit. Because FTS matches normalized text, a position-mapped
normalization (:func:`_search_index`) translates normalized-query offsets
back onto original-text offsets before the window is taken.
"""

from dataclasses import dataclass

from pdfprepare.codes import normalize_code
from pdfprepare.normalize import normalize_for_search, normalize_part_number
from pdfgraph.store import Store

#: Snippet window length in characters of original page text.
SNIPPET_CHARS = 200
#: Preferred number of original-text characters kept before the match.
SNIPPET_LEAD = 60
#: Score multiplier for the tolerant OR retry of the text branch (fix round 1).
OR_PENALTY = 0.8


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
              subsystem_code: str, score: float, kind: str, needle: str) -> Fragment:
    text = store.page_text(document, page)
    norm, spans = _search_index(text)
    match = _match_span(norm, spans, needle) if norm else None
    snippet, char_start, char_end = _window(text, match)
    return Fragment(
        document=document, page=page, system_code=system_code,
        subsystem_code=subsystem_code, snippet=snippet,
        char_start=char_start, char_end=char_end, score=score, kind=kind,
    )


def _fts_expr(tokens: list[str], joiner: str) -> str:
    """Join search tokens into an FTS5 MATCH expression of quoted phrases."""
    return joiner.join('"{}"'.format(token.replace('"', '""')) for token in tokens)


def search(
    store: Store,
    query: str,
    aircraft_type: str | None = None,
    limit: int = 10,
) -> list[Fragment]:
    """Return ranked fragments for ``query`` across the graph.

    Branches: normalized code → sheets of that code; existing part-number key
    → sheets carrying that part; otherwise FTS5/BM25 over the whole query
    (all tokens, AND) and — when nothing matches — a tolerant OR retry with
    the score multiplied by :data:`OR_PENALTY`. ``aircraft_type`` restricts
    hits to pages of that type. A topic absent from the index returns ``[]``.
    """
    stripped = query.strip()
    if not stripped:
        return []

    code = normalize_code(stripped)
    if code:
        return _by_code(store, code, aircraft_type)

    part_key = normalize_part_number(stripped)
    if part_key and store.has_node("part", part_key):
        return _by_part(store, part_key, aircraft_type)

    needle = normalize_for_search(stripped)
    if not needle:
        return []
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
            limit: int) -> list[Fragment]:
    tokens = needle.split()
    rows = store.search_pages(_fts_expr(tokens, " AND "), aircraft_type, limit)
    penalty = 1.0
    if not rows:
        # Tolerant retry (fix round 3): a conversational query ("рисунок 12")
        # must still find documents phrased differently ("Рис. 12").
        rows = store.search_pages(_fts_expr(tokens, " OR "), aircraft_type, limit)
        penalty = OR_PENALTY
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  penalty / (1.0 + abs(rank)), "text", needle)
        for document, page, system_code, subsystem_code, rank in rows
    ]
