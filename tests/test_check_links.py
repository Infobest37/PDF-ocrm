"""Тесты сверки PDF-ссылок (tools/check_links.py) — карточка 0.1б, Шаг 3.

tools/ — не пакет, поэтому модуль грузится по пути (тот же приём, что в
``test_golden_metrics.py``). Хранилище и папка PDF — временные; никаких
запусков внешних просмотрщиков (``open_pdf_at_page`` не вызывается).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

DOC_NAME = "РТЭ Ми-8МТВ-1 кн. 6 (изм. от 08.07.2025).pdf"
PAGE_TEXT = "023.11.30 РАДИОМАЯК ПАРМ-406А"


def _load_tools(name: str):
    """Загрузить модуль из ``tools/`` по пути (tools/ не пакет)."""
    path = REPO_ROOT / "tools" / name
    spec = importlib.util.spec_from_file_location("tools_" + name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pdf_dir(tmp_path: Path, created: tuple[str, ...]) -> Path:
    """Временная папка PDF с указанными файлами."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    for name in created:
        (pdf_dir / name).write_bytes(b"%PDF-1.4\n")
    return pdf_dir


@pytest.fixture
def link_store(tmp_path):
    """Хранилище с одним документом и одной страницей (закрыто после)."""
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "g.db"))
    try:
        store.upsert_page(DOC_NAME, 47, "Ми-8МТВ", "023.00.00", "023.11.30",
                          PAGE_TEXT, normalize_for_search(PAGE_TEXT))
        yield store
    finally:
        store.close()


def _main(check, tmp_path: Path, pdf_dir: Path, json_mode: bool = False):
    """Запуск CLI с фикстурным хранилищем и папкой PDF."""
    argv = ["--store", str(tmp_path / "g.db"), "--pdf-dir", str(pdf_dir)]
    if json_mode:
        argv.append("--json")
    return check.main(argv)


def test_main_all_found(tmp_path, capsys, link_store):
    """Точный файл на месте → exit 0, все имена найдены."""
    check = _load_tools("check_links.py")
    code = _main(check, tmp_path, _pdf_dir(tmp_path, (DOC_NAME,)))
    assert code == 0
    out = capsys.readouterr().out
    assert "ок" in out and DOC_NAME in out
    assert "Итого: 1/1 найдено" in out


def test_main_all_found_json(tmp_path, capsys, link_store):
    """JSON-режим: документ найден, missing пуст, путь и размер указаны."""
    check = _load_tools("check_links.py")
    pdf_dir = _pdf_dir(tmp_path, (DOC_NAME,))
    code = _main(check, tmp_path, pdf_dir, json_mode=True)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"] == []
    assert payload["pdf_dir"] == str(pdf_dir)
    row = payload["documents"][0]
    assert row == {"document": DOC_NAME, "found": True,
                   "path": str(pdf_dir / DOC_NAME), "size_bytes": 9}


def test_main_missing_file(tmp_path, capsys, link_store):
    """Файла нет → exit 1, документ в missing, печатается пометка «НЕТ»."""
    check = _load_tools("check_links.py")
    code = _main(check, tmp_path, _pdf_dir(tmp_path, ("другой.pdf",)),
                 json_mode=True)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"][0]["document"] == DOC_NAME
    assert payload["missing"][0]["found"] is False


def test_main_missing_text_tail(tmp_path, capsys, link_store):
    """Текстовый режим при бите: строка «НЕТ» + счётчик битых ссылок."""
    check = _load_tools("check_links.py")
    code = _main(check, tmp_path, _pdf_dir(tmp_path, ()))
    assert code == 1
    out = capsys.readouterr().out
    assert "НЕТ" in out and "Итого: 0/1 найдено" in out
    assert "Битые ссылки" in out and "1" in out


def test_main_nfc_casefold_match(tmp_path, capsys, link_store):
    """Casefold/NFC-скан находит файл в другой раскладке регистра."""
    check = _load_tools("check_links.py")
    pdf_dir = _pdf_dir(tmp_path, ("ртэ ми-8мтв-1 кн. 6 (изм. от 08.07.2025).pdf",))
    code = _main(check, tmp_path, pdf_dir)
    assert code == 0
    out = capsys.readouterr().out
    assert "ок" in out and DOC_NAME in out


def test_main_finds_document_without_ext(tmp_path, capsys, link_store):
    """Имя в графе без «.pdf» → резерв «+ .pdf» находит файл."""
    from pdfgraph.store import Store
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "noext.db"))
    try:
        store.upsert_page("лист 12", 1, "Ми-8МТВ", "023.00.00", "023.11.30",
                          PAGE_TEXT, normalize_for_search(PAGE_TEXT))
    finally:
        store.close()
    check = _load_tools("check_links.py")
    pdf_dir = _pdf_dir(tmp_path, ("лист 12.pdf",))
    code = check.main(["--store", str(tmp_path / "noext.db"),
                       "--pdf-dir", str(pdf_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert "ок" in out and "лист 12" in out


def test_main_no_pdf_dir(tmp_path, capsys, monkeypatch, link_store):
    """Ни --pdf-dir, ни env, ни default не существует → exit 2."""
    check = _load_tools("check_links.py")
    monkeypatch.delenv("PDFGRAPH_PDF_DIR", raising=False)
    monkeypatch.setattr("pdfgraph.gui.pdf_opener.DEFAULT_PDF_DIR",
                        tmp_path / "нет_папки")
    code = check.main(["--store", str(tmp_path / "g.db"),
                       "--pdf-dir", str(tmp_path / "нет_папки")])
    assert code == 2
    assert "папка PDF не найдена" in capsys.readouterr().err


def test_main_unreadable_store(tmp_path, capsys, link_store):
    """Хранилище не открывается → exit 2, stderr с диагностикой."""
    check = _load_tools("check_links.py")
    pdf_dir = _pdf_dir(tmp_path, (DOC_NAME,))
    code = check.main(["--store", str(tmp_path / "absent.db"),
                       "--pdf-dir", str(pdf_dir)])
    assert code == 2
    assert "хранилище не читается" in capsys.readouterr().err