"""Тесты пересчёта векторов на очищенном тексте (карточка Ф1.17).

Писались ДО реализации (ожидался RED). Контракты:

- :func:`pdfgraph.textclean.clean_search_text_mapped` — очищенная строка
  байт-в-байт равна :func:`pdfgraph.textclean.clean_search_text`; карта
  ``spans[i] = (start, end)`` в исходном тексте; срез исходника
  ``text[spans[i][0]:spans[j][1]]`` покрывает ``cleaned[i:j+1]`` дословно.
- :func:`pdfgraph.embedding.chunk_span_raw` — границы чанка ``chunk_ix``
  в координатах ИСХОДНОГО текста; срез исходника дословный, мусор («нннн»,
  «кгс/см?») в сниппет попадает только как часть исходника между границами.
- :func:`pdfgraph.embedding.build_page_embeddings` кодирует чанки
  ОЧИЩЕННОГО текста (вход ``encode_texts`` не содержит мусора) и ставит
  маркер конфигурации ``emb_chunk = "<size>:<overlap>:clean"``.
- :func:`pdfgraph.query.vector_search` отдаёт дословные сниппеты исходного
  текста (verify не ломается).
"""

from __future__ import annotations

import numpy as np
import pytest

from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search


# --- mapped-чистка ----------------------------------------------------------


def test_mapped_string_equals_plain_clean():
    """Строка mapped-чистки совпадает с clean_search_text байт-в-байт."""
    from pdfgraph.textclean import clean_search_text, clean_search_text_mapped

    texts = [
        "Давление масла …………… 2 кгс/см?\nТемпература +15 °С",
        "ннннннннн давление масла кгс/см* ннннннн",
        "ДавлениеМасла на входе",
        "обычный текст без мусора",
        "",
    ]
    for text in texts:
        mapped, _spans = clean_search_text_mapped(text)
        assert mapped == clean_search_text(text)


def test_mapped_spans_cover_verbatim():
    """Каждый диапазон карты — дословный срез исходника, порождающий символ."""
    from pdfgraph.textclean import clean_search_text_mapped

    text = "Давление ……… 2 кгс/см? нннннннн при +15 °С"
    cleaned, spans = clean_search_text_mapped(text)
    assert len(spans) == len(cleaned)
    for start, end in spans:
        assert 0 <= start <= end <= len(text)
    # Произвольные срезы cleaned маппятся в дословные срезы исходника.
    for i in range(0, len(cleaned), 7):
        for j in (i, len(cleaned) - 1):
            if j < i:
                continue
            raw = text[spans[i][0]:spans[j][1]]
            assert raw  # непустой срез исходника
            assert raw.casefold() != ""


def test_mapped_cut_uploads_comb_and_unit():
    """Мусор («нннн…», «кгс/см?», точки) вырезан из очищенной строки."""
    from pdfgraph.textclean import clean_search_text_mapped

    text = "нннннннн давление .......... масла кгс/см? при запуске"
    cleaned, _spans = clean_search_text_mapped(text)
    assert "нннннннн" not in cleaned
    assert "…" not in cleaned
    assert "кгс/см?" not in cleaned
    assert "кгс/см2" in cleaned
    assert "давление" in cleaned and "масла" in cleaned


def test_mapped_case_boundary_zero_width():
    """Синтетический пробел на стыке регистра не рвёт дословность."""
    from pdfgraph.textclean import clean_search_text_mapped

    text = "ДавлениеМасла"
    cleaned, spans = clean_search_text_mapped(text)
    assert cleaned == "Давление Масла"
    # Срез cleaned[i:j+1] -> дословный срез исходника, содержащий оба слова.
    raw = text[spans[0][0]:spans[len(cleaned) - 1][1]]
    assert "Давление" in raw and "Масла" in raw


# --- chunk_span_raw ---------------------------------------------------------


def test_chunk_span_raw_verbatim_on_clean_text():
    """Чистый текст: границы чанка совпадают со старым chunk_span."""
    from pdfgraph.embedding import chunk_span, chunk_span_raw

    text = "а" * 1000
    start, end = chunk_span_raw(text, 1)
    assert (start, end) == chunk_span(text, 1)
    assert text[start:end] == "а" * (end - start)


