"""Тесты чанкового канала поиска (карточка Ф1.23а).

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

- Миграция v6→v7 создаёт FTS5-таблицу ``chunks_fts`` (chunk_ix/document/
  page UNINDEXED, body); существующие таблицы не меняются (счётчики).
- Заполнение идемпотентно: повторный прогон ``chunk-index`` не дублирует.
- ``pdfgraph.query.chunk_search`` возвращает ЛИСТЫ (Fragment, kind="text",
  уникальные (document, page)), а не чанки; сниппет — дословный срез
  ``pages.text`` (verify-совместимость).
- Агрегация: лист = лучший чанк по bm25; лист с >=2 чанками в топе
  получает фикс. бонус :data:`CHUNK_SECOND_BONUS`; несколько чанков
  одного листа сворачиваются в один слот.
- Каскад веток AND → prefix → OR → trigram — как в ``_by_fts``.
- Выключатель ``PDFGRAPH_CHUNK_CHANNEL``: по умолчанию выключен;
  ``fuse_hybrid`` с флагом выкл — порядок байт-в-байт прежний;
  незаполненный ``chunks_fts`` при включённом флаге — пустой канал,
  слияние прежнее (fail-open).
"""

from __future__ import annotations

import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

FX_DOC = "РТЭ кн.1.pdf"
FX_DOC2 = "РЭ кн.2.pdf"
FX_AIRCRAFT = "Ми-8МТВ"

#: Лист 10: плотный текст, где «давление масла» в одном чанке из многих
#: (плотная таблица — мотивировка карточки). Лист 20: короткий текст,
#: целиком умещающийся в один чанк. Чанк 600/120 — модульные константы.
FX_LONG = ("давление масла в двигателе 3,5 кгс/см2. " * 12
           + "прочий текст без совпадений. " * 40
           + "давление масла на входе в фильтр. " * 12)
FX_TEXTS = {
    10: FX_LONG,          # 2+ чанка с «давление масла»
    20: "давление масла в редукторе проверяется по прибору",
    30: "только прочий текст без ключевых слов запроса",
}

DOC38 = "РТЭ кн.1.pdf"


def _build_store(tmp_path) -> Store:
    """Store с тремя листами; chunks_fts ещё не заполнен."""
    store = Store(str(tmp_path / "graph.db"))
    for page, text in FX_TEXTS.items():
        store.upsert_page(FX_DOC, page, FX_AIRCRAFT, "", "", text,
                          normalize_for_search(text))
    store.upsert_page(FX_DOC2, 10, FX_AIRCRAFT, "", "", FX_TEXTS[20],
                      normalize_for_search(FX_TEXTS[20]))
    return store


@pytest.fixture
def chunk_store(tmp_path):
    store = _build_store(tmp_path)
    yield store
    store.close()


def _fill_chunks(store: Store) -> int:
    """Заполнить chunks_fts (как делает команда chunk-index)."""
    from pdfgraph.cli import rebuild_chunk_index
    return rebuild_chunk_index(store)


def _frag(document: str, page: int, score: float,
          kind: str = "text") -> Fragment:
    text = FX_TEXTS.get(page, FX_TEXTS[20])
    return Fragment(
        document=document, page=page, system_code="", subsystem_code="",
        snippet=text, char_start=0, char_end=len(text), score=score,
        kind=kind,
    )


# --- миграция и заполнение ---------------------------------------------------


