"""Build answers grounded in verbatim snippets from the knowledge graph.

Every answerer, including any future LLM-backed answerer, must pass its
citations through :func:`pdfgraph.verify.verify_citations` before the answer
is shown to a user.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Protocol

from pdfgraph.query import (
    SNIPPET_CHARS,
    Fragment,
    effective_search_mode,
    expand_continuations,
    search,
    vector_search,
)
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
    """A response, its claims, and the fragments offered as evidence.

    ``neighbors`` (Ф1.7) — окно соседних листов к топ-5: отдельный список
    ссылок ``{"document", "page", "label"}`` WITHOUT цитат; в ранжирование,
    claims и проверку цитат не попадает.
    """

    question: str
    claims: list[Claim]
    fragments: list[Fragment]
    refusal: bool = False
    reason: str = ""
    clarification: dict | None = None
    neighbors: list[dict] = field(default_factory=list)


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

        channel_depth = max(10, FUSION_CHANNEL_DEPTH)
        fragments = search(store, question, aircraft_type, channel_depth)[:10]
        mode_now = (effective_search_mode()
                    if search_mode is None else search_mode)
        if mode_now != "lexical" and (mode_now == "hybrid_full" or fragments):
            vector_ranking = vector_search(store, question,
                                           limit=channel_depth)
            fragments = fuse_hybrid(fragments, vector_ranking,
                                    limit=10,
                                    spec_fn=make_specificity_fn(
                                        store, question))
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
    """
    answer = _build_answer_core(
        store, question, aircraft_type, answerer, min_score,
        use_rag_fusion, use_synthesis, llm_settings, search_mode)
    answer.neighbors = collect_neighbors(store, answer.fragments)
    return answer