def test_chunk_span_raw_strips_junk_from_bounds():
    """Сниппет чанка на мусорном листе не начинается внутри «нннннннн»."""
    from pdfgraph.embedding import chunk_span_raw

    junk = "н" * 40
    text = junk + " давление масла на входе двигателя " + "х" * 700
    start, end = chunk_span_raw(text, 0)
    raw = text[start:end]
    assert raw.startswith("давление масла")  # гребёнка вырезана до нарезки
    assert raw.endswith("х") or "давление" in raw


def test_chunk_span_raw_out_of_range():
    """Чанк вне диапазона — IndexError."""
    from pdfgraph.embedding import chunk_span_raw

    with pytest.raises(IndexError):
        chunk_span_raw("короткий текст", 5)


# --- построение индекса на очищенных чанках ---------------------------------


class _CaptureEncoder:
    """Мок encode_texts: ловит вход, отдаёт фиксированные вектора."""

    def __init__(self):
        self.texts: list[list[str]] = []

    def __call__(self, texts, model_dir=None):
        self.texts.append(list(texts))
        return np.ones((len(texts), 4), dtype=np.float32)


def _add_page(store: Store, document: str, page: int, text: str) -> None:
    store.upsert_page(document, page, "Ми-8МТВ", "", "", text,
                      normalize_for_search(text))


def test_build_encodes_cleaned_chunks(tmp_path, monkeypatch):
    """build_page_embeddings кодирует ОЧИЩЕННЫЕ чанки и ставит :clean-маркер."""
    import pdfgraph.embedding as embedding

    capture = _CaptureEncoder()
    monkeypatch.setattr(embedding, "encode_texts", capture)

    store = Store(str(tmp_path / "g.db"))
    try:
        junk = "н" * 40
        _add_page(store, "РЭ.pdf", 1,
                  f"{junk} давление масла 3,5 кгс/см? .... температура +15 °С")
        monkeypatch.setattr(embedding, "model_file_paths", lambda d=None: (
            "stub.onnx", "stub.json"))
        report = embedding.build_page_embeddings(store, rebuild=True)
    finally:
        store.close()

    assert report["embedded"] == 1
    encoded = [chunk for batch in capture.texts for chunk in batch]
    assert encoded, "encode_texts вызван"
    for chunk in encoded:
        assert "нннн" not in chunk
        assert "кгс/см?" not in chunk
        assert "…" not in chunk
        assert "кгс/см2" in chunk


def test_build_sets_clean_marker(tmp_path, monkeypatch):
    """meta emb_chunk = '600:120:clean' — старые и новые векторы не смешать."""
    import pdfgraph.embedding as embedding

    monkeypatch.setattr(embedding, "encode_texts", _CaptureEncoder())
    monkeypatch.setattr(embedding, "model_file_paths", lambda d=None: (
        "stub.onnx", "stub.json"))

    store = Store(str(tmp_path / "g.db"))
    try:
        _add_page(store, "РЭ.pdf", 1, "давление масла")
        embedding.build_page_embeddings(store, rebuild=True)
        assert store.get_meta("emb_chunk") == "600:120:clean"
    finally:
        store.close()


# --- векторный сниппет остаётся дословным -----------------------------------


def test_vector_search_snippet_is_verbatim(tmp_path, monkeypatch):
    """Сниппет semantic-фрагмента — дословный срез pages.text (verify)."""
    from unittest import mock

    import pdfgraph.embedding as embedding
    from pdfgraph.query import vector_search

    store = Store(str(tmp_path / "g.db"))
    try:
        junk = "н" * 40
        text = (f"ннннннн {junk} Давление масла на входе двигателя "
                "3,5 кгс/см? при прогреве")
        _add_page(store, "РЭ.pdf", 1, text)
        qvec = np.asarray([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
        monkeypatch.setattr(embedding, "query_vector",
                            lambda q, model_dir=None: qvec)
        monkeypatch.setattr(embedding, "search_matrix", lambda s: (
            mock.Mock(dim=4,
                      rows=(("РЭ.pdf", 1, 0, False),),
                      matrix=np.asarray([[0.0, 0.0, 1.0, 0.0]],
                                        dtype=np.float32))))
        fragments = vector_search(store, "давление масла")

        assert fragments, "semantic-фрагмент получен"
        fragment = fragments[0]
        raw = store.page_text(fragment.document, fragment.page)
        # Дословность: snippet — точный срез исходного текста.
        assert raw[fragment.char_start:fragment.char_end] == fragment.snippet
        assert fragment.snippet in raw
    finally:
        store.close()
