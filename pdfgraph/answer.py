"""Build answers grounded in verbatim snippets from the knowledge graph.

Every answerer, including any future LLM-backed answerer, must pass its
citations through :func:`pdfgraph.verify.verify_citations` before the answer
is shown to a user.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from pdfgraph.query import Fragment, search
from pdfgraph.store import Store

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
    """A response, its claims, and the fragments offered as evidence."""

    question: str
    claims: list[Claim]
    fragments: list[Fragment]
    refusal: bool = False
    reason: str = ""


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


def build_answer(
    store: Store,
    question: str,
    aircraft_type: str | None = None,
    answerer: Answerer | None = None,
    min_score: float = 0.35,
    use_rag_fusion: bool = False,
    use_synthesis: bool = False,
    llm_settings=None,
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
    """
    if use_rag_fusion:
        from pdfgraph.rag_fusion import rag_search
        fragments = rag_search(store, question, aircraft_type, limit=10,
                               settings=llm_settings)
    else:
        fragments = search(store, question, aircraft_type)
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
    if not fragments or best_score < min_score:
        logger.debug("question=%r -> refusal (%s)", question,
                     "нет фрагментов" if not fragments else
                     f"best_score {best_score:.4f} < {min_score:.3f}")
        return Answer(
            question=question,
            claims=[],
            fragments=fragments,
            refusal=True,
            reason="нет подтверждений в документах",
        )

    if synthesis_enabled:
        from pdfgraph.rag_fusion import synthesize_answer
        synthesis = synthesize_answer(store, question, fragments, llm_settings)
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
        )
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
    logger.debug("question=%r -> answer with %d claims",
                 question, len(answer.claims))
    return answer
