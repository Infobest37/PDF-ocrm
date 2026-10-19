"""Tests for optional RAG-Fusion retrieval (step 3).

Written BEFORE the implementation (expected RED): ``pdfgraph.rag_fusion``
does not exist yet, ``build_answer`` has no ``use_rag_fusion`` parameter and
the GUI has no checkbox — every test below fails until the code agent lands
them. Contracts pinned here come from the step-3 brief:

- ``pdfgraph.rag_fusion.generate_queries(question, n=3, settings=None)`` —
  aitunnel.ru via settings when a QSettings API key is supplied, otherwise
  local Ollama (``PDFGRAPH_OLLAMA_MODEL``) or fallback. All HTTP is mocked.
- ``rrf_fuse(rankings, k=60)`` — RRF over ``(document, page)``, ranks from 1,
  stable first-seen order for ties, original ``Fragment`` with the maximum
  original ``.score`` per key, ``.score`` never mutated/replaced.
- ``rag_search(store, question, aircraft_type, limit=10)`` — expansion +
  fusion for free text, single plain ``search`` for code queries, fallback
  equals ordinary search.
- ``build_answer(..., use_rag_fusion=False)`` — new final parameter, routing
  only; ``min_score`` keeps its meaning on both paths.
- GUI checkbox «Умный поиск (RAG-Fusion)» — unchecked by default, its state
  is passed to ``build_answer`` as ``use_rag_fusion``.

No test in this module touches the network: every provider call is intercepted
via ``urllib.request.urlopen``. Every Ollama-path test also pins
``PDFGRAPH_OLLAMA_MODEL`` explicitly, so results never depend on a
developer's ambient environment (an implementation may return
``[question]`` when no Ollama model is configured).
"""

import json
import re
import urllib.request
from io import BytesIO
from unittest.mock import Mock

import pytest
from urllib.error import HTTPError, URLError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _frag(document: str, page: int, score: float):
    """Build a Fragment-like fixture without touching a Store."""
    from pdfgraph.query import Fragment
    snippet = f"фрагмент {document} лист {page}"
    return Fragment(
        document=document, page=page, system_code="023.00.00",
        subsystem_code="023.11.30", snippet=snippet, char_start=0,
        char_end=len(snippet), score=score, kind="text",
    )


class _FakeResponse:
    """Minimal urlopen() result: supports both ``read()`` and context manager."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def close(self) -> None:
        pass


def _patch_urlopen(monkeypatch, payload: bytes | None = None, error=None):
    """Replace ``urllib.request.urlopen``; returns captured call info."""
    captured = {"request": None, "args": (), "kwargs": {}, "timeout": None}

    def fake_urlopen(req, *args, **kwargs):
        captured["request"] = req
        captured["args"] = args
        captured["kwargs"] = kwargs
        captured["timeout"] = kwargs.get(
            "timeout", args[0] if args and isinstance(args[0], (int, float)) else None)
        if error is not None:
            raise error
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _sent_url_and_body(captured):
    """Extract (url, body bytes) from a captured urlopen call."""
    req = captured["request"]
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if hasattr(req, "data") and req.data is not None:
        body = bytes(req.data)
    elif captured["args"] and isinstance(captured["args"][0], (bytes, bytearray)):
        body = bytes(captured["args"][0])
    else:
        body = b""
    return url, body


def _ollama_reply(text: str) -> bytes:
    return json.dumps({"response": text}).encode("utf-8")


def _openai_reply(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": content}}]}).encode("utf-8")


def _patch_rag_search_mock(monkeypatch, mock_rag):
    """Point every plausible rag_search binding at ``mock_rag``.

    ``build_answer`` imports rag_search lazily (per the brief), but a
    module-level import is patched too when present, so routing is mocked
    regardless of import style.
    """
    import pdfgraph.answer as answer_module
    import pdfgraph.rag_fusion as rag_fusion
    monkeypatch.setattr(rag_fusion, "rag_search", mock_rag, raising=False)
    if hasattr(answer_module, "rag_search"):
        monkeypatch.setattr(answer_module, "rag_search", mock_rag)


def _call_arg(call, index: int, name: str, default=None):
    """Value of a mock-call argument regardless of positional/keyword style."""
    if len(call.args) > index:
        return call.args[index]
    return call.kwargs.get(name, default)


def _qss_font_size(qss: str, selector: str) -> int:
    """font-size (px) of a bare top-level QSS rule, e.g. ``QCheckBox#rag``."""
    rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
    assert rule is not None, f"no QSS rule for {selector}"
    size = re.search(r"font-size:\s*(\d+)px", rule.group(1))
    assert size is not None, f"{selector} rule has no font-size"
    return int(size.group(1))


