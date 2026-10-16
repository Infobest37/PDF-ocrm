"""Приёмочный тест задачи 2.7: «граф точно работает».

Сквозной прогон: генерация трёх PDF (``graph_corpus``) → реальный конвейер
OCR ``pdfprepare`` → сборка графа ``pdfgraph`` (через CLI ``build``) →
ответы на «золотые» вопросы с обязательной машинной проверкой цитат.
Тесты и вопросы перенесены дословно из брифа; ожидание документов — базовые
имена файлов (ключ документа в графе = basename, ``pdfgraph/build.py``).
Обозначение детали в Q2 заменено на чисто цифровое ``869309.001-03``
(рулинг ревью-раунда 1: Tesseract rus+eng кириллические ``МТ`` систематически
выдаёт латиницей ``MT``, кириллический запрос через реальный OCR недостижим,
а цифровой лист обходил бы OCR; шаблон ``_looks_like_part_number`` такое
обозначение принимает — прецедент из реальных каталогов).
"""

import pytest

QUESTIONS = [
    ("023.11.30",                        {"023.11.30"}, {"techcard.pdf"}, {1}),
    ("869309.001-03",                    None,          {"techcard.pdf"}, {1}),
    ("аварийный переносной радиомаяк",   None,          {"techcard.pdf"}, {1}),
    ("рисунок 12",                        None,         {"figure.pdf"},   {1}),
    ("что сказано про ремонт двигателя", "ОТКАЗ",       set(),            set()),
]

@pytest.mark.parametrize("question,expect_code,expect_docs,expect_pages", QUESTIONS)
def test_acceptance_answers_with_verified_citations(graph_corpus, question, expect_code, expect_docs, expect_pages):
    store, _ = graph_corpus
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations, all_verified
    a = build_answer(store, question)
    if expect_code == "ОТКАЗ":
        assert a.refusal is True
        return
    assert a.refusal is False
    checks = verify_citations(a, store)                 # ни одной неподтверждённой
    assert all_verified(checks) is True
    assert {f.document for f in a.fragments} & expect_docs
    assert {f.page for f in a.fragments} & expect_pages

def test_verse_every_sheet_with_code_is_reachable(graph_corpus):
    store, _ = graph_corpus
    from pdfgraph.query import search
    pages = {f.page for f in search(store, "023.11.30")}
    assert pages == {1}          # в корпусе ровно один лист этого кода

def test_blank_divider_is_not_a_defect(graph_corpus):
    store, _ = graph_corpus
    from pdfgraph.query import search
    assert search(store, "023.00.00")            # лист-разделитель в графе есть
    assert store.defect_sheets() == []           # и не помечен дефектом

def test_part_query_flows_through_ocr_table(graph_corpus):
    """Review 2.7, находка 1: деталь обязана находить лист через part-ветку —
    узел ``part`` + ребро ``has_part``, извлечённые из растровой страницы
    реальным OCR (таблица 3×3), а не из цифрового текстового слоя."""
    store, _ = graph_corpus
    from pdfgraph.query import search
    fragments = search(store, "869309.001-03")
    assert fragments
    assert all(f.kind == "part" for f in fragments)
    assert {(f.document, f.page) for f in fragments} == {("techcard.pdf", 1)}
    assert store.sheets_with_part("869309.001-03") == [
        ("techcard.pdf", 1, "023.00.00", "023.11.30")]
