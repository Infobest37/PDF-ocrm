# -*- coding: utf-8 -*-
"""Ф1.0: оффлайн-пайплайн эмбеддингов (нарезка листа на чанки).

Лист режется на куски по CHUNK_SIZE символов с перекрытием OVERLAP,
границы чанков всегда внутри листа (page_from == page_to == page).
Пустой лист не кодируется: это граница раздела, а не дефект скана.
"""
import numpy as np

from pdfgraph import embedding
from pdfgraph.store import Store

# Размерность стаб-векторов (у настоящей e5-small — 384).
DIM = 4


def _add_page(store, document, page, text, is_cont=False,
              system_code="075.00.00", aircraft="Ми-8МТВ"):
    from pdfprepare.normalize import normalize_for_search

    store.upsert_page(document, page, aircraft, system_code, "",
                      text, normalize_for_search(text))
    if is_cont:
        store.set_page_continuation(document, page, True)


def _stub_paths(model_dir=None):
    """Стаб путей модели — настоящий onnxruntime не нужен."""
    return ("stub-model.onnx", "stub-tokenizer.json")


def _stub_encode(texts, model_dir=None):
    """Детерминированные стаб-векторы: разные тексты — разные векторы."""
    out = []
    for text in texts:
        seed = sum(ord(ch) for ch in text[:32]) + len(text)
        arr = np.asarray([float((seed * (i + 3)) % (i + 5) + 1.0)
                          for i in range(DIM)], dtype="f4")
        arr /= float(np.linalg.norm(arr))
        out.append(arr)
    return out


def _install_stubs(monkeypatch):
    monkeypatch.setattr(embedding, "model_file_paths", _stub_paths)
    monkeypatch.setattr(embedding, "encode_texts", _stub_encode)


def test_chunk_math_and_constants():
    """Шаг нарезки 480 = 600 - 120; пустой текст не даёт кусков."""
    assert embedding.CHUNK_SIZE == 600
    assert embedding.OVERLAP == 120
    assert embedding.chunk_span("а" * 1000, 0) == (0, 600)
    assert embedding.chunk_span("а" * 1000, 1) == (480, 1000)
    assert embedding.chunk_spans("") == []
    assert embedding.chunk_spans("а" * 1000) == [(0, 600), (480, 1000)]


def test_build_page_embeddings_rows(tmp_path, monkeypatch):
    """Пайплайн пишет чанки с нормированными векторами и метами."""
    _install_stubs(monkeypatch)
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "Продолжение табл. 19 " + "а" * 1000)
        _add_page(store, "РЭ.pdf", 8, "Б" * 1000)
        report = embedding.build_page_embeddings(store)
        assert report == {"chunks": 4, "embedded": 2, "bumped": True,
                          "version": 1}
        assert store.get_meta("emb_chunk") == "600:120"
        rows = store.embedding_rows()
        assert [(r.page, r.chunk_ix) for r in rows] == [(7, 0), (7, 1), (8, 0), (8, 1)]
        for row in rows:
            assert (row.page_from, row.page_to) == (row.page, row.page)
            assert row.dim == DIM
        assert rows[0].is_continuation is True   # флаг живьём из pages
        assert rows[2].is_continuation is False
        vec0 = np.frombuffer(bytes(rows[0].vec), dtype="f4")
        vec1 = np.frombuffer(bytes(rows[1].vec), dtype="f4")
        assert vec0.shape == (DIM,)
        assert abs(float(np.linalg.norm(vec0)) - 1.0) < 1e-5  # L2-нормировка
        assert not np.array_equal(vec0, vec1)  # разные куски — разные векторы
        assert set(store.embedded_pages()) == {("РЭ.pdf", 7), ("РЭ.pdf", 8)}
    finally:
        store.close()


def test_empty_page_not_embedded(tmp_path, monkeypatch):
    """Пустой лист не кодируется и не попадает в embedded_pages."""
    _install_stubs(monkeypatch)
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "")
        _add_page(store, "РЭ.pdf", 8, "рабочее давление масла 4.5 кгс")
        report = embedding.build_page_embeddings(store)
        assert report == {"chunks": 1, "embedded": 1, "bumped": True,
                          "version": 1}
        assert ("РЭ.pdf", 7) not in store.embedded_pages()
        assert [r.page for r in store.embedding_rows()] == [8]
    finally:
        store.close()


def test_rerun_is_idempotent(tmp_path, monkeypatch):
    """Повторный прогон не дублирует чанки и не двигает версию."""
    _install_stubs(monkeypatch)
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "а" * 1000)
        first = embedding.build_page_embeddings(store)
        assert first == {"chunks": 2, "embedded": 1, "bumped": True,
                         "version": 1}
        before = [(r.page, r.chunk_ix, bytes(r.vec))
                  for r in store.embedding_rows()]
        second = embedding.build_page_embeddings(store)
        assert second == {"chunks": 0, "embedded": 0, "bumped": False,
                          "version": 1}
        after = [(r.page, r.chunk_ix, bytes(r.vec))
                 for r in store.embedding_rows()]
        assert after == before  # строки не переписывались
        assert store.get_meta("emb_version") == "1"
    finally:
        store.close()


def test_put_embeddings_replaces_sheet_chunks(tmp_path):
    """Повторная запись того же листа заменяет его чанки без дублей."""
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 9, "текст про давление масла")
        store.set_meta("emb_model", "stub")
        store.set_meta("emb_dim", str(DIM))
        store.set_meta("emb_chunk", "600:120")
        store.set_meta("emb_version", "1")
        blob_a = _stub_encode(["кусок один"])[0].tobytes()
        blob_b = _stub_encode(["кусок два"])[0].tobytes()
        first = store.put_embeddings("РЭ.pdf", 9, 9, 9, 0, "stub", DIM,
                                     [blob_a, blob_b])
        assert first == 2
        second = store.put_embeddings("РЭ.pdf", 9, 9, 9, 0, "stub", DIM,
                                      [blob_b])
        assert second == 1
        rows = store.embedding_rows()
        assert [(r.page, r.chunk_ix) for r in rows] == [(9, 0)]
        assert bytes(rows[0].vec) == blob_b
    finally:
        store.close()