def _gui_rag_checkbox(window):
    """The single «Умный поиск (RAG-Fusion)» checkbox of the window."""
    from PyQt5.QtWidgets import QCheckBox
    boxes = [cb for cb in window.findChildren(QCheckBox)
             if cb.text() == "Умный поиск (RAG-Fusion)"]
    assert len(boxes) == 1, (
        f"expected exactly one RAG-Fusion checkbox, found {len(boxes)}")
    return boxes[0]


def _gui_fixture_answer():
    """Valid Answer for the fixture store: verification of citations is real."""
    from pdfgraph.answer import Answer, Claim
    from pdfgraph.query import Fragment
    document = "РТЭ кн.1.pdf"
    text = (
        "023.11.30 АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А "
        "СМ. 028.10.00"
    )
    return Answer(
        question="радиомаяк",
        claims=[Claim(text=text, evidence=[0])],
        fragments=[Fragment(
            document=document,
            page=47,
            system_code="023.00.00",
            subsystem_code="023.11.30",
            snippet=text,
            char_start=0,
            char_end=len(text),
            score=1.0,
            kind="text",
        )],
    )


# ---------------------------------------------------------------------------
# rrf_fuse
# ---------------------------------------------------------------------------

def test_rrf_fuse_merges_two_rankings_with_ties_in_first_seen_order():
    """RRF order/ties: [A,B,C] + [B,A,D] => A,B,C,D.

    A and B tie (1/61 + 1/62 each) — first-seen order puts A first; the same
    holds for C and D (1/63 each). Cross-ranking promotion: A/B (seen in both
    rankings) beat C/D (seen in one).
    """
    import pdfgraph.rag_fusion as rag_fusion
    a_first, b_first, c_first = _frag("ДокА", 1, 0.9), _frag("ДокБ", 2, 0.8), _frag("ДокВ", 3, 0.7)
    b_second, a_second, d_second = _frag("ДокБ", 2, 0.85), _frag("ДокА", 1, 0.5), _frag("ДокГ", 4, 0.6)
    fused = rag_fusion.rrf_fuse([[a_first, b_first, c_first],
                                 [b_second, a_second, d_second]])
    keys = [(f.document, f.page) for f in fused]
    assert keys == [("ДокА", 1), ("ДокБ", 2), ("ДокВ", 3), ("ДокГ", 4)]
    by_key = {(f.document, f.page): f for f in fused}
    # The returned instance is the one with the maximum original score.
    assert by_key[("ДокА", 1)].score == 0.9
    assert by_key[("ДокБ", 2)].score == 0.85
    assert by_key[("ДокВ", 3)].score == 0.7
    assert by_key[("ДокГ", 4)].score == 0.6


def test_rrf_fuse_promotes_keys_present_in_several_rankings():
    """A page ranked second in two rankings outranks rank-1 singles."""
    import pdfgraph.rag_fusion as rag_fusion
    y, x_first = _frag("ДокБ", 1, 0.5), _frag("ДокА", 2, 0.4)
    z, x_second = _frag("ДокВ", 1, 0.9), _frag("ДокА", 2, 0.3)
    fused = rag_fusion.rrf_fuse([[y, x_first], [z, x_second]])
    keys = [(f.document, f.page) for f in fused]
    assert keys == [("ДокА", 2), ("ДокБ", 1), ("ДокВ", 1)]
    by_key = {(f.document, f.page): f for f in fused}
    assert by_key[("ДокА", 2)] is x_first  # max original score (0.4 > 0.3)
    assert by_key[("ДокА", 2)].score == 0.4


