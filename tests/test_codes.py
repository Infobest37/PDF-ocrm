from pdfprepare.codes import normalize_code, system_of, extract_codes


def test_normalize_code_repairs_ocr_damage():
    assert normalize_code("023.11.30") == "023.11.30"
    assert normalize_code(" 023.11 .30 ") == "023.11.30"
    assert normalize_code("О23.11.30") == ""  # leading Cyrillic О -> rejected, not guessed
    assert normalize_code("O23.1O.OO") == ""  # homoglyphs rejected, never coerced
    assert normalize_code("023.10.00") == "023.10.00"  # a real code is never altered


def test_system_of():
    assert system_of("023.11.30") == "023.00.00"
    assert system_of("bad") == ""
    assert system_of("") == ""


def test_extract_codes_ordered_unique():
    text = "см. 028.10.00, рис. 1 ... 028.10.00 ... 023.00.00"
    assert extract_codes(text) == ["028.10.00", "023.00.00"]


def test_no_codes_returns_empty():
    assert extract_codes("нет кодов здесь") == []


def test_extract_codes_ignores_longer_digit_run():
    # A part number like 078.00.5700 must not yield the bogus 078.00.57.
    assert extract_codes("078.00.5700") == []


def test_extract_codes_ignores_date_like_prefix():
    # A date 2026.10.16 must not yield 026.10.16; a real code still matches.
    assert extract_codes("дата 2026.10.16, см. 028.10.00") == ["028.10.00"]
