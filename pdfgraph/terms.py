# -*- coding: utf-8 -*-
"""Ф1.32: словарь алиасов и подстановка обозначения расшифровкой.

Проблема: карта 021.40.00h содержит норму «минимально допустимая
высота щеток равна 13,5 мм» (РТЭ кн.3, л.64), но слова «МВ-1200» на
этом листе нет — единственное упоминание в Регламенте ч.2 л.141, где
нормы нет. Вопрос «какая минимальная высота щеток на МВ-1200» упирается
в упоминание, а норма не находится. Подстановка заменяет обозначение
расшифровкой только для ДОПОЛНИТЕЛЬНОГО лексического канала ранжирования
(:func:`pdfgraph.rag_fusion.fuse_hybrid`); сам вопрос не переписывается —
гейты, verify и humanize работают по оригиналу.

Контракты:

- файл словаря ``data/terms.toml`` читается при каждом вызове (он
  крошечный); битый TOML → ``logger.warning`` и ``None``; отсутствие
  файла → ``None`` без шума;
- :func:`expand_question` — регистронезависимая замена всех вхождений
  всех ключей одним проходом ``re.Pattern.sub`` (общий regex из
  ``re.escape`` ключей, отсортированных по длине по убыванию), без
  рекурсии: замещённый текст значения не сканируется повторно — замена
  выполняется за один проход по исходной строке; ключей в вопросе
  нет → ``None``;
- выключатель ``PDFGRAPH_TERMS`` — по умолчанию включён (рабочая
  конфигурация; решение Б9, 11.11.2026): флаг выключен → ``None`` и
  поведение байт-в-байт прежнее.

Границы: при нескольких разных ключах в одном вопросе каждый заменяется
своим вхождением за тот же один проход. Пересекающиеся ключи
(«МВ-1200» и «МВ-1200А») разрешаются длиннейшим ключом — сортировка
по длине, не порядок TOML (в словаре сейчас одна запись, коллизий нет).
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path

#: Выключатель; читается при каждом вызове, без кэша. По умолчанию
#: (переменная не задана) канал ВКЛЮЧЁН — рабочая конфигурация (Б9).
TERMS_ENV = "PDFGRAPH_TERMS"
#: Явное выключение (после strip + casefold); пустая строка и мусор —
#: включение: выключенный канал даёт ложный ответ с зелёной проверкой
#: цитат, что дороже любой опечатки в значении.
_OFF_VALUES = {"0", "false", "off", "no"}

#: Вес канала алиасов в RRF-слиянии (карточка Ф1.32): через
#: ``ranking_weights`` — не множитель к ``Fragment.score`` (скор
#: используется гейтами).
TERMS_WEIGHT = 0.7

#: Путь словаря по умолчанию: ``data/terms.toml`` относительно корня
#: проекта (родитель пакета). Внутренний атрибут — точка изоляции для
#: тестов (подмена пути), публичный контракт не меняется. В
#: frozen-сборке файл не бандлится: отсутствие → ``None``, канал не
#: добавляется.
_TERMS_PATH = str(Path(__file__).resolve().parent.parent / "data"
                  / "terms.toml")

logger = logging.getLogger(__name__)


def effective_terms_enabled() -> bool:
    """Выключатель Б9: включён по умолчанию (рабочая конфигурация).

    Выключается только явным ``PDFGRAPH_TERMS`` = «0»/«false»/«off»/«no»
    (без учёта регистра и пробелов); пустая строка и любое другое
    значение — включение.
    """
    raw = os.environ.get(TERMS_ENV, "").strip().casefold()
    return raw not in _OFF_VALUES


def _load_aliases() -> dict[str, str] | None:
    """Словарь алиасов из ``data/terms.toml``.

    Отсутствие файла → ``None`` (без лога); битый TOML →
    ``logger.warning`` и ``None``; пустая секция ``[alias]`` → ``None``
    (канал без ключей не имеет смысла). Читается при каждом вызове.
    """
    path = Path(_TERMS_PATH)
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("битый TOML словаря алиасов %s: %s", path, exc)
        return None
    aliases = data.get("alias")
    if not isinstance(aliases, dict):
        return None
    return {str(key): str(value) for key, value in aliases.items()
            if key and value}


def expand_question(question: str) -> str | None:
    """Замена обозначений расшифровками; ключей в вопросе → ``None``.

    Регистронезависимая замена всех вхождений всех ключей словаря
    (:func:`_load_aliases`) одним проходом ``re.Pattern.sub``: общий
    regex — ``re.escape`` каждого ключа (ключи содержат дефисы и могут
    содержать спецсимволы), альтернативы отсортированы по длине по
    убыванию, поэтому пересекающиеся ключи разрешаются длиннейшим.
    Замена выполняется за один проход по исходной строке — замещённый
    текст значения не сканируется повторно (без рекурсии).
    Выключенный флаг или пустой словарь → ``None``.
    """
    if not effective_terms_enabled():
        return None
    aliases = _load_aliases()
    if not aliases:
        return None
    lookup = {key.casefold(): value for key, value in aliases.items()}
    pattern = re.compile(
        "|".join(re.escape(key)
                 for key in sorted(aliases, key=len, reverse=True)),
        re.IGNORECASE)
    expanded = pattern.sub(
        lambda match: lookup[match.group(0).casefold()], question)
    if expanded == question:
        return None
    return expanded
