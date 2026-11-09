# -*- coding: utf-8 -*-
"""Ф1.0: локальный офлайн векторный канал (e5-small ONNX int8).

Дополнение владельца от 30.10.2026: чанк не выходит за границы листа;
``page_embeddings`` хранит page_from/page_to и is_continuation; ответ по
листу-продолжению расширяется предыдущим листом с диапазоном листов в
источнике; конфигурации поиска A=lexical/B=hybrid_gated/C=hybrid_full.
Тесты с настоящей моделью пропускаются, если модель не скачана
(scripts/fetch_models.ps1; каталог — PDFGRAPH_MODELS_DIR, по умолчанию
C:\\PDF_Data\\models).
"""
from pathlib import Path

import numpy as np
import pytest

from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

#: Фиктивная размерность стаб-векторов (у настоящей e5-small она 384).
EMB_INDEX_TEST_DIM = 4


def _stub_vec(values: list[float]) -> bytes:
    """L2-нормализованный float32 BLOB — как пишет настоящий кодировщик."""
    arr = np.asarray(values, dtype="<f4")
    norm = float(np.linalg.norm(arr))
    return (arr / norm if norm else arr).tobytes()


def _put_stub_embeddings(store: Store, document: str, page: int,
                         vecs: list[list[float]], *, version: int = 1) -> None:
    """Записать чанки вручную — не требует onnxruntime."""
    store.set_meta("emb_model", "stub")
    store.set_meta("emb_dim", str(EMB_INDEX_TEST_DIM))
    store.set_meta("emb_chunk", "600:120")
    store.set_meta("emb_version", str(version))
    blobs = [_stub_vec(values) for values in vecs]
    count = store.put_embeddings(document, page, page, page, 0, "stub",
                                 EMB_INDEX_TEST_DIM, blobs)
    assert count == len(vecs)


def _add_page(store: Store, document: str, page: int, text: str,
              system_code: str = "075.00.00",
              aircraft: str = "Ми-8МТВ") -> None:
    store.upsert_page(document, page, aircraft, system_code, "",
                      text, normalize_for_search(text))


# ---------------------------------------------------------------------------
# 1. Миграция и схема (дополнение владельца, п. 2)


def test_schema_v5_has_page_embeddings_columns(tmp_path):
    from pdfgraph.store import SCHEMA_VERSION

    store = Store(str(tmp_path / "g.db"))
    try:
        # Ф0.3б/Ф1.23а: актуальная схема — v7; колонки v5 сохраняются.
        assert SCHEMA_VERSION == 7
        assert store.get_meta("schema_version") == "7"
        columns = {row[1] for row in store._connection.execute(
            "PRAGMA table_info(page_embeddings)")}
        assert {"document", "page", "chunk_ix", "dim", "model", "vec",
                "page_from", "page_to", "is_continuation"} <= columns
        page_columns = {row[1] for row in store._connection.execute(
            "PRAGMA table_info(pages)")}
        assert "is_continuation" in page_columns
    finally:
        store.close()


def test_migration_keeps_counters(tmp_path):
    store = Store(str(tmp_path / "g.db"))
    try:
        store.add_node("system", "025.00.00")
        _add_page(store, "РЭ.pdf", 5, "Смазка агрегатов")
        before = store.stats()
    finally:
        store.close()
    reopened = Store(str(tmp_path / "g.db"))
    try:
        assert reopened.stats() == before
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 2. Чанкер: текст одного листа режется внутри себя (п. 1 дополнения)


def test_chunk_page_slices_and_sizes():
    from pdfgraph.embedding import chunk_page

    text = "а" * 1000
    chunks = chunk_page(text, 600, 120)
    assert chunks[0] == text[:600]
    assert chunks[1] == text[480:1000]
    assert all(len(chunk) <= 600 for chunk in chunks)
    assert chunk_page("", 600, 120) == []
    assert chunk_page("короткий текст", 600, 120) == ["короткий текст"]


