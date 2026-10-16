# Финальная волна правок перед приёмкой

Дата: 2026-10-16. Рабочая ветка: `master`.

## Находка 1 — кодовый и part-поиск не должен усекать листы

**Изменено:** `limit` оставлен только для BM25/FTS ветки. Кодовая и part-ветки запрашивают все совпадения из `Store`; проверка включает 12 листов и явный `limit=1`.

**Файлы:** `pdfgraph/query.py`, `tests/test_query.py`.

**RED**

Команда:

```text
venv\Scripts\python.exe -m pytest tests/test_query.py::test_code_query_is_not_truncated_by_default_limit tests/test_query.py::test_part_query_is_not_truncated_by_default_limit -q
```

Вывод:

```text
FF                                                                       [100%]
FAILED tests/test_query.py::test_code_query_is_not_truncated_by_default_limit - AssertionError: assert 10 == 12
FAILED tests/test_query.py::test_part_query_is_not_truncated_by_default_limit - AssertionError: assert 10 == 12
2 failed in 0.13s
```

**GREEN**

Команда:

```text
venv\Scripts\python.exe -m pytest tests/test_query.py::test_code_query_is_not_truncated_by_default_limit tests/test_query.py::test_part_query_is_not_truncated_by_default_limit -q
```

Вывод:

```text
..                                                                       [100%]
2 passed in 0.10s
```

## Находка 2 — уникальный ключ документа при совпадающих basename

**Изменено:** `build(..., root=None)` формирует POSIX-ключ относительно `root`; без `root` сохраняется basename для прямых старых вызовов. CLI связывает JSON с путём соответствующего PDF внутри корня обхода, поэтому `left/techcard.pdf` и `right/techcard.pdf` остаются отдельными. С тем же ключом строятся page text, sheet, фрагменты, ссылки и дефекты. Приёмочный корпус теперь хранит JSON в `docs/`, а CLI-тест проверяет раздельный текст и machine-проверяемые цитаты. Совместимы оба распространённых имени JSON: `techcard.json` и `techcard.pdf.json`.

**Файлы:** `pdfgraph/build.py`, `pdfgraph/cli.py`, `tests/test_graph_cli.py`, `tests/conftest.py`, `tests/test_graph_acceptance.py`.

**RED**

Команда:

```text
venv\Scripts\python.exe -m pytest tests/test_graph_cli.py::test_cli_keeps_same_named_documents_in_distinct_subfolders -q
```

Вывод:

```text
F                                                                        [100%]
FAILED tests/test_graph_cli.py::test_cli_keeps_same_named_documents_in_distinct_subfolders
assert {'techcard.pdf'} == {'left/techcard.pdf', 'right/techcard.pdf'}
1 failed in 0.08s
```

**GREEN**

Команда:

```text
venv\Scripts\python.exe -m pytest tests/test_graph_cli.py::test_cli_keeps_same_named_documents_in_distinct_subfolders -q
```

Вывод:

```text
.                                                                        [100%]
1 passed in 0.08s
```

Дополнительная проверка совместимости обнаружила после первого изменения три случая удвоенного `.pdf` для старых JSON `имя.pdf.json`.

RED:

```text
venv\Scripts\python.exe -m pytest -q
```

```text
..................................F.......FF............................ [ 54%]
.............................................................            [100%]
3 failed, 130 passed in 9.31s
```

Исправлено сохранением существующего `.pdf`-суффикса; GREEN:

```text
venv\Scripts\python.exe -m pytest tests/test_graph_cli.py::test_cli_ask_json_shape_and_verdict tests/test_graph_cli.py::test_cli_build_marks_defects_from_report tests/test_graph_cli.py::test_cli_build_report_garbage_lines_ignored -q
```

```text
...                                                                      [100%]
3 passed in 0.16s
```

## Находка 3 — неоднозначные OCR-ячейки не угадываются и не создают part

