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
