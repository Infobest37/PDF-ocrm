# -*- coding: utf-8 -*-
"""Пометки листов 0.2 (пересмотр 29.10.2026): needs_rescan + is_service.

По разметке владельца (30 листов выборки) «чужого мусора» в базе нет:
плохие листы — это либо свой содержательный лист с вырожденным OCR, либо
служебные страницы. Поэтому два независимых флага вместо одного is_garbage:

* ``needs_rescan`` — повреждённый лист (обрывочный или вырожденный-
  короткий текст). Авто по сохранённому тексту (R1/R2, пороги по precision
  на разметке), исключается из всех четырёх веток поиска, снимается
  вручную ``unmark-rescan``; и авто-метка, и ручное снятие переживают
  пересборку.
* ``is_service`` — пустой лист, заглушка «страница оставлена пустой»,
  разделитель-заголовок (исключаются из выдачи) и титул каталога
  (только флаг, из выдачи НЕ исключается). Штампы копий и листы изменений
  автоматикой не ловятся — только ручной CLI и перечисление в отчёте.

Проверяемые инварианты разметки: латиница в артикулах — содержание
(«SAFIR» — законный текст), пустой лист — граница раздела, цитаты
остаются проверяемыми (page_text/has_node не скрываются).
"""

import pytest

from pdfprepare.models import DocResult, PageInfo


def _page(**overrides) -> PageInfo:
    """PageInfo с разумными значениями по умолчанию для тестов признаков."""
    params = dict(page_index=0, is_native_text=False, is_blank=False,
                  system_code="078.11.00", subsystem_code="",
                  full_text="", words=[])
    params.update(overrides)
    return PageInfo(**params)


# ---------------------------------------------------------------------------
# needs_rescan-признаки (pdfprepare.quality): R1 обрывочный, R2 вырожденный.
# Пороги из калибровки по разметке 29.10 (precision важнее охвата).
# ---------------------------------------------------------------------------

def test_rescan_component_flags_fragmented_long_text():
    """R1: длинный лист из обрывков (frag>=0.85 или single>=0.36) — ловим."""
    from pdfprepare.quality import rescan_component
    fragmented = " ".join(["о"] * 18 + ["т"] * 3)  # frag=1.0, tokens=21
    assert rescan_component(fragmented)[0] == "обрывочный лист"


def test_rescan_component_flags_degenerated_short_text():
    """R2: 1..15 токенов, мало длинных слов и frag>=0.60 — ловим."""
    from pdfprepare.quality import rescan_component
    short = "р A |, 76 iA ow sh i?"  # 7 токенов, длинных слов нет
    assert rescan_component(short)[0] == "вырожденный короткий лист"


def test_rescan_component_skips_normal_text():
    """Осмысленный кириллический текст (в т.ч. слово SAFIR) не ловится."""
    from pdfprepare.quality import rescan_component
    normal = ("СИСТЕМА ЭЛЕКТРОСНАБЖЕНИЯ ПОСТОЯННЫМ ТОКОМ от генератора "
              "SAFIR описана в настоящем разделе листа правил эксплуатации")
    assert rescan_component(normal) is None


def test_rescan_component_skips_latin_articul_table():
    """Латиница в артикулах — содержание таблицы, а не мусор (№ 354)."""
    from pdfprepare.quality import rescan_component
    articuls = ("TV3117-A-072-53-00-00A-941A-D (6) 7857.0445 "
                "TV3117-A-072-53-00-00A-941A-D (13) 7857.0446 "
                "TV3117-A-072-53-00-00A-941A-D (24) 7857.0447")
    assert rescan_component(articuls) is None


def test_rescan_component_skips_borderline_drawing_text():
    """Пограничный лист-чертёж ниже порогов не ловится (№ 121: 30 токенов)."""
    from pdfprepare.quality import rescan_component
    drawing = " ".join(["а", "б", "клм"] + [str(i) for i in range(20, 47)])
    assert rescan_component(drawing) is None


# ---------------------------------------------------------------------------
# is_service-признаки: пустой, заглушка, разделитель, титул (флаг).
# Штамп копии и лист изменений автоматикой не ловятся.
# ---------------------------------------------------------------------------

