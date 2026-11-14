"""Д1а: классификатор типов листов и миграция page_kind.

Проверяются восемь содержательных типов, служебные blank/other,
приоритет по позиции в шапке, отсечение колонтитула, разрыв
«ТЕХНОЛОГ ИЧЕСКАЯ», однострочная книга (окно 400 символов) и
аддитивная миграция схемы 7→8 без изменения числа строк.
"""

from __future__ import annotations

import sqlite3

import pytest

from pdfgraph.page_kind import (
    ALL_KINDS,
    HEADER_CHARS,
    PAGE_KINDS,
    PAGE_KIND_LABELS,
    SPECIAL_KINDS,
    classify_page,
    page_kind_label,
)
from pdfgraph.store import SCHEMA_VERSION, Store


def test_kinds_and_special_statuses():
    """Содержательные типы плюс blank/service/other; у всех есть подпись."""
    assert len(PAGE_KINDS) == 9
    assert SPECIAL_KINDS == ("blank", "service", "other")
    assert len(ALL_KINDS) == 12
    assert set(ALL_KINDS) == set(PAGE_KIND_LABELS)
    assert page_kind_label("purpose") == "описание назначения"
    assert page_kind_label("unknown-kind") == "unknown-kind"


@pytest.mark.parametrize("text, expected", [
    ("МИ-8МТВ-1\nСИСТЕМА ОБОГРЕВА - ОПИСАНИЕ И РАБОТА\n"
     "1. Общие сведения", "purpose"),
    ("1.1. Назначение\nГенератор предназначен для питания",
     "purpose"),
    ("ОБЩИЕ СВЕДЕНИЯ\n1. Технические данные", "purpose"),
    ("СОСТАВ\n1. Корпус 2. Крышка", "purpose"),
    ("УСТРОЙСТВО И РАБОТА\n1. Общие сведения", "purpose"),
    ("4. Описание работы\nКорпус предназначен", "purpose"),
    ("РАБОТА СИСТЕМЫ\n1. Принцип работы", "purpose"),
    ("ПРИНЦИП РАБОТЫ\n1. Общие сведения", "purpose"),
    ("ТЕХНОЛОГИЧЕСКАЯ КАРТА № 5\n"
     "Наименование работы: ПРОВЕРКА ВСУ", "techcard"),
    ("Содержание операции и технические требования\n"
     "Работы, выполняемые при отклонениях", "techcard"),
    ("Работы, выполняемые при отклонениях от ТТ\n"
     "Наименование работы: Осмотр", "techcard"),
    ("ОТЫСКАНИЕ И УСТРАНЕНИЕ НЕИСПРАВНОСТЕЙ\n"
     "Неисправность Возможная причина Способ устранения", "faults"),
    ("Каталог дефектов Ми-8МТВ\nДЕФЕКТ", "faults"),
    ("ТРЕЩИНЫ НА ОРГАНИЧЕСКИХ СТЕКЛАХ\nосмотр остекления",
     "faults"),
    ("СОДЕРЖАНИЕ\nНаименование ... Раздел, Стр.", "contents"),
    ("ОГЛАВЛЕНИЕ\n1. Общие положения ...", "contents"),
    ("ВВЕДЕНИЕ\nНастоящее дополнение ...", "contents"),
    ("ПЕРЕЧЕНЬ ДЕЙСТВУЮЩИХ СТРАНИЦ\nРаздел, подраздел, Стр.",
     "contents"),
    ("Таблица 2\nРасположение комплектующих изделий", "table"),
    ("ПЕРЕЧЕНЬ РИСУНКОВ\nРис. № Наименование рисунка", "list"),
    ("СХЕМА 1 Запуск ВСУ не происходит", "scheme"),
    ("РИСУНОК 4\nОбщий вид ВСУ", "scheme"),
    ("УТВЕРЖДЕН\nМи-8МТВ-1.0000.00 РЭ", "title"),
    ("УТВЕРЖДАЮ\nГлавный инженер", "title"),
    ("Лист регистрации изменений\nИзм. № Лист", "title"),
    ("ЛИСТ РЕГИСТРАЦИИ ВРЕМЕННЫХ ИЗМЕНЕНИЙ\n"
     "Изм. № Лист", "title"),
    ("Копия документа Ми-8МТВ-1.0000.00 РЭ от 24.08.2023",
     "service"),
    ("ДЕЙСТВИТЕЛЬНО ДЛЯ ВЕРТОЛЕТОВ №№ 97432-97435",
     "title"),
    ("КАТАЛОГ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ\n"
     "Номер рисунка Номер позиции Обозначение", "catalog"),
    ("Номер рисунка Номер позиции Обозначение Наименование",
     "catalog"),
    ("Рис. 4 Установка блока\nпоз. 12", "catalog"),
    ("", "blank"),
    ("   \n\t  ", "blank"),
    ("Эта страница преднамеренно оставлена пустой", "blank"),
    ("Просто текст без заголовков и маркеров", "other"),
])
def test_classify_page_kinds(text, expected):
    """Базовые примеры содержательных типов и статусов."""
    assert classify_page(text) == expected


