# -*- coding: utf-8 -*-
"""Ф1.0: локальный офлайн векторный канал (e5-small, ONNX Runtime int8).

Владелец, 30.10.2026, дополнения к задаче:
1. Чанк не пересекает границу листа: резать можно только текст одного листа,
   ``page_from``/``page_to`` всегда равны номеру листа.
2. Лист, начинающийся маркером «Продолжение табл.» (или продолжающий таблицу
   предыдущего листа), помечается ``pages.is_continuation = 1``.
3. Ответ по листу-продолжению расширяется предыдущим листом (kind
   ``continuation``, дословный фрагмент), в источнике — диапазон листов.
4. Структурные метаданные (ATA и т.п.) в векторный канал не добавляются.

Модель multilingual-e5-small (int8, ~118 МБ) скачивается единожды скриптом
``scripts\\fetch_models.ps1``; кодировщик работает офлайн, без выхода в сеть.
"""
import dataclasses
import os
import re
from pathlib import Path

from pdfgraph.textclean import clean_search_text, clean_search_text_mapped

import numpy as np

#: URL модели — единственный источник, задаётся и в скрипте выгрузки.
_E5_MODEL_NAME = "multilingual-e5-small"
_E5_ONNX_FILE = "model.onnx"
_E5_QUANTIZED_FILE = "model_quantized.onnx"
_E5_TOKENIZER_FILE = "tokenizer.json"
#: Константы конвейера.
CHUNK_SIZE = 600
CHUNK_OVERLAP = 120
#: Псевдоним для контракта нарезки Ф1.0 (равен CHUNK_OVERLAP).
OVERLAP = CHUNK_OVERLAP
BATCH_SIZE = 64
ONNX_THREADS = 4
#: Ограничение длины входа токенизатора.
MAX_TOKENS = 256

DEFAULT_MODELS_ENV = "PDFGRAPH_MODELS_DIR"
DEFAULT_MODELS_DIR = r"C:\PDF_Data\models"
DEFAULT_EMB_MODEL = "e5-multilingual-small-int8"
#: Диапазон оценок semantic-фрагментов: (0.99, 1.0] — как у kind="table".
SEMANTIC_BASE_HIGH = 1.0
SEMANTIC_BASE_LOW = 0.9901
#: Ниже порога лист не выделяем (шумовая близость).
DEFAULT_MIN_COSINE = 0.18


def default_models_dir() -> Path:
    """Каталог моделей: PDFGRAPH_MODELS_DIR, иначе C:\\PDF_Data\\models."""
    return Path(os.environ.get(DEFAULT_MODELS_ENV, DEFAULT_MODELS_DIR))


def model_file_paths(model_dir=None):
    """Пути (onnx, tokenizer); FileNotFoundError — модель не скачана.

    Предпочитается int8-файл ``model_quantized.onnx`` — ровно его выкладывает
    ``scripts\\fetch_models.ps1``; при отсутствии берётся полный ``model.onnx``.
    """
    base = Path(model_dir) if model_dir is not None else default_models_dir()
    subdir = base if base.name == _E5_MODEL_NAME else base / _E5_MODEL_NAME
    onnx = subdir / _E5_QUANTIZED_FILE
    if not onnx.is_file():
        onnx = subdir / _E5_ONNX_FILE
    tokenizer = subdir / _E5_TOKENIZER_FILE
    if not onnx.is_file():
        raise FileNotFoundError(
            f"модель e5 не найдена: {onnx}. Выполните "
            "powershell -ExecutionPolicy Bypass -File "
            "scripts\\fetch_models.ps1")
    return onnx, tokenizer


_SESSION_CACHE: dict[str, object] = {}


def _load_tokenizer(tokenizer_path):
    """Токенизатор из локального json — сети недоступно by design."""
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.no_truncation()
    tok.enable_truncation(max_length=MAX_TOKENS)
    return tok


