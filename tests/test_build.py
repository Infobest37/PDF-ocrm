"""Tests for assembling the knowledge graph from prepared documents."""


def test_build_shapes_the_graph(tmp_path):
    from pdfprepare.models import Word, PageInfo, DocResult, Figure, Reference
    from pdfgraph.store import Store
    from pdfgraph.build import build

    p1 = PageInfo(page_index=47, is_native_text=False, is_blank=False,
                  system_code="023.00.00", subsystem_code="023.11.30",
                  full_text="АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А",
                  words=[Word("ПАРМ-406А", 100, 100, 200, 120, 0.9)],
                  figures=[Figure(47, "12", "Рис. 12")],
                  references=[Reference(47, "system", "028.10.00", "см. 028.10.00")])
    p2 = PageInfo(page_index=48, is_native_text=False, is_blank=True,
                  system_code="023.00.00", subsystem_code="023.11.30",
                  full_text="", words=[])
    res = DocResult(source_path="РТЭ кн.1.pdf", page_count=2, pages=[p1, p2],
                    system_names={"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    s = Store(str(tmp_path / "g.db"))
    stats = build([res], "Ми-8МТВ", {"023.11.30": "АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А"}, s)
    assert stats["sheets"] == 2
    again = build([res], "Ми-8МТВ", {}, s)          # повторная сборка не удваивает
    assert again["nodes"] == stats["nodes"] and again["edges"] == stats["edges"]
    assert again["pending"] == 1                    # цель 028.10.00 в графе отсутствует
    s.close()


def _rows(store, sql, args=()):
    return store._connection.execute(sql, args).fetchall()


def _make_result():
    from pdfprepare.models import DocResult, PageInfo, Table, Word

    page = PageInfo(
        page_index=47,
        is_native_text=False,
        is_blank=False,
        system_code="023.00.00",
        subsystem_code="023.11.30",
        full_text="023.11.30 АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А",
        words=[Word("ПАРМ-406А", 100, 100, 200, 120, 0.9)],
        tables=[Table(47, (10, 10, 200, 120), [["", "8МТ.6101.100"]])],
    )
    return DocResult(
        source_path="out/РТЭ кн.1.json",
        page_count=1,
        pages=[page],
        system_names={"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"},
    )


def test_build_creates_nodes_and_edges_with_expected_directions(tmp_path):
    from pdfgraph.build import build
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    stats = build([_make_result()], "Ми-8МТВ", {"023.11.30": "РАДИОМАЯК ПАРМ-406А"}, store)
    assert stats == {"nodes": 6, "edges": 5, "sheets": 1, "pending": 0}

    kinds = dict(_rows(store, "SELECT key, kind FROM nodes"))
    assert kinds == {
        "Ми-8МТВ": "type",
        "023.00.00": "system",
        "023.11.30": "subsystem",
        "РТЭ кн.1.json": "document",
        "РТЭ кн.1.json:47": "sheet",
        "8МТ.6101.100": "part",
    }

    def edge_ids(src_kind, src_key, dst_kind, dst_key, edge_kind):
        return _rows(
            store,
            """
            SELECT e.id FROM edges e
            JOIN nodes s ON s.id = e.src
            JOIN nodes d ON d.id = e.dst
            WHERE s.kind = ? AND s.key = ? AND d.kind = ? AND d.key = ? AND e.kind = ?
            """,
            (src_kind, src_key, dst_kind, dst_key, edge_kind),
        )

    # contained_in — от ребёнка к родителю
    assert edge_ids("sheet", "РТЭ кн.1.json:47", "subsystem", "023.11.30", "contained_in")
    assert edge_ids("subsystem", "023.11.30", "system", "023.00.00", "contained_in")
    assert edge_ids("system", "023.00.00", "type", "Ми-8МТВ", "contained_in")
    # documented_on — документ → лист
    assert edge_ids("document", "РТЭ кн.1.json", "sheet", "РТЭ кн.1.json:47", "documented_on")
    # has_part — лист → деталь (ключ — нормализованная ячейка)
    assert edge_ids("sheet", "РТЭ кн.1.json:47", "part", "8МТ.6101.100", "has_part")
    store.close()


def test_build_skips_blank_cells_in_tables(tmp_path):
    from pdfprepare.models import DocResult, PageInfo, Table
    from pdfgraph.build import build
    from pdfgraph.store import Store

    page = PageInfo(
        page_index=1,
        is_native_text=False,
        is_blank=False,
        system_code="023.00.00",
        subsystem_code="",
        full_text="023.00.00",
        words=[],
        tables=[Table(1, (0, 0, 10, 10), [["", "  ", "8МТ.6101.100"]])],
    )
    store = Store(str(tmp_path / "g.db"))
    build([DocResult("d.pdf", 1, [page], {})], "Ми-8МТВ", {}, store)
    parts = {row[0] for row in _rows(store, "SELECT key FROM nodes WHERE kind = 'part'")}
    # пустые ячейки не создают узлов; обозначение с буквами и точкой — создаёт
    assert parts == {"8МТ.6101.100"}
    store.close()


def test_part_cells_require_letter_dot_and_min_length(tmp_path):
    # Правило fix round 1: ячейка даёт part только если ключ normalize_part_number
    # содержит букву (кириллицу/латиницу) И точку, и длина ключа >= 6.
    from pdfprepare.models import DocResult, PageInfo, Table
    from pdfgraph.build import build
    from pdfgraph.store import Store

    page = PageInfo(
        page_index=1,
        is_native_text=False,
        is_blank=False,
        system_code="023.00.00",
        subsystem_code="",
        full_text="023.00.00",
        words=[],
        tables=[Table(1, (0, 0, 10, 10), [["8МТ.6101.100", "1.", "3"]])],
    )
    store = Store(str(tmp_path / "g.db"))
    build([DocResult("d.pdf", 1, [page], {})], "Ми-8МТВ", {}, store)
    parts = {row[0] for row in _rows(store, "SELECT key FROM nodes WHERE kind = 'part'")}
    assert parts == {"8МТ.6101.100"}        # «1.» и «3» — служебные ячейки, не детали
    edges = _rows(store, "SELECT COUNT(*) FROM edges WHERE kind = 'has_part'")[0][0]
    assert edges == 1
    store.close()


def test_part_filter_edges(tmp_path):
    from pdfprepare.models import DocResult, PageInfo, Table
    from pdfgraph.build import build
    from pdfgraph.store import Store

    page = PageInfo(
        page_index=2,
        is_native_text=False,
        is_blank=False,
        system_code="023.00.00",
        subsystem_code="",
        full_text="023.00.00",
        words=[],
        tables=[Table(2, (0, 0, 10, 10), [
            ["МИ-8.100-01"],   # буквы + точка + длина>=6 — деталь
            ["869309"],        # без точки — мимо
            ["РИС."],          # короткое (<6) — мимо
            ["1234.6"],        # без букв и ведущих цифр <5 — мимо
            ["8MTB.7226.000"], # латиница тоже подходит
            ["54321.00"],      # 5 ведущих цифр без букв — подходит (чисто цифровое)
        ])],
    )
    store = Store(str(tmp_path / "g.db"))
    build([DocResult("d.pdf", 1, [page], {})], "Ми-8МТВ", {}, store)
    parts = {row[0] for row in _rows(store, "SELECT key FROM nodes WHERE kind = 'part'")}
    assert parts == {"МИ-8.100-01", "8MTB.7226.000", "54321.00"}
    store.close()


def test_part_cells_designation_pattern(tmp_path):
    # Правило fix round 2 (ровно из задания координатора): строка из шести
    # ячеек даёт ровно два узла part — 8МТ.6101.100 и 869309.001-03.
    from pdfprepare.models import DocResult, PageInfo, Table
    from pdfgraph.build import build
    from pdfgraph.store import Store

    page = PageInfo(
        page_index=1,
        is_native_text=False,
        is_blank=False,
        system_code="023.00.00",
        subsystem_code="",
        full_text="023.00.00",
        words=[],
        tables=[Table(1, (0, 0, 10, 10), [
            ["8МТ.6101.100", "1.", "КОЛИЧ. НА ЕД. СБ.",
             "960-2. ® A OOO ®", "869309.001-03", "3"],
        ])],
    )
    store = Store(str(tmp_path / "g.db"))
    build([DocResult("d.pdf", 1, [page], {})], "Ми-8МТВ", {}, store)
    parts = {row[0] for row in _rows(store, "SELECT key FROM nodes WHERE kind = 'part'")}
    assert parts == {"8МТ.6101.100", "869309.001-03"}
    edges = _rows(store, "SELECT COUNT(*) FROM edges WHERE kind = 'has_part'")[0][0]
    assert edges == 2
    store.close()


def test_third_level_code_label_from_page_text(tmp_path):
    from pdfprepare.models import DocResult, PageInfo
    from pdfgraph.build import build
    from pdfgraph.store import Store

    line = "СИСТЕМА 029.11.05 " + "ЗАПОЛНЕНИЕ " * 30    # длиннее 120 символов
    page = PageInfo(
        page_index=3,
        is_native_text=False,
        is_blank=False,
        system_code="029.00.00",
        subsystem_code="029.11.05",
        full_text="СТРОКА БЕЗ КОДА\n" + line,
        words=[],
    )
    store = Store(str(tmp_path / "g.db"))
    build([DocResult("d.pdf", 1, [page], {})], "Ми-8МТВ", {}, store)
    label = _rows(store, "SELECT label FROM nodes WHERE kind = 'subsystem' AND key = '029.11.05'")[0][0]
    assert label == line[:120]                   # строка с кодом, обрезанная до 120
    assert "029.11.05" in label
    store.close()


def test_references_resolve_to_existing_or_pending_nodes(tmp_path):
    from pdfprepare.models import DocResult, Figure, PageInfo, Reference
    from pdfgraph.build import build
    from pdfgraph.store import Store

    p1 = PageInfo(
        page_index=0,
        is_native_text=False,
        is_blank=False,
        system_code="023.00.00",
        subsystem_code="",
        full_text="см. рис. 7, см. 028.10.00, см. рис. 99",
        words=[],
        figures=[Figure(0, "7", "Рис. 7 — схема")],
        references=[
            Reference(0, "figure", "7", "см. рис. 7"),
            Reference(0, "system", "028.10.00", "см. 028.10.00"),
            Reference(0, "figure", "99", "см. рис. 99"),
        ],
    )
    p2 = PageInfo(
        page_index=1,
        is_native_text=False,
        is_blank=False,
        system_code="028.00.00",
        subsystem_code="028.10.00",
        full_text="028.10.00",
        words=[],
    )
    store = Store(str(tmp_path / "g.db"))
    stats = build([DocResult("a.pdf", 2, [p1, p2], {})], "Ми-8МТВ", {}, store)
    assert stats["pending"] == 1  # только отсутствующий рис. 99

    node = _rows(
        store,
        "SELECT id FROM nodes WHERE kind = 'pending_reference' AND key = 'figure:a.pdf:99'",
    )
    assert len(node) == 1
    # ссылка на рисунок, который появляется позже в том же документе, разрешена
    resolved = _rows(
        store,
        """
        SELECT e.id FROM edges e
        JOIN nodes s ON s.id = e.src
        JOIN nodes t ON t.id = e.dst
        WHERE s.kind = 'sheet' AND t.kind = 'figure' AND t.key = 'a.pdf:7'
          AND e.kind = 'references_figure'
        """,
    )
    assert len(resolved) == 1
    # ссылка 028.10.00 разрешилась в узел подсистемы (есть на листе p2) — не pending
    sys_ref = _rows(
        store,
        """
        SELECT e.id FROM edges e
        JOIN nodes s ON s.id = e.src
        JOIN nodes t ON t.id = e.dst
        WHERE s.kind = 'sheet' AND t.kind = 'subsystem' AND t.key = '028.10.00'
          AND e.kind = 'references_system'
        """,
    )
    assert len(sys_ref) == 1
    store.close()


def test_blank_sheet_is_regular_node_and_page_text_is_search_normalized(tmp_path):
    from pdfgraph.build import build
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "g.db"))
    build([_make_result()], "Ми-8МТВ", {}, store)
    body = _rows(store, "SELECT body FROM pages_fts WHERE document = ?", ("РТЭ кн.1.json",))[0][0]
    text = store.page_text("РТЭ кн.1.json", 47)
    assert text == "023.11.30 АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А"  # оригинал сохранён
    assert body == normalize_for_search(text)                              # FTS — по нормализованному
    store.close()