def test_rrf_fuse_returns_max_score_instance_without_mutating_scores():
    """The RRF value never lands in ``.score``: originals stay untouched."""
    import pdfgraph.rag_fusion as rag_fusion
    a_low, c = _frag("ДокА", 1, 0.2), _frag("ДокВ", 3, 0.6)
    b, a_high = _frag("ДокБ", 2, 0.55), _frag("ДокА", 1, 0.9)
    inputs = [a_low, c, b, a_high]
    before = [(id(f), f.score) for f in inputs]
    fused = rag_fusion.rrf_fuse([[a_low, c], [b, a_high]])
    keys = [(f.document, f.page) for f in fused]
    assert keys == [("ДокА", 1), ("ДокБ", 2), ("ДокВ", 3)]
    by_key = {(f.document, f.page): f for f in fused}
    assert by_key[("ДокА", 1)] is a_high
    assert by_key[("ДокА", 1)].score == 0.9
    # Only original scores come back — no RRF value replaced any .score.
    assert {f.score for f in fused} == {0.9, 0.55, 0.6}
    # And the input fragments were not mutated either.
    assert [(id(f), f.score) for f in inputs] == before


def test_rrf_fuse_empty_and_single_ranking():
    """Empty input yields nothing; a single ranking passes through as-is."""
    import pdfgraph.rag_fusion as rag_fusion
    assert rag_fusion.rrf_fuse([]) == []
    assert rag_fusion.rrf_fuse([[], []]) == []
    first, second = _frag("ДокА", 1, 0.9), _frag("ДокБ", 2, 0.5)
    fused = rag_fusion.rrf_fuse([[first, second]])
    assert [(f.document, f.page) for f in fused] == [("ДокА", 1), ("ДокБ", 2)]
    assert fused[0] is first and fused[1] is second
    assert fused[0].score == 0.9 and fused[1].score == 0.5
    # An empty ranking next to a populated one changes nothing.
    fused2 = rag_fusion.rrf_fuse([[first, second], []])
    assert [(f.document, f.page) for f in fused2] == [("ДокА", 1), ("ДокБ", 2)]


# ---------------------------------------------------------------------------
# generate_queries
# ---------------------------------------------------------------------------

def test_generate_queries_parses_numbered_and_bulleted_ollama_lines(
        monkeypatch):
    """Numbered/bulleted lines parse; blanks, duplicates and the original
    question are omitted; markers and padding are stripped."""
    import pdfgraph.rag_fusion as rag_fusion
    question = "как проверить радиомаяк"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PDFGRAPH_OLLAMA_MODEL", "test-model")
    reply = "\n".join([
        "1. Первый вариант проверки",
        "2)Второй вариант проверки",
        "   3. Третий вариант проверки",
        "- Первый вариант проверки",
        "",
        "* Четвёртый вариант",
        "• Пятый вариант",
        "9. как проверить радиомаяк",
    ])
    _patch_urlopen(monkeypatch, payload=_ollama_reply(reply))
    variants = rag_fusion.generate_queries(question, n=10)
    assert variants == [
        "Первый вариант проверки",
        "Второй вариант проверки",
        "Третий вариант проверки",
        "Четвёртый вариант",
        "Пятый вариант",
    ]


def test_generate_queries_caps_results_at_n(monkeypatch):
    import pdfgraph.rag_fusion as rag_fusion
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PDFGRAPH_OLLAMA_MODEL", "test-model")
    _patch_urlopen(monkeypatch, payload=_ollama_reply(
        "1. Один\n2. Два\n3. Три"))
    assert rag_fusion.generate_queries("вопрос", n=2) == ["Один", "Два"]


