"""Cross-references: ``см. 028.10.00``, ``см. рис. 1`` and combined clauses.

A reference is triggered by ``см.`` (also ``см`` without the dot). After the
trigger, the clause may name a system code, a figure number, or a comma-
separated list of both (``см. 028.10.00, рис. 1`` yields two references, in
text order). System targets are validated with :func:`pdfprepare.codes.normalize_code`
— anything that is not an exact ``XXX.XX.XX`` code produces no reference
rather than a guessed one.
"""

import re

from pdfprepare.codes import _CODE_FIND_RE, normalize_code
from pdfprepare.models import Reference

#: The "see" trigger and any whitespace up to the first named token.
_SEE_RE = re.compile(r"(?i)\bсм\.?\s*")

#: A full code token, optionally separated from the previous token of the
#: same clause by ``,`` / ``;``. Kept in sync with ``codes._CODE_FIND_RE``.
_CODE_TOKEN_RE = re.compile(r"[,;]?\s*(" + _CODE_FIND_RE.pattern + ")")

#: A figure token (``рис. 1`` / ``рисунок 1`` / ``рис. № 1``), with the same
#: optional separator. Group 1 is the whole ``рис. N`` text, group 2 the number.
_FIG_TOKEN_RE = re.compile(r"(?i)[,;]?\s*(рис\.?\s*№?\s*(\d{1,3}))")


def extract_references(text: str, page_index: int) -> list[Reference]:
    """Return references found in ``text``, in text order.

    Each reference carries the trigger phrase as evidence: ``raw`` spans from
    ``см.`` through the named token for the first token of a clause, and is
    just the token itself (``рис. 1``) for later tokens of the same clause.
    Duplicates by ``(kind, target)`` collapse to the first occurrence.
    """
    references: list[Reference] = []
    seen: set[tuple[str, str]] = set()
    for see in _SEE_RE.finditer(text):
        pos = see.end()
        while True:
            code_m = _CODE_TOKEN_RE.match(text, pos)
            fig_m = _FIG_TOKEN_RE.match(text, pos)
            if code_m is not None:
                kind = "system"
                target = normalize_code(code_m.group(1))
                token_start, token_end = code_m.start(1), code_m.end()
                pos = token_end
            elif fig_m is not None:
                kind = "figure"
                target = fig_m.group(2)
                token_start, token_end = fig_m.start(1), fig_m.end()
                pos = token_end
            else:
                break
            if not target or (kind, target) in seen:
                continue
            if token_start <= see.end():
                raw = text[see.start():token_end]
            else:
                raw = text[token_start:token_end]
            seen.add((kind, target))
            references.append(
                Reference(
                    page_index=page_index,
                    kind=kind,
                    target=target,
                    raw=raw,
                )
            )
    return references
