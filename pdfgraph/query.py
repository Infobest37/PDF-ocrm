"""Search over the knowledge graph: code, part-number and BM25 text branches.

Three deterministic branches, tried in order:

1. **Code query** — a query that survives :func:`pdfprepare.codes.normalize_code`
   returns every sheet stored under that ``system_code``/``subsystem_code``
   (``kind="code"``, ``score=1.0``).
2. **Part-number query** — a query whose :func:`normalize_part_number` key
   matches an existing ``part`` node returns the sheets whose tables carry it
   (``kind="part"``, ``score=1.0``).
3. **Free text** — FTS5 ``MATCH`` over the stored search text with ``bm25()``
   ranking, ``score = 1 / (1 + |bm25|)`` (``kind="text"``). Unknown topic →
   no hits.

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


def _node_exists(store: Store, kind: str, key: str) -> bool:
    return store._connection.execute(
        "SELECT 1 FROM nodes WHERE kind = ? AND key = ?", (kind, key)
    ).fetchone() is not None


def search(
    store: Store,
    query: str,
    aircraft_type: str | None = None,
    limit: int = 10,
) -> list[Fragment]:
    """Return ranked fragments for ``query`` across the graph.

    Branches: normalized code → sheets of that code; existing part-number key
    → sheets carrying that part; otherwise FTS5/BM25. ``aircraft_type``
    restricts hits to pages of that type. An unknown topic returns ``[]``.
    """
    stripped = query.strip()
    if not stripped:
        return []

    code = normalize_code(stripped)
    if code:
        return _by_code(store, code, aircraft_type, limit)

    part_key = normalize_part_number(stripped)
    if part_key and _node_exists(store, "part", part_key):
        return _by_part(store, part_key, aircraft_type, limit)

    needle = normalize_for_search(stripped)
    if not needle:
        return []
    return _by_fts(store, needle, aircraft_type, limit)


def _by_code(store: Store, code: str, aircraft_type: str | None,
             limit: int) -> list[Fragment]:
    sql = (
        "SELECT document, page, system_code, subsystem_code FROM pages "
        "WHERE (system_code = ? OR subsystem_code = ?)"
    )
    args: list = [code, code]
    if aircraft_type is not None:
        sql += " AND aircraft_type = ?"
        args.append(aircraft_type)
    sql += " ORDER BY document, page LIMIT ?"
    args.append(limit)
    rows = store._connection.execute(sql, args).fetchall()
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  1.0, "code", normalize_for_search(code))
        for document, page, system_code, subsystem_code in rows
    ]


def _by_part(store: Store, part_key: str, aircraft_type: str | None,
             limit: int) -> list[Fragment]:
    sql = (
        "SELECT pg.document, pg.page, pg.system_code, pg.subsystem_code "
        "FROM edges e "
        "JOIN nodes dst ON dst.id = e.dst AND dst.kind = 'part' AND dst.key = ? "
        "JOIN pages pg ON pg.document = e.document AND pg.page = e.page "
        "WHERE e.kind = 'has_part'"
    )
    args: list = [part_key]
    if aircraft_type is not None:
        sql += " AND pg.aircraft_type = ?"
        args.append(aircraft_type)
    sql += " GROUP BY pg.document, pg.page ORDER BY pg.document, pg.page LIMIT ?"
    args.append(limit)
    rows = store._connection.execute(sql, args).fetchall()
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  1.0, "part", normalize_for_search(part_key))
        for document, page, system_code, subsystem_code in rows
    ]


def _by_fts(store: Store, needle: str, aircraft_type: str | None,
            limit: int) -> list[Fragment]:
    match_expr = " AND ".join(
        '"{}"'.format(token.replace('"', '""')) for token in needle.split()
    )
    sql = (
        "SELECT pg.document, pg.page, pg.system_code, pg.subsystem_code, "
        "bm25(pages_fts) AS rank "
        "FROM pages_fts "
        "JOIN pages pg ON pg.document = pages_fts.document "
        "AND pg.page = CAST(pages_fts.page AS INTEGER) "
        "WHERE pages_fts MATCH ?"
    )
    args: list = [match_expr]
    if aircraft_type is not None:
        sql += " AND pg.aircraft_type = ?"
        args.append(aircraft_type)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    rows = store._connection.execute(sql, args).fetchall()
    return [
        _fragment(store, document, page, system_code, subsystem_code,
                  1.0 / (1.0 + abs(rank)), "text", needle)
        for document, page, system_code, subsystem_code, rank in rows
    ]