def test_generate_queries_posts_to_ollama_with_model_from_env(monkeypatch):
    """Ollama endpoint, model from PDFGRAPH_OLLAMA_MODEL, question and the
    aircraft type named in the prompt, timeout capped at 15 s."""
    import pdfgraph.rag_fusion as rag_fusion
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PDFGRAPH_OLLAMA_MODEL", "llama-test")
    captured = _patch_urlopen(
        monkeypatch, payload=_ollama_reply("1. Вариант"))
    question = "как проверить радиомаяк"
    assert rag_fusion.generate_queries(question) == ["Вариант"]
    url, body = _sent_url_and_body(captured)
    assert "localhost:11434" in url and url.endswith("/api/generate")
    payload = json.loads(body.decode("utf-8"))
    assert payload["model"] == "llama-test"
    assert question in payload["prompt"]
    # Worker-review regression: the prompt names the aircraft, so variants
    # stay in-domain instead of generic.
    assert "Ми-8МТВ" in payload["prompt"]
    timeout = captured["timeout"]
    assert timeout is not None and timeout <= 15


def test_generate_queries_uses_aitunnel_settings(monkeypatch):
    """An explicitly supplied QSettings profile selects aitunnel.ru."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.gui.settings import LLMSettings
    settings = LLMSettings(
        api_key="settings-test-key",
        base_url="https://aitunnel.example/v1",
        model="deepseek-chat",
        temperature=0.3,
        max_tokens=320,
    )
    captured = _patch_urlopen(
        monkeypatch, payload=_openai_reply("1. Раз\n2. Два"))
    assert rag_fusion.generate_queries("вопрос", settings=settings) == [
        "Раз", "Два"]
    url, body = _sent_url_and_body(captured)
    assert url == "https://aitunnel.example/v1/chat/completions"
    request = captured["request"]
    assert request.get_header("Authorization") == "Bearer settings-test-key"
    payload = json.loads(body.decode("utf-8"))
    assert payload["model"] == "deepseek-chat"
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 320


def test_generate_queries_without_settings_does_not_read_api_key_env(
        monkeypatch):
    """API credentials come from QSettings, never OPENAI_API_KEY env."""
    import pdfgraph.rag_fusion as rag_fusion
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.delenv("PDFGRAPH_OLLAMA_MODEL", raising=False)
    urlopen = Mock(side_effect=AssertionError("no configured model: no request"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert rag_fusion.generate_queries("двигатель") == ["двигатель"]
    urlopen.assert_not_called()


def test_call_llm_403_raises_runtimeerror_with_body_and_redacts_key(
        monkeypatch):
    """The provider's 403 body is diagnostic, but the bearer key stays hidden."""
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.rag_fusion import _call_llm

    key = "test-secret-key"
    body = json.dumps({
        "error": {"message": "model not allowed for this API key"}
    }).encode("utf-8")
    http_error = HTTPError(
        "https://aitunnel.example/v1/chat/completions", 403, "Forbidden",
        hdrs=None, fp=BytesIO(body))
    monkeypatch.setattr(urllib.request, "urlopen",
                        Mock(side_effect=http_error))
    settings = LLMSettings(api_key=key, base_url="https://aitunnel.example/v1",
                            model="gpt-6-luna")

    with pytest.raises(RuntimeError) as error:
        _call_llm(settings, [{"role": "user", "content": "test"}])

    assert "403" in str(error.value)
    assert "model not allowed for this API key" in str(error.value)
    assert key not in str(error.value)


def _rate_limit_error(retry_after=None):
    from email.message import Message
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPError(
        "https://aitunnel.example/v1/chat/completions", 429,
        "Too Many Requests", headers, BytesIO(b'{"error":"rate limit"}'))


