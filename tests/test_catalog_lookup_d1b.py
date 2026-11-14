# -*- coding: utf-8 -*-
"""Д1б: поиск по каталогу деталей — обозначение ↔ наименование.

RED до реализации ``pdfgraph.catalog``:

- ``catalog_pairs`` разбирает лист каталога (строка-ячейка) в пары
  «обозначение — наименование», включая многострочные наименования и
  стандарты (``ОСТ 131528-80``);
- ``lookup_catalog`` ищет только по листам ``page_kind = 'catalog'``,
  работает в обе стороны (обозначение → имя, имя → обозначение);
- не найдено — честная причина «в каталоге не нашёл»;
- живой ответ по «что за деталь 8АТ.7714.150-01» ведёт на лист
  каталога, цитата дословная и подтверждена.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

REPO_ROOT = Path(__file__).resolve().parents[1]
STORE_SOURCE = REPO_ROOT / "graph.db"
DOC = "КДЭ_Ми-8МТВ-1_книга_2.pdf"
AIR = "Ми-8МТВ"

CATALOG_TEXT = """Ми-8МТВ-1
КАТАЛОГ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ
142.10.00
Стр. 5
Номер
рисунка
Номер
позиции
Обозначение
Наименование сборочной единицы
Колич.
1
8МТ.7714.000
Установка системы БУР-1-2
1
2
8АТ.7714.150-01
.Установка блока БСПИ-4-2
(см. 142.10.00, рис.3)
1
3
8АТ.7714.150.017
.Крышка
1
4
4-12-Кд – ОСТ 131528-80
.Винт
8
"""


def _store(tmp_path, text: str = CATALOG_TEXT) -> Store:
    store = Store(str(tmp_path / "graph.db"))
    store.upsert_page(DOC, 601, AIR, "142.10.00", "", text,
                      normalize_for_search(text))
    store.set_page_kind(DOC, 601, "catalog")
    return store


# --- разбор пар -------------------------------------------------------------


def test_catalog_pairs_extracts_designator_name():
    from pdfgraph.catalog import catalog_pairs

    pairs = catalog_pairs(CATALOG_TEXT)
    found = {(pair.designator, pair.name) for pair in pairs}

    assert ("8АТ.7714.150-01", "Установка блока БСПИ-4-2") in found
    assert ("8АТ.7714.150.017", "Крышка") in found


def test_catalog_pairs_keeps_multiline_name():
    from pdfgraph.catalog import catalog_pairs

    text = ("8МТ.7227.000\n.Установка датчика высоты ДВ-15МВ и\n"
            "датчика приборной скорости ДАС\n1\n")
    pairs = catalog_pairs(text)

    assert len(pairs) == 1
    assert pairs[0].designator == "8МТ.7227.000"
    assert "ДВ-15МВ" in pairs[0].name
    assert "ДАС" in pairs[0].name


def test_catalog_pairs_keeps_standard():
    from pdfgraph.catalog import catalog_pairs

    pairs = catalog_pairs(CATALOG_TEXT)
    standards = [pair for pair in pairs if "ОСТ 131528-80" in pair.designator]

    assert standards
    assert standards[0].name == "Винт"


# --- поиск в обе стороны ----------------------------------------------------


def test_lookup_by_designator(tmp_path):
    from pdfgraph.catalog import lookup_catalog

    store = _store(tmp_path)
    lookup = lookup_catalog(store, "что за деталь 8АТ.7714.150-01")

    assert lookup.found is True
    assert lookup.page == 601
    assert lookup.designator == "8АТ.7714.150-01"
    assert any("БСПИ-4-2" in pair.name for pair in lookup.pairs)
    assert "8АТ.7714.150-01" in lookup.snippet


def test_lookup_by_name(tmp_path):
    from pdfgraph.catalog import lookup_catalog

    store = _store(tmp_path)
    lookup = lookup_catalog(store, "какой номер у крышки")

    assert lookup.found is True
    assert any(pair.designator == "8АТ.7714.150.017"
               for pair in lookup.pairs)


def test_lookup_ignores_non_catalog_pages(tmp_path):
    from pdfgraph.catalog import lookup_catalog

    store = _store(tmp_path)
    store.set_page_kind(DOC, 601, "other")
    lookup = lookup_catalog(store, "что за деталь 8АТ.7714.150-01")

    assert lookup.found is False
    assert lookup.reason == "в каталоге не нашёл"


def test_lookup_not_found_is_honest(tmp_path):
    from pdfgraph.catalog import lookup_catalog

    store = _store(tmp_path)
    lookup = lookup_catalog(store, "что за деталь 9ХХ.0000.000-99")

    assert lookup.found is False
    assert lookup.reason == "в каталоге не нашёл"
    assert lookup.pairs == []


def test_catalog_fragment_is_verbatim(tmp_path):
    from pdfgraph.catalog import catalog_fragment, lookup_catalog

    store = _store(tmp_path)
    lookup = lookup_catalog(store, "что за деталь 8АТ.7714.150-01")
    fragment = catalog_fragment(store, lookup)

    assert isinstance(fragment, Fragment)
    assert fragment.document == DOC
    assert fragment.page == 601
    assert fragment.kind == "catalog"
    assert lookup.snippet in store.page_text(DOC, 601)


# --- живая база -------------------------------------------------------------


@pytest.fixture(scope="module")
def store_copy_d1b(tmp_path_factory):
    if not STORE_SOURCE.is_file():
        pytest.skip("graph.db отсутствует в корне проекта")
    import importlib.util
    import sys

    path = REPO_ROOT / "tests" / "golden" / "metrics.py"
    spec = importlib.util.spec_from_file_location("golden_metrics_d1b", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    dest = tmp_path_factory.mktemp("d1b_store") / "graph.db"
    module.copy_store(STORE_SOURCE, dest)
    return dest


def test_live_designator_found_in_catalog(store_copy_d1b):
    from pdfgraph.catalog import lookup_catalog
    from pdfgraph.store import Store

    store = Store(str(store_copy_d1b))
    try:
        lookup = lookup_catalog(store, "что за деталь 8АТ.7714.150-01")
        assert lookup.found is True
        assert store.page_kind(lookup.document, lookup.page) == "catalog"
        assert any("БСПИ-4-2" in pair.name for pair in lookup.pairs)
    finally:
        store.close()


def test_live_build_answer_leads_to_catalog(store_copy_d1b):
    from pdfgraph.answer import build_answer
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    store = Store(str(store_copy_d1b))
    try:
        answer = build_answer(store, "что за деталь 8АТ.7714.150-01")
        assert answer.refusal is False
        leader = answer.fragments[0]
        assert store.page_kind(leader.document, leader.page) == "catalog"
        assert "8АТ.7714.150-01" in answer.claims[0].text
        checks = verify_citations(answer, store)
        assert checks and all(check.ok for check in checks)
    finally:
        store.close()


def test_live_unknown_clamp_is_honest_refusal(store_copy_d1b):
    """«МКТ-163» в каталоге базы нет — честный отказ, не выдумка."""
    from pdfgraph.catalog import lookup_catalog
    from pdfgraph.store import Store

    store = Store(str(store_copy_d1b))
    try:
        lookup = lookup_catalog(store, "какой номер у клапана МКТ-163")
        assert lookup.found is False
        assert lookup.reason == "в каталоге не нашёл"
    finally:
        store.close()
