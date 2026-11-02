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
