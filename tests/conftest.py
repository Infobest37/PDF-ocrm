"""Shared fixtures for the query/report CLI tasks (2.3–3.2).

The graph is populated DIRECTLY through the Store API — not via ``build()`` —
so tasks do not depend on the builder through fixtures. One document, three
sheets: 47 (подсистема 023.11.30, деталь в таблице), 48 (та же подсистема),
12 (код 028.10.00, без деталей).
"""

import os

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


# ---------------------------------------------------------------------------
# graph_corpus (задача 2.7): приёмочный корпус «граф точно работает».
#
# Три PDF в docs/ собираются PyMuPDF один раз за сессию, прогоняются через
# реальный конвейер pdfprepare и через CLI ``pdfgraph build`` в отдельную
# базу. ВСЕ пять листов — реальный OCR: содержательные страницы
# растеризуются в «скан» 150 dpi (текстового слоя нет — native-путь
# конвейера не срабатывает). Состав листов:
#   techcard.pdf — лист 0: пустой лист-разделитель (код 023.00.00), лист 1:
#     скан техкарты 023.11.30 с таблицей 3×3 (обозначение детали
#     869309.001-03 в ячейке, «Аварийный переносной радиомаяк ПАРМ-406А»).
#     Сетка — со «смещёнными щелями» (по одной на линию, шахматкой):
#     замкнутая сетка заставляет tesseract --psm 6 выбрасывать весь текст
#     ячеек, а сомкнутые линии нужны детекции tables.py — компромисс найден
#     свипом сквозь реальный конвейер (артефакты в журнале:
#     task-2.7-grid-sweep{,2,3}.py / .log). Обозначение детали чисто
#     цифровое: Tesseract rus+eng кириллические М/Т в «8МТ…» систематически
#     выдаёт латиницей «MT» (свип 17 шрифтов × 5 контекстов, лог в
#     журнале), а транслитерация запрещён политикой normalize —
#     кириллический золотой запрос через реальный OCR не достигался бы.
#   figure.pdf — лист 0: такой же разделитель, лист 1: скан листа с
#     подписью «Рис. 12 …» и ссылкой «См. 028.10.00, рис. 1» (переживает
#     реальный OCR — проверено).
#   divider.pdf — отдельный пустой лист-разделитель с кодом 023.00.00.
# ---------------------------------------------------------------------------

GC_PAGE = (595.0, 842.0)  # A4 в точках
GC_SCAN_DPI = 150
GC_SYSTEM_CODE = "023.00.00"
GC_DOCS = ("divider.pdf", "figure.pdf", "techcard.pdf")
#: Обозначение детали в приёмочном корпусе — цифровое, переживает OCR.
GC_PART = "869309.001-03"
GC_PART2 = "869309.002-03"


def _gc_text(page, items, fontfile):
    """Набор строк (x, y, размер, текст) на странице PyMuPDF."""
    for x, y, size, text in items:
        page.insert_text((x, y), text, fontname="gc", fontfile=fontfile,
                         fontsize=size)


def _gc_raster_page(scratch_page, final_doc):
    """Страница-макет → растр GC_SCAN_DPI dpi и как единственная картинка
    в новую страницу final_doc: текстового слоя нет — конвейер идёт OCR."""
    pix = scratch_page.get_pixmap(dpi=GC_SCAN_DPI)
    page = final_doc.new_page(width=GC_PAGE[0], height=GC_PAGE[1])
    page.insert_image(page.rect, stream=pix.tobytes("png"))


def _gc_scan_pdf(path, pages, fontfile):
    """Собрать PDF из «сканов»: каждая items-страница растрируется."""
    import fitz
    scratch = fitz.open()
    final = fitz.open()
    try:
        for items in pages:
            sp = scratch.new_page(width=GC_PAGE[0], height=GC_PAGE[1])
            _gc_text(sp, items, fontfile)
            _gc_raster_page(sp, final)
        final.save(str(path))
    finally:
        final.close()
        scratch.close()


def _gc_blank_scan_page(final, fontfile):
    """Растеризованный пустой лист-разделитель: в углу только код системы."""
    import fitz
    scratch = fitz.open()
    try:
        sp = scratch.new_page(width=GC_PAGE[0], height=GC_PAGE[1])
        _gc_text(sp, [(60, 90, 26, GC_SYSTEM_CODE)], fontfile)
        _gc_raster_page(sp, final)
    finally:
        scratch.close()


