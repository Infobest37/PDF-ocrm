def test_models_construct_and_defaults():
    from pdfprepare.models import Word, PageInfo, DocResult, QualityFlag
    w = Word("А", 0.0, 0.0, 10.0, 10.0, 85.0)
    assert w.conf == 85.0 and w.text == "А"
    p = PageInfo(page_index=0, is_native_text=False, is_blank=True,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert p.is_blank is True and p.system_code == ""
    r = DocResult(source_path="a.pdf", page_count=1, pages=[p], system_names={})
    assert len(r.pages) == 1