def test_copy_stamp_is_stripped_before_classification():
    """Штамп копии вырезается; без остального текста — service."""
    assert classify_page(
        "Копия документа Ми-8МТВ-1.0000.00 РЭ\n"
        "Контрольная копия хранится на сайте АО «ВСК»\n"
        "Подлинник хранится в КАЗАНСКИЙ ВЕРТОЛЕТНЫЙ ЗАВОД"
    ) == "service"
    assert classify_page(
        "Копия документа Ми-8МТВ-1.0000.00 РЭ\n"
        "ОПИСАНИЕ И РАБОТА\n1. Общие сведения"
    ) == "purpose"


def test_catalog_only_by_header():
    """Каталог — шапка/начало строки, а не «рис.» в середине текста."""
    assert classify_page(
        "Номер рисунка Номер позиции Обозначение\nНаименование"
    ) == "catalog"
    assert classify_page(
        "Ми-8МТВ-1 КАТАЛОГ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ\n"
        "СОДЕРЖАНИЕ\nРаздел, подраздел, Стр."
    ) == "contents"
    assert classify_page(
        "Ми-8МТВ-1 КАТАЛОГ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ\n"
        "ВВЕДЕНИЕ\nКаталог представляет собой перечень"
    ) == "contents"
    assert classify_page(
        "Номер рисунка\nНаименование сборки"
    ) == "other"
    assert classify_page(
        "Сильное дымление, течь масла; см. рис. 5 и поз. 12 в абзаце"
    ) == "other"
    assert classify_page(
        "Рис. 4 Установка блока\nпоз. 12"
    ) == "catalog"


def test_conflict_priority_is_header_position():
    """Побеждает маркер, который в шапке встретился раньше."""
    assert classify_page(
        "СОДЕРЖАНИЕ\nОписание и работа .... 1") == "contents"
    assert classify_page(
        "ОПИСАНИЕ И РАБОТА\nСОДЕРЖАНИЕ .... 1") == "purpose"


def test_techcard_requires_number_or_work_name():
    """Ссылка на карту без №/«Наименование работы» — упоминание."""
    assert classify_page(
        "ТЕХНОЛОГИЧЕСКАЯ КАРТА № 5\nСодержание операции") == "techcard"
    assert classify_page(
        "ТЕХНОЛОГИЧЕСКАЯ КАРТА\nНаименование работы: КОНТРОЛЬ") == "techcard"
    assert classify_page(
        "ТЕХНОЛОГИЧЕСКАЯ КАРТА\nСсылка на карту в приложении") == "other"


def test_techcard_continuation_and_control_context():
    """Продолжение карты без бланка — тот же techcard."""
    assert classify_page(
        "Содержание операции и технические требования") == "techcard"
    assert classify_page(
        "Контроль\nСодержание операции и технические требования"
    ) == "techcard"
    assert classify_page("Контроль выполнения работ") == "other"


def test_techcard_does_not_become_contents():
    """«Содержание операции» — шапка таблицы карты, не оглавление."""
    assert classify_page(
        "ТЕХНОЛОГИЧЕСКАЯ КАРТА № 1\n"
        "Содержание операции и технические требования") == "techcard"
    assert classify_page(
        "Содержание операции и технические требования") == "techcard"


