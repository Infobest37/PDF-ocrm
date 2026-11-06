# -*- coding: utf-8 -*-
"""Ф2.4: мобильный вид и непрерывная прокрутка PDF.

Писались ДО правок (ожидался RED). Контракты:

- index.html отдаётся с метатегом viewport;
- style.css содержит медиазапрос `@media (max-width: 768px)` с
  однаколонным `.screen`, тач-таргетами 44px и шрифтом 16px;
- app.js открывает листы через `?view=full` (fallback без
  одностраничного viewer.html);
- контракт ссылок не тронут (Ф2.9): граф-лист 47 → viewer_url `page=48`;
  граф-лист 38 → `?view=full` открывает `const PAGE = 39` (Ф2.2);
- full_view.html — непрерывная прокрутка (renderWindow по текущему
  центру), старт с запрошенного листа.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import FX_DOCUMENT  # noqa: E402
from tests.test_web_f21 import make_client  # noqa: E402

WEB_DIR = Path(REPO_ROOT) / "pdfgraph" / "web"
STYLE = (WEB_DIR / "static" / "style.css").read_text(encoding="utf-8")
APP_JS = (WEB_DIR / "static" / "app.js").read_text(encoding="utf-8")


# --- мобильная вёрстка -------------------------------------------------------


def test_index_has_viewport_meta(tmp_path):
    """Страница отдаётся с метатегом viewport."""
    client, _ = make_client(tmp_path)
    html = client.get("/").text
    assert '<meta name="viewport"' in html


def test_style_has_mobile_media_query():
    """Медиазапрос мобильной ширины: одна колонка, без горизонтали."""
    assert "@media (max-width: 768px)" in STYLE
    # Внутри медиаблока — однаколонный grid и защита от горизонталей.
    media_start = STYLE.index("@media (max-width: 768px)")
    media_block = STYLE[media_start:]
    assert "grid-template-columns: 1fr" in media_block
    assert "min-width: 0" in media_block


def test_style_touch_targets_44px():
    """Тач-таргеты: кнопки и ссылки-действия не ниже 44px, шрифт 16px."""
    media_start = STYLE.index("@media (max-width: 768px)")
    media_block = STYLE[media_start:]
    assert "min-height: 44px" in media_block
    assert "font-size: 16px" in media_block
    for selector in (".ask-send", ".clarify .option", ".feedback-toggle",
                     ".type-option", ".citation a", ".techcard-open"):
        assert selector in media_block, selector


def test_style_long_words_wrap():
    """Длинные ссылки/номера карт переносятся, а не вылезают за экран."""
    media_start = STYLE.index("@media (max-width: 768px)")
    media_block = STYLE[media_start:]
    assert "overflow-wrap: anywhere" in media_block
    assert "word-break: break-word" in media_block


# --- ссылки на просмотр -------------------------------------------------------


def test_app_js_prefers_full_view():
    """Fallback ссылок — без одностраничного viewer.html (Ф2.4)."""
    assert "citation.viewer_url" not in APP_JS


def test_link_keeps_physical_page_contract(tmp_path):
    """Контракт Ф2.9/Ф2.2 цел: граф-лист 47 → viewer_url page=48;
    граф-лист 38 → ?view=full открывает const PAGE = 39."""
    client, _ = make_client(tmp_path)
    from pdfgraph.web.app import sheet_links

    links = sheet_links(FX_DOCUMENT, 47)
    assert links["physical_page"] == 48          # PAGE_OFFSET = 1
    assert "page=48" in links["viewer_url"]
    html = client.get(f"/doc/{FX_DOCUMENT}/page/38?view=full").text
    assert "const PAGE = 39;" in html


def test_full_view_is_continuous(tmp_path):
    """full-вьюер листается прокруткой: рендер окна вокруг текущего листа."""
    client, _ = make_client(tmp_path)
    html = client.get(f"/doc/{FX_DOCUMENT}/page/38?view=full").text
    for marker in ("renderWindow", "updatePosition", "container.addEventListener"
                   '("scroll"'):
        assert marker in html, marker
