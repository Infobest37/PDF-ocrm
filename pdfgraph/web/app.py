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

Ф2.11: журнал промахов — ``POST /api/feedback`` дописывает строку JSON в
``feedback.jsonl`` рядом с базой (вопрос, исход, выданные листы,
top_documents, правильный лист, комментарий); необработанные исключения
маршрутов пишутся в ``errors.log`` рядом с базой (время, маршрут,
вопрос если был, тип и текст ошибки), сервер продолжает работать.
Поиск и база при этом не меняются.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from pdfgraph.answer import Answer, build_answer
from pdfgraph.cli import _base_aircraft_type, _evidence_rows
from pdfgraph.humanize import (explain_display, is_explain_question,
                               value_display)
from pdfgraph.query import ROLE_LABELS
from uuid import uuid4

from pdfgraph.conversation import Conversation
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
    """Тело POST /api/ask.

    Ф1.26: ``session_id``/``selected_option`` — опциональные поля сессии
    уточнений. Выбор варианта (``selected_option``) резолвится в
    ``Conversation.resolve_clarification_reply`` — второй запрос ищет
    дальше по обогащённому запросу, а не начинается заново (цикл
    уточнений в вебе). Поля аддитивные: старые клиенты не ломаются.
    """

    question: str
    session_id: str = ""
    selected_option: str = ""


class SheetRef(BaseModel):
    """Выданный лист: документ и номер листа (Ф2.11)."""

    document: str
    page: int


class FeedbackRequest(BaseModel):
    """Тело POST /api/feedback (Ф2.11): промах, отмеченный инженером.

    ``sheets`` — выданные листы (для refusal/clarify пустой);
    ``correct_sheet`` и ``comment`` необязательны.
    """

    question: str
    status: str
    sheets: list[SheetRef] = []
    correct_sheet: str | None = None
    comment: str | None = None


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


def _append_feedback(store_path: str, entry: dict) -> None:
    """Журнал промахов: строка JSON в feedback.jsonl рядом с базой (Ф2.11).

    Поиск и база не меняются — журнал только для разбора промахов.
    """
    log_path = Path(store_path).with_name("feedback.jsonl")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def sheet_links(document: str, page: int) -> dict[str, int | str]:
    """Единая сборка ссылок на лист PDF (Ф2.9, п. 1).

    Все ссылки на PDF — цитаты, карточка техкарты, соседние листы —
    строятся только здесь. ``link`` — граф-лист (сырой PDF), ``full_url``
    — тот же граф-лист с ``?view=full`` (смещение по-прежнему применяется
    на сервере, контракт Ф2.2 неизменен), ``viewer_url`` — PDF.js-viewer
    с физической страницей, ``physical_page`` — физическая страница
    вьюера (graph + PAGE_OFFSET; единый источник смещения —
    pdfgraph.gui.pdf_opener). Подпись кнопки открытия строится из
    ``physical_page`` (решение владельца Ф2.9: адрес — граф-лист 372,
    подпись — «стр. 373»).
    """
    link = f"/doc/{quote(document)}/page/{page}"
    physical = max(1, page + PAGE_OFFSET)
    return {
        "link": link,
        "full_url": link + "?view=full",
        "viewer_url": (f"/static/pdfjs/viewer.html?file={quote(link)}"
                       f"&page={physical}"),
        "physical_page": physical,
    }