def test_weak_title_yields_to_content_after_header():
    """«Действительно для вертолетов» — колонтитул, не всегда title."""
    assert classify_page(
        "Ми-8МТВ-1 ДОПОЛНЕНИЕ К РУКОВОДСТВУ ПО ТЕХНИЧЕСКОЙ "
        "ЭКСПЛУАТАЦИИ ДЕЙСТВИТЕЛЬНО ДЛЯ ВЕРТОЛЕТОВ №№ 97432-97435 "
        "084.30.00 Стр. 101/102 ХВОСТОВОЙ РЕДУКТОР - ОПИСАНИЕ И РАБОТА"
    ) == "purpose"
    assert classify_page(
        "Ми-8МТВ-1 ДОПОЛНЕНИЕ К РУКОВОДСТВУ ПО ТЕХНИЧЕСКОЙ "
        "ЭКСПЛУАТАЦИИ ДЕЙСТВИТЕЛЬНО ДЛЯ ВЕРТОЛЕТОВ №№ 97432-97435 "
        "084.30.00 Стр. 101/102") == "title"


def test_running_header_is_skipped_before_markers():
    """Маркер сразу после длинного колонтитула снова попадает в окно."""
    header = (
        "Ми-8МТВ-1 РУКОВОДСТВО ПО ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ "
        "034.10.00 Стр. 5 "
    )
    text = header + "x" * 350 + " ОПИСАНИЕ И РАБОТА"
    assert len(text) > HEADER_CHARS
    assert classify_page(text) == "purpose"


def test_space_and_line_break_insensitive():
    """Разрывы пробелами/переносами не мешают маркеру."""
    assert classify_page("ОПИСАНИЕ\nИ\nРАБОТА") == "purpose"
    assert classify_page(
        "ТЕХНОЛОГ\nИЧЕСКАЯ\nКАРТА № 1") == "techcard"
    assert classify_page("ТЕХНОЛОГ  ИЧЕСКАЯ  КАРТА № 1") == "techcard"


def test_one_line_book_header_window():
    """Однострочная книга без колонтитула: окно 400 символов."""
    assert HEADER_CHARS == 400
    text = "x" * 380 + "ОПИСАНИЕ И РАБОТА"
    assert len(text) <= HEADER_CHARS
    assert classify_page(text) == "purpose"
    beyond = "x" * HEADER_CHARS + "ОПИСАНИЕ И РАБОТА"
    assert classify_page(beyond) == "other"


def test_store_migration_adds_page_kind(tmp_path):
    """Схема v8: колонка page_kind, чтение/запись, bulk-запись."""
    store = Store(str(tmp_path / "graph.db"))
    try:
        assert SCHEMA_VERSION == 8
        assert store.get_meta("schema_version") == "8"
        columns = {row[1] for row in store._connection.execute(
            "PRAGMA table_info(pages)")}
        assert "page_kind" in columns
        store.upsert_page("d.pdf", 1, "Ми-8МТВ", "", "", "текст", "текст")
        store.upsert_page("d.pdf", 2, "Ми-8МТВ", "", "", "текст", "текст")
        assert store.page_kind("d.pdf", 1) == ""
        store.set_page_kind("d.pdf", 1, "purpose")
        assert store.page_kind("d.pdf", 1) == "purpose"
        assert store.set_page_kinds([
            ("d.pdf", 1, "table"), ("d.pdf", 2, "techcard")]) == 2
        assert store.page_kind("d.pdf", 1) == "table"
        assert store.page_kind("d.pdf", 2) == "techcard"
        assert store.page_kind("missing.pdf", 1) == ""
    finally:
        store.close()


def test_migration_v7_to_v8_keeps_rows(tmp_path):
    """Аддитивная миграция 7→8: версия поднята, строки на месте."""
    database = tmp_path / "old.db"
    connection = sqlite3.connect(str(database))
    connection.executescript(
        """
        CREATE TABLE pages (
            document TEXT NOT NULL,
            page INTEGER NOT NULL,
            aircraft_type TEXT NOT NULL,
            system_code TEXT NOT NULL,
            subsystem_code TEXT NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (document, page)
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta (key, value) VALUES ('schema_version', '7');
        INSERT INTO pages
            (document, page, aircraft_type, system_code, subsystem_code, text)
        VALUES ('d.pdf', 1, 'Ми-8МТВ', '', '', 'текст листа');
        """
    )
    connection.commit()
    connection.close()
    store = Store(str(database))
    try:
        assert store.get_meta("schema_version") == "8"
        assert store.page_text("d.pdf", 1) == "текст листа"
        assert store.page_kind("d.pdf", 1) == ""
        assert store._connection.execute(
            "SELECT COUNT(*) FROM pages").fetchone()[0] == 1
    finally:
        store.close()
