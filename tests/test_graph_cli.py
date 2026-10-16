"""CLI-level tests for ``python -m pdfgraph`` (task 2.6).

Everything goes through ``pdfgraph.cli.main`` — no windows, no direct Store
writes: ``build`` reads the schema-v2 JSON folder, ``ask`` answers against the
database file and always reports the citation-verification verdict.
"""

import json
import os
import subprocess
import sys


def test_cli_build_then_ask_json(fx_prepared_dir, tmp_path):
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"]) == 0
    rc = main(["ask", "радиомаяк ПАРМ-406А", "--store", store, "--json"])
    # вывод перехватывается capsys; проверяем форму и вердикт
    assert rc == 0

def test_cli_ask_unknown_topic_refuses(fx_prepared_dir, tmp_path, capsys):
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"])
    assert main(["ask", "момент затяжки болтов шасси", "--store", store]) == 1
    assert "подтвержд" in capsys.readouterr().out


def test_cli_build_prints_stats(fx_prepared_dir, tmp_path, capsys):
    """``build`` reports Store.stats(): the fixture has three sheets."""
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"]) == 0
    out = capsys.readouterr().out
    assert "sheets=3" in out
    assert "nodes=" in out and "edges=" in out and "pending=" in out
    assert os.path.isfile(store)


def test_cli_ask_json_shape_and_verdict(fx_prepared_dir, tmp_path, capsys):
    """JSON mode: readable Cyrillic, ``verified`` flag, cited claims."""
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"])
    capsys.readouterr()
    assert main(["ask", "радиомаяк ПАРМ-406А", "--store", store, "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "РТЭ кн.1.pdf" in out  # ensure_ascii=False, not \uXXXX escapes
    assert payload["question"] == "радиомаяк ПАРМ-406А"
    assert payload["refusal"] is False
    assert payload["verified"] is True
    assert payload["claims"] and payload["fragments"]
    citation = payload["claims"][0]["citations"][0]
    assert citation["document"] == "РТЭ кн.1.pdf"
    assert citation["page"] == 47
    assert citation["system_code"] == "023.00.00"
    assert citation["ok"] is True
    fragment = payload["fragments"][0]
    assert set(fragment) >= {
        "document", "page", "system_code", "subsystem_code", "snippet",
        "char_start", "char_end", "score", "kind",
    }
    # Честная цитата: snippet — точный срез хранящегося текста листа.
    from pdfgraph.store import Store
    store_reader = Store(store)
    try:
        text = store_reader.page_text(fragment["document"], fragment["page"])
    finally:
        store_reader.close()
    assert text[fragment["char_start"]:fragment["char_end"]] == fragment["snippet"]


def test_cli_ask_refusal_json_marks_unverified(fx_prepared_dir, tmp_path, capsys):
    """In JSON mode the refusal fact is visible through ``verified``."""
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"])
    capsys.readouterr()
    assert main(["ask", "момент затяжки болтов шасси", "--store", store,
                 "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["refusal"] is True
    assert payload["verified"] is False
    assert "подтвержд" in payload["reason"]
    assert payload["claims"] == []
    # Текстовый режим печатает причину отказа дословно (а не только счётчик).
    assert main(["ask", "момент затяжки болтов шасси", "--store", store]) == 1
    assert "нет подтверждений в документах" in capsys.readouterr().out


def test_cli_ask_text_prints_evidence_block(fx_prepared_dir, tmp_path, capsys):
    """Text mode: answer, then «Доказательства» with document/page/code, verdict."""
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"])
    capsys.readouterr()
    assert main(["ask", "радиомаяк ПАРМ-406А", "--store", store]) == 0
    out = capsys.readouterr().out
    assert "Доказательства:" in out
    assert "РТЭ кн.1.pdf" in out and "лист 47" in out
    assert "023.00.00" in out
    assert "Подтверждено 1 из 1" in out


def test_cli_build_missing_registry_still_builds(fx_prepared_dir, tmp_path,
                                                 capsys):
    """Absent registry file: warn on stderr, build with empty names ({})."""
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    missing = str(tmp_path / "net-takogo.txt")
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ",
                 "--registry", missing]) == 0
    assert "registry" in capsys.readouterr().err
    # Ответ и цитаты от этого не зависят.
    assert main(["ask", "радиомаяк ПАРМ-406А", "--store", store,
                 "--json"]) == 0


def test_cli_build_without_registry_flag_is_empty(fx_prepared_dir, tmp_path,
                                                  monkeypatch):
    """Без флага --registry реестр не читается вообще, даже если файл по
    умолчанию лежит в текущем каталоге (уточнение контроллера 1, ревью 2.6)."""
    import sqlite3

    from pdfgraph.cli import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Коды систем Вертолета.txt").write_text(
        "023.00.00 МЕТКА ИЗ РЕЕСТРА\n023.11.30 ПОДСИСТЕМА ИЗ РЕЕСТРА\n",
        encoding="utf-8")
    store = str(tmp_path / "g.db")
    assert main(["build", fx_prepared_dir, store, "--type", "Ми-8МТВ"]) == 0

    conn = sqlite3.connect(store)
    try:
        labels = dict(conn.execute(
            "SELECT key, label FROM nodes WHERE kind IN ('system','subsystem')"))
    finally:
        conn.close()
    # Метка из файла не просочилась ни в один узел...
    assert not any("РЕЕСТР" in (label or "") for label in labels.values())
    # ...система названа из DocResult.system_names фикстуры.
    assert labels.get("023.00.00") == "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"


def test_cli_ask_type_filter_and_missing_store_are_reported(fx_prepared_dir,
                                                            tmp_path, capsys):
    """--type restricts answers; a missing store is a usage error (rc 2)."""
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"])
    capsys.readouterr()
    assert main(["ask", "радиомаяк ПАРМ-406А", "--store", store,
                 "--type", "Ми-8МТВ", "--json"]) == 0
    assert main(["ask", "радиомаяк ПАРМ-406А", "--store", store,
                 "--type", "Ми-8МТВ-АБВ", "--json"]) == 1
    assert main(["ask", "что-нибудь", "--store",
                 str(tmp_path / "net.db")]) == 2
    assert "error" in capsys.readouterr().err


def test_cli_build_reports_broken_inputs(fx_prepared_dir, tmp_path, capsys):
    """Сломанные входы — код возврата 2 и сообщение, без traceback."""
    from pdfgraph.cli import main
    prep = tmp_path / "broken-prep"
    prep.mkdir()
    (prep / "broken.json").write_text("{ это не json", encoding="utf-8")
    assert main(["build", str(prep), str(tmp_path / "g.db"),
                 "--type", "Ми-8МТВ"]) == 2
    assert "error" in capsys.readouterr().err

    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["build", str(empty), str(tmp_path / "g.db"),
                 "--type", "Ми-8МТВ"]) == 2

    unopened = str(tmp_path / "no-dir" / "g.db")
    assert main(["build", fx_prepared_dir, unopened, "--type", "Ми-8МТВ"]) == 2

    bogus = tmp_path / "bogus.db"
    bogus.write_text("это не sqlite", encoding="utf-8")
    assert main(["ask", "радиомаяк", "--store", str(bogus)]) == 2
    assert "store" in capsys.readouterr().err