def _gc_techcard_pdf(path, fontfile):
    """techcard.pdf: растр-разделитель (лист 0) + растр-«скан» техкарты
    (лист 1): колонтитул 023.11.30, заголовок, сетка 3×3 со смещёнными
    щелями (lw 3.0, щель 60pt по шахматке), деталь GC_PART в ячейке.
    Геометрия варнанта X7 свипа: единственный, где реальный конвейер И
    детектировал таблицу, И прочитал все ячейки без мусора."""
    import fitz
    xs = [40.0, 212.0, 384.0, 556.0]     # 3 колонки по 172pt
    ys = [240.0, 315.0, 390.0, 465.0]    # 3 строки по 75pt
    gap, line_width, cell_size = 60.0, 3.0, 18
    cells = [["N", "ОБОЗНАЧЕНИЕ", "КОЛ"],
             ["1", GC_PART, "2"],
             ["2", GC_PART2, "2"]]
    final = fitz.open()
    try:
        _gc_blank_scan_page(final, fontfile)
        scratch = fitz.open()
        try:
            page = scratch.new_page(width=GC_PAGE[0], height=GC_PAGE[1])
            _gc_text(page, [
                (40, 90, 24, "023.11.30"),
                (300, 90, 20, "ТЕХКАРТА"),
                (40, 140, 26, "Аварийный переносной"),
                (40, 175, 26, "радиомаяк ПАРМ-406А"),
                (40, 520, 20, "Изготовление по настоящему разделу."),
            ], fontfile)
            # горизонтальные линейки: щель в центре «своего» столбца j % 3
            for j, y in enumerate(ys):
                m = (xs[j % 3] + xs[j % 3 + 1]) / 2
                page.draw_line(fitz.Point(xs[0], y), fitz.Point(m - gap / 2, y),
                               width=line_width)
                page.draw_line(fitz.Point(m + gap / 2, y), fitz.Point(xs[-1], y),
                               width=line_width)
            # вертикальные линейки: щель в центре «своего» ряда (i + 1) % 3
            for i, x in enumerate(xs):
                m = (ys[(i + 1) % 3] + ys[(i + 1) % 3 + 1]) / 2
                page.draw_line(fitz.Point(x, ys[0]), fitz.Point(x, m - gap / 2),
                               width=line_width)
                page.draw_line(fitz.Point(x, m + gap / 2), fitz.Point(x, ys[-1]),
                               width=line_width)
            font = fitz.Font(fontfile=fontfile)
            for r, row in enumerate(cells):
                for c, text in enumerate(row):
                    width = font.text_length(text, fontsize=cell_size)
                    x0 = xs[c] + ((xs[c + 1] - xs[c]) - width) / 2
                    baseline = (ys[r] + ys[r + 1]) / 2 + cell_size * 0.35
                    page.insert_text((x0, baseline), text, fontname="gc",
                                     fontfile=fontfile, fontsize=cell_size)
            _gc_raster_page(page, final)
        finally:
            scratch.close()
        final.save(str(path))
    finally:
        final.close()


def _gc_figure_pdf(path, fontfile):
    """figure.pdf: растр-разделитель (лист 0) + растр-«скан» листа с
    рисунком (лист 1): подпись «Рис. 12 …» и ссылка «См. 028.10.00, рис. 1»."""
    import fitz
    final = fitz.open()
    try:
        _gc_blank_scan_page(final, fontfile)
        scratch = fitz.open()
        try:
            sp = scratch.new_page(width=GC_PAGE[0], height=GC_PAGE[1])
            _gc_text(sp, [
                (60, 90, 26, "023.20.00"),
                (60, 150, 26, "Схема электрическая"),
                (60, 185, 22, "Аварийный переносной"),
                (60, 210, 22, "радиомаяк ПАРМ-406А"),
                (60, 320, 26, "Рис. 12 Монтаж"),
                (60, 355, 26, "аппаратуры на грузовой"),
                (60, 390, 26, "полке кабины экипажа"),
                (60, 430, 26, "См. 028.10.00, рис. 1"),
            ], fontfile)
            _gc_raster_page(sp, final)
        finally:
            scratch.close()
        final.save(str(path))
    finally:
        final.close()


@pytest.fixture(scope="session")
def graph_corpus(tmp_path_factory):
    """Приёмочный корпус задачи 2.7: (store, prepared_dir).

    Генерация трёх PDF → реальный конвейер ``pdfprepare`` (все пять листов
    — растр 150 dpi, сквозной OCR бандлом Tesseract rus+eng) → ``pdfgraph``
    CLI build (он же помечает дефекты из отчётов о пересканировании — на
    этом корпусе их нет). Каталог временный, в репозиторий ничего не
    пишется.
    """
    from pdfgraph.cli import main as graph_main
    from pdfprepare.output import resolve_font
    from pdfprepare.pipeline import process_file
    from pdfprepare.registry import parse_registry

    base = tmp_path_factory.mktemp("graph_corpus")
    docs_dir = base / "docs"
    docs_dir.mkdir()
    prepared_dir = base / "prepared"
    prepared_dir.mkdir()
    fontfile = resolve_font(None)
    registry_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "Коды систем Вертолета.txt")
    registry = parse_registry(registry_path)

    _gc_techcard_pdf(docs_dir / "techcard.pdf", fontfile)
    _gc_figure_pdf(docs_dir / "figure.pdf", fontfile)
    _gc_scan_pdf(docs_dir / "divider.pdf",
                 [[(60, 90, 26, GC_SYSTEM_CODE)]], fontfile)

    for name in GC_DOCS:
        process_file(str(docs_dir / name), str(prepared_dir), registry)

    store_path = str(base / "graph.db")
    assert graph_main(["build", str(prepared_dir), store_path,
                       "--type", FX_AIRCRAFT, "--registry", registry_path]) == 0
    store = Store(store_path)
    yield store, str(prepared_dir)
    store.close()