def test_service_component_blank_page_and_empty_text():
    from pdfprepare.quality import service_component
    assert service_component("", is_blank=True) == ("пустой лист", 1.0)
    assert service_component("", is_blank=False) == ("пустой лист", 1.0)


def test_service_component_blank_stub_text():
    from pdfprepare.quality import service_component
    stub = ("ОДК КЛИМОВ НЕСЕКРЕТНО [Эта страница преднамеренно оставлена "
            "пустой] Действительно: Все")
    assert service_component(stub)[0] == "заглушка «страница оставлена пустой»"


def test_service_component_divider_heading_with_bom():
    """Разделитель «ПРИЛОЖЕНИЯ»; BOM перед словом не мешает (лист 151)."""
    from pdfprepare.quality import service_component
    for text in ("\ufeffПРИЛОЖЕНИЯ", "ПРИЛОЖЕНИЯ ", "Содержание"):
        assert service_component(text)[0] == "разделитель-заголовок"


def test_service_component_title_flag():
    """Титул каталога — флаг «титул каталога» (из выдачи не исключается)."""
    from pdfprepare.quality import service_component
    title = ("Mu-8MTB-1 ДОПОЛНЕНИЕ К КАТАЛОГУ ДЕТАЛЕЙ И СБОРОЧНЫХ ЕДИНИЦ "
             "ДЕЙСТВИТЕЛЬНО ДЛЯ ВЕРТОЛЕТОВ Стр. 3")
    assert service_component(title) == ("титул каталога", 1.0)
    assert service_component(
        "дополнение к каталогу") == ("титул каталога", 1.0)


def test_service_component_ignores_stamp_and_changes():
    """Штамп копии и «Изменение №» — НЕ признак служебности (авто-ноль)."""
    from pdfprepare.quality import service_component
    content_with_stamp = ("{Копия документа Ми-8МТВ-1.0000.00 РЭ СИСТЕМА "
                          "ЭЛЕКТРОСНАБЖЕНИЯ ПОСТОЯННЫМ ТОКОМ Генератор "
                          "SAFIR ОПИСАНИЕ И РАБОТА 1. Введение "
                          "https://doclib.hsc-copter.com")
    assert service_component(content_with_stamp) is None
    changes = ("Изменение № 9 PG 05-04 ВВЕДЕНИЕ Настоящий иллюстрированный "
               "каталог запасных частей относится к вспомогательной "
               "силовой установке")
    assert service_component(changes) is None


# ---------------------------------------------------------------------------
# Хранилище: миграция 3→4, mark/unmark/auto для обоих флагов, списки и
# статистика. Колонки аддитивные: needs_rescan/is_service + reason + score.
# ---------------------------------------------------------------------------

#: Все новые колонки таблицы pages и их значения по умолчанию.
FLAG_COLUMNS = (
    ("needs_rescan", "needs_rescan_reason", "needs_rescan_score"),
    ("is_service", "is_service_reason", "is_service_score"),
)


def _two_sheet_store(tmp_path):
    from pdfgraph.store import Store
    store = Store(str(tmp_path / "g.db"))
    store.upsert_page("d.pdf", 1, "Ми-8МТВ", "023.00.00", "023.11.30",
                      "текст первого листа", "текст первого листа")
    store.upsert_page("d.pdf", 2, "Ми-8МТВ", "023.00.00", "",
                      "текст второго листа", "текст второго листа")
    return store


def _page_row(store, document, page):
    """Строка флагов листа без текста: (нужен_перескан, причина, ...)."""
    return store._connection.execute(
        "SELECT needs_rescan, needs_rescan_reason, needs_rescan_score, "
        "is_service, is_service_reason, is_service_score "
        "FROM pages WHERE document = ? AND page = ?",
        (document, page),
    ).fetchone()


