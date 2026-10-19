"""Optional RAG-Fusion retrieval and citation-grounded answer synthesis.

Free-text questions are paraphrased by a local/remote LLM, every variant is
searched through the ordinary :func:`pdfgraph.query.search` branch logic,
and the per-query rankings are fused with RRF (Cormack et al., ``k=60``) so
a sheet that several phrasings agree on rises to the top. Design notes:

* **Provider fallback, never a crash.** Query expansion uses the QSettings
  profile (aitunnel.ru's OpenAI-compatible endpoint) when a key is present,
  otherwise local Ollama (``PDFGRAPH_OLLAMA_MODEL``). Only stdlib
  ``urllib.request`` is used. Expansion falls back to ``[question]`` on any
  provider/HTTP/JSON/empty-reply failure.
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

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING
import urllib.request
from urllib.error import HTTPError

from pdfprepare.codes import normalize_code
from pdfgraph.query import Fragment, search
from pdfgraph.store import Store

if TYPE_CHECKING:
    from pdfgraph.gui.settings import LLMSettings

#: Hard cap the brief pins: the request blocks the GUI thread.
REQUEST_TIMEOUT_SECONDS = 10
LLM_TIMEOUT_SECONDS = 30
OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
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


def _post_json(url: str, payload: dict, headers: dict,
               timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict:
    """POST ``payload`` as JSON and decode the JSON answer."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
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


def _call_llm(settings: LLMSettings, messages: list[dict]) -> str:
    """Call the configured OpenAI-compatible provider (aitunnel by default).

    Credentials are supplied only by the GUI's QSettings profile; this
    function never reads an API-key environment variable. Provider failures
    raise ``RuntimeError`` so the calling operation can choose its fallback.
    """
    base_url = settings.base_url.strip().rstrip("/")
    if not settings.api_key.strip():
        raise RuntimeError("API-ключ LLM не задан")
    try:
        body = _post_json(
            base_url + "/chat/completions",
            {
                "model": settings.model,
                "messages": messages,
                "temperature": settings.temperature,
                "max_tokens": settings.max_tokens,
            },
            {"Authorization": f"Bearer {settings.api_key}"},
            timeout=LLM_TIMEOUT_SECONDS,
        )
    except HTTPError as exc:
        try:
            provider_body = exc.read().decode("utf-8", "replace").strip()
        except Exception:
            provider_body = ""
        if settings.api_key:
            provider_body = provider_body.replace(settings.api_key, "[redacted]")
        detail = provider_body or str(exc)
        raise RuntimeError(f"LLM API ошибка {exc.code}: {detail}") from exc
    except Exception as exc:
        detail = str(exc).replace(settings.api_key, "[redacted]")
        raise RuntimeError(f"LLM API ошибка: {detail}") from exc
    choices = body.get("choices") or []
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM API вернул пустой ответ")
    return content


def generate_queries(question: str, n: int = 3,
                     settings: LLMSettings | None = None) -> list[str]:
    """Return up to ``n`` paraphrases of ``question``, or ``[question]``.

    A configured QSettings profile calls aitunnel.ru; absent a key, Ollama on
    ``localhost:11434`` uses ``PDFGRAPH_OLLAMA_MODEL`` (no model configured —
    no network call). Any provider/HTTP/JSON/empty-response failure and an
    empty parsed list fall back to ``[question]``; exceptions never escape.
    """
    prompt = _PROMPT_TEMPLATE.format(question=question, n=max(1, n))
    try:
        if settings is not None and settings.api_key.strip():
            reply = _call_llm(settings, [{
                "role": "system",
                "content": "Переформулируй технический вопрос, сохраняя смысл. "
                           "Верни только варианты: по одному на строку.",
            }, {
                "role": "user",
                "content": prompt,
            }])
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
               limit: int = 10,
               settings: LLMSettings | None = None) -> list[Fragment]:
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
    for variant in generate_queries(question, settings=settings):
        stripped = (variant or "").strip()
        if stripped and stripped != question and stripped not in queries:
            queries.append(stripped)
    rankings = [search(store, query, aircraft_type, limit)
                for query in queries]
    return rrf_fuse(rankings)[:limit]


