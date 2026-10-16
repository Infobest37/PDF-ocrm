def test_models_construct_and_defaults():
    from pdfprepare.models import Word, PageInfo, DocResult, QualityFlag
    w = Word("А", 0.0, 0.0, 10.0, 10.0, 85.0)
    assert w.conf == 85.0 and w.text == "А"
    p = PageInfo(page_index=0, is_native_text=False, is_blank=True,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert p.is_blank is True and p.system_code == ""
    r = DocResult(source_path="a.pdf", page_count=1, pages=[p], system_names={})
    assert len(r.pages) == 1


def test_structural_dataclasses_and_page_defaults():
    from pdfprepare.models import Table, Figure, Reference, PageInfo, SCHEMA_VERSION
    assert SCHEMA_VERSION == 2
    t = Table(page_index=3, bbox=(0.0, 0.0, 100.0, 50.0), rows=[["8МТ.6101.100", "2"]])
    assert t.rows[0][0] == "8МТ.6101.100"
    f = Figure(page_index=3, number="12", caption="Каталожный лист")
    r = Reference(page_index=3, kind="system", target="028.10.00", raw="см. 028.10.00")
    p = PageInfo(page_index=3, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert p.tables == [] and p.figures == [] and p.references == []
    assert f.number == "12" and r.kind == "system"
