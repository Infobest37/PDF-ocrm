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
* **One fusion policy (Ф1.1).** The per-variant rag_search fusion and the
  two-channel hybrid fusion (:func:`fuse_hybrid`) share one config: ``k``
  (:func:`effective_fusion_k`, default 10 — при k=60 первые ранги каналов
  почти не различаются) and the exact-branch boost
  (:func:`effective_exact_boost`, default 0.05).

The module is imported lazily from :func:`pdfgraph.answer.build_answer` only
when ``use_rag_fusion=True``, keeping the default retrieval path free of it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from typing import TYPE_CHECKING
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError

from pdfprepare.codes import normalize_code
from pdfgraph.query import (
    Fragment,
    effective_search_mode,
    search,
    vector_search,
)
from pdfgraph.store import Store

if TYPE_CHECKING:
    from pdfgraph.gui.settings import LLMSettings

#: Hard cap the brief pins: the request blocks the GUI thread.
REQUEST_TIMEOUT_SECONDS = 10
LLM_TIMEOUT_SECONDS = 30
DEFAULT_429_RETRY_SECONDS = 5
OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
#: RRF damping constant from the original paper.
#: RRF-константа оригинальной статьи — дефолт прямых вызовов :func:`rrf_fuse`;
#: канальное слияние Ф1.1 берёт k из :func:`effective_fusion_k`.
RRF_K = 60

#: Конфиг слияния каналов гибридного поиска (карточка Ф1.1).
#: k слияния каналов: env старше дефолта, читается при каждом вызове;
#: k=60 сглаживает каналы настолько, что первый и пятый ранги почти
#: не различаются — при четырёх-пяти каналах берётся меньший k.
FUSION_K_ENV = "PDFGRAPH_FUSION_K"
DEFAULT_FUSION_K = 10
#: Буст листов точной ветки (код/партномер): не тонут в семантике.
EXACT_BOOST_ENV = "PDFGRAPH_EXACT_BOOST"
DEFAULT_EXACT_BOOST = 0.05
#: Виды фрагментов точных веток — чей kind получает буст.
EXACT_KINDS = ("code", "part")
#: Глубина выдачи КАЖДОГО канала до слияния: ранги глубже топ-N
#: участвуют в слиянии; отсечение топ-N — только после него.
FUSION_CHANNEL_DEPTH = 50


def _env_float(env_name: str, default: float) -> float:
    """Число из env при каждом вызове; мусор, inf/nan и минус — дефолт.

    Стиль :func:`pdfgraph.embedding.effective_min_cosine`: правка видна
    без перезапуска, а опечатка в переменной окружения не ломает выдачу.
    """
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if math.isnan(value) or math.isinf(value) or value < 0.0:
        return default
    return value


def effective_fusion_k() -> float:
    """k RRF-слияния каналов: PDFGRAPH_FUSION_K, иначе
    DEFAULT_FUSION_K."""
    return _env_float(FUSION_K_ENV, DEFAULT_FUSION_K)