def test_migration_3_to_4_adds_flag_columns(tmp_path):
    from pdfgraph.store import Store
    database = tmp_path / "mig.db"
    store = Store(str(database))
    store.upsert_page("d.pdf", 1, "Ми-8МТВ", "023.00.00", "",
                      "текст листа", "текст листа")
    store.close()
    # откатить файл к схеме «до 0.2»: убрать флаговые колонки, версию снизить
    import sqlite3 as sqlite3_module
    with sqlite3_module.connect(str(database)) as connection:
        for flag_columns in FLAG_COLUMNS:
            for column in flag_columns:
                connection.execute(
                    f"ALTER TABLE pages DROP COLUMN {column}")
        connection.execute(
            "UPDATE meta SET value = '3' WHERE key = 'schema_version'")
    reopened = Store(str(database))
    try:
        assert reopened.page_text("d.pdf", 1) == "текст листа"
        columns = {row[1] for row in reopened._connection.execute(
            "PRAGMA table_info(pages)")}
        assert {column for flag_columns in FLAG_COLUMNS
                for column in flag_columns} <= columns
        version = reopened._connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert version == "4"
        assert _page_row(reopened, "d.pdf", 1) == (0, "", 0.0, 0, "", 0.0)
    finally:
        reopened.close()


def test_mark_and_unmark_rescan_and_service_idempotent(tmp_path):
    store = _two_sheet_store(tmp_path)
    try:
        assert store.mark_rescan("d.pdf", 1, "manual: тест") is True
        assert store.mark_rescan("d.pdf", 1, "manual: тест") is False
        assert _page_row(store, "d.pdf", 1)[0] == 1
        with pytest.raises(LookupError):
            store.mark_rescan("нет.pdf", 1, "r")
        with pytest.raises(LookupError):
            store.mark_rescan("d.pdf", 99, "r")
        assert store.unmark_rescan("d.pdf", 2) is False  # не был помечен
        assert store.unmark_rescan("d.pdf", 1) is True
        # снятый лист остаётся защищённым от авто-перепометки
        assert _page_row(store, "d.pdf", 1)[1] == "manual: снято"
        # service — тот же контракт на другой паре колонок
        assert store.mark_service("d.pdf", 2, "manual: штамп") is True
        assert store.mark_service("d.pdf", 2, "manual: штамп") is False
        assert _page_row(store, "d.pdf", 2)[3] == 1
        assert store.unmark_service("d.pdf", 2) is True
        assert store.unmark_service("d.pdf", 2) is False
        assert _page_row(store, "d.pdf", 2)[4] == "manual: снято"
    finally:
        store.close()


def test_set_auto_flags_preserve_manual_and_rewrite_auto(tmp_path):
    store = _two_sheet_store(tmp_path)
    try:
        assert store.set_rescan_auto(
            "d.pdf", 1, 0.91, "auto: обрывочный лист") is True
        # auto → auto перезаписывается
        assert store.set_rescan_auto(
            "d.pdf", 1, 0.95, "auto: вырожденный короткий лист") is True
        assert store.mark_rescan("d.pdf", 1, "manual: владелец") is True
        # ручная пометка не переписывается авто
        assert store.set_rescan_auto(
            "d.pdf", 1, 0.5, "auto: обрывочный лист") is False
        row = store._connection.execute(
            "SELECT needs_rescan, needs_rescan_reason FROM pages "
            "WHERE document = 'd.pdf' AND page = 1").fetchone()
        assert row == (1, "manual: владелец")
        # service: защита manual независима от rescan
        assert store.set_service_auto(
            "d.pdf", 2, 1.0, "auto: титул каталога") is True
        assert store.mark_service("d.pdf", 2, "manual: штамп") is True
        assert store.set_service_auto(
            "d.pdf", 2, 1.0, "auto: пустой лист") is False
        row = store._connection.execute(
            "SELECT is_service, is_service_reason FROM pages "
            "WHERE document = 'd.pdf' AND page = 2").fetchone()
        assert row == (1, "manual: штамп")
    finally:
        store.close()


def test_rescan_sheets_service_sheets_and_stats(tmp_path):
    store = _two_sheet_store(tmp_path)
    try:
        assert store.set_rescan_auto(
            "d.pdf", 2, 0.91, "auto: обрывочный лист") is True
        assert store.mark_service(
            "d.pdf", 1, "manual: штамп копии") is True
        assert store.rescan_sheets() == [("d.pdf", 2)]
        assert store.service_sheets() == [("d.pdf", 1)]
        stats = store.garbage_stats()
        assert stats["total"] == 2
        assert stats["rescan"] == 1
        assert stats["service"] == 1
        assert stats["reasons"] == {"manual: штамп копии": 1,
                                    "auto: обрывочный лист": 1}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Поиск: needs_rescan скрыт из 4 веток, is_service-титул остаётся;
