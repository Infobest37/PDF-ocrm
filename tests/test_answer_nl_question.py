"""Task 2.1: a natural-language question must produce visible claims.

The question is conversational («какие подсистемы у двигателя»): the AND
pass misses (no stemming), the tolerant OR retry finds sheets, and after
the Stage-2 score normalization the answer must carry claims instead of a
refusal. If a refusal still happens it must be an explicit one — non-empty
``reason``.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_DB = REPO_ROOT / "graph.db"

pytestmark = pytest.mark.skipif(
    not PROD_DB.is_file(), reason="нет graph.db в корне репо")


def test_answer_nl_question():
    from pdfgraph.answer import build_answer
    from pdfgraph.store import Store

    store = Store(str(PROD_DB))
    try:
        answer = build_answer(store, "какие подсистемы у двигателя")
        if answer.refusal:
            # Явное «не найдено» допускается, но только честное: с причиной.
            assert answer.reason, "refusal обязан иметь причину"
        else:
            assert answer.claims, "непустой ответ обязан иметь claims"
    finally:
        store.close()


def test_answer_nl_question_finds_engine_after_fix():
    """Stricter green-state expectation: OR-retry hits pass min_score."""
    from pdfgraph.answer import build_answer
    from pdfgraph.store import Store

    store = Store(str(PROD_DB))
    try:
        answer = build_answer(store, "какие подсистемы у двигателя")
        assert answer.claims, (
            f"refusal={answer.refusal!r} reason={answer.reason!r} "
            f"fragments={len(answer.fragments)}")
    finally:
        store.close()