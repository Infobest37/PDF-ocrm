# -*- coding: utf-8 -*-
"""Ф2.1: запуск веб-каркаса — ``python -m pdfgraph.web``.

Порт: ``PDFGRAPH_WEB_PORT`` (по умолчанию 8000), адрес 127.0.0.1.
База: ``--db`` (по умолчанию graph.db рядом с запуском). Папка PDF:
``--pdf-dir`` > ``PDFGRAPH_PDF_DIR`` > дефолт GUI — тот же приоритет,
что в :mod:`pdfgraph.gui.pdf_opener`.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from pdfgraph.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    """Собрать приложение и поднять uvicorn на 127.0.0.1."""
    parser = argparse.ArgumentParser(
        prog="python -m pdfgraph.web",
        description="Web scaffold: chat, /api/ask, PDF.js sheet viewer.")
    parser.add_argument("--db", default="graph.db",
                        help="SQLite graph file (default: graph.db).")
    parser.add_argument(
        "--port", type=int,
        default=int(os.environ.get("PDFGRAPH_WEB_PORT", "8000")),
        help="Port (default: PDFGRAPH_WEB_PORT or 8000).")
    parser.add_argument(
        "--pdf-dir", default=None,
        help="Folder of the source PDFs (default: PDFGRAPH_PDF_DIR, "
             "then the GUI default folder).")
    args = parser.parse_args(argv)
    app = create_app(args.db, pdf_dir=args.pdf_dir)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