def _load_session(onnx_path):
    """ONNX-сессия с фиксированным числом CPU-потоков; кэш по пути."""
    import onnxruntime as ort

    key = str(Path(onnx_path).resolve())
    session = _SESSION_CACHE.get(key)
    if session is None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = ONNX_THREADS
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(
            key, sess_options=options, providers=["CPUExecutionProvider"])
        _SESSION_CACHE[key] = session
    return session


def _mean_pool(logits, mask):
    """Mean-pooling по attention-маске (как в README модели e5)."""
    hidden = np.asarray(logits, dtype=np.float32)
    if hidden.ndim == 3:  # last_hidden_state [batch, seq, hidden]
        maskf = mask.astype(np.float32)[..., None]
        summed = (hidden * maskf).sum(axis=1)
        counts = np.clip(maskf.sum(axis=1), 1e-9, None)
        pooled = summed / counts
    else:  # модель уже отдаёт готовый пул [batch, hidden]
        pooled = hidden
    norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return pooled / norms


def encode_texts(texts, model_dir=None):
    """Кодировать тексты (prefix passage:) → float32 [n, dim], L2-норма."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    onnx, tokenizer = model_file_paths(model_dir)
    tok = _load_tokenizer(tokenizer)
    session = _load_session(onnx)
    parts: list[np.ndarray] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = [f"passage: {chunk}"
                 for chunk in texts[start:start + BATCH_SIZE]]
        encodings = tok.encode_batch(batch)
        # Выравнивание батча: длины токен-последовательностей внутри батча
        # различаются (короткий лист, обрыв чанка на границе) — прямое
        # np.asarray даёт ragged-массив и ValueError на рабочей базе
        # (баг приёмки Ф1.0, воспроизведён 30.10.2026). Пад-позиции получают
        # attention_mask=0 и выпадают и из внимания модели, и из mean-pooling,
        # поэтому вектор реальной позиции совпадает с одиночным прогоном.
        width = max(len(e.ids) for e in encodings)
        n_batch = len(encodings)
        input_ids = np.zeros((n_batch, width), dtype=np.int64)
        mask = np.zeros((n_batch, width), dtype=np.int64)
        for i, enc in enumerate(encodings):
            n_i = len(enc.ids)
            input_ids[i, :n_i] = enc.ids
            mask[i, :n_i] = enc.attention_mask
        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": mask,
        }
        if "token_type_ids" in {i.name for i in session.get_inputs()}:
            feed["token_type_ids"] = np.zeros_like(mask)
        logits = session.run(None, feed)[0]
        parts.append(_mean_pool(logits, mask))
    return np.concatenate(parts, axis=0)


def query_vector(question, model_dir=None):
    """Вектор вопроса (prefix query:), L2-нормализованный."""
    onnx, tokenizer = model_file_paths(model_dir)
    tok = _load_tokenizer(tokenizer)
    session = _load_session(onnx)
    encoding = tok.encode(f"query: {question}")
    input_ids = np.asarray([encoding.ids], dtype=np.int64)
    mask = np.asarray([encoding.attention_mask], dtype=np.int64)
    feed = {
        "input_ids": input_ids,
        "attention_mask": mask,
    }
    if "token_type_ids" in {i.name for i in session.get_inputs()}:
        feed["token_type_ids"] = np.zeros_like(mask)
    logits = session.run(None, feed)[0]
    return _mean_pool(logits, mask)[0]


def chunk_spans(text: str, chunk_size: int = CHUNK_SIZE,
                overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """Границы чанков ``[(start, end), …]`` внутри stripped-текста.

    Граница листа для чанка непроходима: чанк из двух листов запрещён
    (дополнение владельца 30.10.2026, п. 1). Нарезка детерминирована и
    совместима с :func:`chunk_span` (тот ищет в том же stripped-тексте).
    """
    stripped = text.strip()
    total = len(stripped)
    if total == 0:
        return []
    if total <= chunk_size:
        return [(0, total)]
    step = max(1, chunk_size - overlap)
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + chunk_size, total)
        spans.append((start, end))
        if end >= total:
            return spans
        start += step


def chunk_page(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Порезать текст одного листа на чанки-строки с перекрытием.

    Исторический контракт (tests/test_semantic_f10.py): возвращаются сами
    чанки-строки; пустой лист кусков не даёт. Граница листа для чанка
    непроходима: чанк из двух листов запрещён (дополнение владельца, п. 1).
    """
    stripped = text.strip()
    return [stripped[start:end]
            for start, end in chunk_spans(stripped, chunk_size, overlap)]