def effective_exact_boost() -> float:
    """Буст точных веток: PDFGRAPH_EXACT_BOOST, иначе
    DEFAULT_EXACT_BOOST."""
    return _env_float(EXACT_BOOST_ENV, DEFAULT_EXACT_BOOST)

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
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    for attempt in range(2):
        try:
            body = _post_json(
                base_url + "/chat/completions", payload, headers,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            break
        except HTTPError as exc:
            if exc.code == 429:
                if attempt == 0:
                    time.sleep(_retry_after_seconds(exc))
                    continue
                raise RuntimeError(
                    "429: Rate limit. Подождите и повторите.") from exc
            try:
                provider_body = exc.read().decode("utf-8", "replace").strip()
            except Exception:
                provider_body = ""
            if settings.api_key:
                provider_body = provider_body.replace(settings.api_key,
                                                      "[redacted]")
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


def _retry_after_seconds(error: HTTPError) -> float:
    """Parse Retry-After seconds/date, defaulting to five seconds."""
    headers = getattr(error, "headers", None) or getattr(error, "hdrs", None)
    value = headers.get("Retry-After") if headers is not None else None
    if not value:
        return float(DEFAULT_429_RETRY_SECONDS)
    try:
        return max(0.0, float(value.strip()))
    except (ValueError, AttributeError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return float(DEFAULT_429_RETRY_SECONDS)


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


def rrf_fuse(rankings: list[list[Fragment]], k: int = RRF_K,
             boost: float = 0.0,
             boost_kinds: tuple[str, ...] = ()) -> list[Fragment]:
    """Reciprocal Rank Fusion over ``(document, page)`` keys, ranks from 1.

    Each key accumulates ``1 / (k + rank)`` from every ranking it appears in;
    keys whose fragment kind is in ``boost_kinds`` additionally gain a one-off
    ``boost`` (карточка Ф1.1: лист точной ветки — код или партномер — не
    должен утонуть в семантике). The result is sorted by the fused value
    (descending); ties keep the first-seen order. The returned ``Fragment``
    for a key is the original instance with the maximum *search* ``.score`` —
    scores are never mutated or replaced by RRF values.
    """
    fused: dict[tuple[str, int], float] = {}
    best: dict[tuple[str, int], Fragment] = {}
    boosted: set[tuple[str, int]] = set()
    for ranking in rankings:
        for rank, fragment in enumerate(ranking, start=1):
            key = (fragment.document, fragment.page)
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
            if boost_kinds and fragment.kind in boost_kinds:
                boosted.add(key)
            current = best.get(key)
            if current is None or fragment.score > current.score:
                best[key] = fragment
    for key in boosted:
        fused[key] += boost
    # dict iteration is insertion-ordered (first-seen) and sorted() is stable,
    # so equal fused scores keep the order in which the keys were first met.
    ordered = sorted(best, key=lambda key: -fused[key])
    return [best[key] for key in ordered]


def fuse_hybrid(lexical: list[Fragment], vector: list[Fragment],
                limit: int = 10) -> list[Fragment]:
    """RRF-слияние лексического и векторного каналов до отсечения (Ф1.1).

    Лексический список — ранги :func:`pdfgraph.query.search`, векторный —
    :func:`pdfgraph.query.vector_search`; каждый канал отдаётся глубиной
    не ниже ``FUSION_CHANNEL_DEPTH`` (выше топ-N). Слитая оценка = СУММА
    1/(k + ранг) по каналам; ключи точной ветки (kind code/part) получают
    буст; k = :func:`effective_fusion_k`, буст = :func:`effective_exact_boost`.
    Скор фрагментов не перезаписывается. Отсечение топ-``limit`` — только
    после слияния, поэтому лист встречается в топ-5, даже если он был
    только в векторном канале.
    """
    if limit <= 0:
        return []
    fused = rrf_fuse([list(lexical), list(vector)],
                     k=effective_fusion_k(), boost=effective_exact_boost(),
                     boost_kinds=EXACT_KINDS)
    return fused[:limit]


def rag_search(store: Store, question: str, aircraft_type: str | None = None,
               limit: int = 10,
               settings: LLMSettings | None = None,
               search_mode: str | None = None) -> list[Fragment]:
    """Search ``question`` and its LLM paraphrases, fused with RRF.

    Code queries (``normalize_code`` non-empty) get exactly one ordinary
    ``search`` — the LLM would only add noise. Free text is expanded with
    :func:`generate_queries`; the original question and each distinct,
    non-identical variant are searched with the same ``aircraft_type`` and
    ``limit``. The fused rankings are truncated to ``limit``. When expansion
    falls back to the original question, the result equals ordinary
    ``search`` in fragments, order and scores.

    ``search_mode`` overrides the ``PDFGRAPH_SEARCH_MODE`` mode of
    :func:`pdfgraph.query.effective_search_mode`. In the hybrid modes the
    semantic channel (:func:`pdfgraph.query.vector_search`) joins as an
    extra RRF ranking whenever the mode allows it: ``hybrid_gated`` only
    when at least one lexical ranking is non-empty (the gate keeps the
    refusal invariant — no lexical hits means no model call),
    ``hybrid_full`` always, ``lexical`` never. ``rrf_fuse`` never mutates
    scores, so semantic fragments keep their ``(0.9901, 1.0]`` band.
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
    mode = search_mode if search_mode is not None else effective_search_mode()
    if mode != "lexical" and (mode == "hybrid_full" or any(rankings)):
        rankings.append(vector_search(store, question, limit=limit))
    # Единая политика слияния Ф1.1: k и буст точных веток из конфига.
    return rrf_fuse(rankings, k=effective_fusion_k(),
                    boost=effective_exact_boost(),
                    boost_kinds=EXACT_KINDS)[:limit]


_SYNTHESIS_SYSTEM_PROMPT = (
    "Ты помощник инженера по технической документации вертолёта Ми-8МТВ. "
    "Отвечай только на основе переданных фрагментов. Не додумывай факты. "
    "Верни только JSON вида {\"answer\": str, \"citations\": "
    "[{\"document\": str, \"page\": int, \"snippet\": str}]}. "
    "В snippet копируй дословную выдержку из соответствующего фрагмента. "
    "Если фрагменты частично отвечают на вопрос, дай только подтверждённую "
    "часть и обозначь ограничение. Если ответа нет и фрагменты совсем не "
    "по теме, answer должен быть «нет подтверждений», а citations — пустым "
    "списком."
)

_ANSWERABILITY_SYSTEM_PROMPT = (
    "Ты проверяешь, есть ли в переданных фрагментах ответ на вопрос. "
    "Если фрагменты содержат частичный ответ (упоминание темы, синоним "
    "или OCR-вариант) — true. Если фрагменты совсем не по теме — false. "
    "Учитывай очевидные опечатки в вопросе, включая перестановку соседних "
    "букв и близкие OCR-варианты терминов. "
    "Если не уверен — false. Верни только JSON с полями has_answer и reason."
)

_CLASSIFICATION_SYSTEM_PROMPT = (
    "Ты определяешь, можно ли ответить на вопрос по найденным фрагментам. "
    "Верни decision=answer, если вопрос понятен и фрагменты подтверждают один "
    "смысл; decision=clarify, если запрос неясен или есть несколько "
    "правдоподобных тем с опорой на фрагменты; decision=refuse, если "
    "подходящих фрагментов нет или ты не уверен. Для clarify предложи "
    "2-3 коротких варианта и укажи fragment_ids — нулевые индексы только "
    "в переданном списке. Не придумывай документы, листы и цитаты. "
    "Верни только JSON с полями decision, reason, options. Вариант содержит "
    "id, label, fragment_ids и query_terms."
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


def _check_answerability(settings: LLMSettings, question: str,
                         fragments: list[Fragment]) -> bool:
    """Check whether the top fragments contain an answer; fail closed."""
    snippets = [
        {
            "document": fragment.document,
            "page": fragment.page,
            "snippet": fragment.snippet[:200],
        }
        for fragment in fragments[:3]
    ]
    logger.debug("answerability snippets question=%r fragments=%s", question,
                 json.dumps(snippets, ensure_ascii=False))
    response_text = ""
    try:
        contexts = [
            {
                "document": fragment.document,
                "page": fragment.page,
                "text": fragment.snippet[:1000],
            }
            for fragment in fragments[:10]
        ]
        messages = [
            {"role": "system", "content": _ANSWERABILITY_SYSTEM_PROMPT},
            {"role": "user", "content": (
                "Есть ли в этих фрагментах ответ на вопрос? Верни JSON "
                "{\"has_answer\": true/false, \"reason\": \"...\"}. "
                "Если не уверен — false.\n"
                f"Вопрос: {question}\nФрагменты:\n"
                + json.dumps(contexts, ensure_ascii=False, indent=2)
            )},
        ]
        response_text = _call_llm(settings, messages).strip()
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text,
                          flags=re.IGNORECASE).strip()
        parsed = json.loads(response_text)
        if (not isinstance(parsed, dict)
                or not isinstance(parsed.get("has_answer"), bool)
                or not isinstance(parsed.get("reason"), str)):
            logger.debug("answerability judge invalid response question=%r "
                         "response=%r", question, response_text)
            return False
        logger.debug("answerability judge question=%r has_answer=%s reason=%r",
                     question, parsed["has_answer"], parsed["reason"])
        return parsed["has_answer"]
    except Exception as exc:
        logger.debug("answerability judge failed question=%r error=%s "
                     "response=%r", question, exc, response_text)
        return False


def classify_answerability(settings: LLMSettings, question: str,
                           fragments: list[Fragment]) -> dict:
    """Classify as answer, clarify, or refuse using the top ten fragments.

    Candidate options refer to fragment indices, not model-generated
    citations. Invalid references are discarded; no valid option means a
    fail-closed refusal.
    """
    def refuse(reason: str) -> dict:
        return {"decision": "refuse", "reason": reason, "options": []}

    if not getattr(settings, "api_key", "").strip():
        return refuse("LLM недоступен: API-ключ не задан")
    if not fragments:
        return refuse("нет подтверждений в документах")

    contexts = [
        {
            "document": fragment.document,
            "page": fragment.page,
            "text": fragment.snippet[:1000],
        }
        for fragment in fragments[:10]
    ]
    messages = [
        {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Вопрос: {question}\nФрагменты:\n"
            + json.dumps(contexts, ensure_ascii=False, indent=2)
        )},
    ]

    response_text = ""
    try:
        response_text = _call_llm(settings, messages).strip()
        if response_text.startswith("```"):
            response_text = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", response_text,
                flags=re.IGNORECASE,
            ).strip()
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return refuse("Некорректный ответ классификатора")

        decision = parsed.get("decision")
        reason = parsed.get("reason")
        if decision not in {"answer", "clarify", "refuse"}:
            return refuse("Некорректное решение классификатора")
        if not isinstance(reason, str):
            return refuse("Некорректный ответ классификатора")
        if decision != "clarify":
            return {"decision": decision, "reason": reason, "options": []}

        options = []
        seen_ids = set()
        raw_options = parsed.get("options")
        if not isinstance(raw_options, list):
            return refuse("Нет подтверждённых вариантов уточнения")
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            label = option.get("label")
            raw_ids = option.get("fragment_ids")
            if (not isinstance(option_id, str) or not option_id.strip()
                    or option_id in seen_ids or not isinstance(label, str)
                    or not label.strip() or not isinstance(raw_ids, list)):
                continue
            fragment_ids = list(dict.fromkeys(
                fragment_id for fragment_id in raw_ids
                if type(fragment_id) is int
                and 0 <= fragment_id < len(contexts)
            ))
            if not fragment_ids:
                continue
            raw_terms = option.get("query_terms", [])
            query_terms = (
                list(dict.fromkeys(term.strip() for term in raw_terms
                                   if isinstance(term, str) and term.strip()))
                if isinstance(raw_terms, list) else []
            )
            options.append({
                "id": option_id,
                "label": label.strip(),
                "fragment_ids": fragment_ids,
                "query_terms": query_terms,
            })
            seen_ids.add(option_id)
            if len(options) == 3:
                break

        if not options:
            return refuse("Нет подтверждённых вариантов уточнения")
        return {"decision": "clarify", "reason": reason,
                "options": options}
    except Exception as exc:
        logger.debug("answerability classification failed question=%r "
                     "error=%s response=%r", question, exc, response_text)
        return refuse("нет подтверждений в документах")


def synthesize_answer(store: Store, question: str,
                      fragments: list[Fragment], settings: LLMSettings, *,
                      answerability_checked: bool = False) -> dict:
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
    if (not answerability_checked
            and not _check_answerability(settings, question, fragments)):
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
        logger.debug("answer synthesis response question=%r json=%s",
                     question, response)
        return _parse_synthesis_response(response)
    except Exception as exc:
        logger.debug("answer synthesis failed: %s", exc)
        if str(exc).startswith("429: Rate limit"):
            reason = "429: превышен лимит запросов. Подождите 10-30 сек."
        else:
            reason = f"LLM недоступен: {exc}"
        return {"answer": "", "citations": [], "refusal": True,
                "reason": reason}
