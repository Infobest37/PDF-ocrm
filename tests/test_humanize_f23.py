# -*- coding: utf-8 -*-
"""Ф2.3: ответ по-человечески — чистка лида и выбор предложений.

Проверяются: чистка шапок/колонтитулов (порог 25 символов не убивает
заголовки «1.1. Назначение» и строки со значимым словом вопроса),
выбор ≤3 дословных предложений по покрытию значимых слов, fallback
«фрагмент листа», интеграция в build_answer без касания verify.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdfgraph.humanize import (
    clean_page_lines,
    is_header_line,
    lead_paragraph,
    select_sentences,
)


def test_header_line_rules():
    """«Стр. 213» удаляется; «1.1. Назначение» и короткая строка со
    значимым словом вопроса — выживают."""
    assert is_header_line("Стр. 213") is True
    assert is_header_line("Авг 13/13") is True
    assert is_header_line("022.10.00") is True
    assert is_header_line("К РО") is True
    assert is_header_line("ПУЛЬТАЦИИ") is True
    assert is_header_line("1.1. Назначение") is False
    assert is_header_line("Раздел 3. Подсистема") is False
    assert is_header_line("Рис. 7. Схема") is False
    # Короткая строка со значимым словом вопроса — не шапка.
    assert is_header_line("Давление масла", question_words={"давление"}) \
        is False


def test_clean_page_lines(tmp_path):
    lines = [
        "Ми-8МТВ-1",
        "РУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ",
        "022.10.00",
        "Стр. 213",
        "Авг 13/13",
        "1.1. Назначение",
        "Аварийный радиомаяк ПАРМ-406А обеспечивает передачу сигналов "
        "на частоте 121,5 МГц в течение 48 часов после включения, что "
        "соответствует требованиям лётной годности вертолёта.",
    ]
    cleaned = clean_page_lines(lines, header_lines=set())
    assert "1.1. Назначение" in cleaned
    assert "Стр. 213" not in cleaned
    assert "022.10.00" not in cleaned
    assert "Ми-8МТВ-1" not in cleaned


def test_clean_page_lines_kills_running_header(tmp_path):
    """Колонтитул: первые две строки, совпадающие с другими листами."""
    lines = ["Ми-8МТВ-1", "РУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ",
             "Содержательное предложение про систему и её проверку."]
    cleaned = clean_page_lines(lines, header_lines={
        "Ми-8МТВ-1|РУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ"})
    assert cleaned == ["Содержательное предложение про систему и её "
                       "проверку."]


def test_select_sentences_coverage_and_order():
    question = "давление масла за редуктором на режиме малого газа"
    text = (
        "Параметры фиксируются в таблице 4. Давление масла за редуктором "
        "на режиме малого газа должно быть не менее 0,15 МПа. Проворачивание "
        "винтов запрещено. Температура масла на входе контролируется "
        "указателем. Минимальное давление масла на режиме малого газа "
        "достигается при прогретом двигателе.")
    sentences = select_sentences(question, text, limit=3)
    # Порог Ф2.3: одно совпадение — шум, «Температура масла на входе…»
    # (1 термин) не попадает.
    assert len(sentences) == 2
    # Порядок как в тексте: 2-е предложение раньше 5-го.
    positions = [text.find(sentence) for sentence in sentences]
    assert positions == sorted(positions)
    # Все предложения дословные фрагменты текста.
    assert all(sentence in text for sentence in sentences)
    # Покрытие: выбраны предложения со словами вопроса, а не «Проворачивание».
    assert all("Проворачивание" not in sentence for sentence in sentences)
    assert all("Температура масла на входе" not in sentence
               for sentence in sentences)
    # Вопрос с одним термином: одного совпадения достаточно
    # (substring-правило не матчит флексии — «масло»≠«масла»).
    sentences_one = select_sentences("давление", text, limit=3)
    assert len(sentences_one) == 2


def test_select_sentences_no_match_falls_back():
    question = "давление масла за редуктором"
    text = ("Совершенно посторонний текст без совпадений. "
            "Ещё одно предложение ни о чём.")
    sentences = select_sentences(question, text, limit=3)
    assert sentences == []


def test_lead_paragraph_fallback(tmp_path):
    from pdfgraph.store import Store

    store = Store(str(tmp_path / "graph.db"))
    try:
        text = ("Шапка 1\nСтр. 5\n\nПервый содержательный абзац листа, "
                "в котором нет совпадений по смыслу.\nВторая строка абзаца.")
        store.upsert_page("Д.pdf", 1, "Ми-8МТВ", "", "", text, "текст")
        fragment = type("F", (), {"document": "Д.pdf", "page": 1})()
        paragraph, fallback = lead_paragraph(store, "вопрос ни о чём",
                                             fragment)
        assert fallback is True
        assert "Первый содержательный абзац" in paragraph
        assert "Шапка 1" not in paragraph
        assert "Стр. 5" not in paragraph
    finally:
        store.close()


def test_build_answer_sets_answer_text_without_breaking_verify(tmp_path):
    from pdfgraph.answer import build_answer
    from pdfgraph.store import Store
    from pdfgraph.verify import all_verified, verify_citations
    from pdfprepare.normalize import normalize_for_search

    store = Store(str(tmp_path / "graph.db"))
    try:
        text = ("022.10.00\nСтр. 5\nАвг 13/13\nАварийный радиомаяк "
                "обеспечивает передачу сигналов в течение 48 часов после "
                "включения, давление масла контролируется по указателю.")
        store.upsert_page("Д.pdf", 1, "Ми-8МТВ", "023.00.00", "", text,
                          normalize_for_search(text))
        answer = build_answer(store, "давление масла радиомаяк", None)
        assert not answer.refusal
        assert answer.claims
        lead = answer.claims[0].answer_text or ""
        assert lead
        assert "Стр. 5" not in lead
        assert "Авг 13/13" not in lead
        # Цитата не тронута: verify проходит на прежнем дословном text.
        checks = verify_citations(answer, store)
        assert checks
        assert all_verified(checks)
    finally:
        store.close()
