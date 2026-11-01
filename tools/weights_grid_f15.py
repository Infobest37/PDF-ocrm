# -*- coding: utf-8 -*-
"""Ф1.5: сетка веса слагаемого конкретики на корпусных золотых вопросах.

Перебирает WEIGHT в (0.0, 0.02, 0.05, 0.1, 0.2) через env
PDFGRAPH_SPECIFICITY_WEIGHT (читается при каждом слиянии) и прогоняет
production-цепочку build_answer на КОПИИ graph.db (рабочая БД не
открывается). По 28 корпусным ready-вопросам для каждого веса:

- hit_at_5 — правильный лист (документ+лист) в топ-5;
- false_on_found (refusal_false_on_found) — отказ при правильном листе
  в топ-5; требование — 0.00 (Ф1.3б.1);
- missed_refused — отказы на промахах (правильного листа в топ-5 не
  было); доля — refusal_on_miss, база Ф1.4.1 — 0.100;
- clarify — число уточнений на корпусе; требование сетки — не больше 1.

WEIGHT=0.0 — контроль базы: должен повторить прежний hit_at_5 0.6429
(n=28); расхождение означает, что выдача изменилась не слагаемым —
точка помечается, использовать сетку тогда нельзя.

Отчёты: reports/weights_grid_f15.json и .md (сетка целиком, включая
провальные точки — карточка Ф1.5, п. 4). Выбор по карточке —
максимальный hit_at_5 при false_on_found == 0.0 и clarify <= 1; скрипт
только печатает рекомендацию — включение выбранного значения в конфиг
отдельная правка DEFAULT_SPECIFICITY_WEIGHT.

Запуск: venv\\Scripts\\python.exe -X utf8 tools\\weights_grid_f15.py [путь_БД]
"""

import json
import os
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.answer import build_answer  # noqa: E402
from pdfgraph.rag_fusion import SPECIFICITY_WEIGHT_ENV  # noqa: E402
from pdfgraph.store import Store  # noqa: E402

#: Сетка весов из карточки Ф1.5.
WEIGHTS: tuple[float, ...] = (0.0, 0.02, 0.05, 0.1, 0.2)
#: База сравнения: hit_at_5 прежнего golden-прогона (18/28 = 0.6429).
BASE_HIT_5 = 18 / 28
DEFAULT_STORE = REPO_ROOT / "graph.db"
QUESTIONS_PATH = REPO_ROOT / "tests" / "golden" / "questions.toml"
JSON_REPORT = REPO_ROOT / "reports" / "weights_grid_f15.json"
MD_REPORT = REPO_ROOT / "reports" / "weights_grid_f15.md"
#: Холостой прогрев перед сеткой (в метрики не попадает, как в golden).
WARMUP_QUESTION = "несущий винт"


def copy_store(source: Path, target: Path) -> None:
    """Скопировать БД в темп-каталог (как tests/golden/metrics.copy_store):
    sqlite backup() поверх read-only подключения, иначе файловые копии."""
    import shutil
    import sqlite3
    from urllib.request import pathname2url
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


