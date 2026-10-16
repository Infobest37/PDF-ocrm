from pdfprepare.registry import parse_registry


def test_parse_registry_sample(tmp_path):
    p = tmp_path / "codes.txt"
    p.write_text("021.00.00\tСИСТЕМА КОНДИЦИОНИРОВАНИЯ\n021.20.00\tраспределение\n023.11.30 | компоненты сети\n", encoding="utf-8")
    r = parse_registry(str(p))
    assert r["021.00.00"] == "СИСТЕМА КОНДИЦИОНИРОВАНИЯ"
    assert r["021.20.00"] == "распределение"
    assert r["023.11.30"] == "компоненты сети"


def test_parse_registry_skips_headers_and_chapters(tmp_path):
    p = tmp_path / "codes.txt"
    p.write_text("21 СИСТЕМА КОНДИЦИОНИРОВАНИЯ\nКод ТО\tНаименование\n021.00.00\tСИСТЕМА КОНДИЦИОНИРОВАНИЯ\n", encoding="utf-8")
    r = parse_registry(str(p))
    assert "21" not in r and "Код ТО" not in r and len(r) == 1
