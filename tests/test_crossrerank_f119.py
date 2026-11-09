"""Тесты кросс-переранжировщика (карточка Ф1.19, этап Б).

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

- ``pdfgraph.crossrerank.rerank_pairs(question, passages)`` — логиты
  релевантности пар «вопрос × фрагмент», батчами по :data:`BATCH_SIZE`,
  обрезка входа до :data:`MAX_LENGTH` (512) токенов; модель недоступна —
  ``None`` (не исключение), вызывающая сторона сохраняет прежний порядок.
- ``pdfgraph.crossrerank.crossrerank_enabled()`` — env
  ``PDFGRAPH_CROSSRERANK``; по умолчанию ВЫКЛЮЧЕН, мусор — выключено.
- ``fuse_hybrid`` (Ф1.19): флаг выключен — порядок байт-в-байт прежний;
  флаг включён — топ-``TOP_N`` слитого списка пересортирован по логиту
  (устойчиво), хвост не тронут; модели нет — тихий откат.
- Реальная ONNX-модель в тестах не используется: подставные токенизатор
  и сессия через monkeypatch ``_ensure_runtime``.
"""

from __future__ import annotations

import numpy as np
import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

FX_DOC = "РТЭ кн.1.pdf"
FX_AIRCRAFT = "Ми-8МТВ"

#: Словоформы точные (стемминга в индексе нет). Длины в словах различны:
#: 14 / 6 / 4 — подставная сессия отдаёт логит = число токенов, поэтому
#: ожидаемый порядок по логитам — 20, 10, 30.
FX_TEXTS = {
    10: "давление масла в двигателе проверяется визуально",
    20: ("давление масла в двигателе 3,5 кгс/см2 при запуске "
         "и на режиме малого газа прогрева"),
    30: "масла в системе циркуляция",
}


def _build_store(tmp_path, texts=None) -> Store:
    """Store с листами (через pages/pages_fts, без графа)."""
    store = Store(str(tmp_path / "graph.db"))
    for page, text in (texts or FX_TEXTS).items():
        store.upsert_page(FX_DOC, page, FX_AIRCRAFT, "", "", text,
                          normalize_for_search(text))
    return store


@pytest.fixture
def rerank_store(tmp_path):
    store = _build_store(tmp_path)
    yield store
    store.close()


def _frag(page: int, score: float, kind: str = "text") -> Fragment:
    """Фрагмент реального листа фикс-магазина; сниппет — срез текста."""
    text = FX_TEXTS[page]
    return Fragment(
        document=FX_DOC, page=page, system_code="", subsystem_code="",
        snippet=text, char_start=0, char_end=len(text), score=score,
        kind=kind,
    )


# --- подставной рантайм ------------------------------------------------------


class _FakeEncoding:
    """ids = последовательность заданной длины; маска единичная."""

    def __init__(self, n_ids: int) -> None:
        self.ids = list(range(1, n_ids + 1))
        self.attention_mask = [1] * len(self.ids)

    def pad_to(self, total: int, pad_id: int) -> None:
        """Дотянуть ряд до ``total`` токенов: замаскированный паддинг."""
        extra = total - len(self.ids)
        if extra > 0:
            self.ids += [pad_id] * extra
            self.attention_mask += [0] * extra


class _FakeTokenizer:
    """Токенизатор: id-длина пары = число слов фрагмента, капнутые до
    ``max_length``; батч выравнивается паддингом (как настоящий с
    ``enable_padding``)."""

    def __init__(self, max_length: int, pad_id: int = 0) -> None:
        self.max_length = max_length
        self.pad_id = pad_id
        self.batches: list[list] = []

    def encode_batch_fast(self, pairs):
        self.batches.append(list(pairs))
        encodings = [
            _FakeEncoding(min(len(passage.split()), self.max_length))
            for _question, passage in pairs
        ]
        longest = max((len(e.ids) for e in encodings), default=0)
        for encoding in encodings:
            encoding.pad_to(longest, self.pad_id)
        return encodings


class _FakeSession:
    """Сессия: логит пары = сумма attention_mask (число реальных токенов);
    прогон пишется."""

    def __init__(self) -> None:
        self.runs: list[dict] = []

    def run(self, output_names, feeds):
        self.runs.append({name: np.asarray(value)
                          for name, value in feeds.items()})
        masks = np.asarray(feeds["attention_mask"], dtype=np.float32)
        return [masks.sum(axis=1, keepdims=True)]


