# Состояние проекта

Срез на 16.11.2026, один срез. Заменяет `PROJECT_STATE.md`.
Среда и команды — `AGENTS.md`; правила процесса — `.clinerules`; ловушки — `docs/PITFALLS.md`; история и факты полигонов — `docs/HISTORY.md`.

## 1. Срез

| Параметр | Значение |
|---|---|
| Дата среза | 16.11.2026 |
| HEAD | `8dc02d7` — «docs: .clinerules версия 7 — коды CLI, политика ответа, ссылки» (2026-11-16 16:03 +0300) |
| Тесты | `venv\Scripts\python.exe -m pytest -q` → 863 passed, 0 падений, 178.14 с (прогон 16.11.2026) |
| Корпус | один тип ВС: Ми-8МТВ (метка `pages.aircraft_type` у всех листов), 49 документов, 13 700 листов |
| Граф | 15 111 узлов, 31 563 ребра (`reports/golden_run.json`, 16.11.2026) |
| Файл базы | `graph.db`, 507,1 МБ |

Незакоммичено в рабочем дереве на момент среза: `M .gitignore`, `M PROGRESS.md`; untracked `check_reranker.py`, `count_chunks.py`, `docs/AUDIT_FOR_PLANNER.md`, `tools/depth_probe_f120.py`.

## 2. Метрики

Замер 16.11.2026 (`tests/golden/metrics.py` внутри полного прогона pytest, работа на копии `graph.db`, отчёт `reports/golden_run.json`). Золотой набор: 48 записей — 37 готовы (31 ожидает ответа, 6 ожидают отказа), 11 в `todo`.

| Метрика | Значение | n | Требование |
|---|---|---|---|
| hit_at_5 | 0.6774 | 31 | ≥0.90 |
| hit_at_5_with_neighbors | 0.7419 | 31 | — |
| doc_hit_at_5 | 0.9677 | 31 | — |
| citation_precision | 1.000 (102/102) | 102 | ≥0.98 |
| refusal_false_on_found | 0.000 | 21 | 0.00 |
| refusal_on_miss | 0.000 | 10 | — |
| refusal_recall | 0.500 | 6 | 0.80 — потолок детерминированных гейтов |
| latency_median_ms | 1401 | 37 | — |

Значения совпадают с замером Д1б (14.11.2026, `docs/HISTORY.md` §15 п.4: hit@5 0.6774, citation_precision 102/102). После Д1б поиск не менялся: Д1в-1-А (`98f833f`) расширил только окно классификации `purpose` (400 → 800 символов).

Полигоны: clarify-полигон, группа 1 — 4/10, группа 2 — 0/10, корпусные 28 answer (базовая линия `docs/HISTORY.md` §13 п.5); negative_recall полигона негативов — 0.50 (`docs/HISTORY.md` §13 п.3).

## 3. Флаги и значения по умолчанию (по коду)

| Переменная / константа | По умолчанию | Где |
|---|---|---|
| `PDFGRAPH_SEARCH_MODE` | `hybrid_gated` | `pdfgraph/query.py:64,67` |
| `ENABLED_GATES` | `numbers, designators, aircraft_type` | `pdfgraph/gates.py:105` |
| `PDFGRAPH_NORMS` | включён; выключают `0/false/off/no` | `pdfgraph/norms.py:37,41,155` |
| `NORMS_WEIGHT` | 2.0 — вес канала норм в RRF | `pdfgraph/norms.py:45` |
| `PDFGRAPH_TERMS` | включён; выключают `0/false/off/no` | `pdfgraph/terms.py:44,48,73` |
| `PDFGRAPH_NEIGHBORS` | включён; выключают `0/false` | `pdfgraph/answer.py:130,139` |
| `PDFGRAPH_RERANK` | выключен | `pdfgraph/rerank.py:63–66,107` |
| `PDFGRAPH_CROSSRERANK` | выключен, включают только `1/true/yes/on` | `pdfgraph/crossrerank.py:34,35,70` |
| `PDFGRAPH_CHUNK_CHANNEL` | выключен, включают только `1/true/yes/on` | `pdfgraph/query.py:810,811,824` |
| `PDFGRAPH_MIN_COSINE` | 0.18 (порог не отсекает — `docs/PITFALLS.md:42`) | `pdfgraph/embedding.py:47,453,465` |
| `PDFGRAPH_MODELS_DIR` | `C:\PDF_Data\models` | `pdfgraph/embedding.py:40,41,52` |
| `PDFGRAPH_OLLAMA_MODEL` | пусто — переформулировка вопроса выключена | `pdfgraph/rag_fusion.py:339` |
| `PDFGRAPH_WEB_PORT` | 8000 | `pdfgraph/web/__main__.py:29` |
| `PDFPREPARE_TESSERACT` | `pdfprepare\tesseract\` | `pdfprepare/bundle.py:25,32` |
| `HEADER_CHARS` | 400 — окно шапки при классификации листа | `pdfgraph/page_kind.py:78` |
| `PURPOSE_CHARS` | 800 — окно маркеров `purpose` (Д1в-1-А, было 400) | `pdfgraph/page_kind.py:85` |

Типы листа (`pdfgraph/page_kind.py:88–104`): содержательные `purpose, techcard, faults, contents, table, list, scheme, title, catalog`; служебные `blank, service, other`.

## 4. Что в работе

Д1в — разметка `page_kind`, листы назначения.

- `e582833` (14.11.2026) — Д1а: классификатор `pdfgraph/page_kind.py`, схема 8, разметка 13 700 листов (`other` 30.7%).
- `dd5f22d` (14.11.2026) — Д1б: интент вопроса к типу листа, отказ/уточнение по типу, каталог по обозначению (`pdfgraph/catalog.py`).
- `98f833f` (16.11.2026) — Д1в-1-А: окно маркеров `purpose` 400 → 800 символов.

Остаток Д1в — листы назначения 138, 150, 156: в Д1а они размечены `other`/`techcard` (`docs/HISTORY.md` §14 п.3, §15 п.6).

## 5. Следующие задачи

1. Д1в: разметка `page_kind` — листы назначения 138/150/156 (`pdfgraph/page_kind.py`, `tools/mark_page_kind_d1.py`).
2. Ранжирование норм: контрольный вопрос «какой допустимый люфт автомата перекоса» — правильный лист 726 (РТЭ кн. 2) не в топ-5, лидер л.555 `techcard` (`docs/HISTORY.md` §15 п.5).
3. Принадлежность числа вопроса теме листа: выдуманное число совпадает в тексте случайно, уточнение срывается в ответ (`docs/HISTORY.md` §13 п.6).
4. Слой «модель-верификатор» поверх гейтов для вопросов без конкретики — решение владельца (`docs/HISTORY.md` §10 п.6, §12 п.4).
5. Расширение золотого набора и отдельный holdout: 48 записей, 11 в `todo`.

## 6. Известные расхождения

- Clarify-полигон: §11.3 («фонарные 10/10») и §12.5 («clarify только е06») в `docs/HISTORY.md` описывают конфигурацию до веса Ф1.5.0; действующая базовая линия — §13.5 (группа 1 — 4/10, группа 2 — 0/10, корпусные 28 answer), старые значения помечены датой и причиной, не переписаны.
- §11.2 `docs/HISTORY.md` (clarify только гейт-путь, слотless не уточняются) снят §14.3: уточнение для «неполного» вопроса разрешено (`.clinerules`, раздел «Политика ответа»).