# цитаты остаются проверяемыми (page_text/has_node не скрываются).
# ---------------------------------------------------------------------------

def _four_sheet_store(tmp_path):
    """Листы: 1 rescan, 2 service-титул (флаг), 3 service-пустой, 4 чистый.
    Все содержательные листы (2/3/4) кодовые 023.11.30 со словом запроса,
    чтобы проверять сами ветки поиска, а не только фильтрацию."""
    from pdfgraph.store import Store
    store = _two_sheet_store(tmp_path)
    store.upsert_page("d.pdf", 2, "Ми-8МТВ", "023.00.00", "023.11.30",
                      "ДОПОЛНЕНИЕ К КАТАЛОГУ радиомаяк Стр. 3",
                      "дополнение к каталогу радиомаяк стр. 3")
    store.upsert_page("d.pdf", 3, "Ми-8МТВ", "023.00.00", "023.11.30",
                      "радиомаяк на третьем листе",
                      "радиомаяк на третьем листе")
    store.upsert_page("d.pdf", 4, "Ми-8МТВ", "023.00.00", "023.11.30",
                      "радиомаяк на четвёртом листе",
                      "радиомаяк на четвертом листе")
    store.mark_rescan("d.pdf", 1, "manual: тест")
    store.set_service_auto("d.pdf", 2, 1.0, "auto: титул каталога")
    store.set_service_auto("d.pdf", 3, 1.0, "auto: пустой лист")
    for page in (1, 2, 3, 4):  # sheet-узлы делает build; тут — явно
        store.add_node("sheet", f"d.pdf:{page}", document="d.pdf", page=page)
    return store


def test_rescan_hidden_from_all_branches_service_title_not(tmp_path):
    from pdfgraph.query import search
    store = _four_sheet_store(tmp_path)
    try:
        part = store.add_node("part", "8МТ.6101.100")
        for page in (2, 4):
            sheet_node = store.add_node("sheet", f"d.pdf:{page}",
                                        document="d.pdf", page=page)
            store.add_edge(sheet_node, part, "has_part",
                           document="d.pdf", page=page)
        visible = {("d.pdf", 2), ("d.pdf", 4)}
        # ветка кода: rescan и service-пустой скрыты, service-титул видим
        assert {(doc, page) for doc, page, _, _ in
                store.sheets_by_code("023.11.30")} == visible
        # ветка детали
        assert {(doc, page) for doc, page, _, _ in
                store.sheets_with_part("8МТ.6101.100")} == visible
        # ветка BM25
        hits = search(store, "радиомаяк")
        assert {(h.document, h.page) for h in hits} == visible
        # ветка триграмм
        trigram = store.search_trigrams("%радиомаяк%")
        assert {tuple(row[:2]) for row in trigram} == visible
        # цитаты с rescan/service листов остаются проверяемыми
        assert store.page_text("d.pdf", 1) == "текст первого листа"
        assert store.page_text("d.pdf", 3) == "радиомаяк на третьем листе"
        assert store.has_node("sheet", "d.pdf:1")
        # снятие rescan возвращает лист во все ветки: код и текст
        assert search(store, "первого") == []  # лист 1 скрыт по своему слову
        assert store.unmark_rescan("d.pdf", 1) is True
        assert {(doc, page) for doc, page, _, _ in
                store.sheets_by_code("023.11.30")} == visible | {("d.pdf", 1)}
        assert {(h.document, h.page) for h in
                search(store, "первого")} == {("d.pdf", 1)}
    finally:
        store.close()


def test_unmark_service_restores_hits(tmp_path):
    from pdfgraph.query import search
    store = _four_sheet_store(tmp_path)
    try:
        assert {(h.document, h.page) for h in
                search(store, "радиомаяк")} == {("d.pdf", 2), ("d.pdf", 4)}
        assert store.unmark_service("d.pdf", 3, "не мусор") is True
        assert _page_row(store, "d.pdf", 3)[4] == "manual: снято: не мусор"
        assert {(h.document, h.page) for h in
                search(store, "радиомаяк")} == {
            ("d.pdf", 2), ("d.pdf", 3), ("d.pdf", 4)}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Сборка: авто-разметка в build(): rescan/заглушка/разделитель/титул;