def load_corpus(path: Path) -> list[dict]:
    """Корпусные golden-вопросы: status=ready, expected_pages непуст."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [q for q in data["question"]
            if q["status"] == "ready" and q["expected_pages"]]


def run_weight(store: Store, questions: list[dict], weight: float) -> dict:
    """Один прогон сетки: build_answer по корпусным, метрики по весу."""
    os.environ[SPECIFICITY_WEIGHT_ENV] = str(weight)
    try:
        rows: list[dict] = []
        for question in questions:
            answer = build_answer(store, question["question"],
                                  question["aircraft_type"])
            hit = any(
                fragment.document == question["expected_doc"]
                and fragment.page in question["expected_pages"]
                for fragment in answer.fragments[:5])
            rows.append({
                "id": question["id"],
                "hit": hit,
                "refusal": bool(answer.refusal),
                "clarify": answer.clarification is not None,
            })
    finally:
        os.environ.pop(SPECIFICITY_WEIGHT_ENV, None)
    found = [row for row in rows if row["hit"]]
    missed = [row for row in rows if not row["hit"]]
    return {
        "hit_at_5": len(found) / len(rows),
        "false_on_found": (sum(1 for row in found if row["refusal"])
                           / len(found)) if found else 0.0,
        "missed_refused": [row["id"] for row in missed if row["refusal"]],
        "clarify_count": sum(1 for row in rows if row["clarify"]),
        "clarify_ids": [row["id"] for row in rows if row["clarify"]],
        "found_refused_ids": [row["id"] for row in found if row["refusal"]],
        "miss_count": len(missed),
        "ids_missed": [row["id"] for row in missed],
    }

def choose_weight(grid: dict[float, dict]) -> float | None:
    """Выбор по карточке Ф1.5: максимальный hit_at_5 при
    false_on_found == 0.0 и clarify <= 1; ничья — меньший вес."""
    candidates = [
        weight for weight, metrics in grid.items()
        if metrics["false_on_found"] == 0.0 and metrics["clarify_count"] <= 1
    ]
    if not candidates:
        return None
    return max(candidates,
               key=lambda weight: (grid[weight]["hit_at_5"], -weight))


def render_md(grid: dict[float, dict], choice: float | None,
              base_ok: bool) -> str:
    """Полная сетка целиком (включая провальные точки) в Markdown."""
    lines = [
        "# Ф1.5: сетка веса слагаемого конкретики (WEIGHT * specificity)",
        "",
        "Прогон: build_answer на копии graph.db, 28 корпусных "
        "ready-вопросов golden.",
        "Критерии карточки: false_on_found == 0.00, уточнений на корпусе "
        "<= 1, hit_at_5 максимальный.",
        "",
        "| вес | hit@5 | false_on_found | уточнений | уточнены | "
        "отказов на промахе | промахи (ids) | база 0.6429 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for weight, metrics in grid.items():
        ids = ", ".join(metrics["clarify_ids"]) or "—"
        missed = ", ".join(metrics["ids_missed"]) or "—"
        note = "" if weight != 0.0 else ("совпадает" if base_ok else
                                         "РАСХОЖДЕНИЕ с базой")
        lines.append(
            f"| {weight} | {metrics['hit_at_5']:.4f} (n=28) | "
            f"{metrics['false_on_found']:.3f} | {metrics['clarify_count']} "
            f"({ids}) | {ids} | {len(metrics['missed_refused'])} "
            f"({', '.join(metrics['missed_refused']) or '—'}) | {missed} | "
            f"{note or '—'} |")
    verdict = ("рекомендация: WEIGHT = "
               f"{choice}" if choice is not None else
               "ни одно значение не улучшает hit@5 — остаёмся на 0.0")
    lines += ["", f"Выбор (скрипт, автоматический): {verdict}.",
              "Включение в конфиг — правка DEFAULT_SPECIFICITY_WEIGHT "
              "в pdfgraph/rag_fusion.py."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Сетка целиком на копии БД; json + md в reports/, таблица в stdout."""
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(argv[0]) if argv else DEFAULT_STORE
    if not source.is_file():
        print(f"error: нет базы: {source}", file=sys.stderr)
        return 2
    questions = load_corpus(QUESTIONS_PATH)
    grid: dict[float, dict] = {}
    with tempfile.TemporaryDirectory(prefix="weights_grid_f15_") as tmp:
        copy_path = Path(tmp) / "graph.db"
        copy_store(source, copy_path)
        store = Store(str(copy_path))
        try:
            build_answer(store, WARMUP_QUESTION, None)  # холостой прогрев
            for weight in WEIGHTS:
                grid[weight] = run_weight(store, questions, weight)
        finally:
            store.close()
    base_ok = abs(grid[0.0]["hit_at_5"] - BASE_HIT_5) < 1e-9
    if not base_ok:
        print("ВНИМАНИЕ: WEIGHT=0.0 расходится с базой "
              f"{BASE_HIT_5:.4f} — сетка недостоверна", file=sys.stderr)
    choice = choose_weight(grid) if base_ok else None
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "meta": {
            "generated_at": generated,
            "store": str(source.resolve()),
            "questions_file": str(QUESTIONS_PATH),
            "n_questions": len(questions),
            "weights": list(WEIGHTS),
            "base_hit_5": BASE_HIT_5,
            "base_reproduced": base_ok,
            "env": SPECIFICITY_WEIGHT_ENV,
            "warmup": WARMUP_QUESTION,
        },
        "grid": {str(weight): metrics for weight, metrics in grid.items()},
        "choice": {"weight": choice},
    }
    for path, data in ((JSON_REPORT, payload),
                       (MD_REPORT, render_md(grid, choice, base_ok))):
        path.write_text(
            data if isinstance(data, str) else
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
    print(render_md(grid, choice, base_ok))
    print(f"Отчёты: {JSON_REPORT.name}, {MD_REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())