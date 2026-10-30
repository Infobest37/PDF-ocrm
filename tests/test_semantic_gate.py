# -*- coding: utf-8 -*-
"""Ф1.0: гейт векторного канала — порог косинусной близости.

Чанк с косинусом ниже DEFAULT_MIN_COSINE (или PDFGRAPH_MIN_COSINE, если
задан) не выдаётся — посторонний вопрос уходит в отказ; без скачанной
модели канал молча даёт []. Тесты офлайн: сеть и модель не нужны.
"""
import numpy as np
import pytest

from pdfgraph import embedding
from pdfgraph.query import Fragment
from pdfgraph.store import Store

DIM = 4


def _put(store, document, page, arr):
    """Записать листу один чанк со стаб-вектором и метами."""
    store.set_meta("emb_model", "stub")
    store.set_meta("emb_dim", str(DIM))
    store.set_meta("emb_chunk", "600:120")
    store.set_meta("emb_version", "1")
    vec = np.asarray(arr, dtype="<f4")
    norm = float(np.linalg.norm(vec))
    blob = (vec / norm if norm else vec).tobytes()
    assert store.put_embeddings(document, page, page, page, 0, "stub",
                                DIM, [blob]) == 1


def _add_page(store, document, page, text, system_code="075.00.00",
              aircraft="Ми-8МТВ"):
    from pdfprepare.normalize import normalize_for_search

    store.upsert_page(document, page, aircraft, system_code, "",
                      text, normalize_for_search(text))


def _set_q(monkeypatch, arr):
    monkeypatch.setattr(embedding, "query_vector",
                        lambda *a, **k: np.asarray(arr, dtype="f4"))


def _gate_store(tmp_path):
    """л.37 совпадает с запросом (e1), л.38 — нет (e2)."""
    store = Store(str(tmp_path / "g.db"))
    _add_page(store, "РЭ.pdf", 37, "Таблица расхода масла в системе")
    _add_page(store, "РЭ.pdf", 38, "рабочее давление масла 4.5 кгс")
    _put(store, "РЭ.pdf", 37, [1.0, 0.0, 0.0, 0.0])
    _put(store, "РЭ.pdf", 38, [0.0, 1.0, 0.0, 0.0])
    return store


def test_default_gate_without_env(monkeypatch):
    """Без PDFGRAPH_MIN_COSINE гейт держит дефолтный порог 0.18."""
    monkeypatch.delenv(embedding.MIN_COSINE_ENV, raising=False)
    assert embedding.MIN_COSINE_ENV == "PDFGRAPH_MIN_COSINE"
    assert embedding.DEFAULT_MIN_COSINE == 0.18
    assert embedding.effective_min_cosine() == embedding.DEFAULT_MIN_COSINE


def test_env_gate_override_and_bad_values(monkeypatch):
    """Значение читается при каждом вызове; мусор и минус — дефолт."""
    monkeypatch.setenv(embedding.MIN_COSINE_ENV, "0.5")
    assert embedding.effective_min_cosine() == 0.5
    monkeypatch.setenv(embedding.MIN_COSINE_ENV, "0")
    assert embedding.effective_min_cosine() == 0.0
    monkeypatch.setenv(embedding.MIN_COSINE_ENV, "abc")
    assert embedding.effective_min_cosine() == embedding.DEFAULT_MIN_COSINE
    monkeypatch.setenv(embedding.MIN_COSINE_ENV, "-1")
    assert embedding.effective_min_cosine() == embedding.DEFAULT_MIN_COSINE


def test_search_chunks_filters_below_gate(tmp_path, monkeypatch):
    """Косинус ниже порога чанк не возвращается; явный параметр старше."""
    monkeypatch.delenv(embedding.MIN_COSINE_ENV, raising=False)
    _set_q(monkeypatch, [1.0, 0.0, 0.0, 0.0])
    store = _gate_store(tmp_path)
    try:
        hits = embedding.search_chunks(store, "любой вопрос")
        assert [(h[0], h[1]) for h in hits] == [(("РЭ.pdf", 37, 0), 1.0)]
        both = embedding.search_chunks(store, "любой вопрос", min_cosine=0.0)
        assert sorted(h[0] for h in both) == [("РЭ.pdf", 37, 0), ("РЭ.pdf", 38, 0)]
    finally:
        store.close()


def test_env_gate_changes_query_result(tmp_path, monkeypatch):
    """Косинус 0.25 проходит дефолтный порог и режется порогом 0.5."""
    _set_q(monkeypatch, [0.25, -0.968246, 0.0, 0.0])
    monkeypatch.delenv(embedding.MIN_COSINE_ENV, raising=False)
    store = _gate_store(tmp_path)
    try:
        hits = embedding.search_chunks(store, "вопрос")
        assert [h[0] for h in hits] == [("РЭ.pdf", 37, 0)]
        assert hits[0][1] == pytest.approx(0.25)
        monkeypatch.setenv(embedding.MIN_COSINE_ENV, "0.5")
        assert embedding.search_chunks(store, "вопрос") == []
    finally:
        store.close()


def test_query_paths_survive_missing_model(tmp_path, monkeypatch):
    """Векторный канал деградирует в [] без FileNotFoundError."""
    from pdfgraph import query

    def _no_model(model_dir=None):
        raise FileNotFoundError("модель e5 не найдена")

    monkeypatch.setattr(embedding, "model_file_paths", _no_model)
    stub = Fragment(document="РЭ.pdf", page=38, system_code="075.00.00",
                    subsystem_code="", snippet="рабочее давление",
                    char_start=0, char_end=15, score=0.9, kind="text")
    monkeypatch.setattr(query, "search", lambda *args, **kwargs: [stub])
    store = _gate_store(tmp_path)
    try:
        assert query.vector_search(store, "давление масла") == []
        assert query.hybrid_search(store, "давление масла") == [stub]
    finally:
        store.close()


def test_hybrid_full_respects_gate(tmp_path, monkeypatch):
    """hybrid_full: семантика доступна пустой лексике, гейт режет её."""
    from pdfgraph import query

    monkeypatch.setenv("PDFGRAPH_SEARCH_MODE", "hybrid_full")
    monkeypatch.delenv(embedding.MIN_COSINE_ENV, raising=False)
    monkeypatch.setattr(query, "search", lambda *args, **kwargs: [])
    _set_q(monkeypatch, [1.0, 0.0, 0.0, 0.0])
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 37, "Таблица расхода масла в системе")
        _put(store, "РЭ.pdf", 37, [1.0, 0.0, 0.0, 0.0])
        hits = query.hybrid_search(store, "расход масла")
        assert len(hits) == 1
        assert (hits[0].document, hits[0].page) == ("РЭ.pdf", 37)
        assert hits[0].kind == "semantic"
        assert hits[0].snippet == "Таблица расхода масла в системе"
        assert hits[0].score == pytest.approx(embedding.SEMANTIC_BASE_HIGH)
        monkeypatch.setenv(embedding.MIN_COSINE_ENV, "1.05")
        assert query.hybrid_search(store, "расход масла") == []
    finally:
        store.close()