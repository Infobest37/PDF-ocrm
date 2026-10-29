"""Золотой набор и автотест метрик — карточка 0.1 (Фаза 0).

Роль файла: загрузчик вопросов, прогон через цепочку CLI ``ask`` без
старта процесса, расчёт метрик, таблица и отчёт ``reports/golden_run.json``.

Формат вопросов — TOML (``tests/golden/questions.toml``), читается
стандартным :mod:`tomllib`; pyyaml не добавляется — формат изменён
с YAML на TOML по согласованию владельца.

Прогон: ``Store`` на КОПИИ ``graph.db`` (рабочая БД не открывается на
запись, правка 4) → один холостой прогрев-запрос вне учёта (правка 3) →
для каждого вопроса ``build_answer(store, question, aircraft_type)`` →
``verify_citations`` → ``all_verified``; замер ``time.perf_counter``
охватывает только поиск + ответ + верификацию цитат.

Метрики — по каждому mode отдельно и по итогу (``summary``), с n
(правка 2), при пустом знаменателе value=None (JSON null, в таблице
«n/a», правка 1):
- ``hit_at_5`` — доля ready-вопросов с ожидаемыми листами, у которых
  хотя бы один ожидаемый лист входит в топ-5 фрагментов;
- ``citation_precision`` — ok-цитаты / все проверенные цитаты
  ready-вопросов;
- ``refusal_recall`` — корректно отклонённые внекорпусные / все
  внекорпусные (готовые вопросы с пустыми ожиданиями);
- ``refusal_false`` — зря отклонённые с известным листом / все вопросы
  с известным листом;
- ``latency_median_ms`` — медиана (build_answer + verify) по
  ready-вопросам; старт процесса и открытие БД не входят.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import pathname2url

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE_PATH = REPO_ROOT / "graph.db"
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "tests" / "golden" / "questions.toml"
DEFAULT_REPORT_PATH = REPO_ROOT / "reports" / "golden_run.json"

try:
    from pdfgraph.answer import Answer, build_answer
    from pdfgraph.store import Store
    from pdfgraph.verify import CitationCheck, all_verified, verify_citations
except ModuleNotFoundError:  # запуск как скрипт вне pytest
    sys.path.insert(0, str(REPO_ROOT))
    from pdfgraph.answer import Answer, build_answer
    from pdfgraph.store import Store
    from pdfgraph.verify import CitationCheck, all_verified, verify_citations

MODES: tuple[str, ...] = ("explain", "fault", "procedure")
STATUSES: tuple[str, ...] = ("todo", "ready")
REQUIRED_FIELDS: tuple[str, ...] = (
    "id", "mode", "status", "question", "aircraft_type", "ata",
    "expected_doc", "expected_pages", "notes",
)
#: Холостой прогрев-запрос перед замером; в метрики не попадает.
WARMUP_QUESTION = "несущий винт"


@dataclass(frozen=True)
class GoldenQuestion:
    """Одна запись золотого набора; все поля обязательны при загрузке."""

    id: str
    mode: str
    status: str
    question: str
    aircraft_type: str
    ata: str
    expected_doc: str
    expected_pages: tuple[int, ...]
    notes: str

    @property
    def in_metrics(self) -> bool:
        """todo-вопросы не считаются в метриках (карточка 0.1)."""
        return self.status == "ready"

    @property
    def expect_answer(self) -> bool:
        """Готовый вопрос с известными листами: должен получить ответ."""
        return self.status == "ready" and bool(self.expected_pages)

    @property
    def expect_refusal(self) -> bool:
        """Готовый вопрос вне корпуса (пустые ожидания): должен отказать."""
        return self.status == "ready" and not self.expected_pages


def load_questions(path: str | Path = DEFAULT_QUESTIONS_PATH
                   ) -> list[GoldenQuestion]:
    """Прочитать набор и проверить схему: поля, статусы, дубли id.

    Готовому вопросу нужны ожидания: непустой ``expected_pages``
    (обычный вопрос) либо пустые ``expected_pages`` + пустой
    ``expected_doc`` (вне корпуса — «должен отказать»).
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"нет файла золотого набора: {src}")
    with open(src, "rb") as handle:
        data = tomllib.load(handle)
    items = data.get("question")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{src}: нет массива записей [[question]]")
    questions: list[GoldenQuestion] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        _validate_item(src, index, item, seen)
        seen[item["id"]] = index
        questions.append(GoldenQuestion(
            id=item["id"], mode=item["mode"], status=item["status"],
            question=item["question"], aircraft_type=item["aircraft_type"],
            ata=item["ata"], expected_doc=item["expected_doc"],
            expected_pages=tuple(item["expected_pages"]),
            notes=item["notes"]))
    return questions


