"""Tests for search over the knowledge graph (code / part / BM25 branches)."""

from pdfgraph.query import Fragment, search


def test_code_query_returns_every_sheet_of_that_code(fx_store):
    hits = search(fx_store, "023.11.30")
    assert hits and all(h.kind == "code" for h in hits)
    assert {h.page for h in hits} == {47, 48}          # оба листа подсистемы


def test_part_number_query_hits_table_sheet(fx_store):
    hits = search(fx_store, "8МТ.6101.100")
    assert hits[0].kind == "part" and hits[0].page == 47


def test_free_text_is_ranked_and_unknown_gives_nothing(fx_store):
    hits = search(fx_store, "радиомаяк ПАРМ-406А")
    assert hits and hits[0].page == 47 and 0.0 < hits[0].score <= 1.0
    assert search(fx_store, "совершенно отсутствующая тема") == []


def test_snippet_is_verbatim_slice_of_original_page_text(fx_store):
    # Инвариант задачи 2.5: цитата обязана быть точным фрагментом оригинала.
    for query in ("023.11.30", "028.10.00", "8МТ.6101.100",
                  "радиомаяк ПАРМ-406А", "радиомаяк"):
        hits = search(fx_store, query)
        assert hits, query
        for h in hits:
            text = fx_store.page_text(h.document, h.page)
            assert text[h.char_start:h.char_end] == h.snippet
            assert 0 <= h.char_start <= h.char_end <= len(text)
            assert len(h.snippet) <= 200


def test_code_query_matches_system_code_column(fx_store):
    hits = search(fx_store, "028.10.00")
    assert {h.page for h in hits} == {12}
    assert all(h.kind == "code" and h.score == 1.0 for h in hits)
    assert all(h.system_code == "028.10.00" for h in hits)


def test_snippet_is_centered_on_the_match(fx_store):
    from pdfprepare.normalize import normalize_for_search

    prefix = "ЗАПИСЬ ЖУРНАЛА " * 40                     # >200 символов до совпадения
    match = "АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А ВКЛЮЧЁН "
    suffix = "ПРОВЕРКА СИСТЕМЫ " * 40                   # >200 символов после
    long_text = prefix + match + suffix
    fx_store.upsert_page("РТЭ кн.1.pdf", 90, "Ми-8МТВ", "023.00.00",
                         "023.11.30", long_text, normalize_for_search(long_text))
    hits = search(fx_store, "радиомаяк", limit=50)
    hit90 = next(h for h in hits if h.page == 90)
    assert "РАДИОМАЯК" in hit90.snippet.upper()
    assert len(hit90.snippet) == 200                    # полное окно вокруг совпадения
    assert hit90.char_start > 200                       # окно сдвинуто к совпадению
    assert long_text[hit90.char_start:hit90.char_end] == hit90.snippet


def test_aircraft_type_filters_results(fx_store):
    assert search(fx_store, "радиомаяк", aircraft_type="Ми-8МТВ")
    assert search(fx_store, "радиомаяк", aircraft_type="Ми-26") == []
    assert search(fx_store, "023.11.30", aircraft_type="Ми-26") == []
    assert search(fx_store, "8МТ.6101.100", aircraft_type="Ми-26") == []


def test_limit_is_respected(fx_store):
    assert len(search(fx_store, "радиомаяк ПАРМ-406А", limit=1)) == 1


def test_fragment_is_a_dataclass_with_declared_fields(fx_store):
    fields = {f.name for f in __import__("dataclasses").fields(Fragment)}
    assert fields == {"document", "page", "system_code", "subsystem_code",
                      "snippet", "char_start", "char_end", "score", "kind"}


def test_whitespace_query_returns_nothing(fx_store):
    assert search(fx_store, "   ") == []