def test_build_needs_model_and_starts_empty(tmp_path):
    from pdfgraph.embedding import build_page_embeddings

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "Текст листа семь")
        with pytest.raises(FileNotFoundError):
            build_page_embeddings(store, model_dir=str(tmp_path / "empty"))
        assert store.embedded_pages() == set()
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 3. Детектор продолжений (п. 2 дополнения)


def test_detect_continuation_markers():
    from pdfgraph.embedding import detect_continuation

    assert detect_continuation("Продолжение табл. 34\nданные", None)
    assert detect_continuation("продолжение таблицы 5\nряды", None)
    assert detect_continuation("давление масла в магистрали",
                               "Таблица 18\nряды данных")
    assert detect_continuation("Таблица 18 Расход масла\nряды",
                               "Таблица 18 Расход масла\nдругие ряды")
    assert not detect_continuation("023.11.30 АВАРИЙНЫЙ РАДИОМАЯК", None)
    assert not detect_continuation("Таблица 18 Расход масла",
                                   "Совершенно другой лист")
    assert not detect_continuation("", None)


def test_write_continuation_flags_is_idempotent(tmp_path):
    from pdfgraph.embedding import write_continuation_flags

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "Таблица 18 Расход масла")
        _add_page(store, "РЭ.pdf", 8, "Продолжение табл. 18\nряды")
        _add_page(store, "РЭ.pdf", 9, "давление масла упало")
        _add_page(store, "РЭ.pdf", 10, "Обычный лист с заглавной")

        assert write_continuation_flags(store) == 2
        assert store.is_continuation("РЭ.pdf", 8) is True
        assert store.is_continuation("РЭ.pdf", 9) is True
        assert store.is_continuation("РЭ.pdf", 7) is False
        assert store.is_continuation("РЭ.pdf", 10) is False
        # Повторный прогон ничего не меняет.
        assert write_continuation_flags(store) == 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 4. Векторный поиск и конфигурации A/B/C (модель не нужна — стаб-векторы)


def test_vector_search_returns_verbatim_semantic_fragment(tmp_path):
    from unittest import mock

    from pdfgraph import embedding, query

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 37, "Таблица расхода масла в системе")
        _add_page(store, "РЭ.pdf", 38, "рабочее давление масла 4.5 кгс")
        _put_stub_embeddings(store, "РЭ.pdf", 37, [[0.2, 0.9, 0.0, 0.0]])
        _put_stub_embeddings(store, "РЭ.pdf", 38, [[0.9, 0.2, 0.0, 0.0]])

        qvec = np.asarray([0.9, 0.2, 0.0, 0.0], dtype=np.float32)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            hits = query.vector_search(store, "рабочее давление масла")
        assert hits and hits[0].kind == "semantic"
        assert (hits[0].document, hits[0].page) == ("РЭ.pdf", 38)
        assert hits[0].char_start is not None
        assert hits[0].snippet == store.page_text("РЭ.pdf", 38)[
            hits[0].char_start:hits[0].char_end]
        assert 0.99 < hits[0].score <= 1.0
    finally:
        store.close()