**Изменено:** введён `MIN_CELL_CONF = 0.5` для нормализованной уверенности Tesseract `[0, 1]`. Если уверенность хотя бы одного слова ниже порога, ячейка сериализуется пустой и отмечается `ambiguous=True`. Флаг проходит через `Table`, JSON v2 и конвертацию координат; старый JSON читается с пустой матрицей флагов. `pdfgraph.build` пропускает неоднозначные ячейки. Чистые ячейки продолжают распознаваться как раньше.

**Файлы:** `pdfprepare/tables.py`, `pdfprepare/models.py`, `pdfprepare/output.py`, `pdfprepare/pipeline.py`, `pdfgraph/build.py`, `tests/test_tables.py`, `tests/test_build.py`, `tests/test_output_v2.py`.

RED — извлекатель сохранял низкоуверенный текст вместо пустой ячейки:

```text
venv\Scripts\python.exe -m pytest tests/test_tables.py::test_low_confidence_cell_is_blank_and_marked_ambiguous -q
```

```text
F                                                                        [100%]
FAILED tests/test_tables.py::test_low_confidence_cell_is_blank_and_marked_ambiguous
assert '8МТ.6101.100' == ''
1 failed in 0.24s
```

RED — граф не принимал признак неоднозначности:

```text
venv\Scripts\python.exe -m pytest tests/test_build.py::test_build_does_not_extract_part_from_ambiguous_cell -q
```

```text
F                                                                        [100%]
FAILED tests/test_build.py::test_build_does_not_extract_part_from_ambiguous_cell
TypeError: Table.__init__() got an unexpected keyword argument 'ambiguous'
1 failed in 0.18s
```

RED — JSON round-trip терял дополнительный флаг:

```text
venv\Scripts\python.exe -m pytest tests/test_output_v2.py::test_table_ambiguity_is_optional_in_v2_json -q
```

```text
F                                                                        [100%]
FAILED tests/test_output_v2.py::test_table_ambiguity_is_optional_in_v2_json
assert [] == [[True, False]]
1 failed in 0.09s
```

GREEN, включая низкоуверенную и существующую высокоуверенную ячейки:

```text
venv\Scripts\python.exe -m pytest tests/test_tables.py::test_low_confidence_cell_is_blank_and_marked_ambiguous tests/test_tables.py::test_detect_and_extract_three_by_three -q
```

```text
..                                                                       [100%]
2 passed in 0.23s
```

GREEN — отсутствие part и обратная совместимость JSON:

```text
venv\Scripts\python.exe -m pytest tests/test_build.py::test_build_does_not_extract_part_from_ambiguous_cell tests/test_output_v2.py::test_table_ambiguity_is_optional_in_v2_json -q
```

```text
..                                                                       [100%]
2 passed in 0.08s
```

## Находка 4 — acceptance проверяет пары «документ + лист»

**Изменено:** раздельные проверки множеств документов и страниц заменены пересечением множества `(document, page)` с ожидаемыми парами. Ветка `ОТКАЗ` оставлена. Сохранённая логическая контрпроверка показывает, что прежний предикат мог принять два разных фрагмента, в то время как ожидаемой пары нет:

```text
venv\Scripts\python.exe -c "hits = [('docs/A.pdf', 2), ('docs/B.pdf', 1)]; docs = {'docs/A.pdf'}; pages = {1}; pairs = {('docs/A.pdf', 1)}; print(bool({doc for doc, _ in hits} & docs), bool({page for _, page in hits} & pages), bool(set(hits) & pairs))"
```

```text
True True False
```

**Файлы:** `tests/test_graph_acceptance.py`, `tests/conftest.py`.

GREEN — acceptance, включая отказ и точные пары цитирования:

```text
venv\Scripts\python.exe -m pytest tests/test_graph_acceptance.py -q
```

```text
........                                                                 [100%]
8 passed in 4.42s
```

## Финальная проверка

Команда:

```text
venv\Scripts\python.exe -m pytest -q
```

Вывод:

```text
........................................................................ [ 54%]
.............................................................            [100%]
133 passed in 9.54s
```

Исходная базовая линия: 127 passed. Ни один тест из неё не упал; добавлено 6 тестов.
