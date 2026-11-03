# -*- coding: utf-8 -*-
"""Ф2.2: полноэкранный просмотр PDF — маршрут view=full и нумерация.

Главный риск карточки — смещение нумерации: лист 38 базы обязан
открыться как страница 39 вьюера (#page=39). Содержимое реального
листа (пункт 2.3.16) проверяется вручную в браузере со скриншотом.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import FX_DOCUMENT
from tests.test_web_f21 import MINIMAL_PDF, make_client  # noqa: F401


def test_view_full_renders_shell_with_offset(tmp_path):
    """view=full: оболочка вьюера, #page=39 для граф-листа 38."""
    client, _ = make_client(tmp_path)
    response = client.get(f"/doc/{FX_DOCUMENT}/page/38?view=full")
    assert response.status_code == 200
    html = response.text
    assert "Назад к ответу" in html
    assert "/static/pdfjs/pdf.min.js" in html
    # Смещение: граф-лист 38 (0-based) → страница 39 вьюера.
    assert "const PAGE = 39;" in html
    # Ссылка на сырой PDF без view-параметра — источник вьюера.
    assert "/page/38" in html


def test_raw_pdf_without_view_unchanged(tmp_path):
    """Регресс Ф2.1: без view=full эндпоинт отдаёт сырой PDF."""
    client, _ = make_client(tmp_path)
    response = client.get(f"/doc/{FX_DOCUMENT}/page/47")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")


def test_full_view_no_external_urls(tmp_path):
    """Офлайн: в оболочке вьюера нет внешних URL (CDN запрещён)."""
    client, _ = make_client(tmp_path)
    html = client.get(f"/doc/{FX_DOCUMENT}/page/38?view=full").text
    assert "http://" not in html
    assert "https://" not in html


def test_citation_carries_full_url(tmp_path):
    """Цитата содержит full_url (?view=full) — его открывают кнопка и
    клик по номеру листа."""
    client, _ = make_client(tmp_path)
    payload = client.post("/api/ask",
                          json={"question": "радиомаяк ПАРМ-406А"}).json()
    citation = payload["citations"][0]
    from urllib.parse import unquote
    assert unquote(citation["full_url"]).endswith(
        f"/doc/{FX_DOCUMENT}/page/47?view=full")


# --- Ф2.9: единая функция ссылок и подпись физической страницы ---------------

def test_sheet_links_builds_all_forms_f29():
    """Ф2.9: sheet_links — единая точка сборки: адрес — граф-лист,
    viewer_url и подпись кнопки — физическая страница (PAGE_OFFSET = 1)."""
    from urllib.parse import unquote

    from pdfgraph.web.app import sheet_links

    links = sheet_links(FX_DOCUMENT, 47)
    assert unquote(links["link"]) == f"/doc/{FX_DOCUMENT}/page/47"
    assert unquote(links["full_url"]) == \
        f"/doc/{FX_DOCUMENT}/page/47?view=full"
    assert "/static/pdfjs/viewer.html?file=" in links["viewer_url"]
    assert "page=48" in links["viewer_url"]
    assert links["physical_page"] == 48


def test_techcard_sheet_link_and_physical_label_f29(tmp_path):
    """Приёмка Ф2.9: карточный лист 372 — адрес page/372, физическая
    страница для подписи кнопки — 373 («Открыть лист — стр. 373»),
    оба числа согласованы."""
    from pdfgraph.answer import Answer
    from pdfgraph.techcard_card import CardSheet, TechCard

    def ask(store, question, aircraft_type):
        card = TechCard(number="033.10.00a", title="Осмотр осветительного "
                        "оборудования",
                        sheets=[CardSheet(document=FX_DOCUMENT, page=372,
                                          role="как выполняется")])
        return Answer(question=question, claims=[], fragments=[],
                      techcard=[card])

    client, _ = make_client(tmp_path, ask_fn=ask)
    payload = client.post("/api/ask",
                          json={"question": "033.10.00a"}).json()
    sheet = payload["techcard"][0]["sheets"][0]
    # Адрес — граф-лист 372, подпись кнопки строится из physical_page 373.
    assert sheet["full_url"].endswith("/page/372?view=full")
    assert sheet["physical_page"] == 373


def test_citation_carries_physical_page_f29(tmp_path):
    """Ф2.9: цитата несёт physical_page — миниатюра и подписи строятся
    без дублирования смещения в app.js."""
    client, _ = make_client(tmp_path)
    payload = client.post("/api/ask",
                          json={"question": "радиомаяк ПАРМ-406А"}).json()
    citation = payload["citations"][0]
    assert citation["physical_page"] == citation["page"] + 1


def test_neighbors_carry_links_f29(tmp_path):
    """Ф2.9: соседние листы получают ссылки той же функцией sheet_links."""
    from pdfgraph.answer import Answer

    def ask(store, question, aircraft_type):
        return Answer(question=question, claims=[], fragments=[],
                      neighbors=[{"document": FX_DOCUMENT, "page": 12,
                                  "label": "следующий лист, возможно "
                                           "продолжение"}])

    client, _ = make_client(tmp_path, ask_fn=ask)
    payload = client.post("/api/ask", json={"question": "тест"}).json()
    neighbor = payload["neighbors"][0]
    from urllib.parse import unquote
    assert unquote(neighbor["full_url"]) == \
        f"/doc/{FX_DOCUMENT}/page/12?view=full"