def _validate_item(src: Path, index: int, item: object,
                   seen: dict[str, int]) -> None:
    """Проверить одну запись: обязательные поля, типы, дубли id."""
    if not isinstance(item, dict):
        raise ValueError(f"{src}: запись #{index} не является таблицей")
    missing = [f for f in REQUIRED_FIELDS if f not in item]
    if missing:
        raise ValueError(f"{src}: запись #{index}: нет обязательных полей: "
                         f"{', '.join(missing)}")
    unknown = [f for f in item if f not in REQUIRED_FIELDS]
    if unknown:
        raise ValueError(f"{src}: запись #{index}: неожиданные поля: "
                         f"{', '.join(unknown)}")
    qid = item["id"]
    if not isinstance(qid, str) or not qid.strip():
        raise ValueError(f"{src}: запись #{index}: id — непустая строка")
    if qid in seen:
        raise ValueError(f"{src}: дубль id {qid!r}: записи #{seen[qid]} "
                         f"и #{index}")
    if item["mode"] not in MODES:
        raise ValueError(f"{src}: запись #{index} (id {qid!r}): "
                         f"mode={item['mode']!r}, ожидался один из {MODES}")
    if item["status"] not in STATUSES:
        raise ValueError(f"{src}: запись #{index} (id {qid!r}): "
                         f"status={item['status']!r}, ожидался один из "
                         f"{STATUSES}")
    for field in ("question", "aircraft_type"):
        if not isinstance(item[field], str) or not item[field].strip():
            raise ValueError(f"{src}: запись #{index} (id {qid!r}): поле "
                             f"{field!r} — непустая строка")
    for field in ("ata", "expected_doc", "notes"):
        if not isinstance(item[field], str):
            raise ValueError(f"{src}: запись #{index} (id {qid!r}): поле "
                             f"{field!r} — строка")
    pages = item["expected_pages"]
    if not isinstance(pages, list) or any(
            isinstance(p, bool) or not isinstance(p, int) for p in pages):
        raise ValueError(f"{src}: запись #{index} (id {qid!r}): "
                         "expected_pages — список целых листов")
    if item["status"] == "ready" and not pages and item["expected_doc"]:
        raise ValueError(f"{src}: запись #{index} (id {qid!r}): ready без "
                         "expected_pages — это внекорпусный вопрос; тогда "
                         "и expected_doc должен быть пустым")


def copy_store(source: str | Path, target: str | Path) -> None:
    """Скопировать рабочую БД в темп-каталог, не записывая в неё.

    Основной путь — sqlite ``backup()`` поверх read-only подключения;
    если это невозможно — файловая копия db + ``-wal`` + ``-shm``.
    """
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        uri = f"file:{pathname2url(str(source))}?mode=ro"
        src = sqlite3.connect(uri, uri=True)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
                return
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error:
        pass
    for suffix in ("", "-wal", "-shm"):
        part = source.with_name(source.name + suffix)
        if part.is_file():
            shutil.copy2(part, target.with_name(target.name + suffix))


def run_all(store_path: str | Path, questions: list[GoldenQuestion],
            warmup_question: str = WARMUP_QUESTION) -> tuple[list[dict], dict]:
    """Прогнать вопросы через цепочку ``ask`` на копии БД.

    Один холостой прогрев-запрос выполняется до замера и не попадает
    ни в строки, ни в метрики (правка 3). Возвращает (rows, meta).
    """
    store = Store(str(store_path))
    try:
        stats_before = store.stats()
        build_answer(store, warmup_question, None)  # холостой прогрев
        rows: list[dict] = []
        for question in questions:
            started = time.perf_counter()
            answer = build_answer(store, question.question,
                                  question.aircraft_type)
            checks = verify_citations(answer, store)
            latency_ms = (time.perf_counter() - started) * 1000.0
            rows.append(_row_for(question, answer, checks, latency_ms))
        stats_after = store.stats()
        meta = {
            "store_path": str(store_path),
            "warmup_question": warmup_question,
            "warmup_excluded": True,
            "stats_before": stats_before,
            "stats_after": stats_after,
            "stats_equal": stats_before == stats_after,
            "latency_note": ("в замер входит только поиск + ответ + "
                             "верификация; старт процесса и открытие БД "
                             "не входят"),
        }
        return rows, meta
    finally:
        store.close()


