# -*- coding: utf-8 -*-
"""Кросс-переранжировщик (карточка Ф1.19, этап Б) — bge-reranker-base, ONNX.

Лексический поиск и e5 не различают «рабочее давление» и «установившееся
давление» (лист 38 находится только при слове «прогреве» в вопросе).
Кросс-энкодер читает вопрос и фрагмент ВМЕСТЕ и выносит суждение о смысле —
единственный способ обойти эту стену. Модель: ``bge-reranker-base``,
ONNX-файл ``onnx/model.onnx``, токенизатор ``tokenizer.json``; входы
``input_ids``/``attention_mask``, выход — логит релевантности пары.

Границы (карточка): переранжируется ТОЛЬКО топ-:data:`TOP_N` уже слитого
списка (:func:`pdfgraph.rag_fusion.fuse_hybrid` вызывает после этапа А Ф1.16
и перед отсечением ``[:limit]``); ``Fragment.score`` и сниппеты не
перезаписываются; гейты, verify, answer, humanize, веса RRF не трогаются.

Выключатель: ``PDFGRAPH_CROSSRERANK`` — по умолчанию ВЫКЛЮЧЕН (карточка);
стиль :func:`pdfgraph.rerank.effective_rerank_enabled` — чтение при каждом
вызове, мусорное значение не включает.

Отсутствие модели — не падение: тихий возврат к прежнему порядку с записью
в лог (:func:`rerank_pairs` возвращает ``None``). torch не используется —
только ``tokenizers`` и ``onnxruntime`` (как в :mod:`pdfgraph.embedding`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

#: Выключатель; читается при каждом вызове, без кэша.
CROSSRERANK_ENV = "PDFGRAPH_CROSSRERANK"
_ON_VALUES = {"1", "true", "yes", "on"}

#: Каталог моделей: env :data:`_MODELS_DIR_ENV`, иначе дефолт, иначе
#: локальный fallback относительно рабочей папки (карточка Ф1.19).
_MODELS_DIR_ENV = "PDFGRAPH_MODELS_DIR"
_DEFAULT_MODELS_DIR = r"C:\PDF_Data\models"
_LOCAL_FALLBACK_DIR = "./models"
#: Подкаталог и файлы модели внутри каталога моделей.
_MODEL_SUBDIR = "bge-reranker-base"
_ONNX_RELATIVE = Path("onnx") / "model.onnx"
_TOKENIZER_FILENAME = "tokenizer.json"

#: Константы конвейера (карточка): батч 8, вход до 512 токенов,
#: переранжируется топ-20 слитого списка.
BATCH_SIZE = 8
MAX_LENGTH = 512
TOP_N = 20
#: Потоки ONNX Runtime — как в :mod:`pdfgraph.embedding`.
ONNX_THREADS = 4

#: Ленивый рантайм: (токенизатор, сессия) либо None; флаг необратимого
#: провала загрузки — повторные попытки не делаются, лог не спамится.
_RUNTIME: tuple[object, object] | None = None
_RUNTIME_FAILED = False

logger = logging.getLogger(__name__)


def crossrerank_enabled() -> bool:
    """Выключатель Ф1.19: ``PDFGRAPH_CROSSRERANK``; по умолчанию выключен.

    «1»/«true»/«yes»/«on» (без учёта регистра) — включён; пусто, «0»
    и любой мусор — выключено (безопасный дефолт: прежнее поведение
    байт-в-байт).
    """
    raw = os.environ.get(CROSSRERANK_ENV, "").strip().casefold()
    return raw in _ON_VALUES


def _candidate_dirs() -> list[Path]:
    """Каталоги поиска модели по порядку: env, дефолт, локальный fallback."""
    directories: list[Path] = []
    env = os.environ.get(_MODELS_DIR_ENV, "").strip()
    if env:
        directories.append(Path(env))
    directories.append(Path(_DEFAULT_MODELS_DIR))
    directories.append(Path(_LOCAL_FALLBACK_DIR))
    unique: list[Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return unique


def _model_paths() -> tuple[Path, Path]:
    """(onnx, tokenizer) первого каталога, где есть оба файла.

    Ни один кандидат не содержит модель — ``FileNotFoundError`` со списком
    проверенных каталогов (превращается в тихий откат в
    :func:`_ensure_runtime`, не наружу).
    """
    for directory in _candidate_dirs():
        base = directory / _MODEL_SUBDIR
        onnx_path = base / _ONNX_RELATIVE
        tokenizer_path = base / _TOKENIZER_FILENAME
        if onnx_path.is_file() and tokenizer_path.is_file():
            return onnx_path, tokenizer_path
    checked = "\n".join(
        str(directory / _MODEL_SUBDIR) for directory in _candidate_dirs())
    raise FileNotFoundError(
        f"модель {_MODEL_SUBDIR} не найдена; ожидались каталоги:\n{checked}")


def _ensure_runtime() -> tuple[object, object] | None:
    """Ленивая загрузка (токенизатор, ONNX-сессия); кэш на уровне модуля.

    Любая ошибка загрузки (нет файлов, битый onnx, нет onnxruntime) —
    одно ``logger.warning`` и ``None`` навсегда (в рамках процесса):
    отсутствие модели — тихий возврат к прежнему порядку, не падение.
    """
    global _RUNTIME, _RUNTIME_FAILED
    if _RUNTIME is not None:
        return _RUNTIME
    if _RUNTIME_FAILED:
        return None
    try:
        onnx_path, tokenizer_path = _model_paths()
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        # Обрезка пары (вопрос, фрагмент) до MAX_LENGTH — стратегия
        # longest_first по умолчанию: длинные фрагменты режутся честно.
        tokenizer.no_truncation()
        tokenizer.enable_truncation(max_length=MAX_LENGTH)
        # Паддинг до длины самого длинного ряда батча: ONNX-тензор обязан
        # быть прямоугольным; замаскированные позиции не влияют на логит
        # (attention_mask = 0). pad_id — из словаря модели.
        pad_id = tokenizer.token_to_id("[PAD]")
        if pad_id is None:
            pad_id = tokenizer.token_to_id("<pad>")
        if pad_id is None:
            pad_id = 0
        tokenizer.enable_padding(pad_id=pad_id)
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = ONNX_THREADS
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(
            str(onnx_path), sess_options=options,
            providers=["CPUExecutionProvider"])
    except Exception as exc:
        _RUNTIME_FAILED = True
        logger.warning("crossrerank: модель не загружена, переранжирование "
                       "отключено — прежний порядок (%s)", exc)
        return None
    _RUNTIME = (tokenizer, session)
    return _RUNTIME


def rerank_pairs(question: str, passages: list[str]) -> list[float] | None:
    """Логиты релевантности пар «вопрос × фрагмент» — в порядке входа.

    Батчами по :data:`BATCH_SIZE`, вход пары обрезан токенизатором до
    :data:`MAX_LENGTH` (512) токенов. Ошибка рантайма или загрузки —
    ``None`` с записью в лог (вызывающая сторона сохраняет прежний
    порядок, исключение наружу не выходит). Пустой список пар — пустой
    список логитов, сессия не вызывается.
    """
    if not passages:
        return []
    runtime = _ensure_runtime()
    if runtime is None:
        return None
    tokenizer, session = runtime
    logits: list[float] = []
    try:
        for start in range(0, len(passages), BATCH_SIZE):
            batch = passages[start:start + BATCH_SIZE]
            encodings = tokenizer.encode_batch_fast(
                [(question, passage) for passage in batch])
            feeds = {
                "input_ids": [encoding.ids for encoding in encodings],
                "attention_mask": [encoding.attention_mask
                                   for encoding in encodings],
            }
            outputs = session.run(None, feeds)
            scores = np.asarray(outputs[0], dtype=np.float32).reshape(
                len(batch), -1)[:, 0]
            logits.extend(float(value) for value in scores)
    except Exception as exc:
        logger.warning("crossrerank: прогон пар не удался, прежний порядок "
                       "(%s)", exc)
        return None
    return logits