def test_hybrid_search_modes(tmp_path, monkeypatch):
    """A=lexical, B=hybrid_gated (дефолт), C=hybrid_full за PDFGRAPH_SEARCH_MODE."""
    from unittest import mock

    from pdfgraph import embedding, query

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 38, "рабочее давление масла 4.5 кгс")
        _put_stub_embeddings(store, "РЭ.pdf", 38, [[0.0, 0.0, 0.9, 0.2]])
        qvec = np.asarray([0.0, 0.0, 0.9, 0.2], dtype=np.float32)

        called = {"query": False}

        def sentinel(question, model_dir=None):
            called["query"] = True
            return qvec

        # B (дефолт): лексика пуста → отказ без цитат, вектор не вызывается.
        monkeypatch.delenv("PDFGRAPH_SEARCH_MODE", raising=False)
        assert query.effective_search_mode() == "hybrid_gated"
        with mock.patch.object(embedding, "query_vector", sentinel):
            assert query.hybrid_search(
                store, "совершенно отсутствующая тема") == []
        assert called["query"] is False

        # C (за флагом): чисто векторный путь работает и без лексики.
        monkeypatch.setenv("PDFGRAPH_SEARCH_MODE", "hybrid_full")
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            hits_full = query.hybrid_search(
                store, "совершенно отсутствующая тема")
        assert [(h.kind, h.page) for h in hits_full] == [("semantic", 38)]

        # A: вектор не участвует вовсе.
        monkeypatch.setenv("PDFGRAPH_SEARCH_MODE", "lexical")
        with mock.patch.object(embedding, "query_vector", sentinel):
            assert query.hybrid_search(
                store, "совершенно отсутствующая тема") == []
        assert called["query"] is False
    finally:
        store.close()


def test_hybrid_fusion_adds_semantic_when_lexical_found(tmp_path):
    from unittest import mock

    from pdfgraph import embedding, query

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 40, "Масломер механический указатель")
        _add_page(store, "РЭ.pdf", 41, "Расход масла фиксируется стрелкой")
        _put_stub_embeddings(store, "РЭ.pdf", 41, [[0.1, 0.0, 0.9, 0.3]])
        qvec = np.asarray([0.1, 0.0, 0.9, 0.3], dtype=np.float32)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            hits = query.hybrid_search(store, "масломер")
        kinds = {hit.kind for hit in hits}
        assert "text" in kinds and "semantic" in kinds
        assert (hits[0].document, hits[0].page) == ("РЭ.pdf", 40)
    finally:
        store.close()


def test_fusion_puts_semantic_only_sheet_into_top5(tmp_path):
    """Карточка Ф1.1: лист из семантики, которого нет в лексике, в топ-5.

    Двенадцать лексических листов-«масломеров» запирают список; лист,
    который находит только векторный канал, обязан оказаться в пятёрке
    после RRF-слияния до отсечения (до правки он дописывался в хвост).
    """
    from unittest import mock

    from pdfgraph import embedding, query

    store = Store(str(tmp_path / "g.db"))
    try:
        for page in range(1, 13):
            _add_page(store, "РЭ.pdf", page,
                      f"Масломер механический указатель лист {page}")
        _add_page(store, "РЭ.pdf", 41, "Расход масла фиксируется стрелкой")
        _put_stub_embeddings(store, "РЭ.pdf", 41, [[0.1, 0.0, 0.9, 0.3]])
        qvec = np.asarray([0.1, 0.0, 0.9, 0.3], dtype=np.float32)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            hits = query.hybrid_search(store, "масломер", limit=10)
        top5 = [(hit.document, hit.page) for hit in hits[:5]]
        assert ("РЭ.pdf", 41) in top5
        assert len(hits) <= 10
    finally:
        store.close()


def test_refusal_invariant_with_vectors_present(tmp_path):
    """Владелец: вектор не превращает отказ в «примерный ответ»."""
    from unittest import mock

    from pdfgraph import embedding
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 38, "рабочее давление масла 4.5 кгс")
        _put_stub_embeddings(store, "РЭ.pdf", 38, [[1.0, 0.0, 0.0, 0.0]])
        qvec = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            answer = build_answer(store, "совершенно отсутствующая тема")
        assert answer.refusal is True
        assert answer.claims == []
        assert verify_citations(answer, store) == []
    finally:
        store.close()