# ---------------------------------------------------------------------------
# Детектор листов-продолжений (дополнение владельца, п. 2)
_MARKERS = (
    "продолжение табл",
    "продолжение таблицы",
    "продолж. табл",
)
_HEADER_PREFIX_RE = re.compile(r"^\s*(?:табл\.?|таблица)\s*:?\s*\d+",
                               re.IGNORECASE)


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0] if text.strip() else ""


def _repeated_header(text: str, prev_text: str | None) -> bool:
    """Заголовок таблицы повторён (та же «Таблица N» или префикс)."""
    first = _first_line(text)
    if not _HEADER_PREFIX_RE.match(first):
        return False
    prev_first = _first_line(prev_text)
    if _HEADER_PREFIX_RE.match(prev_first):
        return True
    return False  # один заголовок без повторения — не продолжение табл.


def _table_context(prev_text: str) -> bool:
    """Предыдущий лист открывал таблицу: «Таблица N» или «Продолжение табл.»."""
    prev_first = _first_line(prev_text)
    if _HEADER_PREFIX_RE.match(prev_first):
        return True
    return any(prev_first.lower().startswith(marker)
               for marker in _MARKERS)


def _row_like_start(text: str) -> bool:
    """Первая строка начинается со строчной буквы — строка данных таблицы."""
    first = _first_line(text)
    return bool(first) and first[0].islower()


def detect_continuation(text: str | None, prev_text: str | None) -> bool:
    """True — лист похоже продолжает таблицу/фразу предыдущего листа."""
    if not text:
        return False
    head = text.strip()[:80].lower()
    if any(head.startswith(marker) for marker in _MARKERS):
        return True
    if prev_text and _repeated_header(text, prev_text):
        return True
    # Без маркера: предыдущий лист открывал таблицу, а текущий начинается
    # со строчной буквы — продолжение строк данных той же таблицы (Ф1.0).
    if prev_text and _table_context(prev_text) and _row_like_start(text):
        return True
    return False


def write_continuation_flags(store) -> int:
    """Пересчитать и проставить ``pages.is_continuation``; идемпотентно.

    Возвращает число листов, у которых флаг фактически изменился (повторный
    прогон по тем же данным — 0). Пустой лист разрывает цепочку: пустота —
    граница раздела, а не продолжение таблицы.
    """
    wanted: set[tuple[str, int]] = set()
    previous: tuple[str, str] | None = None  # (document, text) с текстом
    for document, page in store.searchable_pages():
        text = store.page_text(document, page)
        if not text.strip():
            previous = None
            continue
        prev_text = previous[1] if (previous and previous[0] == document) else None
        if detect_continuation(text, prev_text):
            wanted.add((document, page))
        previous = (document, text)
    changed = 0
    for document, page in store.searchable_pages():
        desired = (document, page) in wanted
        if store.is_continuation(document, page) != desired:
            store.set_page_continuation(document, page, desired)
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Построение постраничного индекса (Ф1.0) и кэш матрицы для поиска


