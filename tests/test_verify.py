def test_correct_answer_verifies(fx_store):
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations, all_verified
    a = build_answer(fx_store, "радиомаяк ПАРМ-406А")
    assert all_verified(verify_citations(a, fx_store)) is True

def test_tampered_claim_is_rejected(fx_store):
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations, all_verified
    a = build_answer(fx_store, "радиомаяк ПАРМ-406А")
    a.claims[0].text = "ПАРМ-406А устанавливается на хвостовой балке"
    checks = verify_citations(a, fx_store)
    assert all_verified(checks) is False
    assert any(c.reason == "текст не найден на листе" for c in checks)

def test_missing_sheet_is_rejected(fx_store):
    from pdfgraph.answer import Answer, Claim
    from pdfgraph.query import Fragment
    from pdfgraph.verify import verify_citations
    a = Answer(question="q", claims=[Claim("есть", [0])],
               fragments=[Fragment("нет-такого.pdf", 999, "", "", "есть", 0, 4, 1.0, "text")])
    checks = verify_citations(a, fx_store)
    assert checks[0].ok is False and checks[0].reason == "лист отсутствует в графе"
