# -*- coding: utf-8 -*-
"""Ф1.12 (этап 1): порядок веток FTS — AND → prefix → OR → trigram.

Префиксная ветка пробуется раньше OR: «та же точность, что AND, но с
учётом словоформ». Штрафы веток не меняются (prefix 0.6, OR 0.8).
"""

from pdfgraph.query import _by_fts, search
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search


def _make_store(tmp_path):
    store = Store(str(tmp_path / "graph.db"))
    # Лист с формой слова, отличной от вопроса: «двигателя» ≠ «двигатель».
    store.upsert_page("РЭ.pdf", 38, "Ми-8МТВ", "071.00.00", "",
                      "давление масла двигателя 2 кгс/см2",
                      normalize_for_search("давление масла двигателя "
                                           "2 кгс/см2"))
    store.upsert_page("РЭ.pdf", 39, "Ми-8МТВ", "071.00.00", "",
                      "масла масла масла совсем другой лист",
                      normalize_for_search("масла масла масла совсем "
                                           "другой лист"))
    return store


def test_and_branch_still_first(tmp_path):
    """Точное совпадение всех токенов — AND-ветка (штраф 1.0)."""
    store = _make_store(tmp_path)
    try:
        fragments = _by_fts(store, "давление масла двигателя", None, 5)
        assert fragments
        assert fragments[0].page == 38
        assert fragments[0].score == 1.0  # AND: без штрафа
    finally:
        store.close()


def test_prefix_branch_before_or(tmp_path):
    """AND не матчит («двигател» ≠ «двигателя»), префиксная ветка —
    матчит («двигател*»); её штраф 0.6 против OR 0.8 доказывает порядок
    веток. Примечание: полный токен с «*» ловит только словоформы, где
    форма вопроса — префикс слова листа (без стемминга)."""
    store = _make_store(tmp_path)
    try:
        fragments = _by_fts(store, "давление масла двигател", None, 5)
        assert fragments
        assert fragments[0].page == 38
        assert fragments[0].score == 0.6  # PREFIX_PENALTY: prefix раньше OR
    finally:
        store.close()


def test_or_branch_still_fires_when_prefix_fails(tmp_path):
    """Prefix-AND не матчится, OR находит по одному токену (штраф 0.8)."""
    store = _make_store(tmp_path)
    try:
        # «смазка» нигде нет; «масл*»... prefix-AND требует ВСЕ токены:
        # «давлен*» есть только на л. 38, «смазк*» нигде — prefix пуст.
        fragments = _by_fts(store, "давление смазка", None, 5)
        assert fragments  # OR: «давление» нашёл л. 38
        assert fragments[0].score == 0.8  # OR_PENALTY
        assert fragments[0].page == 38
    finally:
        store.close()


def test_search_merges_same(tmp_path):
    """search() отдаёт фрагменты префиксной ветки без исключений."""
    store = _make_store(tmp_path)
    try:
        fragments = search(store, "давление масла двигател", None, 5)
        assert fragments
        assert {f.page for f in fragments} == {38}
    finally:
        store.close()
