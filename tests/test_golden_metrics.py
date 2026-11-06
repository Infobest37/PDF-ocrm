"""Тест карточки 0.1: золотой набор и автотест метрик.

Что делает test_golden_run:
- копирует ``graph.db`` в темп-каталог (рабочая БД не открывается —
  правка 4 владельца);
- выполняет один холостой прогрев-запрос вне учёта (правка 3);
- прогоняет 48 вопросов ``tests/golden/questions.toml`` через цепочку CLI
  ``ask`` (``build_answer`` + ``verify_citations``) на копии;
- печатает человекочитаемую таблицу (видна с ``pytest -s`` либо
  отдельным запуском ``venv\\Scripts\\python.exe tests\\golden\\metrics.py``)
  и пишет ``reports/golden_run.json``;
- проверяет только структуру: 48 строк, отчёт записан, у метрик n,
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

#: Метрики карточки 0.1 + doc_hit_at_5 (Ф1.1) + деление отказа
#: (refusal_false_on_found/refusal_on_miss, Ф1.3б.1); по каждой печатается n.
#: ``refusal_false`` остаётся сводным (смесь ложных и обязательных отказов).
#: Ф1.7: hit_at_5_with_neighbors — окно соседних листов, рядом с hit_at_5,
#: hit_at_5 не переопределяется.
PLANNED_METRICS = (
    "hit_at_5", "hit_at_5_with_neighbors", "doc_hit_at_5",
    "citation_precision",
    "refusal_recall", "refusal_false", "refusal_false_on_found",
    "refusal_on_miss", "latency_median_ms",
)

#: Полный размер золотого набора (карточка Ф1.0 + Ф1.18: добавлены
#: живые вопросы владельца e33–e35, все mode=explain; 42 корпусных +
#: 6 внекорпусных).
EXPECTED_TOTAL = 48

#: Распределение записей golden-набора по mode (карточка Ф1.0 + Ф1.18;
#: «incl. 6 refusal» — внекорпусные по всем режимам суммарно).
EXPECTED_PER_MODE: dict[str, int] = {
    "explain": 19, "fault": 11, "procedure": 18,
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


# --- Диагностика отказа 0.1г: семантика refusal-метрик -------------------

def _refusal_row(qid: str, mode: str, refusal: bool,
                 expected_pages: list[int], latency_ms: float = 90.0,
                 ok_citations: int = 0, total_citations: int = 0,
                 hit: bool | None = None) -> dict:
    """Синтетическая строка прогона с полями, нужными compute_metrics.

    correct_refusal/wrong_refusal заполняются как в real-прогоне
    (metrics.py: refus — if expect_refusal, wrong_refusal — if expect_answer).
    """
    refus_known = not expected_pages
    answer_known = bool(expected_pages)
    return {
        "id": qid, "mode": mode, "status": "ready",
        "question": f"вопрос {qid}", "aircraft_type": "Ми-8МТВ",
        "ata": "", "expected_doc": "" if refus_known else f"{qid}.pdf",
        "expected_pages": expected_pages, "notes": "",
        "refusal": refusal,
        "refusal_reason": "нет подтверждений в документах" if refusal else "",
        "verified": False if refusal and total_citations == 0 else True,
        "latency_ms": latency_ms,
        "top5": [], "citations": [  # имитация проверенных цитат ответа
            {"ok": True, "reason": ""}
            for _ in range(total_citations)],
        "hit": hit,
        "correct_refusal": refusal if refus_known else None,
        "wrong_refusal": refusal if answer_known else None,
    }


def test_refusal_recall_counts_true_refusals(metrics):
    """refusal_recall = доля внекорпусных, у которых answer.refusal True.

    Фиксация достоверности (0.1г): формулировка «вопрос вне корпуса»
    должна превращаться в отказ только при фактическом refusal=True;
    перевёрнутая формула или счёт пустой выдачи дают ложь — тест валит.
    """
    rows = [
        _refusal_row("f10", "fault", refusal=False, expected_pages=[]),
        _refusal_row("e11", "explain", refusal=True, expected_pages=[]),
        _refusal_row("p11", "procedure", refusal=False, expected_pages=[]),
    ]
    block = metrics.compute_metrics(rows)["summary"]["refusal_recall"]
    assert block == {"value": 1 / 3, "n": 3}
    none_refusals = [
        _refusal_row("p12", "procedure", refusal=False, expected_pages=[]),
    ]
    block = metrics.compute_metrics(none_refusals)["summary"]["refusal_recall"]
    assert block == {"value": 0.0, "n": 1}


def test_refusal_false_counts_refusals_on_answerable(metrics):
    """refusal_false = доля корпусных (с известным листом), получивших отказ."""
    rows = [
        _refusal_row("q_ok", "explain", refusal=False, expected_pages=[30],
                     total_citations=3, hit=True),
        _refusal_row("q_refused", "fault", refusal=True, expected_pages=[9],
                     total_citations=0, hit=False),
    ]
    block = metrics.compute_metrics(rows)["summary"]["refusal_false"]
    assert block == {"value": 0.5, "n": 2}


def test_refusal_split_separates_on_found_from_on_miss(metrics):
    """Ф1.3б.1: деление отказа — как на полигоне tools/gates_polygon_f13b.py.

    ``refusal_false_on_found`` — отказ при правильном листе в топ-5
    (ложный, требование 0.00); ``refusal_on_miss`` — доля промахов, где
    отказ отдан (обязательный); сводный ``refusal_false`` = смесь обоих.
    Негативы (пустые ожидания) не попадают ни в on_found, ни в on_miss.
    """
    rows = [
        _refusal_row("q_found_ok", "explain", refusal=False,
                     expected_pages=[30], total_citations=3, hit=True),
        _refusal_row("q_found_refused", "fault", refusal=True,
                     expected_pages=[9], total_citations=0, hit=True),
        _refusal_row("q_miss_refused", "procedure", refusal=True,
                     expected_pages=[5], total_citations=0, hit=False),
        _refusal_row("q_miss_answered", "explain", refusal=False,
                     expected_pages=[7], total_citations=2, hit=False),
        _refusal_row("e_ooc", "explain", refusal=False, expected_pages=[]),
    ]
    summary = metrics.compute_metrics(rows)["summary"]
    assert summary["refusal_false_on_found"] == {"value": 0.5, "n": 2}
    assert summary["refusal_on_miss"] == {"value": 0.5, "n": 2}
    # Сводный смешивает оба класса: 2 отказанных из 4 корпусных.
    assert summary["refusal_false"] == {"value": 0.5, "n": 4}
    # Негативы остались только в refusal_recall.
    assert summary["refusal_recall"] == {"value": 0.0, "n": 1}


def test_refusal_rows_use_out_of_corpus_denominator_only(metrics):
    """Внекорпусные не попадают в refusal_false, а корпусные — в refusal_recall."""
    rows = [
        _refusal_row("e11", "explain", refusal=False, expected_pages=[]),
        _refusal_row("q_ok", "fault", refusal=False, expected_pages=[11],
                     total_citations=1, hit=True),
    ]
    summary = metrics.compute_metrics(rows)["summary"]
    assert summary["refusal_recall"] == {"value": 0.0, "n": 1}
    assert summary["refusal_false"] == {"value": 0.0, "n": 1}


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