def test_matrix_cache_invalidation_by_emb_version(tmp_path):
    """Кэш матрицы в памяти сбрасывается при росте meta.emb_version."""
    from unittest import mock

    from pdfgraph import embedding, query

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 38, "давление масла")
        _put_stub_embeddings(store, "РЭ.pdf", 38, [[0.0, 0.9, 0.1, 0.0]],
                             version=1)
        qvec = np.asarray([0.0, 0.9, 0.1, 0.0], dtype=np.float32)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            assert query.vector_search(store, "давление масла")
        # Новый чанк с ростом версии → кэш перечитан, оба листа найдены.
        _add_page(store, "РЭ.pdf", 44, "масляный насос агрегата")
        _put_stub_embeddings(store, "РЭ.pdf", 44, [[0.0, 0.9, 0.1, 0.4]],
                             version=2)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            hits = query.vector_search(store, "давление масла")
        assert {(hit.document, hit.page)
                for hit in hits} == {("РЭ.pdf", 38), ("РЭ.pdf", 44)}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 5. Расширение контекста продолжений в ответе (п. 3 дополнения)


def test_answer_expands_continuation_with_verbatim_quote(tmp_path,
                                                         monkeypatch):
    from unittest import mock

    from pdfgraph import embedding
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations

    monkeypatch.setenv("PDFGRAPH_SEARCH_MODE", "hybrid_gated")
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "Таблица 18 Расход масла")
        _add_page(store, "РЭ.pdf", 8,
                  "Продолжение табл. 18 давление масла в нагнетающей магистрали")
        assert embedding.write_continuation_flags(store) == 1

        answer = build_answer(store, "Продолжение табл. 18 давление масла")
        assert answer.refusal is False
        top = answer.fragments[0]
        assert (top.document, top.page) == ("РЭ.pdf", 8)
        continuations = [fragment for fragment in answer.fragments
                         if fragment.kind == "continuation"]
        assert [(fragment.document, fragment.page)
                for fragment in continuations] == [("РЭ.pdf", 7)]
        for fragment in continuations:
            assert fragment.char_start is not None
            assert fragment.snippet == store.page_text("РЭ.pdf", 7)[
                fragment.char_start:fragment.char_end]
        checks = verify_citations(answer, store)
        assert checks and all(check.ok for check in checks)
    finally:
        store.close()


def test_evidence_ranges_printed_for_continuation(tmp_path, capsys):
    from unittest import mock

    from pdfgraph import embedding
    from pdfgraph.answer import build_answer
    from pdfgraph.cli import _evidence_rows, _print_text
    from pdfgraph.verify import verify_citations

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 7, "Таблица 18 Расход масла")
        _add_page(store, "РЭ.pdf", 8,
                  "Продолжение табл. 18 давление масла в магистрали")
        embedding.write_continuation_flags(store)
        qvec = np.asarray([0.0, 0.9, 0.1, 0.0], dtype=np.float32)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            answer = build_answer(store, "Продолжение табл. 18 давление масла")
        checks = verify_citations(answer, store)
        rows = _evidence_rows(answer, checks, store)
        _print_text(answer, rows, True, store)
        out = capsys.readouterr().out
        assert "листы 7–8" in out
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 6. Прогон с настоящей моделью (skipif — 118 МБ, не в git)


def _model_present() -> bool:
    from pdfgraph.embedding import model_file_paths

    try:
        onnx, tokenizer = model_file_paths(None)
    except FileNotFoundError:
        return False
    return Path(onnx).is_file() and Path(tokenizer).is_file()


@pytest.mark.skipif(not _model_present(), reason="модель e5 не установлена")
def test_build_page_embeddings_real_model_and_search(tmp_path):
    from pdfgraph import embedding
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 38,
                  "Таблица 19. Давление масла в магистрали, кгс/см2")
        _add_page(store, "РЭ.pdf", 39, "Продолжение табл. 19 ряды данных")
        report = embedding.build_page_embeddings(store)
        assert report["embedded"] == 2 and report["bumped"] is True
        rows = store.embedding_rows()
        assert len(rows) >= 2
        assert all(row.page == row.page_from == row.page_to for row in rows)
        assert any(row.is_continuation for row in rows)
        answer = build_answer(store, "какое давление масла в магистрали")
        assert answer.refusal is False
        top = answer.fragments[0]
        assert (top.document, top.page) in (("РЭ.pdf", 38), ("РЭ.pdf", 39))
        checks = verify_citations(answer, store)
        assert checks and all(check.ok for check in checks)
    finally:
        store.close()