def test_call_llm_retries_429_after_retry_after_header(monkeypatch):
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.rag_fusion import _call_llm

    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise _rate_limit_error("2")
        return _FakeResponse(_openai_reply("Работает"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))
    settings = LLMSettings(api_key="test-key", base_url="https://aitunnel.example/v1",
                            model="gpt-6-luna")

    assert _call_llm(settings, [{"role": "user", "content": "hi"}]) == "Работает"
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_call_llm_429_without_retry_after_waits_five_seconds(monkeypatch):
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.rag_fusion import _call_llm

    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise _rate_limit_error()
        return _FakeResponse(_openai_reply("Да"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))
    settings = LLMSettings(api_key="test-key", base_url="https://aitunnel.example/v1",
                            model="gpt-6-luna")

    assert _call_llm(settings, [{"role": "user", "content": "hi"}]) == "Да"
    assert len(calls) == 2
    assert sleeps == [5]


def test_call_llm_repeated_429_raises_rate_limit_runtimeerror(monkeypatch):
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.rag_fusion import _call_llm

    calls = []

    def always_limited(request, timeout):
        calls.append(request)
        raise _rate_limit_error("0")

    monkeypatch.setattr(urllib.request, "urlopen", always_limited)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    settings = LLMSettings(api_key="test-key", base_url="https://aitunnel.example/v1",
                            model="gpt-6-luna")

    with pytest.raises(RuntimeError, match="429: Rate limit"):
        _call_llm(settings, [{"role": "user", "content": "hi"}])
    assert len(calls) == 2


def test_generate_queries_urlerror_falls_back_to_question(monkeypatch):
    """A URL error is swallowed: the original question comes back alone."""
    import pdfgraph.rag_fusion as rag_fusion
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PDFGRAPH_OLLAMA_MODEL", "test-model")
    _patch_urlopen(monkeypatch, error=URLError("connection refused"))
    assert rag_fusion.generate_queries("как проверить радиомаяк") == [
        "как проверить радиомаяк"]


def test_generate_queries_bad_or_empty_response_falls_back_to_question(
        monkeypatch):
    """Broken JSON and empty/blank replies are failures, not crash fodder."""
    import pdfgraph.rag_fusion as rag_fusion
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PDFGRAPH_OLLAMA_MODEL", "test-model")
    question = "как проверить радиомаяк"
    for payload in (
            b"<not json>",
            b"",
            json.dumps({"response": ""}).encode("utf-8"),
            json.dumps({"response": "   \n"}).encode("utf-8"),
    ):
        _patch_urlopen(monkeypatch, payload=payload)
        assert rag_fusion.generate_queries(question) == [question]


# ---------------------------------------------------------------------------
# rag_search
# ---------------------------------------------------------------------------

def test_rag_search_expands_dedupes_forwards_type_and_limit(fx_store,
                                                            monkeypatch):
    """Free-text questions are expanded, deduplicated, every query searches
    with the same aircraft_type and limit, results are fused and truncated."""
    import pdfgraph.rag_fusion as rag_fusion
    question = "радиомаяк на вертолёте"
    variants = ["работа радиомаяка ПАРМ", question, "приёмник радиомаяка"]
    generate = Mock(return_value=variants)
    monkeypatch.setattr(rag_fusion, "generate_queries", generate, raising=False)
    fa, fb, fc = _frag("ДокА", 1, 0.9), _frag("ДокБ", 2, 0.8), _frag("ДокВ", 3, 0.7)
    responses = {
        question: [fb, fa],                      # fb rank 1
        "работа радиомаяка ПАРМ": [fb, fc],      # fb rank 1 again
        "приёмник радиомаяка": [fc],             # fc rank 1
    }
    calls = []

    def fake_search(store, query, aircraft_type=None, limit=10):
        calls.append((store, query, aircraft_type, limit))
        return list(responses[query])

    monkeypatch.setattr(rag_fusion, "search", fake_search, raising=False)
    import pdfgraph.query as query_module
    monkeypatch.setattr(query_module, "search", fake_search)

    settings = Mock(api_key="test-key")
    result = rag_fusion.rag_search(fx_store, question,
                                   aircraft_type="Ми-8МТВ", limit=2,
                                   settings=settings)
    generate.assert_called_once_with(question, settings=settings)
    queries = [q for (_s, q, _t, _l) in calls]
    assert len(calls) == 3                       # deduped: question once
    assert queries[0] == question                # original question first
    assert sorted(queries) == sorted({question, *variants})
    assert all(store is fx_store for (store, _q, _t, _l) in calls)
    assert all(t == "Ми-8МТВ" for (_s, _q, t, _l) in calls)
    assert all(l == 2 for (_s, _q, _t, l) in calls)
    # Fusion: fb seen twice at rank 1 -> first, then fc, then fa; limit=2.
    assert result == [fb, fc]
    assert result[0] is fb and result[1] is fc
    assert fa.score == 0.9 and fb.score == 0.8 and fc.score == 0.7


