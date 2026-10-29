"""Тесты отчёта дублей (tools/duplicates_report.py) — карточка 0.1б, Шаг 1.

tools/ — не пакет, поэтому модуль грузится по пути (как в
``test_golden_metrics.py``). Все прогоны — по временным хранилищам
и временной копии questions.toml; рабочая ``graph.db`` не открывается.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

CAN_DOC = "РТЭ Ми-8МТВ-1 кн. 6 (изм. от 08.07.2025).pdf"
OLD_DOC = (
    "Руководство по технической эксплуатации РТЭ Ми-8МТВ-1 кн. 6 "
    "(файл от 17.04.2023).pdf"
)
NEW_DOC = "РТЭ Ми-8МТВ-1 кн. 7 (изм. от 24.08.2023).pdf"

PAGE_TEXT = "023.11.30 РАДИОМАЯК ПАРМ-406А"


def _load_tools(name: str):
    """Загрузить модуль из ``tools/`` по пути (tools/ не пакет)."""
    path = REPO_ROOT / "tools" / name
    spec = importlib.util.spec_from_file_location("tools_" + name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _page(store, document: str, page: int, text: str) -> None:
    from pdfprepare.normalize import normalize_for_search

    store.upsert_page(document, page, "Ми-8МТВ", "023.00.00", "023.11.30",
                      text, normalize_for_search(text))


@pytest.fixture(scope="module")
def report():
    return _load_tools("duplicates_report.py")


@pytest.fixture
def clone_store(tmp_path):
    """Хранилище с двумя экземплярами одного документа (клон/канон)."""
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "g.db"))
    try:
        for document in (CAN_DOC, OLD_DOC):
            _page(store, document, 47, PAGE_TEXT)
        yield store
    finally:
        store.close()


def _write_questions(path: Path, canon: str) -> None:
    """Минимальный questions.toml для CLI-прогона read-only отчёта."""
    path.write_text(
        "[[question]]\n"
        'id = "e03"\n'
        'mode = "explain"\n'
        'status = "ready"\n'
        'question = "радиомаяк"\n'
        'aircraft_type = "Ми-8МТВ"\n'
        'ata = "023"\n'
        f'expected_doc = "{canon}"\n'
        "expected_pages = [47]\n"
        'notes = ""\n'
        "\n"
        "[[question]]\n"
        'id = "e04"\n'
        'mode = "fault"\n'
        'status = "ready"\n'
        'question = "гидросистема"\n'
        'aircraft_type = "Ми-8МТВ"\n'
        'ata = "024"\n'
        'expected_doc = ""\n'
        "expected_pages = []\n"
        'notes = ""\n'
        "\n"
        "[[question]]\n"
        'id = "e06"\n'
        'mode = "explain"\n'
        'status = "ready"\n'
        'question = "кран"\n'
        'aircraft_type = "Ми-8МТВ"\n'
        'ata = "028"\n'
        'expected_doc = ""\n'
        "expected_pages = []\n"
        'notes = ""\n',
        encoding="utf-8",
    )


# --- тесты --------------------------------------------------------------

def test_norm_key_groups_synonyms_and_revisions(report):
    """Клон с префиксом «руководство по…» попадает в кластер канона."""
    assert report.norm_key(OLD_DOC) == report.norm_key(CAN_DOC)
    assert report.norm_key(OLD_DOC) == "ртэ ми-8мтв-1 кн. 6"
    assert report.norm_key(NEW_DOC) == "ртэ ми-8мтв-1 кн. 7"


def test_edition_date_and_sort_key(report):
    """Дата «от ДД.ММ.ГГ(ГГГГ)» в имени; двузначный год < 30 → 20xx."""
    assert report.edition_date(OLD_DOC) == "17.04.2023"
    assert report.edition_date(CAN_DOC) == "08.07.2025"
    assert report.edition_date("без даты.pdf") == ""
    assert report._edition_sort_key(OLD_DOC) == (2023, 4, 17)
    assert report._edition_sort_key(CAN_DOC) == (2025, 7, 8)
    assert report._edition_sort_key("док (от 17.04.20).pdf") == (2020, 4, 17)
    assert report._edition_sort_key("без даты.pdf") is None


def test_suggest_rule1_single_golden(report):
    """Golden-листы только у одного экземпляра → это канон (правило 1)."""
    golden_index = {CAN_DOC: [("e03", (47,))]}
    winner, reason = report.suggest_canonical(
        [CAN_DOC, OLD_DOC], golden_index, {CAN_DOC: None, OLD_DOC: None})
    assert winner == CAN_DOC
    assert "правило 1" in reason


def test_suggest_rule1_ambiguous(report):
    """Golden-листы у обоих → предложение не даётся."""
    golden_index = {CAN_DOC: [("e03", (47,))], OLD_DOC: [("e04", (48,))]}
    winner, reason = report.suggest_canonical(
        [CAN_DOC, OLD_DOC], golden_index, {})
    assert winner is None
    assert "правило 1" in reason


def test_suggest_rule2_newest_edition(report):
    """Без golden-листов канон — самая поздняя редакция (правило 2)."""
    winner, reason = report.suggest_canonical([CAN_DOC, OLD_DOC], {}, {})
    assert winner == CAN_DOC
    assert "правило 2" in reason and "17.04.2023" in reason


def test_suggest_rule3_only_file_left(report):
    """Без дат канон — единственный с существующим PDF (правило 3)."""
    undated_a = "РТЭ том 1 (без даты).pdf"
    undated_b = "РТЭ том 2 (без даты).pdf"
    winner, reason = report.suggest_canonical(
        [undated_a, undated_b], {},
        {undated_a: Path("x"), undated_b: None})
    assert winner == undated_a
    assert "правило 3" in reason


def test_suggest_no_decision(report):
    """Ни golden, ни дат, ни файлов → решение за владельцем."""
    winner, reason = report.suggest_canonical(["а.pdf", "б.pdf"], {}, {})
    assert winner is None
    assert "владельца" in reason


def test_golden_map(report):
    """Индекс документ → [(id, листы)] с учётом пустых expected_doc."""
    golden = [
        ("e01", "q1", "Ми-8МТВ", "doc.pdf", (1, 2)),
        ("e02", "q2", "Ми-8МТВ", "", ()),
        ("e03", "q3", "Ми-8МТВ", "doc.pdf", (3,)),
    ]
    assert report.golden_map(golden) == {
        "doc.pdf": [("e01", (1, 2)), ("e03", (3,))],
    }


def test_control_queries(report):
    """Контрольные id e03/e04/e06 с дословными формулировками."""
    golden = [
        ("e03", "q3", "Ми-8МТВ", "d.pdf", (1,)),
        ("e04", "q4", "Ми-8МТВ", "d2.pdf", (2,)),
        ("e06", "q6", "Ми-8МТВ", "d3.pdf", (3,)),
        ("e01", "q1", "Ми-8МТВ", "d4.pdf", (4,)),
    ]
    assert report.control_queries(golden) == {
        "e03": ("q3", "Ми-8МТВ"),
        "e04": ("q4", "Ми-8МТВ"),
        "e06": ("q6", "Ми-8МТВ"),
    }


def test_control_queries_missing_id(report):
    """Отсутствие контрольного id в golden-наборе → ValueError."""
    with pytest.raises(ValueError):
        report.control_queries([("e03", "q", "Ми-8МТВ", "d", (1,))])


def test_top20_documents_counts(report, clone_store):
    """Топ-20 по вопросу «радиомаяк» — листы обоих клонов."""
    assert report.top20_documents(clone_store, "радиомаяк", "Ми-8МТВ") == {
        CAN_DOC: 1,
        OLD_DOC: 1,
    }


def test_top20_skips_marked_duplicate(report, clone_store):
    """Пометка дубля: экземпляр пропадает из топ-20, unmark возвращает."""
    clone_store.mark_duplicate(OLD_DOC, CAN_DOC)
    assert report.top20_documents(clone_store, "радиомаяк", "Ми-8МТВ") == {
        CAN_DOC: 1,
    }
    clone_store.unmark_duplicate(OLD_DOC)
    assert report.top20_documents(clone_store, "радиомаяк", "Ми-8МТВ") == {
        CAN_DOC: 1,
        OLD_DOC: 1,
    }


def test_collect_documents_ordering_proxy(report, clone_store):
    """collect_documents: число листов и MIN(rowid) как прокси постройки."""
    rows = report.collect_documents(clone_store)
    by_name = {name: (sheets, row_min) for name, sheets, row_min in rows}
    assert by_name[CAN_DOC] == (1, 1)   # первый upsert
    assert by_name[OLD_DOC] == (1, 2)   # второй upsert


def test_pdf_dir_info_matches_and_extras(report, tmp_path):
    """pdf_dir_info: матчинг как в GUI; лишние PDF папки перечислены."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / OLD_DOC).write_bytes(b"%PDF-1.4\n")
    (pdf_dir / "лишний.pdf").write_bytes(b"%PDF-1.4\n")
    files, extras = report.pdf_dir_info(pdf_dir, [CAN_DOC, OLD_DOC])
    assert files == {CAN_DOC: None, OLD_DOC: pdf_dir / OLD_DOC}
    assert extras == ["лишний.pdf"]


def test_main_writes_report_and_stdout(tmp_path, capsys):
    """CLI: отчёт на диске, сводка в stdout, exit 0."""
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "src.db"))
    try:
        for document in (CAN_DOC, OLD_DOC):
            _page(store, document, 47, PAGE_TEXT)
    finally:
        store.close()
    questions = tmp_path / "questions.toml"
    _write_questions(questions, CAN_DOC)
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / OLD_DOC).write_bytes(b"%PDF-1.4\n")
    report_mod = _load_tools("duplicates_report.py")
    report_md = tmp_path / "reports" / "duplicates.md"

    code = report_mod.main([
        "--store", str(tmp_path / "src.db"),
        "--questions", str(questions),
        "--report", str(report_md),
        "--pdf-dir", str(pdf_dir),
    ])

    assert code == 0
    text = report_md.read_text(encoding="utf-8")
    assert "### ``ртэ ми-8мтв-1 кн. 6`` — 2 экз." in text
    assert CAN_DOC in text and OLD_DOC in text
    assert "Кандидат в канон:" in text and "правило 1" in text
    assert "Контрольные запросы" in text
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Документов: 2; кластеров-дублей: 1"
    assert "канон-кандидат" in out and "Отчёт:" in out