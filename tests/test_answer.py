def test_refusal_when_nothing_found(fx_store):
    from pdfgraph.answer import build_answer
    a = build_answer(fx_store, "какой момент затяжки болтов шасси?")
    assert a.refusal is True and a.claims == [] and "подтвержд" in a.reason

def test_every_claim_has_evidence_and_is_substring_of_it(fx_store):
    from pdfgraph.answer import build_answer
    a = build_answer(fx_store, "радиомаяк ПАРМ-406А")
    assert a.refusal is False and a.claims
    for claim in a.claims:
        assert claim.evidence
        for idx in claim.evidence:
            assert claim.text in a.fragments[idx].snippet      # текст не выдуман


def test_build_answer_returns_clarification_before_score_refusal(
        fx_store, monkeypatch):
    from dataclasses import replace

    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.query import search as real_search
    from unittest.mock import Mock

    question = "замена втулки"
    fragments = [replace(real_search(fx_store, "радиомаяк")[0], score=0.1)]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    settings = LLMSettings(api_key="test-key", use_synthesis=True)
    decision = {
        "decision": "clarify",
        "reason": "Есть два возможных значения",
        "options": [{
            "id": "A", "label": "Втулка радиомаяка",
            "fragment_ids": [0], "query_terms": ["втулка"],
        }],
    }
    classify = Mock(return_value=decision)
    synthesize = Mock(side_effect=AssertionError("clarify must not synthesize"))
    monkeypatch.setattr("pdfgraph.rag_fusion.classify_answerability", classify)
    monkeypatch.setattr("pdfgraph.rag_fusion.synthesize_answer", synthesize)

    answer = build_answer(
        fx_store, question, use_synthesis=True, llm_settings=settings,
    )

    classify.assert_called_once_with(settings, question, fragments)
    synthesize.assert_not_called()
    assert answer.refusal is False
    assert answer.clarification == decision
    assert answer.fragments == fragments


def test_build_answer_synthesizes_only_after_classifier_accepts(
        fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.query import search as real_search
    from unittest.mock import Mock

    question = "радиомаяк"
    fragments = real_search(fx_store, question)[:1]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    settings = LLMSettings(api_key="test-key", use_synthesis=True)
    classify = Mock(return_value={
        "decision": "answer", "reason": "Есть прямой ответ", "options": [],
    })
    synthesize = Mock(return_value={
        "answer": "Ответ",
        "citations": [{
            "document": fragments[0].document,
            "page": fragments[0].page,
            "snippet": fragments[0].snippet,
        }],
        "refusal": False,
        "reason": "",
    })
    monkeypatch.setattr("pdfgraph.rag_fusion.classify_answerability", classify)
    monkeypatch.setattr("pdfgraph.rag_fusion.synthesize_answer", synthesize)

    answer = build_answer(
        fx_store, question, use_synthesis=True, llm_settings=settings,
    )

    classify.assert_called_once_with(settings, question, fragments)
    synthesize.assert_called_once_with(
        fx_store, question, fragments, settings, answerability_checked=True,
    )
    assert answer.refusal is False
    assert answer.claims[0].answer_text == "Ответ"


def test_build_answer_fails_closed_on_classifier_refusal(fx_store, monkeypatch):
    import pdfgraph.answer as answer_module
    from pdfgraph.answer import build_answer
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.query import search as real_search
    from unittest.mock import Mock

    question = "расход топлива Боинг-747"
    question = "расход топлива Боинг-747"
    fragments = real_search(fx_store, "радиомаяк")[:1]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    # Предмет теста — fail-closed контракт классификатора answerability.
    # Гейты конкретики срабатывают раньше классификатора; включаем их
    # после (состав ENABLED_GATES в этом тесте выключен), чтобы отказ
    # доходил до классификатора, как до правки гейтов Ф1.3б.1.
    monkeypatch.setattr("pdfgraph.gates.ENABLED_GATES", ())
    fragments = real_search(fx_store, "радиомаяк")[:1]
    monkeypatch.setattr(answer_module, "search",
                        lambda *_args, **_kwargs: fragments)
    settings = LLMSettings(api_key="test-key", use_synthesis=True)
    classify = Mock(return_value={
        "decision": "refuse", "reason": "нет подтверждений в документах",
        "options": [],
    })
    synthesize = Mock(side_effect=AssertionError("refusal must not synthesize"))
    monkeypatch.setattr("pdfgraph.rag_fusion.classify_answerability", classify)
    monkeypatch.setattr("pdfgraph.rag_fusion.synthesize_answer", synthesize)

    answer = build_answer(
        fx_store, question, use_synthesis=True, llm_settings=settings,
    )

    assert answer.refusal is True
    assert answer.reason == "нет подтверждений в документах"
    assert answer.claims == []
    synthesize.assert_not_called()


def test_build_answer_cli_path_does_not_classify(fx_store, monkeypatch):
    from pdfgraph.answer import build_answer
    from unittest.mock import Mock

    classify = Mock(side_effect=AssertionError("CLI path must not classify"))
    monkeypatch.setattr("pdfgraph.rag_fusion.classify_answerability", classify)

    answer = build_answer(fx_store, "радиомаяк", use_synthesis=False)

    assert answer.refusal is False
    classify.assert_not_called()
