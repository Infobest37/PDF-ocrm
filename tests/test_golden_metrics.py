"""Тест карточки 0.1: золотой набор и автотест метрик.

Что делает test_golden_run:
- копирует ``graph.db`` в темп-каталог (рабочая БД не открывается —
  правка 4 владельца);
- выполняет один холостой прогрев-запрос вне учёта (правка 3);
- прогоняет 34 вопроса ``tests/golden/questions.toml`` через цепочку
  CLI ``ask`` (``build_answer`` + ``verify_citations``) на копии;
- печатает человекочитаемую таблицу (видна с ``pytest -s`` либо
  отдельным запуском ``venv\\Scripts\\python.exe tests\\golden\\metrics.py``)
  и пишет ``reports/golden_run.json``;
- проверяет только структуру: 34 строки, отчёт записан, у метрик n,
  счётчики графа на копии не изменились. Порогов нет — низкая метрика
  показывается, но не валит сборку; блокирующие пороги появятся в
  Фазе 1.

Формат TOML вместо YAML по согласованию владельца: pyyaml не
добавляется, читается стандартный :mod:`tomllib`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = REPO_ROOT / "tests" / "golden" / "questions.toml"
STORE_SOURCE = REPO_ROOT / "graph.db"
REPORT_PATH = REPO_ROOT / "reports" / "golden_run.json"

#: Пять метрик карточки 0.1 (по каждой печатается n).
PLANNED_METRICS = (
    "hit_at_5", "citation_precision",
    "refusal_recall", "refusal_false", "latency_median_ms",
)

#: Полный размер золотого набора (дополнение 0.1в: 30 + 4 внекорпусных).
EXPECTED_TOTAL = 34

#: Распределение записей golden-набора по mode (карточка 0.1 + 0.1в).
EXPECTED_PER_MODE: dict[str, int] = {
    "explain": 11, "fault": 11, "procedure": 12,
}


def _load_metrics():
    """Загрузить tests/golden/metrics.py без пакета (нет __init__)."""
    path = REPO_ROOT / "tests" / "golden" / "metrics.py"
    spec = importlib.util.spec_from_file_location("golden_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses при строковых аннотациях ищет модуль в sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def metrics():
    """Модуль метрик для всех тестов модуля."""
    return _load_metrics()


@pytest.fixture(scope="module")
def store_copy(tmp_path_factory, metrics):
    """Копия graph.db в темп-каталоге; рабочая БД остаётся нетронутой."""
    if not STORE_SOURCE.is_file():
        pytest.skip("graph.db отсутствует в корне проекта")
    copy_dir = tmp_path_factory.mktemp("golden_db")
    target = copy_dir / "graph.db"
    metrics.copy_store(STORE_SOURCE, target)
    return target


def _write_questions(tmp_path: Path, body: str, name: str = "broken.toml") -> Path:
    """Записать фрагмент TOML в темп-файл для проверки валидации."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _entry(id_text: str, mode: str = "explain", status: str = "todo",
           expected_doc: str = "", pages: str = "[]") -> str:
    """Корректная по остальным полям запись TOML для тестов валидации."""
    return (
        "\n[[question]]\n"
        f"id = {id_text!r}\n"
        f"mode = {mode!r}\n"
        f"status = {status!r}\n"
        'question = "вопрос"\n'
        'aircraft_type = "Ми-8МТВ"\n'
        'ata = ""\n'
        f"expected_doc = {expected_doc!r}\n"
        f"expected_pages = {pages}\n"
        'notes = ""\n'
    )


# ==== ТЕСТ-ЧАСТЬ 2: негативная валидация загрузчика ====
def test_loader_full_set(metrics):
    """Размер EXPECTED_TOTAL/EXPECTED_PER_MODE; id уникальны; ready валидны."""
    questions = metrics.load_questions(QUESTIONS_PATH)
    assert len(questions) == EXPECTED_TOTAL
    modes = [item.mode for item in questions]
    for mode, count in EXPECTED_PER_MODE.items():
        assert modes.count(mode) == count
    ready = [item for item in questions if item.in_metrics]
    todo = [item for item in questions if not item.in_metrics]
    assert ready and todo
    ids = [item.id for item in questions]
    assert len(set(ids)) == len(ids)
    for item in ready:
        if item.expect_answer:
            assert item.expected_doc
        else:
            assert not item.expected_doc  # внекорпусный, «должен отказать»


def test_loader_duplicate_id(tmp_path, metrics):
    """Дубль id — ValueError с номерами записей."""
    body = _entry("q1") + _entry("q1", mode="fault")
    with pytest.raises(ValueError, match="дубль id 'q1'"):
        metrics.load_questions(_write_questions(tmp_path, body))


def test_loader_missing_field(tmp_path, metrics):
    """Пропуск обязательного поля — ValueError, названо поле."""
    body = "\n[[question]]\nid = \"q1\"\nmode = \"explain\"\n" \
           "status = \"todo\"\nquestion = \"вопрос\"\n" \
           "aircraft_type = \"Ми-8МТВ\"\nexpected_doc = \"\"\n" \
           "expected_pages = []\nnotes = \"\"\n"
    with pytest.raises(ValueError, match="ata"):
        metrics.load_questions(_write_questions(tmp_path, body))


def test_loader_bad_mode(tmp_path, metrics):
    """Недопустимый mode — ValueError."""
    with pytest.raises(ValueError, match="mode"):
        metrics.load_questions(
            _write_questions(tmp_path, _entry("q1", mode="chat")))


def test_ready_with_doc_but_no_pages_rejected(tmp_path, metrics):
    """ready с expected_doc, но без листов — ошибка (не внекорпусный)."""
    with pytest.raises(ValueError, match="expected_doc"):
        metrics.load_questions(
            _write_questions(tmp_path, _entry("q1", status="ready",
                                              expected_doc="РЭ1")))


def test_golden_run(store_copy, metrics):
    """Полный прогон 34 вопросов на копии БД: отчёт и структура.

    Только структурные проверки без порогов: количество строк,
    записанный отчёт, согласованность value/n у метрик, неизменность
    счётчиков графа на КОПИИ (stats_equal).
    """
    questions = metrics.load_questions(QUESTIONS_PATH)
    rows, meta = metrics.run_all(store_copy, questions)
    assert meta["stats_equal"] is True
    metrics_value = metrics.compute_metrics(rows)
    print(metrics.render_table(rows, metrics_value))
    meta_full = {
        **meta,
        "source_store": str(STORE_SOURCE.resolve()),
        "questions_file": str(QUESTIONS_PATH),
    }
    report_path = metrics.save_report(REPORT_PATH, rows, metrics_value,
                                      meta_full)
    assert len(rows) == len(questions) == EXPECTED_TOTAL
    with open(report_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["counts"]["total"] == EXPECTED_TOTAL
    assert payload["todo"] == [{"id": row.id, "question": row.question}
                               for row in questions if not row.in_metrics]
    assert payload["counts"]["expect_answer"] > 0
    assert payload["counts"]["expect_refusal"] > 0
    for group_name in (*metrics.MODES, "summary"):
        block = payload["metrics"][group_name]
        for metric_name in PLANNED_METRICS:
            entry = block[metric_name]
            assert (entry["value"] is None) == (entry["n"] == 0), \
                f"{group_name}/{metric_name}: value/n рассинхронизация"