def build_page_embeddings(store, model_dir=None, *, chunk_size: int = CHUNK_SIZE,
                          overlap: int = CHUNK_OVERLAP,
                          rebuild: bool = False) -> dict:
    """Построить постраничные вектора (идемпотентно).

    Возвращает отчёт ``{chunks, embedded, bumped, version}``: ``embedded`` —
    листы, реально закодированные в этом прогоне; ``bumped`` — выросла ли
    метка версии (сброс кэша матрицы у читателей). Модель не найдена →
    FileNotFoundError (emb-index выходит с кодом 2).
    """
    model_file_paths(model_dir)  # FileNotFoundError при отсутствии модели
    write_continuation_flags(store)  # Ф1.0: маркеры «Продолжение табл.»
    model_name = DEFAULT_EMB_MODEL
    # Ф1.17: чанки строятся из ОЧИЩЕННОГО текста (clean_search_text,
    # правила Ф1.11); маркер конфигурации с суффиксом «:clean» — после
    # смены маркера любой запуск emb-index БЕЗ --rebuild потребует полного
    # перерасчёта (same_config=False → clear + пересчёт всех листов),
    # старые и новые векторы не смешиваются.
    params = f"{chunk_size}:{overlap}:clean"

    version = int(store.get_meta("emb_version", "0") or "0")
    same_config = (store.get_meta("emb_model", "") == model_name
                   and store.get_meta("emb_chunk", "") == params)
    if not same_config or rebuild:
        store.clear_embeddings()
        version += 1
        store.set_meta("emb_version", str(version))
        bumped = True
        todo = store.searchable_pages()
    else:
        done = store.embedded_pages()
        todo = [(document, page) for document, page in store.searchable_pages()
                if (document, page) not in done]
        bumped = False

    embedded = 0
    chunk_total = 0
    dim_written: int | None = None
    for document, page in todo:
        text = store.page_text(document, page)
        # Ф1.17: чанк-спаны — на очищенном тексте (правила Ф1.11);
        # дословные сниппеты читатель восстанавливает через
        # :func:`chunk_span_raw` (карта позиций → срез исходника).
        cleaned = clean_search_text(text)
        spans = chunk_spans(cleaned, chunk_size, overlap)
        if not spans:
            continue
        chunks = [cleaned[start:end] for start, end in spans]
        vectors = encode_texts(chunks, model_dir=model_dir)
        vectors = np.atleast_2d(np.asarray(vectors, dtype=np.float32))
        blobs = [row.astype("<f4", copy=False).tobytes() for row in vectors]
        dim_written = int(vectors.shape[1]) if vectors.size else 0
        # Чанк не пересекает границу листа: page_from == page_to == page.
        store.put_embeddings(document, page, page, page,
                             store.is_continuation(document, page),
                             model_name, dim_written, blobs)
        embedded += 1
        chunk_total += len(chunks)
    if dim_written is not None:
        store.set_meta("emb_model", model_name)
        store.set_meta("emb_dim", str(dim_written))
        store.set_meta("emb_chunk", params)
    return {"chunks": chunk_total, "embedded": embedded, "bumped": bumped,
            "version": int(store.get_meta("emb_version", "0") or "0")}


@dataclasses.dataclass(frozen=True)
class _MatrixEntry:
    """Кэш векторной матрицы одного хранилища (по meta.emb_version)."""

    version: int
    dim: int
    rows: tuple[tuple[str, int, int, bool], ...]  # (doc, page, chunk_ix, cont)
    matrix: np.ndarray


_MATRIX_CACHE: dict[tuple[str, int], _MatrixEntry] = {}


def _db_key(store) -> str:
    """Стабильный ключ кэша: путь файла базы или id соединения."""
    try:
        row = store._connection.execute(
            "PRAGMA database_list").fetchone()
    except Exception:  # noqa: BLE001 — соединение закрыто, кэш не критичен
        return f"mem:{id(store)}"
    if row is None or not row[2]:
        return f"mem:{id(store)}"
    return str(row[2])


def search_matrix(store) -> _MatrixEntry | None:
    """Матрица L2-нормализованных чанка-векторов; None — индекс пуст."""
    rows = store.embedding_rows()
    if not rows:
        return None
    version = int(store.get_meta("emb_version", "0") or "0")
    cache_key = (_db_key(store), version)
    cached = _MATRIX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    matrix = np.asarray(
        [np.frombuffer(bytes(chunk.vec), dtype="<f4") for chunk in rows],
        dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(rows):
        return None
    norms = np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)
    entry = _MatrixEntry(
        version=version,
        dim=int(matrix.shape[1]),
        rows=tuple((chunk.document, chunk.page, chunk.chunk_ix,
                    bool(chunk.is_continuation)) for chunk in rows),
        matrix=matrix / norms,
    )
    _MATRIX_CACHE[cache_key] = entry
    return entry


