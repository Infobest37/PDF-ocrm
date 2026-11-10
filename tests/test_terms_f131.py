# -*- coding: utf-8 -*-
"""Тесты словаря алиасов и канала подстановки (карточка Ф1.32).

Контракты, закреплённые здесь (пишутся ДО реализации — ожидался RED):

- ``pdfgraph.terms.expand_question``: регистронезависимая замена всех
  вхождений ключа из ``data/terms.toml`` за один проход, без рекурсии
  (значение повторно не расширяется); вопрос без ключа → ``None``;
  отсутствие файла → ``None``; битый TOML → ``logger.warning`` и
  ``None``.
- Выключатель ``PDFGRAPH_TERMS``: по умолчанию выключен; флаг выключен
  — поведение байт-в-байт прежнее.
- ``fuse_hybrid``: при подстановке добавляется лексический канал по
  расширенному вопросу с весом ``TERMS_WEIGHT = 0.7`` через
  ``ranking_weights``; вопрос не переписывается. Вопросы без ключей —
  выдача не меняется (дифф нулевой).
- ``aircraft_type`` — keyword-only параметр ``fuse_hybrid``, передаётся
  в лексический поиск канала алиасов; прежние вызовы не меняются.
- Веса каналов при нормах без алиасов — те же, что до правки:
  ``[1.0, 1.0, NORMS_WEIGHT]``.
"""

from __future__ import annotations

import logging

import pytest

from pdfgraph.query import search, vector_search
from pdfgraph.rag_fusion import fuse_hybrid
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

DOC_RTE = "РТЭ кн.3.pdf"
DOC_REG = "Регламент ч.2.pdf"
AIR = "Ми-8МТВ"
PAGE64 = 64
PAGE141 = 141

#: Аналог листа 64 РТЭ кн.3: норма высоты щёток, «МВ-1200» нет вообще.
FX_64 = ("Карта 021.40.00h. Замена щеток электродвигателя. "
         "Минимально допустимая высота щеток равна 13,5 мм. "
         "При износе щеток ниже нормы щетки заменить.")
#: Аналог листа 141 Регламента ч.2: единственное упоминание «МВ-1200»,
#: нормы высоты нет — только отметки «при измерении/при замене».
FX_141 = ("№ пп Содержание записи Место записи 3 Высота щеток "
          "электродвигателя МВ-1200: - при измерении высоты щеток + - "
          "- при замене щеток + +")

FX_TEXTS = {PAGE64: FX_64, PAGE141: FX_141}

ALIAS_TOML = ('[alias]\n"МВ-1200" = '
              '"электродвигатель вентилятора обогревателя КО-50"\n')
BROKEN_TOML = '[alias\n"МВ-1200" = unquoted value'

Q_MB = "какая минимальная высота щеток на МВ-1200"
Q_NO_KEY = "какое давление масла в двигателе"


def _build_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "graph.db"))
    for page, text in FX_TEXTS.items():
        store.upsert_page(DOC_RTE if page == PAGE64 else DOC_REG, page,
                          AIR, "", "", text, normalize_for_search(text))
    return store


@pytest.fixture
def store(tmp_path):
    store = _build_store(tmp_path)
    yield store
    store.close()


@pytest.fixture
def terms_file(tmp_path, monkeypatch):
    """Файл алиасов во временном каталоге; путь — через атрибут модуля."""
    path = tmp_path / "terms.toml"
    path.write_text(ALIAS_TOML, encoding="utf-8")
    monkeypatch.setattr("pdfgraph.terms._TERMS_PATH", str(path))
    return path


def _run_fuse(store, question: str, aircraft_type: str | None = AIR):
    """Обычная цепочка слияния: лексика + вектор, топ-10."""
    lex = search(store, question, aircraft_type, 50)
    vec = vector_search(store, question, 50, aircraft_type=aircraft_type)
    return fuse_hybrid(lex, vec, limit=10, store=store, question=question,
                       aircraft_type=aircraft_type)


def _keys(fragments) -> list[tuple[str, int]]:
    return [(f.document, f.page) for f in fragments]


# --- expand_question ---------------------------------------------------------


def test_expand_replaces_case_insensitive(monkeypatch, terms_file):
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    from pdfgraph.terms import expand_question
    expanded = expand_question(Q_MB)
    assert expanded is not None
    assert "МВ-1200" not in expanded
    assert "электродвигатель вентилятора обогревателя КО-50" in expanded
    # Регистр ключа в вопросе не важен.
    lower = expand_question("высота щеток мв-1200")
    assert lower is not None
    assert "мв-1200" not in lower
    assert "КО-50" in lower


