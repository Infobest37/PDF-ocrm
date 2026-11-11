"""Б9: флаги PDFGRAPH_NORMS и PDFGRAPH_TERMS — выключатели, не включатели.

Семантика (решение владельца 11.11.2026): по умолчанию (переменная не
задана) каналы ВКЛЮЧЕНЫ — рабочая конфигурация; явное выключение — только
значения «0», «false», «off», «no» (после strip + casefold); пустая строка
и любое другое значение (мусор, «2», «abc») — включение. Причина: флаг по
забывчивости даёт ложный ответ с зелёной проверкой цитат, что дороже любой
опечатки в значении.

Живой тест требует graph.db в корне проекта (как test_golden_metrics);
без него пропускается.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from pdfgraph.norms import NORMS_ENV, effective_norms_enabled
from pdfgraph.terms import TERMS_ENV, effective_terms_enabled

REPO_ROOT = Path(__file__).resolve().parents[1]
STORE_SOURCE = REPO_ROOT / "graph.db"

OFF_VALUES = ("0", "false", "off", "no")
ON_VALUES = ("1", "true", "yes", "on")


# --- выключатели -----------------------------------------------------------


@pytest.mark.parametrize("value", OFF_VALUES)
def test_norms_off_values_disable(monkeypatch, value):
    monkeypatch.setenv(NORMS_ENV, value)
    assert effective_norms_enabled() is False


@pytest.mark.parametrize("value", ON_VALUES)
def test_norms_on_values_enable(monkeypatch, value):
    monkeypatch.setenv(NORMS_ENV, value)
    assert effective_norms_enabled() is True


def test_norms_unset_is_enabled(monkeypatch):
    monkeypatch.delenv(NORMS_ENV, raising=False)
    assert effective_norms_enabled() is True


@pytest.mark.parametrize("value", ["", "2", "abc"])
def test_norms_empty_and_junk_enable(monkeypatch, value):
    monkeypatch.setenv(NORMS_ENV, value)
    assert effective_norms_enabled() is True


@pytest.mark.parametrize("value", OFF_VALUES)
def test_terms_off_values_disable(monkeypatch, value):
    monkeypatch.setenv(TERMS_ENV, value)
    assert effective_terms_enabled() is False


@pytest.mark.parametrize("value", ON_VALUES)
def test_terms_on_values_enable(monkeypatch, value):
    monkeypatch.setenv(TERMS_ENV, value)
    assert effective_terms_enabled() is True


def test_terms_unset_is_enabled(monkeypatch):
    monkeypatch.delenv(TERMS_ENV, raising=False)
    assert effective_terms_enabled() is True


@pytest.mark.parametrize("value", ["", "2", "abc"])
def test_terms_empty_and_junk_enable(monkeypatch, value):
    monkeypatch.setenv(TERMS_ENV, value)
    assert effective_terms_enabled() is True


# --- живой тест: ответ без переменных окружения ----------------------------


def _load_golden_metrics():
    """Загрузить tests/golden/metrics.py без пакета (нет __init__)."""
    path = REPO_ROOT / "tests" / "golden" / "metrics.py"
    spec = importlib.util.spec_from_file_location("golden_metrics_b9", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def store_copy_b9(tmp_path_factory):
    """Копия graph.db в темп-каталоге; рабочая БД остаётся нетронутой."""
    if not STORE_SOURCE.is_file():
        pytest.skip("graph.db отсутствует в корне проекта")
    metrics = _load_golden_metrics()
    dest = tmp_path_factory.mktemp("b9_store") / "graph.db"
    metrics.copy_store(STORE_SOURCE, dest)
    return dest


def test_live_brush_height_default_environment(store_copy_b9, monkeypatch):
    """Вопрос про щётки МВ-1200 без обоих флагов: лидер л.64, «13,5 мм».

    До Б9 при пустом окружении каналы норм и алиасов были выключены и
    лист 64 не поднимался — тест фиксирует новое поведение по умолчанию.
    """
    from pdfgraph.answer import build_answer
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    monkeypatch.delenv(NORMS_ENV, raising=False)
    monkeypatch.delenv(TERMS_ENV, raising=False)

    store = Store(str(store_copy_b9))
    try:
        answer = build_answer(
            store, "какая минимальная высота щёток на МВ-1200", None)
        checks = verify_citations(answer, store)
    finally:
        store.close()

    assert not answer.refusal, f"отказ: {answer.reason}"
    assert answer.fragments, "выдача пуста"
    assert answer.fragments[0].page == 64, \
        f"лидер л.{answer.fragments[0].page}, ожидался л.64"
    all_text = " ".join(
        [c.text for c in answer.claims]
        + [f.snippet for f in answer.fragments])
    assert "13,5" in all_text, "в ответе нет «13,5 мм»"
    assert any(check.ok for check in checks), "нет ни одной подтверждённой цитаты"