def chunk_span(text: str, chunk_ix: int, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> tuple[int, int]:
    """Границы чанка ``chunk_ix`` в тексте листа (детерминизм chunk_page)."""
    stripped = text.strip()
    if not stripped or len(stripped) <= chunk_size:
        return 0, len(stripped)
    start = min(chunk_ix * max(1, chunk_size - overlap), len(stripped) - 1)
    return start, min(start + chunk_size, len(stripped))


def chunk_span_raw(text: str, chunk_ix: int, chunk_size: int = CHUNK_SIZE,
                   overlap: int = CHUNK_OVERLAP) -> tuple[int, int]:
    """Границы чанка ``chunk_ix`` в координатах ИСХОДНОГО текста (Ф1.17).

    Чанки кодируются из очищенного текста (:func:`clean_search_text`,
    правила Ф1.11); читатель восстанавливает границы чанка на очищенном
    тексте и через карту позиций
    (:func:`pdfgraph.textclean.clean_search_text_mapped`) переводит их в
    срез ИСХОДНОГО текста — сниппет остаётся дословным, verify не ломается.
    Чистка детерминирована, поэтому пересчёт на чтении даёт те же границы,
    что были при построении. Нарезка совместима с :func:`chunk_spans`
    (та же функция).
    """
    cleaned, spans = clean_search_text_mapped(text)
    spans_all = chunk_spans(cleaned, chunk_size, overlap)
    if chunk_ix < 0 or chunk_ix >= len(spans_all):
        raise IndexError(
            f"чанк {chunk_ix} вне диапазона 0..{len(spans_all) - 1}")
    start, end = spans_all[chunk_ix]
    return spans[start][0], spans[end - 1][1]


#: Переменная окружения, задающая порог косинуса (читается на каждый вызов).
MIN_COSINE_ENV = "PDFGRAPH_MIN_COSINE"


def effective_min_cosine(min_cosine: float | None = None) -> float:
    """Порог векторного гейта: аргумент старше env, env старше дефолта.

    ``"0"`` в env — настоящий ноль (отсечение выключено); мусор и
    отрицательное значение считаются дефолтом, чтобы опечатка не отключала
    гейт совсем.
    """
    if min_cosine is not None:
        return float(min_cosine)
    raw = os.environ.get(MIN_COSINE_ENV)
    if raw is None:
        return DEFAULT_MIN_COSINE
    try:
        value = float(raw.strip())
    except ValueError:
        return DEFAULT_MIN_COSINE
    if value < 0.0:
        return DEFAULT_MIN_COSINE
    return value


def search_chunks(store, question, model_dir=None,
                  limit: int = 10,
                  min_cosine: float | None = None
                  ) -> list[tuple[tuple[str, int, int], float]]:
    """Косинусный поиск по матрице чанков; [(ключ чанка, косинус), …].

    Порог: аргумент ``min_cosine`` (если задан), иначе env
    ``MIN_COSINE_ENV``, иначе ``DEFAULT_MIN_COSINE``. Модель не найдена —
    канал деградирует в ``[]``, лексические ветки продолжают работать.
    """
    entry = search_matrix(store)
    if entry is None:
        return []
    threshold = effective_min_cosine(min_cosine)
    try:
        vector = query_vector(question, model_dir=model_dir)
    except FileNotFoundError:
        return []
    if vector.size != entry.dim:
        return []
    units = np.linalg.norm(vector)
    if units <= 0:
        return []
    sims: np.ndarray = entry.matrix @ (np.asarray(vector, dtype=np.float32) / units)
    order = np.argsort(-sims)
    hits: list[tuple[tuple[str, int, int], float]] = []
    for index in order:
        cosine = float(sims[index])
        if cosine < threshold or len(hits) >= limit:
            break
        document, page, chunk_ix, _continuation = entry.rows[int(index)]
        hits.append(((document, page, chunk_ix), cosine))
    return hits