# -*- coding: utf-8 -*-
"""Ф2.11: журнал промахов — /api/feedback → feedback.jsonl, feedback-list,
errors.log для необработанных исключений.

Фикстура — канонический граф conftest (РТЭ кн.1) как в test_web_f21.
Поиск и база не меняются.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import FX_DOCUMENT, populate  # noqa: E402
from pdfgraph.store import Store  # noqa: E402
from pdfgraph.web.app import create_app  # noqa: E402


def make_client(tmp_path, ask_fn=None):
    """Store канонического графа + TestClient (как в test_web_f21)."""
    from fastapi.testclient import TestClient

    store_path = tmp_path / "graph.db"
    store = Store(str(store_path))
    populate(store)
    store.close()
    app = create_app(str(store_path), ask_fn=ask_fn)
    return TestClient(app), str(store_path)


def _read_feedback(store_path: str) -> list[dict]:
    path = Path(store_path).with_name("feedback.jsonl")
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- /api/feedback → feedback.jsonl ------------------------------------------


def test_feedback_write_all_fields(tmp_path):
    client, store_path = make_client(tmp_path)
    response = client.post("/api/feedback", json={
        "question": "какое давление в двигателе ТВ3-117",
        "status": "answer",
        "sheets": [{"document": FX_DOCUMENT, "page": 47}],
        "correct_sheet": f"{FX_DOCUMENT} л. 12",
        "comment": "норма на другом листе",
    })
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    records = _read_feedback(store_path)
    assert len(records) == 1
    record = records[0]
    assert record["question"] == "какое давление в двигателе ТВ3-117"
    assert record["status"] == "answer"
    assert record["sheets"] == [{"document": FX_DOCUMENT, "page": 47}]
    # top_documents — документы выдачи без листов (решение владельца).
    assert record["top_documents"] == [FX_DOCUMENT]
    assert record["correct_sheet"] == f"{FX_DOCUMENT} л. 12"
    assert record["comment"] == "норма на другом листе"
    assert record["ts"]  # время есть


def test_feedback_top_documents_deduplicated(tmp_path):
    client, store_path = make_client(tmp_path)
    client.post("/api/feedback", json={
        "question": "вопрос",
        "status": "answer",
        "sheets": [{"document": "А.pdf", "page": 1},
                   {"document": "Б.pdf", "page": 2},
                   {"document": "А.pdf", "page": 3}],
    })
    record = _read_feedback(store_path)[0]
    assert record["top_documents"] == ["А.pdf", "Б.pdf"]


def test_feedback_refusal_empty_sheets(tmp_path):
    """Отказ: sheets пустой, top_documents пустой — запись всё равно есть."""
    client, store_path = make_client(tmp_path)
    response = client.post("/api/feedback", json={
        "question": "вопрос про узел",
        "status": "refusal",
        "sheets": [],
        "correct_sheet": "Другой.pdf л. 5",
        "comment": None,
    })
    assert response.status_code == 200
    record = _read_feedback(store_path)[0]
    assert record["status"] == "refusal"
    assert record["sheets"] == [] and record["top_documents"] == []
    assert record["correct_sheet"] == "Другой.pdf л. 5"
    assert record["comment"] is None


def test_feedback_appends_lines(tmp_path):
    client, store_path = make_client(tmp_path)
    for _ in range(2):
        client.post("/api/feedback", json={
            "question": "один и тот же", "status": "answer", "sheets": []})
    assert len(_read_feedback(store_path)) == 2


# --- feedback-list ------------------------------------------------------------


def test_feedback_list_text_summary(tmp_path, capsys):
    client, store_path = make_client(tmp_path)
    client.post("/api/feedback", json={
        "question": "вопрос А", "status": "answer", "sheets": [],
        "correct_sheet": "А.pdf л. 1"})
    client.post("/api/feedback", json={
        "question": "вопрос А", "status": "answer", "sheets": []})
    client.post("/api/feedback", json={
        "question": "вопрос Б", "status": "refusal", "sheets": []})
    from pdfgraph.cli import main
    assert main(["feedback-list", store_path]) == 0
    out = capsys.readouterr().out
    assert "Записей: 3" in out
    assert "С указанным правильным листом: 1" in out
    assert "Без правильного листа: 2" in out
    assert "2 × вопрос А" in out
    assert "1 × вопрос Б" in out


def test_feedback_list_json(tmp_path, capsys):
    client, store_path = make_client(tmp_path)
    client.post("/api/feedback", json={
        "question": "вопрос А", "status": "answer", "sheets": []})
    from pdfgraph.cli import main
    assert main(["feedback-list", store_path, "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "total": 1, "with_correct_sheet": 0, "without_correct_sheet": 1,
        "questions": {"вопрос А": 1},
    }


def test_feedback_list_empty_file(tmp_path, capsys):
    """Журнала нет — нулевая сводка, код 0."""
    from pdfgraph.cli import main
    assert main(["feedback-list", str(tmp_path / "graph.db"), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total"] == 0


# --- errors.log ----------------------------------------------------------------


def test_unhandled_error_logged_and_server_alive(tmp_path):
    client, store_path = make_client(
        tmp_path, ask_fn=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("искусственная ошибка")))
    from fastapi.testclient import TestClient
    strict = TestClient(client.app, raise_server_exceptions=False)
    response = strict.post("/api/ask", json={"question": "проверка ошибки"})
    assert response.status_code == 500
    errors_path = Path(store_path).with_name("errors.log")
    content = errors_path.read_text(encoding="utf-8")
    assert "route=/api/ask" in content
    assert "question=проверка ошибки" in content
    assert "RuntimeError" in content and "искусственная ошибка" in content
    # Сервер продолжает работать: следующий запрос успешен.
    assert strict.get("/").status_code == 200


def test_ask_without_error_writes_no_errors_log(tmp_path):
    from fastapi.testclient import TestClient
    from pdfgraph.answer import Answer, Claim, Fragment

    def fake_ask(store, question, aircraft_type):
        fragment = Fragment(document=FX_DOCUMENT, page=47,
                            system_code="023.00.00", subsystem_code="",
                            snippet="текст", char_start=0, char_end=5,
                            score=1.0, kind="text")
        return Answer(question=question,
                      claims=[Claim(text="текст", evidence=[0])],
                      fragments=[fragment])

    client, store_path = make_client(tmp_path, ask_fn=fake_ask)
    strict = TestClient(client.app, raise_server_exceptions=False)
    assert strict.post("/api/ask",
                       json={"question": "вопрос"}).status_code == 200
    assert not Path(store_path).with_name("errors.log").exists()