_SYNTHESIS_SYSTEM_PROMPT = (
    "Ты помощник инженера по технической документации вертолёта Ми-8МТВ. "
    "Отвечай только на основе переданных фрагментов. Не додумывай факты. "
    "Верни только JSON вида {\"answer\": str, \"citations\": "
    "[{\"document\": str, \"page\": int, \"snippet\": str}]}. "
    "В snippet копируй дословную выдержку из соответствующего фрагмента. "
    "Если подтверждения нет, answer должен быть «нет подтверждений», "
    "а citations — пустым списком."
)


def _parse_synthesis_response(content: str) -> dict:
    """Parse JSON or a small marked-list fallback from the provider."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text,
                      flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        answer = str(parsed.get("answer", "")).strip()
        raw_citations = parsed.get("citations", [])
        citations = []
        if isinstance(raw_citations, list):
            for item in raw_citations:
                if not isinstance(item, dict):
                    continue
                document = str(item.get("document", "")).strip()
                snippet = str(item.get("snippet", "")).strip()
                try:
                    page = int(item.get("page"))
                except (TypeError, ValueError):
                    continue
                if document and snippet:
                    citations.append({
                        "document": document,
                        "page": page,
                        "snippet": snippet,
                    })
    else:
        answer_lines: list[str] = []
        citations = []
        in_citations = False
        citation_re = re.compile(
            r"^\s*[-*•]?\s*document\s*:\s*(.*?)\s*;\s*"
            r"page\s*:\s*(\d+)\s*;\s*snippet\s*:\s*(.+?)\s*$",
            re.IGNORECASE,
        )
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.casefold() in {"citations:", "цитаты:", "источники:"}:
                in_citations = True
                continue
            if not in_citations:
                if stripped.casefold().startswith("answer:"):
                    stripped = stripped.split(":", 1)[1].strip()
                elif stripped.casefold().startswith("ответ:"):
                    stripped = stripped.split(":", 1)[1].strip()
                answer_lines.append(stripped)
                continue
            match = citation_re.match(stripped)
            if match:
                citations.append({
                    "document": match.group(1).strip(),
                    "page": int(match.group(2)),
                    "snippet": match.group(3).strip(),
                })
        answer = "\n".join(line for line in answer_lines if line).strip()

    no_evidence = answer.casefold().strip(" .!\n") == "нет подтверждений"
    if no_evidence:
        return {"answer": "", "citations": [], "refusal": True,
                "reason": "нет подтверждений в документах"}
    if not answer:
        return {"answer": "", "citations": [], "refusal": True,
                "reason": "LLM не вернул текст ответа"}
    return {"answer": answer, "citations": citations, "refusal": False,
            "reason": ""}


def synthesize_answer(store: Store, question: str,
                      fragments: list[Fragment], settings: LLMSettings) -> dict:
    """Synthesize an answer while sending full page text for only top 3 hits.

    All remaining retrieved fragments contribute only their search snippet.
    Returned citations are still independently checked by
    :func:`pdfgraph.verify.verify_citations` before display.
    """
    if not settings.api_key.strip():
        return {"answer": "", "citations": [], "refusal": True,
                "reason": "LLM недоступен: API-ключ не задан"}
    if not fragments:
        return {"answer": "", "citations": [], "refusal": True,
                "reason": "нет подтверждений в документах"}

    contexts = []
    try:
        for index, fragment in enumerate(fragments):
            if index < 3:
                source = store.page_text(fragment.document, fragment.page)[:1500]
            else:
                source = fragment.snippet[:200]
            contexts.append({
                "document": fragment.document,
                "page": fragment.page,
                "text": source,
            })
        messages = [
            {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Вопрос: {question}\nФрагменты:\n"
                + json.dumps(contexts, ensure_ascii=False, indent=2)
            )},
        ]
        response = _call_llm(settings, messages)
        return _parse_synthesis_response(response)
    except Exception as exc:
        logger.debug("answer synthesis failed: %s", exc)
        return {"answer": "", "citations": [], "refusal": True,
                "reason": f"LLM недоступен: {exc}"}