def _install_fake_runtime(monkeypatch, max_length: int):
    """Подставной рантайм вместо ``_ensure_runtime``; возвращает пару."""
    import pdfgraph.crossrerank as cr

    tokenizer = _FakeTokenizer(max_length)
    session = _FakeSession()
    monkeypatch.setattr(cr, "_ensure_runtime",
                        lambda: (tokenizer, session))
    return tokenizer, session


# --- выключатель -------------------------------------------------------------


def test_enabled_default_off(monkeypatch):
    """Карточка Ф1.19: по умолчанию выключен."""
    import pdfgraph.crossrerank as cr

    monkeypatch.delenv(cr.CROSSRERANK_ENV, raising=False)
    assert cr.crossrerank_enabled() is False


def test_enabled_env_on(monkeypatch):
    """«1» включает; мусор — выключено (безопасный дефолт)."""
    import pdfgraph.crossrerank as cr

    monkeypatch.setenv(cr.CROSSRERANK_ENV, "1")
    assert cr.crossrerank_enabled() is True
    monkeypatch.setenv(cr.CROSSRERANK_ENV, "мусор")
    assert cr.crossrerank_enabled() is False


# --- rerank_pairs ------------------------------------------------------------


def test_rerank_pairs_logits_in_input_order(monkeypatch):
    """Логиты — по числу пар, в порядке входа; ни одна пара не потеряна."""
    import pdfgraph.crossrerank as cr

    _install_fake_runtime(monkeypatch, cr.MAX_LENGTH)
    passages = [" ".join(["слово"] * n) for n in range(1, 21)]
    logits = cr.rerank_pairs("вопрос", passages)
    assert logits is not None
    assert len(logits) == len(passages)
    assert logits == [float(n) for n in range(1, 21)]


def test_rerank_pairs_batches_of_eight(monkeypatch):
    """Батчи по 8 (8/8/4 на 20 парах); в сессию уходят все пары."""
    import pdfgraph.crossrerank as cr

    tokenizer, session = _install_fake_runtime(monkeypatch, cr.MAX_LENGTH)
    passages = [" ".join(["слово"] * n) for n in range(1, 21)]
    cr.rerank_pairs("вопрос", passages)
    assert [len(batch) for batch in tokenizer.batches] == [8, 8, 4]
    assert [len(runs["input_ids"]) for runs in session.runs] == [8, 8, 4]
    total_rows = sum(len(runs["input_ids"]) for runs in session.runs)
    assert total_rows == 20
    # ONNX-тензор прямоугольный: все ряды батча одной длины (паддинг).
    for runs in session.runs:
        row_lengths = {len(row) for row in runs["input_ids"]}
        assert len(row_lengths) == 1
        width = row_lengths.pop()
        assert all(len(mask) == width for mask in runs["attention_mask"])


def test_rerank_pairs_truncates_to_max_length(monkeypatch):
    """Длинный фрагмент обрезан до MAX_LENGTH (512) токенов."""
    import pdfgraph.crossrerank as cr

    _install_fake_runtime(monkeypatch, cr.MAX_LENGTH)
    long_passage = " ".join(["слово"] * 3000)
    logits = cr.rerank_pairs("вопрос", [long_passage])
    assert logits == [float(cr.MAX_LENGTH)]


def test_rerank_pairs_none_when_model_missing(monkeypatch, tmp_path):
    """Модели нет: ``None`` и запись в лог, не исключение."""
    import pdfgraph.crossrerank as cr

    monkeypatch.setattr(cr, "_RUNTIME", None)
    monkeypatch.setattr(cr, "_RUNTIME_FAILED", False)
    monkeypatch.setattr(cr, "_candidate_dirs",
                        lambda: [tmp_path / "empty"])
    assert cr.rerank_pairs("вопрос", ["фрагмент"]) is None


def test_rerank_pairs_empty_passages(monkeypatch):
    """Пустой список пар — пустой список логитов, сессия не зовётся."""
    import pdfgraph.crossrerank as cr

    _tokenizer, session = _install_fake_runtime(monkeypatch, cr.MAX_LENGTH)
    assert cr.rerank_pairs("вопрос", []) == []
    assert session.runs == []


# --- подключение в fuse_hybrid ----------------------------------------------

_RRF_KWARGS_NOTE = "слияние Ф1.1: k/буст из effective_*; Ф1.16 не включён"


def test_flag_off_order_unchanged(rerank_store, monkeypatch):
    """Флаг выключен: fuse_hybrid порядок байт-в-байт прежний."""
    import pdfgraph.rag_fusion as rag_fusion
    import pdfgraph.crossrerank as cr

    monkeypatch.delenv(cr.CROSSRERANK_ENV, raising=False)
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8), _frag(20, 0.7)]
    expected = rag_fusion.rrf_fuse(
        [lexical, []], k=rag_fusion.effective_fusion_k(),
        boost=rag_fusion.effective_exact_boost(),
        boost_kinds=rag_fusion.EXACT_KINDS)[:10]
    ordered = rag_fusion.fuse_hybrid(lexical, [], limit=10,
                                     store=rerank_store,
                                     question=question)
    assert [fragment.page for fragment in ordered] == [30, 10, 20]
    assert ordered == expected


