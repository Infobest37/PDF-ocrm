def test_search_key_unifies_dashes_and_case():
    from pdfprepare.normalize import normalize_for_search
    assert normalize_for_search("Ми\u20118МТВ\u20111") == "ми-8мтв-1"
    assert normalize_for_search("ПРИВОД  ЁЛКА") == "привод елка"


def test_part_number_is_not_guessed_and_raw_untouched():
    from pdfprepare.normalize import normalize_part_number
    raw = " 8мт.6101.100 "
    assert normalize_part_number(raw) == "8МТ.6101.100"
    assert raw == " 8мт.6101.100 "          # исходная строка не изменилась
    assert normalize_part_number("8MT.6101.100") == "8MT.6101.100"  # латиница не переписана


def test_real_code_never_altered():
    from pdfprepare.codes import normalize_code
    assert normalize_code("023.10.00") == "023.10.00"