def test_rag_search_code_query_bypasses_generator_with_single_search(
        fx_store, monkeypatch):
    """A code query is answered by one ordinary search — no LLM expansion."""
    import pdfgraph.rag_fusion as rag_fusion
    gen = Mock(return_value=["028.20.00"])
    monkeypatch.setattr(rag_fusion, "generate_queries", gen, raising=False)
    hits = [_frag("РТЭ кн.1.pdf", 12, 1.0)]
    calls = []

    def fake_search(store, query, aircraft_type=None, limit=10):
        calls.append((query, aircraft_type, limit))
        return hits

    monkeypatch.setattr(rag_fusion, "search", fake_search, raising=False)
    import pdfgraph.query as query_module
    monkeypatch.setattr(query_module, "search", fake_search)

    result = rag_fusion.rag_search(fx_store, "028.10.00",
                                   aircraft_type="Ми-8МТВ", limit=5)
    assert len(calls) == 1
    assert calls[0][0] == "028.10.00"
    assert calls[0][1] == "Ми-8МТВ"
    assert result == hits
    gen.assert_not_called()


def test_rag_search_urlerror_fallback_equals_ordinary_search(fx_store,
                                                             monkeypatch):
    """With the generator unreachable, rag_search reduces to ordinary search:
    same fragments, same order, same scores."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.query import search as ordinary_search

    def boom(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setenv("PDFGRAPH_OLLAMA_MODEL", "test-model")
    question = "радиомаяк"
    rag = rag_fusion.rag_search(fx_store, question, None, 10)
    ordinary = ordinary_search(fx_store, question, None, 10)
    assert rag and rag == ordinary  # non-empty: equality is not vacuous


# ---------------------------------------------------------------------------
# build_answer routing
# ---------------------------------------------------------------------------

def test_build_answer_signature_keeps_min_score_and_appends_synthesis_options():
    """New flags follow the established min_score/use_rag_fusion positions."""
    import inspect
    from pdfgraph.answer import build_answer
    params = list(inspect.signature(build_answer).parameters.values())
    names = [p.name for p in params]
    assert names[:4] == ["store", "question", "aircraft_type", "answerer"]
    assert params[4].name == "min_score" and params[4].default == 0.35
    assert params[5].name == "use_rag_fusion" and params[5].default is False
    assert params[6].name == "use_synthesis" and params[6].default is False
    assert params[7].name == "llm_settings" and params[7].default is None
    assert len(names) == 8


def test_build_answer_true_routes_to_rag_search(fx_store, monkeypatch):
    """use_rag_fusion=True goes through rag_search and stays verifiable."""
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as ordinary_search
    from pdfgraph.verify import verify_citations
    mock_rag = Mock(return_value=ordinary_search(fx_store, "радиомаяк"))
    _patch_rag_search_mock(monkeypatch, mock_rag)

    answer = build_answer(fx_store, "радиомаяк", aircraft_type="Ми-8МТВ",
                          use_rag_fusion=True)
    assert mock_rag.call_count == 1
    call = mock_rag.call_args
    assert _call_arg(call, 0, "store") is fx_store
    assert _call_arg(call, 1, "question") == "радиомаяк"
    assert _call_arg(call, 2, "aircraft_type") == "Ми-8МТВ"
    assert _call_arg(call, 3, "limit", 10) == 10
    assert answer.refusal is False and answer.claims
    checks = verify_citations(answer, fx_store)
    assert checks and all(c.ok for c in checks)  # citations remain real


def test_build_answer_false_and_default_stay_on_ordinary_search(fx_store,
                                                                monkeypatch):
    """False and omitted use_rag_fusion never touch rag_search."""
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as ordinary_search
    mock_rag = Mock()
    _patch_rag_search_mock(monkeypatch, mock_rag)
    expected = ordinary_search(fx_store, "радиомаяк")

    for kwargs in ({}, {"use_rag_fusion": False}):
        mock_rag.reset_mock()
        answer = build_answer(fx_store, "радиомаяк", **kwargs)
        mock_rag.assert_not_called()
        assert answer.refusal is False and answer.claims
        assert answer.fragments == expected


def test_build_answer_rag_path_applies_min_score(fx_store, monkeypatch):
    """min_score still filters RAG-path fragments (default 0.35)."""
    from pdfgraph.answer import build_answer
    weak = [_frag("ДокГ", 9, 0.1)]
    mock_rag = Mock(return_value=weak)
    _patch_rag_search_mock(monkeypatch, mock_rag)

    answer = build_answer(fx_store, "радиомаяк", use_rag_fusion=True)
    assert answer.refusal is True and answer.claims == []
    mock_rag.assert_called_once()

    # Exactly at the threshold the fragment is kept (0.35 < 0.35 is False).
    mock_rag.reset_mock()
    mock_rag.return_value = [_frag("ДокГ", 9, 0.35)]
    answer = build_answer(fx_store, "радиомаяк", use_rag_fusion=True)
    assert answer.refusal is False and answer.claims


# ---------------------------------------------------------------------------
# GUI checkbox
# ---------------------------------------------------------------------------

def test_gui_rag_checkbox_is_unchecked_by_default_with_tooltip(
        fx_store_path, monkeypatch):
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from pdfgraph.gui.settings import LLMSettings
    import pdfgraph.gui.app as gui_app
    from pdfgraph.gui.app import MainWindow, _WINDOW_QSS
    monkeypatch.setattr(gui_app, "load_llm_settings", LLMSettings)
    monkeypatch.setattr(gui_app, "save_llm_settings", lambda _settings: None)
    # Worker-review regression: the checkbox type sits on the DESIGN.md ramp
    # (13/15/17px), never the off-ramp 14px.
    assert _qss_font_size(_WINDOW_QSS, "QCheckBox#rag") in (13, 15, 17)
    window = MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    checkbox = _gui_rag_checkbox(window)
    assert checkbox.isChecked() is False
    assert checkbox.toolTip() == (
        "Переформулирует вопрос через LLM. Без LLM работает как обычный поиск.")
    window.close()


@pytest.mark.parametrize(("checked", "expected"), [(True, True), (False, False)])
def test_gui_ask_passes_use_rag_fusion_from_checkbox(fx_store_path,
                                                     monkeypatch, checked,
                                                     expected):
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import pdfgraph.gui.app as gui_app
    from pdfgraph.gui.settings import LLMSettings
    monkeypatch.setattr(gui_app, "load_llm_settings", LLMSettings)
    monkeypatch.setattr(gui_app, "save_llm_settings", lambda _settings: None)
    build_answer = Mock(return_value=_gui_fixture_answer())
    monkeypatch.setattr(gui_app, "build_answer", build_answer)

    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    checkbox = _gui_rag_checkbox(window)
    if checked:
        checkbox.setChecked(True)
    rows = window.ask("радиомаяк")

    assert build_answer.call_args.kwargs.get("use_rag_fusion") is expected
    assert rows and rows[0].verified is True  # citations verified for real
    window.close()
