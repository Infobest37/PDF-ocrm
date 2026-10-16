"""Figure inventory: find ``Рис. 12`` / ``Рисунок 7`` / ``Рис. № 12`` captions.

Each match keeps the remainder of its line as the caption (trimmed to a
fixed maximum length). Duplicates collapse by figure number: the first
occurrence in the text wins.
"""

import re

from pdfprepare.models import Figure

#: Caption pattern. ``Рис.`` / ``Рисунок`` / ``Рис. №`` followed by a number.
_FIGURE_RE = re.compile(r"(?i)рис(?:\.|унок|\.\s*№)?\s*(\d{1,3})")

#: Maximum caption length kept in the Figure record.
_MAX_CAPTION = 120


def extract_figures(text: str, page_index: int) -> list[Figure]:
    """Return figures referenced in ``text``, one per unique number.

    The pattern is searched per line; the caption is the remainder of that
    line (whitespace-stripped, truncated to 120 characters). The first
    occurrence of a number defines the record; later duplicates are dropped.
    """
    figures: list[Figure] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _FIGURE_RE.search(line)
        if not match:
            continue
        number = match.group(1)
        if number in seen:
            continue
        seen.add(number)
        caption = line[match.end():].strip()
        figures.append(
            Figure(
                page_index=page_index,
                number=number,
                caption=caption[:_MAX_CAPTION],
            )
        )
    return figures
