"""Optional RAG-Fusion retrieval: LLM query expansion + Reciprocal Rank Fusion.

Free-text questions are paraphrased by a local/remote LLM, every variant is
searched through the ordinary :func:`pdfgraph.query.search` branch logic,
and the per-query rankings are fused with RRF (Cormack et al., ``k=60``) so
a sheet that several phrasings agree on rises to the top. Design notes:

* **Provider fallback, never a crash.** ``generate_queries`` talks to OpenAI
  Chat Completions when ``OPENAI_API_KEY`` is set, otherwise to a local
  Ollama (``PDFGRAPH_OLLAMA_MODEL``) over ``http://localhost:11434/api/generate``.
  Only stdlib ``urllib.request`` is used, the timeout stays <= 15 s (the call
  runs on the GUI thread), and every provider/HTTP/JSON/empty-reply failure —
  or a missing Ollama model — returns ``[question]``: without an LLM the whole
  pipeline behaves exactly like ordinary search.
* **Scores stay honest.** ``rrf_fuse`` orders fragments by the fused RRF
  value but returns the original ``Fragment`` instances: the highest *search*
  score seen for a ``(document, page)`` is kept, ``.score`` is never replaced
  by an RRF number, so ``answer.build_answer``'s ``min_score`` keeps its
  calibrated meaning (0.35 against BM25-normalised scores).
* **Code queries bypass the LLM.** A normalized ATA code already selects
  exact sheets; paraphrasing adds latency and noise, so ``rag_search`` runs
  one ordinary ``search`` for them.

The module is imported lazily from :func:`pdfgraph.answer.build_answer` only
when ``use_rag_fusion=True``, keeping the default retrieval path free of it.
"""

import json
import logging
import os
import re
import urllib.request

from pdfprepare.codes import normalize_code
from pdfgraph.query import Fragment, search
from pdfgraph.store import Store

#: Hard cap the brief pins: the request blocks the GUI thread.
REQUEST_TIMEOUT_SECONDS = 10
OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
#: Default chat model for the OpenAI path; override with PDFGRAPH_OPENAI_MODEL.
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
#: RRF damping constant from the original paper.
RRF_K = 60

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = (
    "Ты помогаешь искать сведения в технической документации вертолёта "
    "Ми-8МТВ. Переформулируй вопрос «{question}» {n} другими способами, "
    "сохраняя смысл: используй синонимы и термины авиационной техники."
    " Каждое переформулирование напиши с новой строки, без пояснений."
    " Ответ дай только списком вариантов. Вопрос: {question}"
)

# Response parsing: LLMs love numbered/bulleted lists. Markers are stripped,
# blanks/duplicates/the original question itself are dropped, the list is
# capped at ``n``. A dot requires following whitespace ("3.023" stays intact);
# ")" and "、" may run straight into the text ("2)Вариант").
_NUMBER_MARKER_RE = re.compile(r"^\s*\d{1,2}(?:\.\s+|[)、]\s*)")
_BULLET_MARKER_RE = re.compile(r"^\s*[-*•·–—]\s+")


def _match_key(text: str) -> str:
    """Case/whitespace-insensitive identity used for dedupe and comparison
    with the original question."""
    return " ".join(text.split()).casefold()


def _clean_line(line: str) -> str:
    """Strip a leading list marker from one response line."""
    line = _NUMBER_MARKER_RE.sub("", line, count=1)
    line = _BULLET_MARKER_RE.sub("", line, count=1)
    return line.strip()


