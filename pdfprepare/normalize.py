"""Search-key normalization — builds keys, never edits document data.

Both functions here are pure string→string helpers. Their output is a
*search key* only: no field of the parsed document, no ``raw`` evidence
string and no source line is ever rewritten with it. This keeps the
project's anti-goal intact — the document is not "corrected", only made
findable. In particular, letters are never transliterated between the
Cyrillic and Latin alphabets.
"""

import re
import unicodedata

#: Dash-like characters unified to ASCII ``-`` (hyphen, NB-hyphen, en/em
#: dashes, minus, soft hyphen, fullwidth hyphen …).
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u02D7\u2212\u2796\uFE58\uFE63\uFF0D\u00AD"

#: Quote-like characters unified to ASCII ``'`` / ``"`` (incl. «guillemets»).
_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
    "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
    "\u00AB": '"', "\u00BB": '"', "\u2032": "'", "\u2033": '"',
    "\u301D": '"', "\u301E": '"', "\uFF02": '"', "\uFF07": "'",
}

#: Dot-like characters unified to ASCII ``.`` for part numbers (middle dot,
#: one-dot leader, bullet dot, fullwidth/ideographic full stop).
_DOTS = "\u00B7\u2024\u2027\u2219\uFF0E\u3002"

_SEARCH_TABLE = {ord(c): "-" for c in _DASHES}
_SEARCH_TABLE.update({ord(k): v for k, v in _QUOTES.items()})
_PART_TABLE = {ord(c): "-" for c in _DASHES}
_PART_TABLE.update({ord(c): "." for c in _DOTS})


def normalize_for_search(text: str) -> str:
    """Return a lowercase search key for ``text``.

    NFKC normalization, dash/quote variants → ASCII, ``ё→е``, whitespace
    collapsed to single spaces, lower case. Intended for the search index
    only — never written back into document fields.
    """
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_SEARCH_TABLE)
    s = s.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", s).strip()


def normalize_part_number(raw: str) -> str:
    """Return a canonical lookup key for a part number.

    Dash/dot variants are unified, whitespace collapsed, case upper-cased.
    No transliteration and no other "guessing": Latin stays Latin, Cyrillic
    stays Cyrillic. ``raw`` itself is never modified.
    """
    s = raw.translate(_PART_TABLE)
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.upper()
