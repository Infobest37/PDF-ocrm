"""Machine-check answer citations against the stored source sheets."""

from dataclasses import dataclass

from pdfgraph.answer import Answer
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search


@dataclass
class CitationCheck:
    """Verification result for one claim's reference to a sheet."""

    document: str
    page: int
    claim_index: int
    ok: bool
    reason: str


def verify_citations(answer: Answer, store: Store) -> list[CitationCheck]:
    """Check every claim reference against its sheet and original page text.

    Search normalization is applied only to temporary copies used for the
    substring comparison; neither the stored page text nor claim text changes.
    """
    checks: list[CitationCheck] = []
    for claim_index, claim in enumerate(answer.claims):
        if not claim.evidence:
            checks.append(CitationCheck(
                document="", page=-1, claim_index=claim_index, ok=False,
                reason="текст не найден на листе",
            ))
            continue

        for fragment_index in claim.evidence:
            if not 0 <= fragment_index < len(answer.fragments):
                checks.append(CitationCheck(
                    document="", page=-1, claim_index=claim_index, ok=False,
                    reason="лист отсутствует в графе",
                ))
                continue

            fragment = answer.fragments[fragment_index]
            document, page = fragment.document, fragment.page
            if not store.has_node("sheet", f"{document}:{page}"):
                checks.append(CitationCheck(
                    document=document, page=page, claim_index=claim_index,
                    ok=False, reason="лист отсутствует в графе",
                ))
                continue

            page_text = store.page_text(document, page)
            normalized_claim = normalize_for_search(claim.text)
            normalized_page = normalize_for_search(page_text)
            found = bool(normalized_claim) and normalized_claim in normalized_page
            checks.append(CitationCheck(
                document=document,
                page=page,
                claim_index=claim_index,
                ok=found,
                reason="" if found else "текст не найден на листе",
            ))
    return checks


def all_verified(checks: list[CitationCheck]) -> bool:
    """Return True only when at least one citation exists and all are valid."""
    return bool(checks) and all(check.ok for check in checks)
