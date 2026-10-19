import json
import os
import urllib.request
from unittest.mock import Mock

import pytest
from urllib.error import URLError

from conftest import FX_DOCUMENT, FX_TEXT_47


class _Response:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _fragment(document, page, snippet, score=1.0):
    from pdfgraph.query import Fragment
    return Fragment(
        document=document,
        page=page,
        system_code="023.00.00",
        subsystem_code="023.11.30",
        snippet=snippet,
        char_start=0,
        char_end=len(snippet),
        score=score,
        kind="text",
    )


def _settings(**kwargs):
    from pdfgraph.gui.settings import LLMSettings
    fields = {
        "api_key": "aitunnel-test-key",
        "base_url": "https://aitunnel.ru/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_tokens": 500,
    }
    fields.update(kwargs)
    return LLMSettings(**fields)


def _fixture_answer():
    from pdfgraph.answer import Answer, Claim
    fragment = _fragment(FX_DOCUMENT, 47, FX_TEXT_47)
    return Answer(
        question="радиомаяк",
        claims=[Claim(text=FX_TEXT_47, evidence=[0])],
        fragments=[fragment],
    )


def test_call_llm_posts_openai_compatible_aitunnel_request(monkeypatch):
    from pdfgraph.rag_fusion import _call_llm
    settings = _settings(temperature=0.25, max_tokens=430)
    messages = [{"role": "user", "content": "Проверь фрагменты"}]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return _Response(json.dumps({
            "choices": [{"message": {"content": "Ответ"}}],
        }).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = _call_llm(settings, messages)

    request, timeout = calls[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://aitunnel.ru/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer aitunnel-test-key"
    assert payload == {
        "model": "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.25,
        "max_tokens": 430,
    }
    assert timeout == 30
    assert result == "Ответ"


def test_call_llm_raises_runtime_error_without_leaking_key(monkeypatch):
    from pdfgraph.rag_fusion import _call_llm
    settings = _settings()

    def fail(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="offline") as exc:
        _call_llm(settings, [{"role": "user", "content": "question"}])
    assert settings.api_key not in str(exc.value)


def test_synthesize_answer_sends_full_text_only_for_top_three(monkeypatch):
    from pdfgraph.rag_fusion import synthesize_answer

    class RecordingStore:
        def __init__(self):
            self.calls = []

        def page_text(self, document, page):
            self.calls.append((document, page))
            return f"PAGE-{page}:" + ("X" * 2000)

    store = RecordingStore()
    fragments = [
        _fragment(f"doc-{i}.pdf", i, f"SNIPPET-{i}" + ("s" * 250))
        for i in range(10)
    ]
    messages_seen = []
    response = {
        "answer": "Минимальное давление составляет X кгс/см².",
        "citations": [
            {"document": f"doc-{i}.pdf", "page": i,
             "snippet": f"PAGE-{i}:"}
            for i in range(3)
        ],
    }

    def fake_call(_settings, messages):
        messages_seen.extend(messages)
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr("pdfgraph.rag_fusion._call_llm", fake_call)
    result = synthesize_answer(store, "минимальное давление масла", fragments,
                               _settings())

    assert store.calls == [("doc-0.pdf", 0), ("doc-1.pdf", 1),
                           ("doc-2.pdf", 2)]
    prompt = "\n".join(message["content"] for message in messages_seen)
    assert "PAGE-0:" + "X" * (1500 - len("PAGE-0:")) in prompt
    assert "PAGE-0:" + "X" * (1501 - len("PAGE-0:")) not in prompt
    for i in range(3, 10):
        assert f"SNIPPET-{i}" in prompt
        assert f"PAGE-{i}:" not in prompt
    assert result["answer"].startswith("Минимальное давление")
    assert len(result["citations"]) == 3
    assert result["refusal"] is False


def test_synthesize_answer_parses_marked_citation_list(monkeypatch):
    from pdfgraph.rag_fusion import synthesize_answer

    store = Mock()
    fragment = _fragment(FX_DOCUMENT, 47, FX_TEXT_47)
    store.page_text.return_value = FX_TEXT_47
    monkeypatch.setattr(
        "pdfgraph.rag_fusion._call_llm",
        lambda *_args: (
            "Ответ: X кгс/см²\n"
            f"Цитаты:\n- document: {FX_DOCUMENT}; page: 47; "
            f"snippet: {FX_TEXT_47}"
        ),
    )

    result = synthesize_answer(store, "давление", [fragment], _settings())

    assert result["answer"] == "X кгс/см²"
    assert result["citations"] == [{
        "document": FX_DOCUMENT, "page": 47, "snippet": FX_TEXT_47,
    }]
    assert result["refusal"] is False


@pytest.mark.parametrize("response", [
    "нет подтверждений",
    json.dumps({"answer": "нет подтверждений", "citations": []}),
])
def test_synthesize_answer_refuses_when_model_has_no_evidence(
        monkeypatch, response):
    from pdfgraph.rag_fusion import synthesize_answer
    monkeypatch.setattr("pdfgraph.rag_fusion._call_llm",
                        lambda *_args: response)

    result = synthesize_answer(Mock(), "вопрос", [], _settings())

    assert result["refusal"] is True
    assert result["answer"] == ""
    assert result["citations"] == []
    assert result["reason"]


def test_synthesize_answer_provider_error_returns_refusal(monkeypatch):
    from pdfgraph.rag_fusion import synthesize_answer

    class TextStore:
        def page_text(self, document, page):
            return "page text for testing"

    def fail(*_args):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr("pdfgraph.rag_fusion._call_llm", fail)
    result = synthesize_answer(TextStore(), "вопрос",
                               [_fragment("a.pdf", 1, "x")],
                               _settings())
    assert result == {
        "answer": "", "citations": [], "refusal": True,
        "reason": "LLM недоступен: service unavailable",
    }


def test_synthesize_answer_403_returns_refusal_with_provider_diagnostic(
        monkeypatch):
    from pdfgraph.rag_fusion import synthesize_answer

    class TextStore:
        def page_text(self, _document, _page):
            return "page text"

    def forbidden(*_args):
        raise RuntimeError(
            "LLM API ошибка 403: модель не разрешена для этого API-ключа")

    monkeypatch.setattr("pdfgraph.rag_fusion._call_llm", forbidden)
    result = synthesize_answer(TextStore(), "вопрос",
                               [_fragment("a.pdf", 1, "quote")], _settings())

    assert result["refusal"] is True
    assert result["answer"] == ""
    assert result["citations"] == []
    assert "403" in result["reason"]
    assert "модель не разрешена" in result["reason"]


def test_build_answer_synthesis_verifies_quote_and_displays_answer(
        fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as real_search
    from pdfgraph.verify import verify_citations
    from pdfgraph.gui.viewmodel import answer_rows

    fragments = real_search(fx_store, "радиомаяк ПАРМ-406А")[:1]
    assert fragments and fragments[0].page == 47
    monkeypatch.setattr(answer_module, "search", lambda *_args, **_kwargs: fragments)
    monkeypatch.setattr(
        "pdfgraph.rag_fusion.synthesize_answer",
        lambda *_args: {
            "answer": "Минимальное давление масла — X кгс/см².",
            "citations": [{
                "document": FX_DOCUMENT,
                "page": 47,
                "snippet": FX_TEXT_47,
            }],
            "refusal": False,
            "reason": "",
        },
    )

    answer = build_answer(
        fx_store, "минимальное давление масла", use_synthesis=True,
        llm_settings=_settings(),
    )
    checks = verify_citations(answer, fx_store)
    rows = answer_rows(answer, checks)

    assert answer.refusal is False
    assert answer.claims[0].text == FX_TEXT_47  # exact verifier text
    assert answer.claims[0].answer_text == "Минимальное давление масла — X кгс/см²."
    assert checks and all(check.ok for check in checks)
    assert rows[0].text == answer.claims[0].answer_text
    assert rows[0].verified is True
    assert rows[0].document == FX_DOCUMENT and rows[0].page == 47


def test_synthesized_answer_is_shown_once_with_separate_citation_rows(
        fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as real_search
    from pdfgraph.verify import verify_citations
    from pdfgraph.gui.viewmodel import answer_rows
    from conftest import FX_TEXT_48

    first = _fragment(FX_DOCUMENT, 47, FX_TEXT_47)
    second = _fragment(FX_DOCUMENT, 48, FX_TEXT_48)
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: [first, second])
    monkeypatch.setattr(
        "pdfgraph.rag_fusion.synthesize_answer",
        lambda *_args: {
            "answer": "Синтезированный вывод.",
            "citations": [
                {"document": FX_DOCUMENT, "page": 47, "snippet": FX_TEXT_47},
                {"document": FX_DOCUMENT, "page": 48, "snippet": FX_TEXT_48},
            ],
            "refusal": False, "reason": "",
        },
    )

    answer = build_answer(fx_store, "радиомаяк", use_synthesis=True,
                          llm_settings=_settings())
    rows = answer_rows(answer, verify_citations(answer, fx_store))

    assert [row.text for row in rows] == ["Синтезированный вывод.", ""]
    assert [(row.document, row.page) for row in rows] == [
        (FX_DOCUMENT, 47), (FX_DOCUMENT, 48)]
    assert all(row.verified for row in rows)


def test_build_answer_marks_a_bad_citation_unverified(fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as real_search
    from pdfgraph.verify import verify_citations
    from pdfgraph.gui.viewmodel import answer_rows

    fragments = real_search(fx_store, "радиомаяк ПАРМ-406А")[:1]
    monkeypatch.setattr(answer_module, "search", lambda *_args, **_kwargs: fragments)
    monkeypatch.setattr(
        "pdfgraph.rag_fusion.synthesize_answer",
        lambda *_args: {
            "answer": "Минимальное давление — X кгс/см².",
            "citations": [{
                "document": FX_DOCUMENT, "page": 47,
                "snippet": "ТЕКСТА НЕТ НА СТРАНИЦЕ",
            }],
            "refusal": False, "reason": "",
        },
    )

    answer = build_answer(fx_store, "давление", use_synthesis=True,
                          llm_settings=_settings())
    checks = verify_citations(answer, fx_store)
    rows = answer_rows(answer, checks)

    assert checks and checks[0].ok is False
    assert "[НЕ ПОДТВЕРЖДЕНО]" in answer.claims[0].answer_text
    assert rows and rows[0].verified is False


def test_build_answer_synthesis_error_is_a_calm_refusal(fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as real_search

    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: real_search(fx_store, "радиомаяк"))
    monkeypatch.setattr(
        "pdfgraph.rag_fusion.synthesize_answer",
        lambda *_args: {
            "answer": "", "citations": [], "refusal": True,
            "reason": "LLM недоступен: offline",
        },
    )

    answer = build_answer(fx_store, "радиомаяк", use_synthesis=True,
                          llm_settings=_settings())

    assert answer.refusal is True
    assert answer.claims == []
    assert answer.reason == "LLM недоступен: offline"


def test_build_answer_without_synthesis_uses_existing_path(fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.query import search as real_search

    expected = real_search(fx_store, "радиомаяк")
    plain_search = Mock(return_value=expected)
    monkeypatch.setattr(answer_module, "search", plain_search)
    synthesize = Mock(side_effect=AssertionError("synthesis should not run"))
    monkeypatch.setattr("pdfgraph.rag_fusion.synthesize_answer", synthesize)

    answer = build_answer(fx_store, "радиомаяк", use_synthesis=False,
                          llm_settings=_settings())

    plain_search.assert_called_once()
    synthesize.assert_not_called()
    assert answer.fragments == expected


def test_gui_synthesis_checkbox_disabled_without_api_key(
        fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.settings import LLMSettings
    import pdfgraph.gui.app as gui_app

    app = QApplication.instance() or QApplication([])
    settings = LLMSettings(api_key="")
    monkeypatch.setattr(gui_app, "load_llm_settings", lambda: settings)
    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")

    assert window.synthesis_checkbox.isEnabled() is False
    assert window.settings_button.text() == "Настройки LLM"
    window.close()


def test_gui_without_api_key_uses_ordinary_answer(fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.settings import LLMSettings
    import pdfgraph.gui.app as gui_app

    app = QApplication.instance() or QApplication([])
    settings = LLMSettings(api_key="", use_synthesis=True)
    monkeypatch.setattr(gui_app, "load_llm_settings", lambda: settings)
    build = Mock(return_value=_fixture_answer())
    monkeypatch.setattr(gui_app, "build_answer", build)
    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")

    assert window.synthesis_checkbox.isEnabled() is False
    window.ask("радиомаяк")

    assert build.call_args.kwargs["use_synthesis"] is False
    assert build.call_args.kwargs["llm_settings"] is settings
    window.close()


def test_gui_synthesis_passes_settings_and_preserves_rag_option(
        fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.settings import LLMSettings
    import pdfgraph.gui.app as gui_app

    app = QApplication.instance() or QApplication([])
    settings = LLMSettings(api_key="test-key", use_synthesis=True,
                           use_rag_fusion=True)
    monkeypatch.setattr(gui_app, "load_llm_settings", lambda: settings)
    build = Mock(return_value=_fixture_answer())
    monkeypatch.setattr(gui_app, "build_answer", build)
    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    assert window.synthesis_checkbox.isEnabled()
    assert window.synthesis_checkbox.isChecked()
    assert window.rag_check.isChecked()

    window.ask("радиомаяк")

    assert build.call_args.kwargs["use_synthesis"] is True
    assert build.call_args.kwargs["use_rag_fusion"] is True
    assert build.call_args.kwargs["llm_settings"] is settings
    window.close()


def test_gui_checkbox_changes_persist_both_search_preferences(
        fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.settings import LLMSettings
    import pdfgraph.gui.app as gui_app

    app = QApplication.instance() or QApplication([])
    settings = LLMSettings(api_key="test-key")
    saved = []
    monkeypatch.setattr(gui_app, "load_llm_settings", lambda: settings)
    monkeypatch.setattr(
        gui_app, "save_llm_settings",
        lambda value: saved.append((value.use_synthesis, value.use_rag_fusion)),
    )
    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")

    window.rag_check.setChecked(True)
    window.synthesis_checkbox.setChecked(True)

    assert saved == [(False, True), (True, True)]
    window.close()


def test_gui_settings_button_opens_settings_dialog(fx_store_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.settings import LLMSettings
    import pdfgraph.gui.app as gui_app

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui_app, "load_llm_settings", LLMSettings)
    calls = []
    dialogs = []

    class FakeDialog:
        def __init__(self, settings, parent):
            calls.append((settings, parent))
            self.finished = Mock()
            dialogs.append(self)

        def open(self):
            calls.append("open")

    monkeypatch.setattr(gui_app, "SettingsDialog", FakeDialog)
    window = gui_app.MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    window.settings_button.click()

    assert calls[0][1] is window
    dialogs[0].finished.connect.assert_called_once()
    assert calls[1] == "open"
    window.close()