# blank — только «пустой лист», никогда не needs_rescan. Idempotent,
# ручные пометки и снятия переживают пересборку (кейс Д-1638 стр. 432).
# ---------------------------------------------------------------------------

def _catalog_result(tmp_path):
    rescan_page = _page(page_index=0,
                        full_text=" ".join(["о"] * 18 + ["т"] * 3))
    good_page = _page(page_index=1,
                      full_text="Установка кронштейна согласно ведомости")
    blank_page = _page(page_index=2, is_blank=True,
                       full_text=" ".join(["о"] * 40))
    title_page = _page(page_index=3,
                       full_text="ДОПОЛНЕНИЕ К КАТАЛОГУ ДЕТАЛЕЙ Стр. 3")
    return DocResult(source_path=str(tmp_path / "dir" / "Каталог.pdf"),
                     page_count=4,
                     pages=[rescan_page, good_page, blank_page, title_page],
                     system_names={})


def test_build_marks_auto_flags_and_never_blank_rescan(tmp_path):
    from pdfgraph.build import build
    from pdfgraph.store import Store
    store = Store(str(tmp_path / "g.db"))
    try:
        result = _catalog_result(tmp_path)
        build([result], "Ми-8МТВ", {}, store, root=str(tmp_path))
        rows = {tuple(row) for row in store._connection.execute(
            "SELECT page, needs_rescan, needs_rescan_reason, "
            "is_service, is_service_reason FROM pages "
            "WHERE document = 'dir/Каталог.pdf'")}
        assert (0, 1, "auto: обрывочный лист", 0, "") in rows
        assert (1, 0, "", 0, "") in rows
        assert (2, 0, "", 1, "auto: пустой лист") in rows  # граница раздела
        assert (3, 0, "", 1, "auto: титул каталога") in rows
        # идемпотентность: повторный build ничего не меняет
        build([result], "Ми-8МТВ", {}, store, root=str(tmp_path))
        rows_after = {tuple(row) for row in store._connection.execute(
            "SELECT page, needs_rescan, needs_rescan_reason, "
            "is_service, is_service_reason FROM pages "
            "WHERE document = 'dir/Каталог.pdf'")}
        assert rows_after == rows
    finally:
        store.close()


def test_build_manual_survives_rebuild_and_unmarked_page_stays(tmp_path):
    """Кейс владельца (Д-1638 стр.432): авто-метка снята вручную — rebuild
    не возвращает её: manual-снятие защищено от set_*_auto."""
    from pdfgraph.build import build
    from pdfgraph.query import search
    from pdfgraph.store import Store
    store = Store(str(tmp_path / "g.db"))
    try:
        rescue_text = " ".join(["а"] * 18 + ["б"] * 3)  # R1-подобный лист
        result = DocResult(
            source_path=str(tmp_path / "Доп.к РТЭ Д-1638.pdf"), page_count=1,
            pages=[_page(page_index=432, full_text=rescue_text)],
            system_names={})
        build([result], "Ми-8МТВ", {}, store, root=str(tmp_path))
        assert _page_row(store, "Доп.к РТЭ Д-1638.pdf", 432)[0] == 1
        assert store.search_pages('"а"') == []  # авто-метка скрыла лист
        assert store.unmark_rescan("Доп.к РТЭ Д-1638.pdf", 432,
                                   "содержание: аварийные выходы") is True
        assert store.search_pages('"а"') != []
        build([result], "Ми-8МТВ", {}, store, root=str(tmp_path))
        row = _page_row(store, "Доп.к РТЭ Д-1638.pdf", 432)
        assert row[0] == 0  # авто не перепишет manual-снятие
        assert row[1] == "manual: снято: содержание: аварийные выходы"
        assert search(store, "а") != []
    finally:
        store.close()


# ---------------------------------------------------------------------------
# CLI: mark-rescan / mark-service (и unmark-), аудит garbage.log,
# счётчики rescan=/service= в выводе build.
# ---------------------------------------------------------------------------

