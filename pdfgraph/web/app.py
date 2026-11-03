# -*- coding: utf-8 -*-
"""Ф2.1: FastAPI-приложение веб-каркаса (только чтение функций поиска).

Маршруты:

- ``GET /`` — страница чата (Jinja2, три колонки: типы / чат / просмотр);
- ``POST /api/ask`` — обёртка над штатной цепочкой build_answer +
  verify_citations + _evidence_rows; никакого нового ответа; JSON несёт
  status (answer/refusal/clarify), exit_code (0/1/3), claims, citations
  (с link и doc-полями Ф0.3в), neighbors, applicability_warning,
  options при уточнении;
- ``GET /doc/{document}/page/{page}`` — FileResponse исходного PDF;
  файл ищется правилом GUI (:mod:`pdfgraph.gui.pdf_opener`, pure logic
  без Qt); нет папки — 404 «папка с PDF не найдена, укажите
  PDFGRAPH_PDF_DIR», нет файла — 404 с именем;
- ``GET /static/...`` — стили и локальный PDF.js 3.11.174 (legacy),
  никаких обращений к CDN в рантайме.

Каждый вопрос пишется в ``web.log`` рядом с базой: время, вопрос, исход.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from pdfgraph.answer import Answer, build_answer
from pdfgraph.cli import _base_aircraft_type, _evidence_rows
from pdfgraph.query import ROLE_LABELS
from pdfgraph.gui.pdf_opener import PAGE_OFFSET, _find_pdf_file, \
    resolve_pdf_dir
from pdfgraph.store import Store
from pdfgraph.verify import verify_citations

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

#: Подмена производственной цепочки в тестах (инъекция clarify и т.п.).
AskFn = Callable[[Store, str, str | None], Answer]

logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    """Тело POST /api/ask."""

    question: str


def _production_ask(store: Store, question: str,
                    aircraft_type: str | None) -> Answer:
    """Штатная цепочка — единственный источник ответа (Ф2.1, п. 4)."""
    return build_answer(store, question, aircraft_type)


def _log_web(store_path: str, outcome: str, question: str) -> None:
    """Аудит вопросов: web.log рядом с базой (Ф2.1, п. 6)."""
    log_path = Path(store_path).with_name("web.log")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} outcome={outcome} question={question}\n")


def _citation_rows(answer: Answer, store: Store) -> list[dict]:
    """Цитаты с метаданными документа и ссылками на открытие листа.

    ``link`` ведёт на граф-лист (как в CLI), ``viewer_url`` — на
    локальный PDF.js-viewer с физической страницей (graph + PAGE_OFFSET,
    единый источник смещения — pdfgraph.gui.pdf_opener).
    """
    rows = _evidence_rows(answer, verify_citations(answer, store), store)
    citations: list[dict] = []
    for row in rows:
        link = f"/doc/{quote(row['document'])}/page/{row['page']}"
        viewer = (f"/static/pdfjs/viewer.html?file={quote(link)}"
                  f"&page={max(1, row['page'] + PAGE_OFFSET)}")
        # Ф1.9б: подпись роли документа результата карты.
        fragment = next(
            (f for f in answer.fragments
             if f.document == row["document"] and f.page == row["page"]),
            None)
        role = ROLE_LABELS.get(getattr(fragment, "role", ""), "") \
            if fragment is not None else ""
        citations.append({
            "document": row["document"],
            "page": row["page"],
            "ok": row["ok"],
            "reason": row["reason"],
            "system_code": row["system_code"],
            "role": role,
            "doc_layer": row["doc_layer"],
            "doc_applicability": row["doc_applicability"],
            "doc_revision": row["doc_revision"],
            "link": link,
            "full_url": link + "?view=full",
            "viewer_url": viewer,
        })
    return citations


def _status_and_code(answer: Answer) -> tuple[str, int]:
    """Три исхода карточки: answer / refusal / clarify и код 0/1/3."""
    if answer.clarification and not answer.refusal:
        return "clarify", 3
    if answer.refusal:
        return "refusal", 1
    return "answer", 0


def create_app(store_path: str, pdf_dir: str | None = None,
               ask_fn: AskFn | None = None) -> FastAPI:
    """Фабрика приложения; store открывается на каждый запрос.

    ``ask_fn`` — точка инъекции для тестов (clarify-сценарии); по
    умолчанию — производственная цепочка без новой логики.
    """
    app = FastAPI(title="pdfgraph — ИИ-инженер", docs_url=None, redoc_url=None)
    ask = ask_fn or _production_ask
    resolved_pdf_dir = (Path(pdf_dir) if pdf_dir else resolve_pdf_dir())

    @app.get("/")
    def index(request: Request):  # noqa: ANN202 — Jinja2-обработчик
        connection_store = Store(store_path)
        try:
            aircraft_types = [row[0] for row in
                              connection_store._connection.execute(
                                  "SELECT DISTINCT aircraft_type FROM pages "
                                  "WHERE TRIM(COALESCE(aircraft_type,'')) "
                                  "<> '' ORDER BY 1")]
        finally:
            connection_store.close()
        return templates.TemplateResponse(
            request, "index.html",
            {"aircraft_types": aircraft_types})

    @app.post("/api/ask")
    def api_ask(body: AskRequest) -> dict:  # noqa: ANN202 — обёртка
        started = time.perf_counter()
        store = Store(store_path)
        try:
            aircraft_type = _base_aircraft_type(store)
            answer = ask(store, body.question, aircraft_type)
            checks_ok = all(check.ok for check in
                            verify_citations(answer, store))
            citations = _citation_rows(answer, store)
            status, exit_code = _status_and_code(answer)
            options = []
            if answer.clarification and not answer.refusal:
                options = [dict(option) for option
                           in answer.clarification.get("options", [])]
            payload = {
                "status": status,
                "exit_code": exit_code,
                "reason": answer.reason,
                "verified": checks_ok and not answer.refusal,
                "claims": [
                    {"text": claim.answer_text or claim.text}
                    for claim in answer.claims],
                "citations": citations,
                "neighbors": list(answer.neighbors),
                "techcard_missing": list(getattr(
                    answer, "techcard_missing", [])),
                "techcard_more": int(getattr(answer, "techcard_more", 0)),
                # Ф2.5: карточки карт для отображения (не цитаты).
                "techcard": [dataclasses.asdict(card)
                             for card in getattr(answer, "techcard", [])],
                "applicability_warning": answer.applicability_warning,
                "options": options,
            }
        finally:
            store.close()
        _log_web(store_path, status, body.question)
        logger.debug("ask done in %.1f ms",
                     (time.perf_counter() - started) * 1000)
        return payload

    @app.get("/doc/{document:path}/page/{page:int}")
    def doc_page(request: Request, document: str, page: int,
                 view: str = ""):
        folder = resolved_pdf_dir
        if folder is None or not Path(folder).is_dir():
            raise HTTPException(
                status_code=404,
                detail="папка с PDF не найдена, укажите PDFGRAPH_PDF_DIR")
        pdf_file = _find_pdf_file(Path(folder), document)
        if pdf_file is None:
            raise HTTPException(status_code=404,
                                detail=f"PDF-файл не найден: {document}")
        # Ф2.2: view=full — полноэкранный вьюер (масштаб/прокрутка/поиск/
        # поворот); граф-лист → физическая страница через PAGE_OFFSET
        # (лист 38 базы = страница 39 вьюера). Без параметра — сырой PDF.
        if view == "full":
            raw_url = f"/doc/{quote(document)}/page/{page}"
            return templates.TemplateResponse(
                request, "full_view.html",
                {"file_url": raw_url, "page": page + PAGE_OFFSET,
                 "document": document, "graph_page": page})
        return FileResponse(pdf_file, media_type="application/pdf",
                            filename=Path(pdf_file).name)

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")),
              name="static")
    return app