def test_expand_no_key_returns_none(monkeypatch, terms_file):
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    from pdfgraph.terms import expand_question
    assert expand_question(Q_NO_KEY) is None


def test_expand_flag_off_returns_none(monkeypatch, terms_file):
    monkeypatch.delenv("PDFGRAPH_TERMS", raising=False)
    from pdfgraph.terms import expand_question
    assert expand_question(Q_MB) is None


def test_expand_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    monkeypatch.setattr(
        "pdfgraph.terms._TERMS_PATH", str(tmp_path / "absent.toml"))
    from pdfgraph.terms import expand_question
    assert expand_question(Q_MB) is None


def test_expand_broken_toml_warns_and_none(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    path = tmp_path / "broken.toml"
    path.write_text(BROKEN_TOML, encoding="utf-8")
    monkeypatch.setattr("pdfgraph.terms._TERMS_PATH", str(path))
    from pdfgraph.terms import expand_question
    with caplog.at_level(logging.WARNING, logger="pdfgraph.terms"):
        assert expand_question(Q_MB) is None
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


# --- fuse_hybrid: дифф и топ-5 ------------------------------------------------


def test_fuse_no_key_zero_diff(monkeypatch, store, terms_file):
    """Вопрос без ключа: флаг вкл — выдача не отличается от флага выкл."""
    monkeypatch.delenv("PDFGRAPH_TERMS", raising=False)
    baseline = _run_fuse(store, Q_NO_KEY)
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    with_terms = _run_fuse(store, Q_NO_KEY)
    assert baseline == with_terms


def test_fuse_flag_off_byte_identical(monkeypatch, store, terms_file):
    """Флаг выключен: выдача байт-в-байт как без файла алиасов вовсе."""
    monkeypatch.delenv("PDFGRAPH_TERMS", raising=False)
    flag_off = _run_fuse(store, Q_MB)
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    monkeypatch.setattr(
        "pdfgraph.terms._TERMS_PATH",
        str(terms_file.parent / "absent.toml"))
    no_terms = _run_fuse(store, Q_MB)
    assert flag_off == no_terms


def test_mb1200_question_page64_top5(monkeypatch, store, terms_file):
    """Живой вопрос про МВ-1200: л.64 с нормой 13,5 мм — в топ-5."""
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    fused = _run_fuse(store, Q_MB)
    assert (DOC_RTE, PAGE64) in _keys(fused)[:5]


def test_fuse_without_aircraft_type_still_works(monkeypatch, store,
                                                terms_file):
    """Keyword-only параметр: прежние вызовы (без aircraft_type) работают."""
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    lex = search(store, Q_MB, None, 50)
    vec = vector_search(store, Q_MB, 50)
    fused = fuse_hybrid(lex, vec, limit=10, store=store, question=Q_MB)
    assert (DOC_RTE, PAGE64) in _keys(fused)


# --- веса каналов --------------------------------------------------------------


def _capture_weights(monkeypatch):
    """Spy на rrf_fuse: возвращает список captured для ranking_weights."""
    import pdfgraph.rag_fusion as rf

    captured: dict = {}
    original = rf.rrf_fuse

    def spy(rankings, **kwargs):
        captured["weights"] = kwargs.get("ranking_weights")
        return original(rankings, **kwargs)

    monkeypatch.setattr(rf, "rrf_fuse", spy)
    return captured


def test_weights_norms_only_unchanged(monkeypatch, store):
    """Требование владельца Ф1.32: нормы включены, алиасы выключены —
    веса те же, что до правки: [1.0, 1.0, NORMS_WEIGHT]."""
    monkeypatch.delenv("PDFGRAPH_TERMS", raising=False)
    monkeypatch.setenv("PDFGRAPH_NORMS", "1")
    captured = _capture_weights(monkeypatch)
    _run_fuse(store, Q_MB)
    assert captured["weights"] == [1.0, 1.0, 2.0]


def test_weights_with_terms_channel(monkeypatch, store, terms_file):
    """Алиасы включены: канал получает TERMS_WEIGHT = 0.7, веса остальных
    каналов не меняются."""
    monkeypatch.delenv("PDFGRAPH_NORMS", raising=False)
    monkeypatch.setenv("PDFGRAPH_TERMS", "1")
    captured = _capture_weights(monkeypatch)
    _run_fuse(store, Q_MB)
    assert captured["weights"] == [1.0, 1.0, 0.7]