@pytest.mark.skipif(not _model_present(), reason="модель e5 не установлена")
def test_emb_index_is_idempotent_and_rebuild(tmp_path):
    from pdfgraph import embedding

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 38, "давление масла в магистрали")
        first = embedding.build_page_embeddings(store)
        assert first["embedded"] == 1 and first["bumped"] is True
        assert first["chunks"] >= 1
        second = embedding.build_page_embeddings(store)
        assert second["embedded"] == 0 and second["bumped"] is False
        assert second["version"] == first["version"]
        assert len(store.embedding_rows()) == first["chunks"]
        rebuilt = embedding.build_page_embeddings(store, rebuild=True)
        assert rebuilt["embedded"] == 1
        assert rebuilt["version"] == first["version"] + 1
        assert len(store.embedding_rows()) == first["chunks"]
    finally:
        store.close()


@pytest.mark.skipif(not _model_present(), reason="модель e5 не установлена")
def test_embedding_never_touches_network(tmp_path, monkeypatch):
    """Кодировщик и поиск не выходят в сеть (аварийный режим обязателен)."""
    import socket
    from unittest import mock

    from pdfgraph import embedding, query

    def _no_socket(*args, **kwargs):  # pragma: no cover — ошибка, если зовётся
        raise AssertionError("сетевой вызов запрещён")

    monkeypatch.setattr(socket, "socket", _no_socket)
    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 38, "рабочее давление масла 4.5 кгс")
        report = embedding.build_page_embeddings(store)
        assert report["chunks"] >= 1
        # Стаб-вектор — настоящее офлайн-кодирование того же текста
        # (384-мерный e5): косинус с чанком листа 38 = 1.0 детерминированно.
        qvec = np.asarray(embedding.encode_texts(
            ["рабочее давление масла 4.5 кгс"]), dtype=np.float32)[0]
        assert qvec.shape == (384,)
        with mock.patch.object(embedding, "query_vector", return_value=qvec):
            hits = query.vector_search(store, "рабочее давление масла")
        assert hits and hits[0].page == 38
    finally:
        store.close()


@pytest.mark.skipif(not _model_present(), reason="модель e5 не установлена")
def test_encode_texts_batch_with_uneven_lengths():
    """Баг приёмки Ф1.0: батч с разными числами токенов падал ragged-ошибкой.

    ``build_page_embeddings`` кодирует чанки батчами (``BATCH_SIZE = 64``);
    длины токен-последовательностей внутри батча различаются, и до
    выравнивания ``np.asarray`` поднимал
    ``ValueError: setting an array element with a sequence`` — это падение
    emb-index на рабочей базе (13700 листов), воспроизведено 30.10.2026.
    Выравнивание обязано не менять вектор: пад-токены исключены из внимания
    (attention_mask = 0) и из mean-pooling.
    """
    from pdfgraph import embedding

    vectors = embedding.encode_texts([
        "радиомаяк",
        "рабочее давление масла в магистрали смазки " * 30,
        "давление",
    ])
    assert vectors.shape == (3, 384)
    # Паддинг внутри батча семантику не меняет: cos с одиночным прогоном
    # ~1 (замер: 0.99861). Битовой равенства нет — int8-матмул на другой
    # ширине матрицы даёт вариацию ~1e-3; на ранжирование это не влияет
    # (рабочие косинусы выдачи 0.7–0.9, порог гейта 0.18).
    single = np.asarray(embedding.encode_texts(["радиомаяк"]),
                        dtype=np.float32)
    assert float(vectors[0] @ single[0]) > 0.99