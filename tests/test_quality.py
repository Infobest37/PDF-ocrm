from pdfprepare.quality import MIN_CONF, MIN_COVERAGE, flag_pages, write_report


def test_flag_low_confidence_page():
    from pdfprepare.models import PageInfo, DocResult, Word
    p = PageInfo(page_index=2, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="",
                 full_text="", words=[Word("а", 0, 0, 10, 10, 0.4) for _ in range(8)])
    r = DocResult(source_path="a.pdf", page_count=3, pages=[p], system_names={})
    flags = flag_pages(r)
    assert flags and flags[0].page_index == 2 and flags[0].reason == "low_confidence"


def test_blank_page_not_flagged():
    from pdfprepare.models import PageInfo, DocResult
    p = PageInfo(page_index=0, is_native_text=False, is_blank=True,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert flag_pages(DocResult("a.pdf", 1, [p], {})) == []


def test_native_page_not_flagged():
    from pdfprepare.models import PageInfo, DocResult, Word
    p = PageInfo(page_index=1, is_native_text=True, is_blank=False,
                 system_code="", subsystem_code="",
                 full_text="text", words=[Word("а", 0, 0, 10, 10, 0.4)])
    assert flag_pages(DocResult("a.pdf", 2, [p], {})) == []


def test_flag_low_text_coverage_page():
    from pdfprepare.models import PageInfo, DocResult, Word
    p = PageInfo(page_index=0, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="", full_text="",
                 words=[Word("а", 0, 0, 1, 1, 0.9), Word("б", 600, 0, 601, 1, 0.9)])
    flags = flag_pages(DocResult("a.pdf", 1, [p], {}))
    assert flags and flags[0].reason == "low_text_coverage"


def test_single_flag_per_page_when_both_low():
    from pdfprepare.models import PageInfo, DocResult, Word
    p = PageInfo(page_index=0, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="", full_text="",
                 words=[Word("а", 0, 0, 1, 1, 0.4)])
    flags = flag_pages(DocResult("a.pdf", 1, [p], {}))
    assert len(flags) == 1 and flags[0].reason == "low_confidence"


def test_constants():
    assert MIN_CONF == 0.60
    assert MIN_COVERAGE == 0.02


def test_write_report_format(tmp_path):
    from pdfprepare.models import QualityFlag
    out = tmp_path / "report.txt"
    write_report(
        [QualityFlag(2, "low_confidence", 0.421, 0.0833),
         QualityFlag(5, "low_text_coverage", 0.95, 0.0012)],
        str(out),
    )
    text = out.read_text(encoding="utf-8")
    assert "page 2: low_confidence (conf=0.42, coverage=0.0833)" in text
    assert "page 5: low_text_coverage (conf=0.95, coverage=0.0012)" in text
