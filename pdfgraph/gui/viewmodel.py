"""Pure view-model for the answer screen (no Qt imports).

Turns a checked :class:`~pdfgraph.answer.Answer` into display rows and a
verdict line, so the future PyQt5 window stays a thin shell over this module.
Pure Python means the logic runs instantly under pytest.
"""

from dataclasses import dataclass

from pdfgraph.answer import Answer
from pdfgraph.query import Fragment
from pdfgraph.verify import CitationCheck

#: Verdict for an answer with no rows (refusal).
REFUSAL_VERDICT = "Ответ отклонён: нет подтверждений на листах"


@dataclass
class AnswerRow:
    """One display line: a claim, its source sheet and verification state."""

    text: str
    document: str
    page: int
    code: str
    verified: bool
    snippet: str


def answer_rows(answer: Answer, checks: list[CitationCheck]) -> list[AnswerRow]:
    """Build one :class:`AnswerRow` per claim using its citation checks.

    A claim is ``verified`` only when it has references, citation checks and
    every one of them is ``ok``. A refusal answer always yields no rows. Row links (document/page/code) and the
    snippet come from the claim's earliest confirmed fragment, falling back to
    its first available fragment.
    """
    rows: list[AnswerRow] = []
    if answer.refusal:
        return rows
    for claim_index, claim in enumerate(answer.claims):
        claim_checks = [c for c in checks if c.claim_index == claim_index]
        verified = bool(claim.evidence) and bool(claim_checks) and all(
            c.ok for c in claim_checks
        )

        indexes = list(claim.evidence)
        confirmed = [
            i for i in indexes
            if 0 <= i < len(answer.fragments)
            and any(c.ok and c.document == answer.fragments[i].document
                    and c.page == answer.fragments[i].page
                    for c in claim_checks)
        ]
        valid = [i for i in indexes if 0 <= i < len(answer.fragments)]
        source_index = confirmed[0] if confirmed else (valid[0] if valid else None)
        source: Fragment | None = (
            answer.fragments[source_index] if source_index is not None else None
        )

        rows.append(AnswerRow(
            text=(claim.answer_text if claim.answer_text is not None
                  else claim.text),
            document=source.document if source else "",
            page=source.page if source else -1,
            code=source.system_code if source else "",
            verified=verified,
            snippet=source.snippet if source else "",
        ))
    return rows


def verdict_text(rows: list[AnswerRow]) -> str:
    """Summarize rows: confirmed count out of total, or the refusal message."""
    if not rows:
        return REFUSAL_VERDICT
    confirmed = sum(1 for row in rows if row.verified)
    return f"Подтверждено {confirmed} из {len(rows)}"