def test_chunks_fts_created(chunk_store):
    """Миграция v6→v7: таблица chunks_fts существует, схема_version >= 7."""
    from pdfgraph.store import SCHEMA_VERSION
    row = chunk_store._connection.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row[0]) >= SCHEMA_VERSION >= 7
    tables = {r[0] for r in chunk_store._connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "chunks_fts" in tables


def test_fill_idempotent(chunk_store):
    """Повторное заполнение не дублирует чанки."""
    count1 = _fill_chunks(chunk_store)
    count2 = _fill_chunks(chunk_store)
    assert count1 == count2
    rows = chunk_store._connection.execute(
        "SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert rows == count1
    # Каждая пара (document, page, chunk_ix) встречается один раз.
    dupes = chunk_store._connection.execute(
        "SELECT COUNT(*) FROM (SELECT document, page, chunk_ix, "
        "COUNT(*) c FROM chunks_fts GROUP BY document, page, chunk_ix "
        "HAVING c > 1)").fetchone()[0]
    assert dupes == 0


def test_empty_page_no_chunks(tmp_path):
    """Пустой текст листа: чанков нет, заполнение и поиск не ломаются."""
    store = _build_store(tmp_path)
    try:
        store.upsert_page(FX_DOC, 40, FX_AIRCRAFT, "", "", "",
                          normalize_for_search(""))
        assert _fill_chunks(store) >= 0
        from pdfgraph.query import chunk_search
        assert chunk_search(store, "давление масла", 5) is not None
    finally:
        store.close()


# --- chunk_search: листы, агрегация, бонус -----------------------------------


def test_chunk_search_returns_pages(chunk_store):
    """chunk_search возвращает уникальные ЛИСТЫ, kind='text'."""
    _fill_chunks(chunk_store)
    from pdfgraph.query import chunk_search
    fragments = chunk_search(chunk_store, "давление масла", 5)
    keys = [(f.document, f.page) for f in fragments]
    assert len(keys) == len(set(keys))
    assert all(f.kind == "text" for f in fragments)
    # Плотный лист 10 (два чанка с фразой) и лист 20 найдены.
    assert (FX_DOC, 10) in keys
    assert (FX_DOC, 20) in keys


def test_chunk_snippet_verbatim(chunk_store):
    """Сниппет чанка — дословная подстрока pages.text (verify-совместимость)."""
    _fill_chunks(chunk_store)
    from pdfgraph.query import chunk_search
    fragments = chunk_search(chunk_store, "давление масла", 5)
    for fragment in fragments:
        page_text = chunk_store.page_text(fragment.document, fragment.page)
        assert fragment.snippet in page_text


def test_aggregation_best_chunk_and_bonus():
    """Агрегатор: лист = лучший чанк; бонус листу с >=2 чанками."""
    from pdfgraph.query import _aggregate_chunk_rows, CHUNK_SECOND_BONUS
    # bm25 отрицательный, меньше = лучше. Лист A: два чанка, ЛУЧШИЙ по
    # bm25 — самый отрицательный (-5.5); лист B: один чанк (-3.0).
    rows = [
        (FX_DOC, 10, -3.0),
        (FX_DOC2, 10, -3.0),
        (FX_DOC, 10, -5.5),   # лучший чанк листа A
    ]
    aggregated = _aggregate_chunk_rows(rows, limit=10)
    keys = [(document, page) for document, page, _ in aggregated]
    assert keys[0] == (FX_DOC, 10)          # бонус 0.05 перебивает ничью
    assert keys[1] == (FX_DOC2, 10)
    scores = {(document, page): score
              for document, page, score in aggregated}
    # Скор листа A = 1.0 (его чанк — лучший канала) + бонус; лист B — 1.0.
    assert scores[(FX_DOC, 10)] == pytest.approx(
        1.0 + CHUNK_SECOND_BONUS, abs=1e-6)
    assert scores[(FX_DOC2, 10)] == pytest.approx(3.0 / 5.5, abs=1e-6)
    # Один лист — один слот: лист A не дублируется.
    assert keys.count((FX_DOC, 10)) == 1


def test_aggregation_five_chunks_one_slot():
    """5 чанков одного листа в топе сворачиваются в один слот листа."""
    from pdfgraph.query import _aggregate_chunk_rows
    rows = [(FX_DOC, 10, -(1.0 + i)) for i in range(5)]
    rows.append((FX_DOC2, 10, -9.0))
    aggregated = _aggregate_chunk_rows(rows, limit=10)
    keys = [(document, page) for document, page, _ in aggregated]
    assert keys.count((FX_DOC, 10)) == 1
    assert (FX_DOC2, 10) in keys


def test_cascade_or_fallback(chunk_store):
    """AND/prefix пусты, словоформы — OR-ветка находит листы по чанкам."""
    _fill_chunks(chunk_store)
    from pdfgraph.query import chunk_search
    # «давление» есть, «масла» есть; словоформа «маслу» только через OR
    # (у чанка 20 «маслу» нет — проверяем, что OR срабатывает на смешанном
    # запросе с одной словоформой).
    fragments = chunk_search(chunk_store, "давление маслу прибора", 5)
    assert isinstance(fragments, list)
    # AND по трём словам пуст, OR находит лист 20 («прибору» в тексте? нет:
    # «прибора» vs «прибору» — OR-ветка по любому слову) — как минимум
    # один лист возвращён.
    assert len(fragments) >= 1


# --- выключатель и третий канал в fuse_hybrid --------------------------------


def _expected_fusion(lexical, vector):
    from pdfgraph.rag_fusion import (EXACT_KINDS, effective_exact_boost,
                                     effective_fusion_k, rrf_fuse)
    return rrf_fuse([list(lexical), list(vector)],
                    k=effective_fusion_k(),
                    boost=effective_exact_boost(),
                    boost_kinds=EXACT_KINDS)[:10]


def test_flag_off_fuse_unchanged(chunk_store, monkeypatch):
    """Флаг выключен: fuse_hybrid порядок байт-в-байт прежний."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.query import CHUNK_CHANNEL_ENV

    monkeypatch.delenv(CHUNK_CHANNEL_ENV, raising=False)
    lexical = [_frag(FX_DOC, 30, 0.9), _frag(FX_DOC, 10, 0.8)]
    vector = [_frag(FX_DOC2, 10, 0.95, kind="semantic")]
    expected = _expected_fusion(lexical, vector)
    assert rag_fusion.fuse_hybrid(
        lexical, vector, limit=10, store=chunk_store,
        question="давление масла") == expected


def test_flag_on_empty_index_fuse_unchanged(chunk_store, monkeypatch):
    """Флаг включён, chunks_fts пуст: канал пуст, слияние прежнее."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.query import CHUNK_CHANNEL_ENV

    monkeypatch.setenv(CHUNK_CHANNEL_ENV, "1")
    lexical = [_frag(FX_DOC, 30, 0.9), _frag(FX_DOC, 10, 0.8)]
    vector = [_frag(FX_DOC2, 10, 0.95, kind="semantic")]
    expected = _expected_fusion(lexical, vector)
    assert rag_fusion.fuse_hybrid(
        lexical, vector, limit=10, store=chunk_store,
        question="давление масла") == expected


def test_flag_on_chunk_channel_reranks(chunk_store, monkeypatch):
    """Флаг включён + индекс заполнен: чанк-канал поднимает плотный лист.

    Лист 10 (два чанка с фразой в 600-символьном окне) должен войти
    в топ-2 слияния, вытеснив слабые фрагменты лексики.
    """
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.query import CHUNK_CHANNEL_ENV

    _fill_chunks(chunk_store)
    monkeypatch.setenv(CHUNK_CHANNEL_ENV, "1")
    # Лист 30 без совпадений стоит выше по лексическому скору.
    lexical = [_frag(FX_DOC, 30, 0.9)]
    ordered = rag_fusion.fuse_hybrid(lexical, [], limit=5,
                                     store=chunk_store,
                                     question="давление масла")
    pages = [(f.document, f.page) for f in ordered]
    assert (FX_DOC, 10) in pages[:2]