def test_flag_on_reranks_head_by_logits(rerank_store, monkeypatch):
    """Флаг включён: топ-TOP_N пересортирован по логиту (длина текста)."""
    import pdfgraph.rag_fusion as rag_fusion
    import pdfgraph.crossrerank as cr

    monkeypatch.setenv(cr.CROSSRERANK_ENV, "1")
    _install_fake_runtime(monkeypatch, cr.MAX_LENGTH)
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8), _frag(20, 0.7)]
    ordered = rag_fusion.fuse_hybrid(lexical, [], limit=10,
                                     store=rerank_store,
                                     question=question)
    # Слитый порядок [30, 10, 20]; по логитам (число слов 4/6/14) —
    # [20, 10, 30]. Скор и сниппеты не перезаписываются.
    assert [fragment.page for fragment in ordered] == [20, 10, 30]
    source_scores = {fragment.page: fragment.score for fragment in lexical}
    assert all(fragment.score == source_scores[fragment.page]
               for fragment in ordered)


def test_flag_on_missing_model_quiet_fallback(rerank_store, monkeypatch,
                                              tmp_path):
    """Флаг включён, модели нет: тихий откат — порядок прежний."""
    import pdfgraph.rag_fusion as rag_fusion
    import pdfgraph.crossrerank as cr

    monkeypatch.setattr(cr, "_RUNTIME", None)
    monkeypatch.setattr(cr, "_RUNTIME_FAILED", False)
    monkeypatch.setattr(cr, "_candidate_dirs",
                        lambda: [tmp_path / "empty"])
    monkeypatch.setenv(cr.CROSSRERANK_ENV, "1")
    question = "давление масла в двигателе"
    lexical = [_frag(30, 0.9), _frag(10, 0.8), _frag(20, 0.7)]
    expected = rag_fusion.rrf_fuse(
        [lexical, []], k=rag_fusion.effective_fusion_k(),
        boost=rag_fusion.effective_exact_boost(),
        boost_kinds=rag_fusion.EXACT_KINDS)[:10]
    ordered = rag_fusion.fuse_hybrid(lexical, [], limit=10,
                                     store=rerank_store,
                                     question=question)
    assert ordered == expected


def test_head_top_n_only_tail_keeps_slots(rerank_store, monkeypatch):
    """Хвост за TOP_N не переранжируется: свой порядок слитого списка."""
    import pdfgraph.rag_fusion as rag_fusion
    import pdfgraph.crossrerank as cr

    monkeypatch.setenv(cr.CROSSRERANK_ENV, "1")
    _install_fake_runtime(monkeypatch, cr.MAX_LENGTH)
    # 25 листов, число слов = (page * 7) % 23 + 1 — порядок по логитам
    # не совпадает со слитым порядком.
    texts = {
        page: " ".join(["слово"] * ((page * 7) % 23 + 1))
        for page in range(1, 26)
    }
    store = _build_store_alt(texts)
    try:
        lexical = [
            Fragment(
                document=FX_DOC, page=page, system_code="",
                subsystem_code="", snippet=texts[page], char_start=0,
                char_end=len(texts[page]), score=1.0 - page / 100.0,
                kind="text",
            )
            for page in range(1, 26)
        ]
        ordered = rag_fusion.fuse_hybrid(lexical, [], limit=25,
                                         store=store,
                                         question="давление масла")
        pages = [fragment.page for fragment in ordered]
        head = pages[:cr.TOP_N]
        tail = pages[cr.TOP_N:]
        # Хвост — слоты 21..25 слитого списка без изменений.
        assert tail == [21, 22, 23, 24, 25]
        # Голова — топ-20 по числу слов, устойчиво к равным логитам.
        expected_head = sorted(range(1, 21),
                               key=lambda page: -((page * 7) % 23 + 1))
        assert head == expected_head
    finally:
        store.close()


def _build_store_alt(texts: dict[int, str]) -> Store:
    """Второй магазин (временный каталог pytest не нужен — в темпе ОС)."""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="f119_store_"))
    store = Store(str(tmp / "graph.db"))
    for page, text in texts.items():
        store.upsert_page(FX_DOC, page, FX_AIRCRAFT, "", "", text,
                          normalize_for_search(text))
    return store
