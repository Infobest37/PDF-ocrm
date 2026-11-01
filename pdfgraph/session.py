# -*- coding: utf-8 -*-
"""Память диалога ask: файл сессии рядом с графом (карточка Ф1.4).

``--context <id>`` включает диалог: ответ сливается с темой последнего
уточнения (файл ``dialog-<id>.json`` рядом с графом), в файле держат
последние :data:`MAX_TURNS` реплики без ``fragment_ids`` (память сессии —
не журнал). Третье уточнение подряд (счётчик сбрасывается ответом или
отказом) команда ``ask`` превращает в детерминированный отказ о «лимите
уточнений» — та же семантика, что в GUI (:mod:`pdfgraph.conversation`,
``max_clarification_rounds = 2``), только без GUI.
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field

#: Допустимый идентификатор диалога (имя файла ``dialog-<id>.json``).
CONTEXT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
#: Сколько последних реплик диалога держится в файле сессии.
MAX_TURNS = 3
#: Сколько уточняющих вопросов подряд допускается до отказа.
MAX_CONSECUTIVE_CLARIFIES = 2


@dataclass
class Turn:
    """Одна реплика диалога: вопрос и его исход."""

    question: str
    outcome: str
    reason: str = ""
    options: list[dict] = field(default_factory=list)


@dataclass
class Session:
    """Состояние диалога одного ``--context``."""

    context_id: str
    store_path: str
    turns: list[Turn] = field(default_factory=list)

    def clarify_limit_reached(self) -> bool:
        """Истина, когда хвост — :data:`MAX_CONSECUTIVE_CLARIFIES` уточнений."""
        tail = self.turns[-MAX_CONSECUTIVE_CLARIFIES:]
        return (len(tail) == MAX_CONSECUTIVE_CLARIFIES
                and all(turn.outcome == "clarify" for turn in tail))

    def refine(self, question: str) -> str:
        """Ответ, слитый с темой последнего уточнения (семантика GUI).

        Без уточнения в хвосте — вопрос без изменений. Ответ-число,
        совпавший с вариантом, дополняет тему ``query_terms`` того
        варианта (дубли в теме не плодятся; у варианта без ``query_terms``
        — его ярлык). Свободный текст дописывается как есть; «Не то:»
        начинает новую тему без слияния.
        """
        if not self.turns or self.turns[-1].outcome != "clarify":
            return question
        last = self.turns[-1]
        reply = question.strip()
        if not reply or reply.casefold().startswith("не то:"):
            return question
        matched = [option for option in last.options
                   if reply == str(option.get("id", ""))]
        if not matched:
            return f"{last.question} {reply}"
        parts = [last.question]
        terms = [str(term).strip()
                 for term in matched[0].get("query_terms", [])]
        for term in terms:
            if term and term.casefold() not in last.question.casefold():
                parts.append(term)
        if not terms:
            # Fallback GUI Conversation: ярлык варианта вместо кода.
            label = str(matched[0].get("label", "")).strip()
            if label and label.casefold() not in last.question.casefold():
                parts.append(label)
        return " ".join(parts)

    def record(self, question: str, outcome: str, reason: str = "",
               options: list[dict] | None = None) -> None:
        """Дописать реплику, оставив хвост :data:`MAX_TURNS`.

        ``fragment_ids`` вариантов не сохраняются: это индексы ответа на
        единичный вопрос, а не устойчивые ссылки (мёрдж идёт по ярлыкам
        и ``query_terms``).
        """
        stored: list[dict] = []
        for option in options or []:
            stored.append({key: value for key, value in option.items()
                           if key != "fragment_ids"})
        self.turns.append(
            Turn(question=question, outcome=outcome, reason=reason,
                 options=stored))
        del self.turns[:-MAX_TURNS]


def load_context(store_path: str, context_id: str) -> Session:
    """Сессия из ``dialog-<id>.json`` рядом с графом; нет файла — пустая.

    Неправильный идентификатор — :class:`ValueError` (CLI отвечает кодом 2):
    файл сессии не читается и поиск не запускается.
    """
    identifier = str(context_id)
    if not CONTEXT_ID_RE.fullmatch(identifier):
        raise ValueError(
            "неправильный --context: допускаются 1..64 символа "
            "[A-Za-z0-9_-]")
    session = Session(context_id=identifier, store_path=str(store_path))
    path = pathlib.Path(str(store_path)).with_name(f"dialog-{identifier}.json")
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"не читается файл сессии {path}: {exc}")
        for row in payload.get("turns", []):
            session.turns.append(Turn(
                question=str(row.get("question", "")),
                outcome=str(row.get("outcome", "")),
                reason=str(row.get("reason", "")),
                options=list(row.get("options", [])),
            ))
        del session.turns[:-MAX_TURNS]
    return session


def save_context(session: Session) -> None:
    """Записать сессию атомарно: временный файл и замена на место."""
    path = pathlib.Path(session.store_path).with_name(
        f"dialog-{session.context_id}.json")
    payload = {
        "context_id": session.context_id,
        "turns": [
            {"question": turn.question, "outcome": turn.outcome,
             "reason": turn.reason,
             "options": [dict(option) for option in turn.options]}
            for turn in session.turns
        ],
    }
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1),
        encoding="utf-8")
    tmp_path.replace(path)


__all__ = [
    "CONTEXT_ID_RE", "MAX_TURNS", "MAX_CONSECUTIVE_CLARIFIES",
    "Turn", "Session", "load_context", "save_context",
]