"""Task 2.2: the production store must still find the plain term (control).

Uses the REAL ``graph.db`` in the repository root (read-only); skipped when
the file is not there. This test is expected to PASS both before and after
the Stage-2 edits — it guards the working FTS path against regressions.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_DB = REPO_ROOT / "graph.db"

pytestmark = pytest.mark.skipif(
    not PROD_DB.is_file(), reason="нет graph.db в корне репо")


def test_query_known_term():
    """FTS branch: raw term 'двигатель' returns at least one text hit."""
    from pdfgraph.query import search
    from pdfgraph.store import Store

    store = Store(str(PROD_DB))
    try:
        hits = search(store, "двигатель")
        assert len(hits) >= 1
        assert all(hit.kind == "text" for hit in hits)
    finally:
        store.close()