def test_rows_mark_verified_and_build_verdict():
    from pdfgraph.gui.viewmodel import answer_rows, verdict_text
    from pdfgraph.answer import Answer, Claim
    from pdfgraph.query import Fragment
    from pdfgraph.verify import CitationCheck
    frags = [Fragment("РТЭ кн.1.pdf", 47, "023.00.00", "023.11.30", "ПАРМ-406А", 0, 9, 1.0, "text")]
    a = Answer(question="q", claims=[Claim("ПАРМ-406А", [0])], fragments=frags)
    checks = [CitationCheck("РТЭ кн.1.pdf", 47, 0, True, "")]
    rows = answer_rows(a, checks)
    assert rows[0].verified is True and rows[0].page == 47 and rows[0].code == "023.00.00"
    assert verdict_text(rows) == "Подтверждено 1 из 1"
