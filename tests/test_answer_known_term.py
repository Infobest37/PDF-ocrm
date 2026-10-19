"""Task 2.1: raw term must produce a NON-refused answer on the real store.

Stage-2 red/green marker: before the fix ``build_answer`` refuses with
reason «нет подтверждений в документах» even though ``search`` returns
10 hits — because the FTS score scale (penalty/(1+|bm25|), ~0.15 on real
long pages) sits far below ``min_score=0.35``. After the score
normalization (relative to the best hit of the branch) this must answer.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_DB = REPO_ROOT / "graph.db"

pytestmark = pytest.mark.skipif(
    not PROD_DB.is_file(), reason="нет graph.db в корне репо")


def test_answer_known_term_not_refused():
    from pdfgraph.answer import build_answer
    from pdfgraph.store import Store

    store = Store(str(PROD_DB))
    try:
        answer = build_answer(store, "двигатель")
        assert not answer.refusal, (
            f"refusal={answer.refusal!r} reason={answer.reason!r} "
            f"fragments={len(answer.fragments)}")
        assert answer.claims
    finally:
        store.close()