def _parse_variants(text: str, question: str, n: int) -> list[str]:
    """Collect up to ``n`` distinct non-empty paraphrases from a reply."""
    seen = {_match_key(question)}
    variants: list[str] = []
    for line in (text or "").splitlines():
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        key = _match_key(cleaned)
        if key in seen:
            continue
        seen.add(key)
        variants.append(cleaned)
        if len(variants) >= n:
            break
    return variants


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    """POST ``payload`` as JSON and decode the JSON answer."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request,
                                timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def _ollama_reply(prompt: str, model: str) -> str:
    body = _post_json(OLLAMA_GENERATE_URL, {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }, {})
    text = body.get("response")
    return text if isinstance(text, str) else ""


def _openai_reply(prompt: str, api_key: str) -> str:
    body = _post_json(OPENAI_CHAT_URL, {
        "model": os.environ.get("PDFGRAPH_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }, {"Authorization": f"Bearer {api_key}"})
    choices = body.get("choices") or []
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def generate_queries(question: str, n: int = 3) -> list[str]:
    """Return up to ``n`` paraphrases of ``question``, or ``[question]``.

    OpenAI Chat Completions is used when ``OPENAI_API_KEY`` is set; otherwise
    Ollama on ``localhost:11434`` with the model from
    ``PDFGRAPH_OLLAMA_MODEL`` (no model configured — no network call). Any
    provider/HTTP/JSON/empty-response failure, and an empty parsed list,
    fall back to ``[question]``; exceptions never escape.
    """
    prompt = _PROMPT_TEMPLATE.format(question=question, n=max(1, n))
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if api_key:
            reply = _openai_reply(prompt, api_key)
        else:
            model = os.environ.get("PDFGRAPH_OLLAMA_MODEL", "").strip()
            if not model:
                logger.debug("no Ollama model configured — question stays as-is")
                return [question]
            reply = _ollama_reply(prompt, model)
        variants = _parse_variants(reply, question, n)
    except Exception as exc:  # provider down, bad JSON, timeout, …
        logger.debug("query expansion failed (%s) — original question only",
                     exc)
        return [question]
    if not variants:
        logger.debug("query expansion empty — original question only")
        return [question]
    logger.debug("expanded %r -> %d variants", question, len(variants))
    return variants


def rrf_fuse(rankings: list[list[Fragment]], k: int = RRF_K) -> list[Fragment]:
    """Reciprocal Rank Fusion over ``(document, page)`` keys, ranks from 1.

    Each key accumulates ``1 / (k + rank)`` from every ranking it appears in;
    the result is sorted by that fused value (descending), ties keep the
    first-seen order. The returned ``Fragment`` for a key is the original
    instance with the maximum *search* ``.score`` — scores are never mutated
    or replaced by RRF values.
    """
    fused: dict[tuple[str, int], float] = {}
    best: dict[tuple[str, int], Fragment] = {}
    for ranking in rankings:
        for rank, fragment in enumerate(ranking, start=1):
            key = (fragment.document, fragment.page)
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
            current = best.get(key)
            if current is None or fragment.score > current.score:
                best[key] = fragment
    # dict iteration is insertion-ordered (first-seen) and sorted() is stable,
    # so equal fused scores keep the order in which the keys were first met.
    ordered = sorted(best, key=lambda key: -fused[key])
    return [best[key] for key in ordered]


def rag_search(store: Store, question: str, aircraft_type: str | None = None,
               limit: int = 10) -> list[Fragment]:
    """Search ``question`` and its LLM paraphrases, fused with RRF.

    Code queries (``normalize_code`` non-empty) get exactly one ordinary
    ``search`` — the LLM would only add noise. Free text is expanded with
    :func:`generate_queries`; the original question and each distinct,
    non-identical variant are searched with the same ``aircraft_type`` and
    ``limit``. The fused rankings are truncated to ``limit``. When expansion
    falls back to the original question, the result equals ordinary
    ``search`` in fragments, order and scores.
    """
    if normalize_code(question):
        logger.debug("rag-fusion: code query %r — plain search", question)
        return search(store, question, aircraft_type, limit)
    queries = [question]
    for variant in generate_queries(question):
        stripped = (variant or "").strip()
        if stripped and stripped != question and stripped not in queries:
            queries.append(stripped)
    rankings = [search(store, query, aircraft_type, limit)
                for query in queries]
    return rrf_fuse(rankings)[:limit]