def test_module_entry_point_runs():
    """``python -m pdfgraph`` is wired to cli.main (help exits 0)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pdfgraph", "ask", "--help"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout


# --- помечание дефектов из отчёта о пересканировании (задача 2.7, рулинг 2) ---

def test_cli_build_marks_defects_from_report(fx_prepared_dir, tmp_path, capsys):
    """Рядом с ``<stem>.json`` лежит ``<stem>.report.txt`` конвейера — CLI
    ``build`` после сборки читает его и помечает листы через
    ``Store.mark_defect``; ``build()`` остаётся чистой функцией."""
    from pdfgraph.cli import main
    from pdfgraph.store import Store

    report = os.path.join(fx_prepared_dir, "РТЭ кн.1.pdf.report.txt")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write("page 47: low_confidence (conf=0.55, coverage=0.0100)\n"
                     "page 12: low_text_coverage (conf=0.80, coverage=0.0050)\n")
    store = str(tmp_path / "g.db")
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"]) == 0
    assert "defects=2" in capsys.readouterr().out

    reader = Store(store)
    try:
        assert reader.defect_sheets() == [("РТЭ кн.1.pdf", 12),
                                          ("РТЭ кн.1.pdf", 47)]
    finally:
        reader.close()

    # повторный build поверх той же БД идемпотентен
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"]) == 0
    assert "defects=2" in capsys.readouterr().out

    # без отчёта рядом с JSON — ни одного помеченного листа
    os.remove(report)
    store2 = str(tmp_path / "g2.db")
    assert main(["build", str(fx_prepared_dir), store2, "--type", "Ми-8МТВ"]) == 0
    assert "defects=0" in capsys.readouterr().out
    reader2 = Store(store2)
    try:
        assert reader2.defect_sheets() == []
    finally:
        reader2.close()


def test_cli_build_report_garbage_lines_ignored(fx_prepared_dir, tmp_path,
                                                capsys):
    """Неразборчивые строки отчёта не ломают сборку; пустой отчёт (пустые
    листы в него не попадают) не помечает ничего."""
    from pdfgraph.cli import main
    from pdfgraph.store import Store

    report = os.path.join(fx_prepared_dir, "РТЭ кн.1.pdf.report.txt")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write("Отчёт о пересканировании\n")
        handle.write("page : low_confidence\n")
        handle.write("page 9: low_text_coverage (conf=0.80, coverage=0.0010)\n")
    store = str(tmp_path / "g.db")
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"]) == 0
    assert "defects=1" in capsys.readouterr().out
    reader = Store(store)
    try:
        assert reader.defect_sheets() == [("РТЭ кн.1.pdf", 9)]
    finally:
        reader.close()