def _citation_rows(answer: Answer, store: Store) -> list[dict]:
    """Цитаты с метаданными документа и ссылками на открытие листа.

    Ссылки строит :func:`sheet_links` (Ф2.9: единая точка сборки).
    """
    rows = _evidence_rows(answer, verify_citations(answer, store), store)
    citations: list[dict] = []
    for row in rows:
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
            **sheet_links(row["document"], row["page"]),
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
    # Ф1.26: сессии уточнений в памяти процесса {session_id: Conversation}.
    # Веб не имел сессий вовсе — выбор варианта уходил как новый вопрос
    # и уточнение повторялось бесконечно (цикл). GUI вёл Conversation,
    # веб теперь ведёт так же.
    sessions: dict[str, Conversation] = {}

    @app.exception_handler(Exception)
    async def log_unhandled(request: Request, exc: Exception):
        """Ф2.11: необработанные исключения — errors.log рядом с базой.

        Время, маршрут, вопрос если был (request.state.question ставится
        в /api/ask), тип и текст ошибки; сервер продолжает работать —
        клиенту уходит 500.
        """
        log_path = Path(store_path).with_name("errors.log")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        question = getattr(request.state, "question", None)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} route={request.url.path} "
                         f"question={question if question is not None else '—'}"
                         f" error={type(exc).__name__}: {exc}\n")
        return JSONResponse(status_code=500,
                            content={"detail": "внутренняя ошибка сервера"})

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

    @app.post("/api/feedback")
    def api_feedback(body: FeedbackRequest) -> dict:  # noqa: ANN202
        """Ф2.11: отметить промах — строка JSON в feedback.jsonl."""
        top_documents: list[str] = []
        for sheet in body.sheets:
            if sheet.document not in top_documents:
                top_documents.append(sheet.document)
        entry = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question": body.question,
            "status": body.status,
            "sheets": [{"document": sheet.document, "page": sheet.page}
                       for sheet in body.sheets],
            "top_documents": top_documents,
            "correct_sheet": body.correct_sheet or None,
            "comment": body.comment or None,
        }
        _append_feedback(store_path, entry)
        return {"ok": True}

    @app.post("/api/ask")
    def api_ask(request: Request, body: AskRequest) -> dict:  # noqa: ANN202
        started = time.perf_counter()
        store = Store(store_path)
        try:
            request.state.question = body.question
            aircraft_type = _base_aircraft_type(store)
            # Ф1.26: сессия уточнений. Новая (без session_id) — begin_
            # question; существующая — та же Conversation; выбор варианта
            # (selected_option) резолвится и ищется дальше.
            session_id = body.session_id or uuid4().hex
            conversation = sessions.setdefault(session_id, Conversation())
            query = body.question
            if body.selected_option:
                conversation.add_user(body.selected_option)
                resolved = conversation.resolve_clarification_reply(
                    body.selected_option)
                if resolved is not None:
                    query = resolved["query"]
            elif not body.session_id:
                conversation.begin_question(query)
            answer = ask(store, query, aircraft_type)
            checks_ok = all(check.ok for check in
                            verify_citations(answer, store))
            citations = _citation_rows(answer, store)
            status, exit_code = _status_and_code(answer)
            options = []
            if answer.clarification and not answer.refusal:
                options = [dict(option) for option
                           in answer.clarification.get("options", [])]
                # Ф1.26: сохранить pending в сессии — следующий запрос
                # с selected_option продолжит поиск, а не начнёт заново.
                conversation.add_clarification(
                    answer.clarification.get("question", ""),
                    options=options, fragments=answer.fragments)
            payload = {
                "status": status,
                "exit_code": exit_code,
                "reason": answer.reason,
                "session_id": session_id,
                "verified": checks_ok and not answer.refusal,
                "claims": [
                    {"text": claim.answer_text or claim.text}
                    for claim in answer.claims],
                "citations": citations,
                "techcard_missing": list(getattr(
                    answer, "techcard_missing", [])),
                "techcard_more": int(getattr(answer, "techcard_more", 0)),
                # Ф2.5: карточки карт для отображения (не цитаты).
                # Ф2.9: ссылки листов карточки — sheet_links (единая
                # точка сборки), подпись кнопки — physical_page.
                "techcard": [
                    {**dataclasses.asdict(card),
                     "sheets": [{**dataclasses.asdict(sheet),
                                 **sheet_links(sheet.document, sheet.page)}
                                for sheet in card.sheets]}
                    for card in getattr(answer, "techcard", [])],
                # Ф2.9: соседи получают ссылки той же функцией.
                "neighbors": [{**neighbor,
                               "full_url": sheet_links(
                                   neighbor["document"],
                                   neighbor["page"])["full_url"]}
                              for neighbor in answer.neighbors],
                "applicability_warning": answer.applicability_warning,
                # Ф2.10: статус «нормы не нашёл» для вопроса о параметре
                # (строкой, не цитатой; при отказе дублирует reason).
                "param_norm_missing": getattr(
                    answer, "param_norm_missing", ""),
                # Ф1.29: состояние ответа «full»/«mention»/"" — интерфейс
                # помечает «найдено упоминание, значение не указано».
                "answer_state": getattr(answer, "answer_state", ""),
                # Г1: показ вопрос-значений — пункты норм, источник с
                # двумя нумерациями, дословная цитата для свёрнутого
                # блока. Г2: ветку показа выбирают explain-маркеры
                # вопроса (те же ключи + note). None — не тот и не
                # другой (старый рендер).
                "value_display": (
                    None if answer.refusal
                    else (explain_display(store, answer)
                          if is_explain_question(answer.question)
                          else value_display(store, answer))),
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
