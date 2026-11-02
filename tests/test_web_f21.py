# -*- coding: utf-8 -*-
"""Ф2.1: веб-каркас — чат, /api/ask, PDF.js-открытие листа, web.log.

Фикстура — канонический граф conftest (РТЭ кн.1, листы 47/48/12) плюс
минимальный PDF-файл для эндпоинта /doc/…/page/N. Clarify проверяется
через инъекцию ask_fn (продакшн-цепочка clarify зависит от базы).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import json

import pytest

from conftest import FX_DOCUMENT, populate
from pdfgraph.answer import Answer, Claim, Fragment
from pdfgraph.web.app import create_app

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def make_client(tmp_path, ask_fn=None):
    """Store канонического графа + минимальный PDF + TestClient."""
    from fastapi.testclient import TestClient

    store_path = tmp_path / "graph.db"
    store = Store(str(store_path))
    populate(store)
    store.close()
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / FX_DOCUMENT).write_bytes(MINIMAL_PDF)
    app = create_app(str(store_path), pdf_dir=str(pdf_dir),
                     ask_fn=ask_fn)
    return TestClient(app), str(store_path)


from pdfgraph.store import Store  # noqa: E402  (после sys.path)


def _frag(document: str = FX_DOCUMENT, page: int = 47) -> Fragment:
    return Fragment(document=document, page=page, system_code="023.00.00",
                    subsystem_code="023.11.30", snippet="текст",
                    char_start=0, char_end=5, score=1.0, kind="text")


# --- Страница ----------------------------------------------------------------


def test_index_page_three_columns(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for marker in ("col-types", "col-chat", "col-viewer",
                   "ask-form", "ask-input"):
        assert marker in html, marker


# --- /api/ask: ответ, отказ, уточнение ---------------------------------------


def test_ask_answer_with_citation(tmp_path):
    client, store_path = make_client(tmp_path)
    response = client.post("/api/ask",
                           json={"question": "радиомаяк ПАРМ-406А"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answer"
    assert payload["exit_code"] == 0
    assert payload["citations"]
    citation = payload["citations"][0]
    assert citation["document"] == FX_DOCUMENT
    assert citation["page"] == 47
    from urllib.parse import unquote
    assert unquote(citation["link"]).endswith(
        f"/doc/{FX_DOCUMENT}/page/47")
    assert citation["ok"] is True
    # Поля документа Ф0.3в присутствуют (пусто — documents не заполнен).
    assert citation["doc_layer"] == ""
    assert "neighbors" in payload
    assert payload["applicability_warning"] == ""


def test_ask_refusal_without_open_button(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/api/ask", json={"question":
                          "инструкция по ремонту автомобилей ВАЗ"})
    payload = response.json()
    assert payload["status"] == "refusal"
    assert payload["exit_code"] == 1
    assert payload["reason"]
    assert payload["citations"] == []


def test_ask_clarify_with_options(tmp_path):
    def fake_ask(store, question, aircraft_type):
        return Answer(question=question, claims=[],
                      fragments=[_frag()],
                      clarification={
                          "decision": "clarify",
                          "question": "Уточните вариант",
                          "reason": "тема найдена, конкретики нет",
                          "options": [
                              {"id": "1", "label": "Вариант один",
                               "document": FX_DOCUMENT, "page": 47},
                          ],
                      })

    client, _ = make_client(tmp_path, ask_fn=fake_ask)
    response = client.post("/api/ask", json={"question": "давление 118.00"})
    payload = response.json()
    assert payload["status"] == "clarify"
    assert payload["exit_code"] == 3
    assert payload["options"][0]["label"] == "Вариант один"
    assert payload["options"][0]["document"] == FX_DOCUMENT


# --- /doc/…/page/N ------------------------------------------------------------


def test_doc_page_serves_pdf(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get(f"/doc/{FX_DOCUMENT}/page/47")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")


def test_doc_page_missing_file(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/doc/Несуществующий.pdf/page/1")
    assert response.status_code == 404
    assert "PDF" in response.json()["detail"]


def test_doc_page_without_pdf_dir(tmp_path):
    from fastapi.testclient import TestClient

    store_path = tmp_path / "graph.db"
    store = Store(str(store_path))
    populate(store)
    store.close()
    client = TestClient(create_app(str(store_path), pdf_dir=str(
        tmp_path / "нет_такой")))
    response = client.get(f"/doc/{FX_DOCUMENT}/page/47")
    assert response.status_code == 404
    assert "PDFGRAPH_PDF_DIR" in response.json()["detail"]


# --- web.log ------------------------------------------------------------------


def test_web_log_written(tmp_path):
    client, store_path = make_client(tmp_path)
    client.post("/api/ask", json={"question": "радиомаяк ПАРМ-406А"})
    log = Path(store_path).with_name("web.log")
    assert log.is_file()
    content = log.read_text(encoding="utf-8")
    assert "радиомаяк ПАРМ-406А" in content
    assert "answer" in content