def _row_for(question: GoldenQuestion, answer: Answer,
             checks: list[CitationCheck], latency_ms: float) -> dict:
    """Собрать строку отчёта по одному вопросу.

    Нумерация листов — индексация графа (столбец pages.page): та же,
    какую печатает CLI ``ask`` и записывают в ``docs/tests_manual.md``.
    ``hit`` — только для готовых вопросов с ожидаемыми листами: хотя
    бы один ожидаемый лист с точным именем документа в топ-5. Для
    todo — ``None`` (в метриках не участвуют).
    """
    top5 = [
        {"document": fragment.document, "page": fragment.page,
         "score": round(fragment.score, 4), "kind": fragment.kind}
        for fragment in answer.fragments[:5]
    ]
    citations = [
        {"document": check.document, "page": check.page,
         "ok": bool(check.ok), "reason": check.reason}
        for check in checks
    ]
    refusal = bool(answer.refusal)
    hit = None
    if question.expect_answer:
        hit = any(fragment.document == question.expected_doc
                  and fragment.page in question.expected_pages
                  for fragment in answer.fragments[:5])
    return {
        "id": question.id, "mode": question.mode,
        "status": question.status, "question": question.question,
        "aircraft_type": question.aircraft_type, "ata": question.ata,
        "expected_doc": question.expected_doc,
        "expected_pages": list(question.expected_pages),
        "notes": question.notes,
        "refusal": refusal,
        "refusal_reason": answer.reason if refusal else "",
        "verified": all_verified(checks) and not refusal,
        "latency_ms": round(latency_ms, 2),
        "top5": top5, "citations": citations,
        "hit": hit,
        "correct_refusal": refusal if question.expect_refusal else None,
        "wrong_refusal": refusal if question.expect_answer else None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    """Доля с n; при пустом знаменателе — None («n/a», правка 1)."""
    return numerator / denominator if denominator else None


def _bucket_metrics(subset: list[dict]) -> dict[str, dict]:
    """Метрики по одной группе (mode или весь набор) со счётчиками n."""
    ready = [row for row in subset if row["status"] == "ready"]
    answerable = [row for row in ready if row["expected_pages"]]
    out_of_corpus = [row for row in ready if not row["expected_pages"]]
    ok_citations = sum(1 for row in ready
                       for item in row["citations"] if item["ok"])
    total_citations = sum(len(row["citations"]) for row in ready)
    latencies = [row["latency_ms"] for row in ready]
    return {
        "hit_at_5": {
            "value": _ratio(sum(1 for row in answerable if row["hit"]),
                            len(answerable)), "n": len(answerable)},
        "citation_precision": {
            "value": _ratio(ok_citations, total_citations),
            "n": total_citations, "ok": ok_citations},
        "refusal_recall": {
            "value": _ratio(sum(1 for row in out_of_corpus
                                if row["correct_refusal"]),
                            len(out_of_corpus)), "n": len(out_of_corpus)},
        "refusal_false": {
            "value": _ratio(sum(1 for row in answerable
                                if row["wrong_refusal"]),
                            len(answerable)), "n": len(answerable)},
        "latency_median_ms": {
            "value": statistics.median(latencies) if latencies else None,
            "n": len(latencies)},
    }


def compute_metrics(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """Метрики по каждому mode отдельно и по итогу (ключ «summary»)."""
    groups = [(mode, [row for row in rows if row["mode"] == mode])
              for mode in MODES]
    groups.append(("summary", rows))
    return {mode: _bucket_metrics(subset) for mode, subset in groups}


def _clip(text: str, width: int) -> str:
    """Обрезать строку под колонку таблицы."""
    return text if len(text) <= width else text[: width - 1] + "…"


def _pages_str(pages: list[int]) -> str:
    """Листы через запятую; пусто — тире."""
    return ", ".join(str(page) for page in pages) if pages else "—"


def _fmt_share(entry: dict) -> str:
    """Доля вида «0.750 (n=8)» либо «n/a» при пустом знаменателе."""
    return "n/a" if entry["value"] is None else \
        f"{entry['value']:.3f} (n={entry['n']})"


def _fmt_ms(entry: dict) -> str:
    """Медиана в мс либо «n/a»."""
    return "n/a" if entry["value"] is None else \
        f"{entry['value']:.1f} мс (n={entry['n']})"


def render_table(rows: list[dict], metrics: dict[str, dict]) -> str:
    """Человекочитаемая таблица: строка на вопрос + блок метрик + todo."""
    ready_n = sum(1 for row in rows if row["status"] == "ready")
    lines = [f"Золотой прогон: всего {len(rows)} "
             f"(ready {ready_n}, todo {len(rows) - ready_n})", ""]
    lines.append(f"{'id':<5} {'mode':<8} {'ст':<6} {'вопрос':<36} "
                 f"{'ожидания (документ: листы)':<42} {'hit':<4} "
                 f"{'цит':<5} {'лат,мс':>8}")
    for row in rows:
        if row["status"] == "todo":
            expectations = "— (todo)"
        elif row["expected_pages"]:
            expectations = (f"{_clip(row['expected_doc'], 30)}: "
                            f"{_pages_str(row['expected_pages'])}")
        else:
            expectations = "вне корпуса"
        hit = {None: "—", True: "да", False: "нет"}[row["hit"]]
        ok = sum(1 for item in row["citations"] if item["ok"])
        cited = f"{ok}/{len(row['citations'])}"
        lines.append(
            f"{row['id']:<5} {row['mode']:<8} {row['status']:<6} "
            f"{_clip(row['question'], 36):<36} {expectations:<42} "
            f"{hit:<4} {cited:<5} {row['latency_ms']:>8.1f}")
    lines += ["", "Метрики: значение (n); n/a — пустой знаменатель, "
              "не единица."]
    lines.append(f"{'mode':<10} {'hit_at_5':<18} "
                 f"{'citation_prec':<18} {'refus_recall':<18} "
                 f"{'refus_false':<18} {'median_лат':<18}")
    for mode in (*MODES, "summary"):
        block = metrics[mode]
        lines.append(
            f"{mode:<10} {_fmt_share(block['hit_at_5']):<18} "
            f"{_fmt_share(block['citation_precision']):<18} "
            f"{_fmt_share(block['refusal_recall']):<18} "
            f"{_fmt_share(block['refusal_false']):<18} "
            f"{_fmt_ms(block['latency_median_ms']):<18}")
    todos = [row for row in rows if row["status"] == "todo"]
    if todos:
        lines += ["", f"todo-вопросы ({len(todos)} шт.) — в метриках не "
                      "считаются, ожидания заполняет владелец:"]
        for row in todos:
            lines.append(f"  {row['id']}  {row['mode']:<8} "
                         f"{_clip(row['question'], 70)}")
    return "\n".join(lines)


def _counts(rows: list[dict]) -> dict[str, int]:
    """Короткая сводка по набору для отчёта."""
    ready = [row for row in rows if row["status"] == "ready"]
    answerable = sum(1 for row in ready if row["expected_pages"])
    return {
        "total": len(rows),
        "ready": len(ready),
        "todo": len(rows) - len(ready),
        "expect_answer": answerable,
        "expect_refusal": len(ready) - answerable,
    }


def save_report(report_path: str | Path, rows: list[dict],
                metrics: dict[str, dict], meta: dict) -> Path:
    """Записать JSON-отчёт (UTF-8, ensure_ascii=False)."""
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            **meta,
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        },
        "counts": _counts(rows),
        "metrics": metrics,
        "questions": rows,
        "todo": [{"id": row["id"], "question": row["question"]}
                 for row in rows if row["status"] == "todo"],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def main(argv: list[str] | None = None) -> int:
    """Ручной запуск: ``python tests/golden/metrics.py [путь_к_БД]``.

    Копия БД в темп-каталоге, холостой прогрев, 34 вопроса, таблица
    с метриками и отчёт ``reports/golden_run.json``. Код 0 — прогон
    завершён; сами метрики могут быть низкими — это их дело показывать.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(argv[0]) if argv else DEFAULT_STORE_PATH
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    questions = load_questions()
    with tempfile.TemporaryDirectory(prefix="golden_run_") as tmp:
        store_copy = Path(tmp) / "graph.db"
        copy_store(source, store_copy)
        rows, meta = run_all(store_copy, questions)
    meta = {**meta, "source_store": str(source.resolve()),
            "questions_file": str(DEFAULT_QUESTIONS_PATH)}
    metrics = compute_metrics(rows)
    report_path = save_report(DEFAULT_REPORT_PATH, rows, metrics, meta)
    print(render_table(rows, metrics))
    print(f"Отчёт: {report_path}")
    return 0


if __name__ == "__main__":  # скриптовый запуск без pytest
    raise SystemExit(main())