def _document_with_page(store_path: str, page: int) -> str:
    """Имя документа, у которого точно есть лист ``page``."""
    from pdfgraph.store import Store
    probe = Store(store_path)
    try:
        row = probe._connection.execute(
            "SELECT document FROM pages WHERE page = ? ORDER BY document "
            "LIMIT 1", (page,)).fetchone()
        assert row is not None
        return row[0]
    finally:
        probe.close()


def test_cli_mark_and_unmark_rescan_roundtrip(fx_store_path):
    from pdfgraph.cli import main
    from pdfgraph.query import search
    from pdfgraph.store import Store
    store_path = fx_store_path
    document_name = _document_with_page(store_path, 47)
    mark = ["mark-rescan", store_path, "--document", document_name,
            "--page", "47", "--reason", "manual: тест 0.2"]
    unmark = ["unmark-rescan", store_path, "--document", document_name,
              "--page", "47"]
    assert main(mark) == 0
    assert main(mark) == 0  # идемпотентность — повторная пометка не ошибка
    store = Store(store_path)
    try:
        hits = search(store, "радиомаяк ПАРМ-406А")
        assert all(h.page != 47 for h in hits)
        assert store.page_text(document_name, 47)
        assert store.has_node("sheet", f"{document_name}:47")
    finally:
        store.close()
    assert main(unmark) == 0
    store = Store(store_path)
    try:
        hits = search(store, "радиомаяк ПАРМ-406А")
        assert any(h.page == 47 for h in hits)
    finally:
        store.close()
    # несуществующий лист — код 2, а не падение
    assert main(["mark-rescan", store_path, "--document", document_name,
                 "--page", "999", "--reason", "x"]) == 2


def test_cli_mark_and_unmark_service_roundtrip(fx_store_path):
    from pdfgraph.cli import main
    from pdfgraph.query import search
    from pdfgraph.store import Store
    store_path = fx_store_path
    document_name = _document_with_page(store_path, 47)
    assert main(["mark-service", store_path, "--document", document_name,
                 "--page", "47", "--reason",
                 "manual: штамп копии без содержания"]) == 0
    store = Store(store_path)
    try:
        hits = search(store, "радиомаяк ПАРМ-406А")
        assert all(h.page != 47 for h in hits)
    finally:
        store.close()
    assert main(["unmark-service", store_path, "--document", document_name,
                 "--page", "47", "--reason", "содержание"]) == 0
    store = Store(store_path)
    try:
        hits = search(store, "радиомаяк ПАРМ-406А")
        assert any(h.page == 47 for h in hits)
        row = store._connection.execute(
            "SELECT is_service, is_service_reason FROM pages "
            "WHERE document = ? AND page = 47",
            (document_name,)).fetchone()
        assert row == (0, "manual: снято: содержание")
    finally:
        store.close()
    assert main(["mark-service", store_path, "--document", document_name,
                 "--page", "999"]) == 2


def test_cli_mark_flags_use_garbage_log(fx_store_path):
    import pathlib
    from pdfgraph.cli import main
    document_name = _document_with_page(fx_store_path, 47)
    log_path = pathlib.Path(fx_store_path).with_name("garbage.log")
    if log_path.exists():
        log_path.unlink()
    assert main(["mark-service", fx_store_path, "--document", document_name,
                 "--page", "47", "--reason", "manual: аудит"]) == 0
    text = log_path.read_text(encoding="utf-8")
    assert "mark-service" in text and document_name in text
    assert main(["unmark-service", fx_store_path, "--document", document_name,
                 "--page", "47"]) == 0
    text = log_path.read_text(encoding="utf-8")
    assert "unmark-service" in text
    assert main(["unmark-rescan", fx_store_path, "--document", document_name,
                 "--page", "47"]) == 0
    text = log_path.read_text(encoding="utf-8")
    assert "unmark-rescan" in text


def test_cli_build_prints_flag_counts(fx_prepared_dir, tmp_path, capsys):
    from pdfgraph.cli import main
    store_path = str(tmp_path / "build-flags.db")
    assert main(["build", fx_prepared_dir, store_path,
                 "--type", "Ми-8МТВ"]) == 0
    out = capsys.readouterr().out
    assert "rescan=" in out and "service=" in out


# ---------------------------------------------------------------------------
# CLI: mark-flags-auto — разовая авто-разметка готовой базы без пересборки
# (шаг 1 приёмки 0.2, 29.10.2026): правила те же, что при build
# (_mark_page_flags), идемпотентно, «manual: ...» не перезаписывается.
# ---------------------------------------------------------------------------

