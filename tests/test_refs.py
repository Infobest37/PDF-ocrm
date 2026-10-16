def test_references_system_and_figure():
    from pdfprepare.refs import extract_references
    refs = extract_references("см. 028.10.00, рис. 1", page_index=9)
    assert [(r.kind, r.target) for r in refs] == [("system", "028.10.00"), ("figure", "1")]
    assert refs[0].raw == "см. 028.10.00"


def test_reference_to_missing_target_still_parsed():
    from pdfprepare.refs import extract_references
    refs = extract_references("см. 999.99.99", page_index=9)
    assert refs[0].target == "999.99.99"


def test_reference_figure_spelled_out_risunok():
    # The docstring promises ``рисунок 1``; the pattern must deliver it.
    from pdfprepare.refs import extract_references
    refs = extract_references("см. рисунок 1", page_index=3)
    assert [(r.kind, r.target) for r in refs] == [("figure", "1")]
    assert refs[0].raw == "см. рисунок 1"
