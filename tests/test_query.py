"""Tests for search over the knowledge graph (code / part / BM25 branches)."""

from pdfgraph.query import Fragment, search


def test_code_query_returns_every_sheet_of_that_code(fx_store):
    hits = search(fx_store, "023.11.30")
    assert hits and all(h.kind == "code" for h in hits)
    assert {h.page for h in hits} == {47, 48}          # оба листа подсистемы


def test_code_query_is_not_truncated_by_default_limit(tmp_path):
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "many-code-sheets.db"))
    try:
        for page in range(12):
            text = f"Лист {page}"
            store.upsert_page(
                "manual.pdf", page, "Ми-8МТВ", "023.00.00", "023.11.30",
                text, normalize_for_search(text),
            )
        assert len(search(store, "023.11.30")) == 12
        assert len(search(store, "023.11.30", limit=1)) == 12
    finally:
        store.close()


def test_part_query_is_not_truncated_by_default_limit(tmp_path):
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "many-part-sheets.db"))
    try:
        part_id = store.add_node("part", "8МТ.6101.100")
        for page in range(12):
            text = f"Лист {page}"
            store.upsert_page(
                "manual.pdf", page, "Ми-8МТВ", "023.00.00", "023.11.30",
                text, normalize_for_search(text),
            )
            sheet_id = store.add_node(
                "sheet", f"manual.pdf:{page}", document="manual.pdf", page=page,
            )
            store.add_edge(sheet_id, part_id, "has_part", document="manual.pdf", page=page)
        assert len(search(store, "8МТ.6101.100")) == 12
        assert len(search(store, "8МТ.6101.100", limit=1)) == 12
    finally:
        store.close()


def test_part_number_query_hits_table_sheet(fx_store):
    hits = search(fx_store, "8МТ.6101.100")
    assert hits[0].kind == "part" and hits[0].page == 47


def test_free_text_is_ranked_and_unknown_gives_nothing(fx_store):
    hits = search(fx_store, "радиомаяк ПАРМ-406А")
    assert hits and hits[0].page == 47 and 0.0 < hits[0].score <= 1.0
    assert search(fx_store, "совершенно отсутствующая тема") == []


def test_techcard_queries_return_document_pair_and_number_search_results(
        tmp_path):
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "techcard-pair.db"))
    pages = [
        ("Регламент Ми-8.pdf", 1,
         "Перечень техкарты 023.21.00b (вариант 023.21.00 b); № 814."),
        ("РТЭ Ми-8.pdf", 2,
         "Техкарта 023.21.00b (вариант 023.21.00 b); № 814."),
        ("Указатель страниц.pdf", 3, "Указатель страницы № 814."),
        ("РЭ двигателя.pdf", 4, "Двигатель ТВ3-117."),
    ]
    try:
        for document, page, text in pages:
            store.upsert_page(
                document, page, "Ми-8МТВ", "023.00.00", "023.21.00",
                text, normalize_for_search(text),
            )

        system_code_hits = search(store, "023.21.00")
        assert system_code_hits
        # Ф1.9б: карты раздела выше листов раздела; карта «023.21.00b»
        # встречается на листах 1–2, обе книги показываются.
        card_hits = [hit for hit in system_code_hits if hit.kind == "techcard"]
        code_hits = [hit for hit in system_code_hits if hit.kind == "code"]
        assert {hit.page for hit in card_hits} == {1, 2}
        assert code_hits

        for query in ("023.21.00b", "023.21.00 b"):
            hits = search(store, query)
            assert len(hits) == 2
            # Ф1.9: все листы карты, порядок — SQL (документ, лист);
            # бинарная сортировка ставит «РТЭ…» раньше «Регламента…».
            assert "РТЭ" in hits[0].document
            assert "Регламент" in hits[1].document
            assert all(hit.kind == "techcard" for hit in hits)

        number_hits = search(store, "№ 814")
        assert [(hit.document, hit.page) for hit in number_hits[:2]] == [
            ("Регламент Ми-8.pdf", 1),
            ("РТЭ Ми-8.pdf", 2),
        ]
        assert any(hit.document == "Указатель страниц.pdf"
                   for hit in number_hits[2:])

        ordinary_hits = search(store, "двигатель ТВ3-117")
        assert ordinary_hits and all(
            hit.kind == "text" for hit in ordinary_hits)
    finally:
        store.close()


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
    assert search(fx_store, "радиомаяк", limit=0) == []


def test_fragment_is_a_dataclass_with_declared_fields(fx_store):
    fields = {f.name for f in __import__("dataclasses").fields(Fragment)}
    # Ф1.9б: role — аддитивное поле роли документа карты (по умолчанию '').
    assert fields == {"document", "page", "system_code", "subsystem_code",
                      "snippet", "char_start", "char_end", "score", "kind",
                      "role"}


def test_text_query_falls_back_to_or_and_is_penalized(fx_store):
    # Fix round 1: разговорный «рисунок 12» обязан найти лист с «Рис. 12»
    # (AND по токенам не проходит — спасает OR с пониженным баллом).
    from pdfprepare.normalize import normalize_for_search

    text = "СХЕМА МОНТАЖА. СМ. РИС. 12"
    fx_store.upsert_page("РТЭ кн.1.pdf", 77, "Ми-8МТВ",
                         "023.00.00", "023.11.30", text,
                         normalize_for_search(text))
    hits = search(fx_store, "рисунок 12")
    assert hits and hits[0].page == 77 and hits[0].kind == "text"
    assert 0.0 < hits[0].score <= 0.8           # балл OR-ветки урезан множителем
    assert text[hits[0].char_start:hits[0].char_end] == hits[0].snippet
    # неизвестная тема обязана оставаться пустой и после OR-ретрай
    assert search(fx_store, "совершенно отсутствующая тема") == []


def test_text_query_falls_back_to_token_prefix(fx_store):
    from pdfprepare.normalize import normalize_for_search

    text = "Неологизмфланецов установлен"
    fx_store.upsert_page("РТЭ кн.1.pdf", 91, "Ми-8МТВ",
                         "023.00.00", "023.11.30", text,
                         normalize_for_search(text))

    hits = search(fx_store, "неологизмфланец")

    assert hits and hits[0].page == 91 and hits[0].kind == "text"
    assert 0.0 < hits[0].score <= 0.6


def test_text_query_falls_back_to_trigram_for_single_character_typo(fx_store):
    from pdfprepare.normalize import normalize_for_search

    text = "Анероидно-мембранный механизм"
    fx_store.upsert_page("РТЭ кн.1.pdf", 92, "Ми-8МТВ",
                         "023.00.00", "023.11.30", text,
                         normalize_for_search(text))

    hits = search(fx_store, "анеройдно")

    hit = next(h for h in hits if h.page == 92)
    assert hit.kind == "text"
    assert hit.score == 0.5
    assert text[hit.char_start:hit.char_end] == hit.snippet


def test_whitespace_query_returns_nothing(fx_store):
    assert search(fx_store, "   ") == []