def _flags_prepared_dir(tmp_path) -> str:
    """Schema-v2 JSON: резанка (R1), хороший лист, пустой лист, титул."""
    from pdfprepare.output import write_json
    out_dir = tmp_path / "prep"
    out_dir.mkdir()
    pages = [
        _page(page_index=0, full_text=" ".join(["о"] * 18 + ["т"] * 3)),
        _page(page_index=1,
              full_text="Установка кронштейна согласно ведомости"),
        _page(page_index=2, is_blank=True, full_text=""),
        _page(page_index=3,
              full_text="ДОПОЛНЕНИЕ К КАТАЛОГУ ДЕТАЛЕЙ Стр. 3"),
    ]
    result = DocResult(source_path=str(out_dir / "Каталог.pdf"),
                       page_count=4, pages=pages, system_names={})
    write_json(result, str(out_dir / "Каталог.pdf.json"))
    return str(out_dir)


def test_cli_mark_flags_auto_matches_build_and_idempotent(fx_prepared_dir,
                                                          capsys):
    """mark-flags-auto повторяет флаги build (changed=0) и идемпотентен."""
    import pathlib
    from pdfgraph.cli import main
    from pdfgraph.store import Store
    store_path = str(pathlib.Path(fx_prepared_dir).with_name("flags-auto.db"))
    assert main(["build", fx_prepared_dir, store_path,
                 "--type", "Ми-8МТВ"]) == 0
    snapshot_query = (
        "SELECT document, page, needs_rescan, needs_rescan_reason, "
        "is_service, is_service_reason FROM pages ORDER BY document, page")
    store = Store(store_path)
    try:
        rows_after_build = store._connection.execute(
            snapshot_query).fetchall()
    finally:
        store.close()
    assert main(["mark-flags-auto", store_path]) == 0
    out = capsys.readouterr().out
    assert "mark-flags-auto" in out and "changed=0" in out
    assert "manual=0" in out
    store = Store(store_path)
    try:
        assert store._connection.execute(snapshot_query).fetchall() \
            == rows_after_build
        stats = store.garbage_stats()
    finally:
        store.close()
    assert f"rescan={stats['rescan']}" in out
    assert f"service={stats['service']}" in out
    # аудит: одна строка за прогон в garbage.log рядом с базой
    log_path = pathlib.Path(store_path).with_name("garbage.log")
    log_text = log_path.read_text(encoding="utf-8")
    assert "mark-flags-auto" in log_text
    assert f"rescan={stats['rescan']} service={stats['service']}" in log_text
    # повторный прогон тоже ничего не меняет
    assert main(["mark-flags-auto", store_path]) == 0
    out = capsys.readouterr().out
    assert "changed=0" in out


def test_cli_mark_flags_auto_preserves_manual(tmp_path):
    """manual-снятие резанки и manual-пометка переживают mark-flags-auto."""
    from pdfgraph.cli import main
    from pdfgraph.store import Store
    prepared = _flags_prepared_dir(tmp_path)
    store_path = str(tmp_path / "manual.db")
    assert main(["build", prepared, store_path, "--type", "Ми-8МТВ"]) == 0
    store = Store(store_path)
    try:
        row = store._connection.execute(
            "SELECT needs_rescan, needs_rescan_reason FROM pages "
            "WHERE page = 0").fetchone()
        assert row == (1, "auto: обрывочный лист")
        assert store.unmark_rescan("Каталог.pdf", 0,
                                   "проверка содержания") is True
        assert store.mark_rescan("Каталог.pdf", 1, "manual: проба") is True
    finally:
        store.close()
    assert main(["mark-flags-auto", store_path]) == 0
    store = Store(store_path)
    try:
        row = store._connection.execute(
            "SELECT needs_rescan, needs_rescan_reason FROM pages "
            "WHERE page = 0").fetchone()
        assert row == (0, "manual: снято: проверка содержания")
        row = store._connection.execute(
            "SELECT needs_rescan, needs_rescan_reason FROM pages "
            "WHERE page = 1").fetchone()
        assert row == (1, "manual: проба")
    finally:
        store.close()