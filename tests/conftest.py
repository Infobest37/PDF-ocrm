"""Shared fixtures for the query/report CLI tasks (2.3–3.2).

The graph is populated DIRECTLY through the Store API — not via ``build()`` —
so tasks do not depend on the builder through fixtures. One document, three
sheets: 47 (подсистема 023.11.30, деталь в таблице), 48 (та же подсистема),
12 (код 028.10.00, без деталей).
"""

import pytest

from pdfgraph.store import Store
from pdfprepare.models import DocResult, PageInfo, Table
from pdfprepare.normalize import normalize_for_search, normalize_part_number
from pdfprepare.output import write_json

FX_DOCUMENT = "РТЭ кн.1.pdf"
FX_AIRCRAFT = "Ми-8МТВ"
#: Part number on sheet 47; every fixture consumer must use this exact key.
FX_PART = "8МТ.6101.100"
FX_TEXT_47 = "023.11.30 АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А СМ. 028.10.00"
FX_TEXT_48 = "023.11.30 РАДИОМАЯК В СОСТАВЕ ВЕРТОЛЁТА"
FX_TEXT_12 = "028.10.00 ГИДРОАГРЕГАТ ОСНОВНОЙ"


def populate(store: Store) -> None:
    """Fill ``store`` with the canonical three-sheet fixture graph."""
    type_id = store.add_node("type", FX_AIRCRAFT, label=FX_AIRCRAFT)
    sys23 = store.add_node("system", "023.00.00", label="СВЯЗНОЕ РАДИООБОРУДОВАНИЕ")
    sub231130 = store.add_node("subsystem", "023.11.30",
                               label="АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А")
    sys281000 = store.add_node("system", "028.10.00", label="ГИДРОАГРЕГАТ ОСНОВНОЙ")
    doc = store.add_node("document", FX_DOCUMENT, label=FX_DOCUMENT)

    store.add_edge(sub231130, sys23, "contained_in")
    store.add_edge(sys23, type_id, "contained_in")
    store.add_edge(sys281000, type_id, "contained_in")

    sheets = {
        47: (FX_TEXT_47, "023.00.00", "023.11.30"),
        48: (FX_TEXT_48, "023.00.00", "023.11.30"),
        12: (FX_TEXT_12, "028.10.00", ""),
    }
    sheet_ids: dict[int, int] = {}
    for page, (text, system_code, subsystem_code) in sheets.items():
        sheet_id = store.add_node("sheet", f"{FX_DOCUMENT}:{page}",
                                  document=FX_DOCUMENT, page=page)
        sheet_ids[page] = sheet_id
        store.add_edge(doc, sheet_id, "documented_on",
                       document=FX_DOCUMENT, page=page)
        container = sub231130 if subsystem_code else sys281000
        store.add_edge(sheet_id, container, "contained_in",
                       document=FX_DOCUMENT, page=page)
        store.upsert_page(FX_DOCUMENT, page, FX_AIRCRAFT, system_code,
                          subsystem_code, text, normalize_for_search(text))

    part_id = store.add_node("part", normalize_part_number(FX_PART))
    store.add_edge(sheet_ids[47], part_id, "has_part", detail=FX_PART,
                   document=FX_DOCUMENT, page=47)


@pytest.fixture
def _fx_graph(tmp_path):
    path = str(tmp_path / "graph.db")
    store = Store(path)
    populate(store)
    yield store, path
    store.close()


@pytest.fixture
def fx_store(_fx_graph):
    """Store with the canonical three-sheet graph."""
    return _fx_graph[0]


@pytest.fixture
def fx_store_path(_fx_graph):
    """Path (str) to the SAME populated database file."""
    return _fx_graph[1]


@pytest.fixture
def fx_prepared_dir(tmp_path):
    """Folder with schema-v2 JSON for the same three sheets (for CLI tests)."""
    page47 = PageInfo(page_index=47, is_native_text=False, is_blank=False,
                      system_code="023.00.00", subsystem_code="023.11.30",
                      full_text=FX_TEXT_47, words=[],
                      tables=[Table(47, (10, 10, 300, 60), [["", FX_PART]])])
    page48 = PageInfo(page_index=48, is_native_text=False, is_blank=True,
                      system_code="023.00.00", subsystem_code="023.11.30",
                      full_text=FX_TEXT_48, words=[])
    page12 = PageInfo(page_index=12, is_native_text=False, is_blank=False,
                      system_code="028.10.00", subsystem_code="",
                      full_text=FX_TEXT_12, words=[])
    result = DocResult(source_path=FX_DOCUMENT, page_count=49,
                       pages=[page47, page48, page12],
                       system_names={"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    out_dir = tmp_path / "prepared"
    out_dir.mkdir()
    write_json(result, str(out_dir / (FX_DOCUMENT + ".json")))
    return str(out_dir)
