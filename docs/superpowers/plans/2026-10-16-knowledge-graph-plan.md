# Граф знаний для агента-инженера — план реализации

> **Для агентных исполнителей:** REQUIRED SUB-SKILL: используйте
> `superpowers:subagent-driven-development` (рекомендуется) или
> `superpowers:executing-plans` и ведите план по шагам с чекбоксами.
> Каждую задачу исполняет субагент соответствующего тира из `opencode.json`,
> после каждой задачи — независимый **worker-review**.

**Goal:** Достроить конвейер подготовки документов до графа знаний
«тип ВС → система → подсистема → документ → лист → таблица/рисунок» и дать
агента-инженера, который отвечает **только** по найденным фрагментам, каждое
утверждение подкрепляет **ссылкой на лист**, проверяет цитаты и отказывается
отвечать, когда подтверждений нет.

**Architecture:** Две новые подсистемы поверх готового `pdfprepare`:
(1) расширение подготовки — извлечение **таблиц как данных**, **опись рисунков**,
**ссылки «см. …»** и безопасная нормализация индексов; (2) пакет `pdfgraph` —
хранилище графа в SQLite (+FTS5), сборка графа из результатов подготовки, поиск,
экстрактивный сборщик ответа и обязательная проверка цитат. Финал — один
portable-каталог с двумя exe (оконный и консольный) и ярлыком.

**Tech Stack:** Python 3.12, PyMuPDF (`fitz`), `pytesseract` + встроенный
Tesseract (rus+eng), `Pillow`, `numpy`, `opencv-python-headless`, `sqlite3` (FTS5,
проверено: SQLite 3.49.1), PyQt5 5.15.11 (проверено, тесты — `QT_QPA_PLATFORM=offscreen`),
`pytest`, PyInstaller (one-folder).

**Spec:** `docs/superpowers/specs/2026-10-16-pdf-ocr-preparation-design.md`
(разделы 4.4, 4.5, 4.6 и 9 в MVP были явно отложены — этот план их реализует).
MVP-план: `docs/superpowers/plans/2026-10-16-pdf-preparation-mvp.md` (задачи 1–9
выполнены, задача 10 «packaging» выполнена частично: `pdfprepare.spec` есть,
`dist/` пуст).

## Global Constraints

- Только Windows, **zero-install**: готовый каталог копируется и запускается без
  установки; Tesseract и `tessdata` (rus, eng) лежат внутри каталога.
- Языки OCR: `"rus+eng"`.
- Навигация — по **кодам системы/подсистемы**, не по номеру листа.
- **Пустой лист = граница раздела**, не дефект: `is_blank=True`, не попадает в отчёт
  о пересканировании.
- Векторный поиск **не используется** (отвергнут заказчиком: «рвёт» таблицы).
- **Каждое утверждение ответа обязано нести ссылку на документ и лист**; цитата
  проверяется машинно; при отсутствии подтверждений правильный ответ — отказ.
- `raw`-текст документа никогда не перезаписывается нормализованным: нормализация
  создаёт только поисковый ключ.
- Справочник кодов: `Коды систем Вертолета.txt` (корень проекта), 298 записей.
- Артефакты в git добавлять **поимённо**; `git add -A` запрещён (втянет `venv/`, `pdfs/`).
- Тиры субагентов (из `opencode.json`): `fast` (механика, в плане уже готов код),
  `general` (несколько файлов, интеграция), `strong` (архитектура и раунды правок 4–5),
  `worker-review` (только чтение, ревью каждой задачи и финальное ревью ветки).

## Review Focus

Эти пять классов входа план обязан покрыть тестами — каждый в своей задаче:

1. **Код, разорванный OCR** (`023.11 .30`, лишний пробел, перенос строки) — извлечение
   кодов нормализует пробелы/точки и не падает.
2. **Полупустая страница-разделитель с кодом** — помечается `is_blank=True`, код
   сохраняется, лист **не** попадает в отчёт о пересканировании.
3. **Лист вообще без кода** — не падает; `system_code=""`, лист всё равно в графе.
4. **Числа из таблиц** — обозначение и количество берутся из ячеек; ячейка, которую
   не удалось прочитать однозначно, не «угадывается» (остаётся `""` + флаг).
5. **Гомоглифы** (`8МТ` vs `8MT`, `О23.11.30`) — поисковый ключ исправляется,
   реальный код/номер и `raw` не портятся; `023.10.00` остаётся `023.10.00`.

---

## File Structure

Новое и изменяемое (по одному назначению на файл):

**Расширение подготовки (`pdfprepare/`)**
- Modify `pdfprepare/models.py` — новые `Table`, `Figure`, `Reference`; поля в `PageInfo`; `SCHEMA_VERSION`.
- Create `pdfprepare/tables.py` — сетка таблицы по линиям (OpenCV) → ячейки.
- Create `pdfprepare/figures.py` — опись рисунков из подписей.
- Create `pdfprepare/refs.py` — ссылки «см. 028.10.00», «см. рис. 1».
- Create `pdfprepare/normalize.py` — поисковый ключ и ключ номера детали (без потери raw).
- Modify `pdfprepare/output.py` — запись/чтение JSON со схемой v2 (таблицы, рисунки, ссылки).
- Modify `pdfprepare/pipeline.py` — вызов новых экстракторов постранично.
- Create `pdfprepare/replace.py` — приём пересканированных листов и переобработка (дизайн, раздел 8).

**Граф и ответы (`pdfgraph/`)**
- Create `pdfgraph/__init__.py`
- Create `pdfgraph/store.py` — SQLite: узлы, рёбра, листы, FTS5.
- Create `pdfgraph/build.py` — сборка графа из результатов подготовки (идемпотентно).
- Create `pdfgraph/query.py` — поиск: код → листы, номер детали → лист, текст → BM25.
- Create `pdfgraph/answer.py` — `Answer`/`Claim` + экстрактивный сборщик + отказ.
- Create `pdfgraph/verify.py` — машинная проверка цитат по хранилищу.
- Create `pdfgraph/cli.py`, `pdfgraph/__main__.py` — `build`, `ask`, `verify`.

**Приложение и упаковка**
- Create `pdfgraph/gui/viewmodel.py` — чистые функции подготовки строк ответа.
- Create `pdfgraph/gui/app.py` — простое окно (дизайн — через скилл `impeccable`).
- Create `pdfgraph.spec` — один анализ, два exe: оконный + консольный.
- Create `Коды систем Вертолета.txt` — существует, не трогаем.

**Тесты**
- `tests/conftest.py` — общие фикстуры: `fx_store`, `fx_store_path`, `fx_prepared_dir`, `graph_corpus`
- `tests/test_tables.py`, `tests/test_figures.py`, `tests/test_refs.py`,
  `tests/test_normalize.py`, `tests/test_output_v2.py`, `tests/test_replace.py`
- `tests/test_store.py`, `tests/test_build.py`, `tests/test_query.py`,
  `tests/test_answer.py`, `tests/test_verify.py`, `tests/test_graph_cli.py`
- `tests/test_graph_acceptance.py` + `tests/fixtures/graph_corpus/` (генерируется в тесте)

---

# ФАЗА 0. Окружение и видимость кода для агентов

### Task 0.1: Включить граф кода (codebase-memory) и проиндексировать проект

**Тир:** выполняет человек/ведущий агент (требуется перезапуск OpenCode — субагент этого сделать не может).

**Почему:** сейчас MCP-сервер `codebase-memory-mcp` в сессии отмечен как
`failed` (провал был закэширован на старте сессии). Агенты обязаны видеть код
через граф, а не угадывать по grep.

- [ ] **Step 1: Перезапустить OpenCode** (выйти и запустить заново), затем:

Run: `opencode mcp list`
Expected: `✓ codebase-memory-mcp  connected`

- [ ] **Step 2: Проиндексировать проект** — инструментом `index_repository`
  (корень: `PDF_OCR_APP`, имя проекта `C-Users-user-Desktop-pythonCODE-PDF_OCR_APP`).

- [ ] **Step 3: Проверить видимость** — `list_projects` содержит проект;
  `search_graph(name_pattern=".*process_file.*")` возвращает узел
  `pdfprepare.pipeline.process_file`; `index_status` показывает свежую генерацию.

- [ ] **Step 4: Зафиксировать в задаче каждого субагента** требование: перед
  изменениями вызвать `search_graph`/`trace_path`, после — `check_index_coverage`
  по затронутым путям, и передавать эти доказательства в отчёте.

Expected: агент отвечает на «кто вызывает `process_file`» из графа, без grep.

---

### Task 0.2: Привести git в порядок перед большой работой

**Тир:** `general` · **Файлы:** Create `.gitignore`; Modify — существующие файлы проекта.

**Интерфейсы:** Produces — чистое рабочее дерево, готовое к пофайловым коммитам.

- [ ] **Step 1: Создать `.gitignore`** со строками:
  `venv/`, `.venv/`, `__pycache__/`, `*.pyc`, `pdfs/`, `OCRfile/`, `out/`, `dist/`,
  `build/`, `*.db`, `.codebase-memory/`, `.idea/`

- [ ] **Step 2: Проверить, что крупные данные не попадут в коммит**

Run: `git status --short --untracked-files=all | head -50`
Expected: `pdfs/`, `venv/`, `dist/`, `build/` не перечислены как untracked.

- [ ] **Step 3: Закоммитить текущую работу поимённо** (НЕ `git add -A`):
  `pdfprepare/`, `tests/`, `pdfprepare_launcher.py`, `prepare_pdf.bat`, `pdfprepare.spec`,
  `pyproject.toml`, `docs/superpowers/`, `AGENTS.md`, `README.txt`

```bash
git add pdfprepare tests pdfprepare_launcher.py prepare_pdf.bat pdfprepare.spec pyproject.toml docs AGENTS.md README.txt
git commit -m "chore: commit portable pipeline, launcher and project docs"
```

- [ ] **Step 4: Проверить**

Run: `git status --short`
Expected: пусто (или только явно намеренные файлы).

- [ ] **Step 5: Все тесты зелёные до начала работы**

Run: `venv\Scripts\python.exe -m pytest -q`
Expected: `36 passed` (базовая линия; далее число только растёт).

---

# ФАЗА 1. Недостающие данные: таблицы, рисунки, ссылки, нормализация

### Task 1.1: Модели структурных данных

**Тир:** `fast` · **Файлы:** Modify `pdfprepare/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION: int = 2`
  - `@dataclass Table: page_index: int; bbox: tuple[float, float, float, float]; rows: list[list[str]]`
  - `@dataclass Figure: page_index: int; number: str; caption: str`
  - `@dataclass Reference: page_index: int; kind: str; target: str; raw: str` (`kind ∈ {"system","figure"}`)
  - `PageInfo` получает `tables: list[Table] = field(default_factory=list)`, `figures: list[Figure] = ...`, `references: list[Reference] = ...`
  - `DocResult` получает `schema_version: int = SCHEMA_VERSION`

- [ ] **Step 1: Написать падающий тест**

```python
def test_structural_dataclasses_and_page_defaults():
    from pdfprepare.models import Table, Figure, Reference, PageInfo, SCHEMA_VERSION
    assert SCHEMA_VERSION == 2
    t = Table(page_index=3, bbox=(0.0, 0.0, 100.0, 50.0), rows=[["8МТ.6101.100", "2"]])
    assert t.rows[0][0] == "8МТ.6101.100"
    f = Figure(page_index=3, number="12", caption="Каталожный лист")
    r = Reference(page_index=3, kind="system", target="028.10.00", raw="см. 028.10.00")
    p = PageInfo(page_index=3, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert p.tables == [] and p.figures == [] and p.references == []
    assert f.number == "12" and r.kind == "system"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Table'`

- [ ] **Step 3: Реализовать** три dataclass-а и поля с `field(default_factory=list)`; `DocResult.schema_version: int = SCHEMA_VERSION`.

- [ ] **Step 4: Убедиться, что старые тесты не сломаны**

Run: `venv\Scripts\python.exe -m pytest tests/test_models.py tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/models.py tests/test_models.py
git commit -m "feat(models): Table/Figure/Reference and schema v2"
```

---

### Task 1.2: Извлечение таблиц как данных

**Тир:** `strong` (самая рискованная часть проекта: layout-анализ на 150 dpi) ·
**Файлы:** Create `pdfprepare/tables.py`, `tests/test_tables.py`

**Interfaces:**
- Produces:
  - `detect_table_bboxes(img: PIL.Image.Image) -> list[tuple[float, float, float, float]]` — регионы таблиц: морфология по линиям (`cv2.morphologyEx` H/V ядрами), пересечения → рамки, отбрасывание регионов меньше 3 рядов и 2 колонок.
  - `extract_tables(img: PIL.Image.Image, words: list[Word], page_index: int) -> list[Table]` — для каждого региона: ряды и колонки по координатным линиям; слово относится к ячейке по центру; текст ячейки = слова, склеенные одним пробелом; пустая ячейка = `""`.

- [ ] **Step 1: Написать падающий тест (синтетическая сетка, без реальных сканов)**

```python
def _grid_image(rows, cols, texts):
    from PIL import Image, ImageDraw
    img = Image.new("L", (600, 200 * rows), "white")
    d = ImageDraw.Draw(img)
    for r in range(rows + 1):
        d.line((10, 10 + r * 190, 590, 10 + r * 190), fill="black", width=3)
    for c in range(cols + 1):
        d.line((10 + c * 190, 10, 10 + c * 190, 10 + rows * 190), fill="black", width=3)
    for (r, c), txt in texts.items():
        d.text((30 + c * 190, 40 + r * 190), txt, fill="black")
    return img

def test_detect_and_extract_three_by_three():
    from pdfprepare.tables import detect_table_bboxes, extract_tables
    from pdfprepare.models import Word
    img = _grid_image(3, 3, {(0, 0): "N", (0, 1): "ОБОЗНАЧЕНИЕ", (1, 0): "3", (1, 1): "8МТ.6101.100"})
    boxes = detect_table_bboxes(img)
    assert len(boxes) == 1
    words = [Word("8МТ.6101.100", 220, 40, 400, 60, 0.95)]
    tables = extract_tables(img, words, page_index=7)
    assert len(tables) == 1 and len(tables[0].rows) == 3
    assert tables[0].rows[1][1] == "8МТ.6101.100"

def test_empty_cell_is_not_guessed():
    from pdfprepare.tables import extract_tables
    img = _grid_image(2, 2, {(0, 0): "N"})        # остальные ячейки пустые
    tables = extract_tables(img, [], page_index=0)
    assert tables[0].rows[0][1] == "" and tables[0].rows[1][0] == ""

def test_no_grid_returns_empty():
    from PIL import Image
    from pdfprepare.tables import detect_table_bboxes
    assert detect_table_bboxes(Image.new("L", (400, 200), "white")) == []
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `venv\Scripts\python.exe -m pytest tests/test_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdfprepare.tables'`

- [ ] **Step 3: Реализовать** `detect_table_bboxes` (OTSU → инверсия → два `morphologyEx` с ядрами `(60,1)` и `(1,60)` → `findContours`) и `extract_tables` (проекции тёмных пикселей по региону → координаты линий → бинаризация слов по ячейкам). Ячейку без слов оставлять `""`; ничего не «дорисовывать».

- [ ] **Step 4: Проверка + честный замер на реальном скане**

Run: `venv\Scripts\python.exe -m pytest tests/test_tables.py -v`
Expected: PASS

Run (замер, результат — в отчёт задачи): на `pdfs\Каталок деталей … Д-1701 … .pdf` (173 стр.)
посчитать, на скольких страницах найдена хотя бы одна таблица.

Expected: число зафиксировано в отчёте как метрика риска. Если найдено 0 таблиц —
задача **не** закрывается: эскалация, `strong` раунд 2, и в отчёт добавляется вывод
«на 150 dpi таблицы не восстанавливаются, нужен пересканированный лист».

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/tables.py tests/test_tables.py
git commit -m "feat(tables): detect grid regions and extract cells as data"
```

---

### Task 1.3: Опись рисунков

**Тир:** `fast` · **Файлы:** Create `pdfprepare/figures.py`, `tests/test_figures.py`

**Interfaces:**
- Produces: `extract_figures(text: str, page_index: int) -> list[Figure]` — ищет
  `Рис. 12`, `Рисунок 7`, `Рис. № 12`; `number` — только цифры; `caption` — остаток
  строки (обрезается до 120 символов); дубликаты по `number` схлопываются.

- [ ] **Step 1: Написать падающий тест**

```python
def test_figures_caption_and_dedup():
    from pdfprepare.figures import extract_figures
    text = "Рис. 12 Каталожный лист\nрис. 3\nРисунок 12 Повтор\nмусор"
    figs = extract_figures(text, page_index=4)
    assert [(f.number, f.caption) for f in figs] == [("12", "Каталожный лист"), ("3", "")]

def test_no_figures():
    from pdfprepare.figures import extract_figures
    assert extract_figures("нет рисунков", 0) == []
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_figures.py -v` → FAIL

- [ ] **Step 3: Реализовать** регулярным выражением `(?i)рис(?:\.|унок|\.\s*№)?\s*(\d{1,3})` по строкам, с обрезкой подписи.

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_figures.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/figures.py tests/test_figures.py
git commit -m "feat(figures): extract figure inventory from captions"
```

---

### Task 1.4: Ссылки между системами и рисунками

**Тир:** `fast` · **Файлы:** Create `pdfprepare/refs.py`, `tests/test_refs.py`

**Interfaces:**
- Produces: `extract_references(text: str, page_index: int) -> list[Reference]`:
  «см. 028.10.00» → `Reference(kind="system", target="028.10.00")`;
  «см. рис. 1» → `Reference(kind="figure", target="1")`;
  «см. 028.10.00, рис. 1» → **две** ссылки, порядок как в тексте.

- [ ] **Step 1: Написать падающий тест (примеры из дизайна, раздел 3)**

```python
def test_references_system_and_figure():
    from pdfprepare.refs import extract_references
    refs = extract_references("см. 028.10.00, рис. 1", page_index=9)
    assert [(r.kind, r.target) for r in refs] == [("system", "028.10.00"), ("figure", "1")]
    assert refs[0].raw == "см. 028.10.00"

def test_reference_to_missing_target_still_parsed():
    from pdfprepare.refs import extract_references
    refs = extract_references("см. 999.99.99", page_index=9)
    assert refs[0].target == "999.99.99"
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_refs.py -v` → FAIL

- [ ] **Step 3: Реализовать** через `pdfprepare.codes._CODE_FIND_RE` + нормализацию кода, плюс отдельный шаблон для `рис. N`.

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_refs.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/refs.py tests/test_refs.py
git commit -m "feat(refs): extract cross-system and figure references"
```

---

### Task 1.5: Безопасная нормализация (поисковый ключ, не правка данных)

**Тир:** `general` · **Файлы:** Create `pdfprepare/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Produces:
  - `normalize_for_search(text: str) -> str` — `NFKC`, все виды тире/кавычек → ASCII, `ё→е`, сжатие пробелов, нижний регистр. Только для индекса.
  - `normalize_part_number(raw: str) -> str` — верхний регистр, унификация тире/точек и пробелов; **буквы между алфавитами не переписываются** (никакого «угадывания»); исходная строка не меняется.

- [ ] **Step 1: Написать падающий тест (в том числе анти-цель «не портить код»)**

```python
def test_search_key_unifies_dashes_and_case():
    from pdfprepare.normalize import normalize_for_search
    assert normalize_for_search("Ми\u20118МТВ\u20111") == "ми-8мтв-1"
    assert normalize_for_search("ПРИВОД  ЁЛКА") == "привод елка"

def test_part_number_is_not_guessed_and_raw_untouched():
    from pdfprepare.normalize import normalize_part_number
    raw = " 8мт.6101.100 "
    assert normalize_part_number(raw) == "8МТ.6101.100"
    assert raw == " 8мт.6101.100 "          # исходная строка не изменилась
    assert normalize_part_number("8MT.6101.100") == "8MT.6101.100"  # латиница не переписана

def test_real_code_never_altered():
    from pdfprepare.codes import normalize_code
    assert normalize_code("023.10.00") == "023.10.00"
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_normalize.py -v` → FAIL

- [ ] **Step 3: Реализовать** оба преобразования, ничего не мутируя на месте.

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_normalize.py tests/test_codes.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/normalize.py tests/test_normalize.py
git commit -m "feat(normalize): search key and part-number key without data loss"
```

---

### Task 1.6: Интеграция в конвейер и JSON схемы v2

**Тир:** `general` · **Файлы:** Modify `pdfprepare/pipeline.py`, `pdfprepare/output.py`;
Create `tests/test_output_v2.py`

**Interfaces:**
- Consumes: `Table`/`Figure`/`Reference` (1.1), `extract_tables` (1.2), `extract_figures` (1.3), `extract_references` (1.4), `normalize_for_search` (1.5).
- Produces:
  - `process_file(...)` заполняет `PageInfo.tables` (только растровые страницы), `.figures`, `.references` — со **всех** страниц, включая пустые (ссылка на пустом листе теряться не должна).
  - `write_json(result, out_path)` пишет `schema_version` и три новых поля на страницу.
  - `read_result(path: str) -> DocResult` — читает JSON обратно (round-trip).
  - `pipeline.py` импортирует `extract_tables`, `extract_figures`, `extract_references` **на уровне модуля** (`from pdfprepare.tables import extract_tables`), иначе тесты не смогут подменить их через `monkeypatch.setattr(pl, ...)`.

- [ ] **Step 1: Написать падающий тест (round-trip + пустой лист + без кода)**

```python
def test_json_v2_round_trip_includes_structure(tmp_path, monkeypatch):
    import fitz, pdfprepare.pipeline as pl
    from pdfprepare.models import Word, Table, Figure, Reference
    from pdfprepare.output import read_result, write_json
    src = tmp_path / "in.pdf"; d = fitz.open(); d.new_page(); d.new_page(); d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [Word("023.11.30", 72, 700, 140, 710, 0.95)])
    monkeypatch.setattr(pl, "extract_tables", lambda img, words, page_index: [] )
    monkeypatch.setattr(pl, "extract_figures", lambda text, page_index: [Figure(page_index, "12", "Рис. 12")])
    monkeypatch.setattr(pl, "extract_references", lambda text, page_index: [Reference(page_index, "system", "028.10.00", "см. 028.10.00")])
    dst = tmp_path / "out"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})
    assert res.pages[0].figures[0].number == "12"
    assert res.pages[0].references[0].target == "028.10.00"
    back = read_result(str(dst / "in.json"))
    assert back.schema_version == 2 and back.pages[0].references[0].target == "028.10.00"
    assert back.pages[0].subsystem_code == "023.11.30"

def test_blank_page_with_code_not_reported(tmp_path, monkeypatch):
    # пустой лист с кодом: код сохранён, в отчёт не попал, таблиц нет
    import fitz, pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    src = tmp_path / "b.pdf"; d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [Word("023.11.30", 72, 700, 140, 710, 0.95)])
    dst = tmp_path / "o"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})
    assert res.pages[0].subsystem_code == "023.11.30"
    assert (dst / "b.report.txt").read_text(encoding="utf-8") == ""

def test_sheet_without_any_code_is_still_processed(tmp_path, monkeypatch):
    # лист вообще без кода: не падаем, код пустой, лист в результате есть
    import fitz, pdfprepare.pipeline as pl
    from pdfprepare.models import Word
    src = tmp_path / "n.pdf"; d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    monkeypatch.setattr(pl, "ocr_image", lambda img, lang="rus+eng": [Word("просто", 72, 700, 120, 710, 0.9)])
    dst = tmp_path / "o"; dst.mkdir()
    res = pl.process_file(str(src), str(dst), {})
    assert res.pages[0].system_code == "" and res.pages[0].subsystem_code == ""
    assert (dst / "n.json").exists()
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_output_v2.py -v` → FAIL

- [ ] **Step 3: Реализовать** вызовы экстракторов в `process_file` и сериализацию/десериализацию в `output.py`. Старое чтение JSON без новых полей должно работать: отсутствующие ключи → пустые списки.

- [ ] **Step 4: Проверка полного набора.** Run: `pytest -q` → PASS (было 36, стало больше)

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/pipeline.py pdfprepare/output.py tests/test_output_v2.py
git commit -m "feat(pipeline): emit tables/figures/references in schema v2 JSON"
```

---

### Task 1.7: Приём пересканированных листов (эскалация человеком)

**Тир:** `general` · **Файлы:** Create `pdfprepare/replace.py`, `tests/test_replace.py`

**Why:** дизайн (раздел 8) требует замкнуть цикл: программа сообщает, какие листы ей
нужны, человек пересканирует их в лучшем качестве и присылает, программа подставляет
новые сканы и переобрабатывает.

**Interfaces:**
- Produces:
  - `@dataclass Replacement: page_index: int; source: str` (`source` — путь к PDF с одним листом либо к изображению этого листа).
  - `plan_replacements(report_path: str) -> list[int]` — читает `<имя>.report.txt` и возвращает номера листов, требующих перескана (в том же виде, что и в отчёте).
  - `apply_replacements(src_pdf: str, replacements: list[Replacement], out_pdf: str) -> int` — собирает новый PDF: названные листы заменяются страницами из `source` (у PDF берётся первая страница; картинка вставляется как страница того же размера), прочие листы остаются без изменений; возвращает число заменённых листов. Несоответствие числа замен бросает `ValueError` с явным текстом.

- [ ] **Step 1: Написать падающий тест**

```python
def test_plan_replacements_reads_report(tmp_path):
    from pdfprepare.replace import plan_replacements
    rep = tmp_path / "d.report.txt"
    rep.write_text(
        "page 2: low_confidence (conf=0.41, coverage=0.0300)\n"
        "page 5: low_text_coverage (conf=0.90, coverage=0.0010)\n", encoding="utf-8")
    assert plan_replacements(str(rep)) == [2, 5]

def test_apply_replacements_swaps_only_named_sheets(tmp_path):
    import fitz
    from pdfprepare.replace import Replacement, apply_replacements
    src = tmp_path / "d.pdf"; d = fitz.open()
    for i in range(4):
        d.new_page().insert_text((72, 72), f"ЛИСТ {i}")
    d.save(str(src)); d.close()
    new = tmp_path / "new.pdf"; nd = fitz.open()
    nd.new_page().insert_text((72, 72), "ПЕРЕСКАНИРОВАН"); nd.save(str(new)); nd.close()
    out = tmp_path / "out.pdf"
    assert apply_replacements(str(src), [Replacement(2, str(new))], str(out)) == 1
    doc = fitz.open(str(out))
    assert doc.page_count == 4
    assert "ПЕРЕСКАНИРОВАН" in doc[2].get_text()
    assert "ЛИСТ 1" in doc[1].get_text() and "ЛИСТ 3" in doc[3].get_text()
    doc.close()

def test_page_index_out_of_range_raises(tmp_path):
    import fitz, pytest
    from pdfprepare.replace import Replacement, apply_replacements
    src = tmp_path / "d.pdf"; d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    with pytest.raises(ValueError, match="лист 9"):
        apply_replacements(str(src), [Replacement(9, str(src))], str(tmp_path / "o.pdf"))
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_replace.py -v` → FAIL

- [ ] **Step 3: Реализовать** `plan_replacements` (регулярное выражение `^page (\d+):`) и `apply_replacements` (`fitz.open`, `insert_pdf`/`show_pdf_page` по листам).

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_replace.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfprepare/replace.py tests/test_replace.py
git commit -m "feat(replace): accept re-scanned sheets and rebuild the document"
```

---

# ФАЗА 2. Граф, поиск и ответы с доказательствами

### Task 2.1: Хранилище графа

**Тир:** `strong` (схема — архитектурное решение) · **Файлы:** Create
`pdfgraph/__init__.py`, `pdfgraph/store.py`, `tests/test_store.py`

**Interfaces:**
- Produces:
  - `class Store:` — `Store(path: str)`, `close()`, `add_node(kind: str, key: str, label: str = "", document: str = "", page: int = -1) -> int` (идемпотентно по `(kind, key)`), `add_edge(src: int, dst: int, kind: str, detail: str = "", document: str = "", page: int = -1) -> None` (идемпотентно), `upsert_page(document: str, page: int, aircraft_type: str, system_code: str, subsystem_code: str, text: str, search_text: str) -> None`, `page_text(document: str, page: int) -> str`, `defect_sheets() -> list[tuple[str, int]]`, `stats() -> dict[str, int]` с ключами `{"nodes", "edges", "sheets", "pending"}`.
  - Виды узлов: `type`, `system`, `subsystem`, `document`, `sheet`, `part`, `figure`, `pending_reference`.
  - Виды рёбер: `contained_in`, `documented_on`, `has_part`, `has_figure`, `references_system`, `references_figure`.
  - SQLite: `nodes(kind,key,label,document,page, UNIQUE(kind,key))`, `edges(src,dst,kind,document,page,detail, UNIQUE(...))`, `pages(document,page,aircraft_type,system_code,subsystem_code,text, PK(document,page))`, `pages_fts USING fts5(document UNINDEXED, page UNINDEXED, body)`, `meta(key,value)` с `schema_version = 1`.

- [ ] **Step 1: Написать падающий тест**

```python
def test_nodes_and_edges_are_idempotent(tmp_path):
    from pdfgraph.store import Store
    s = Store(str(tmp_path / "g.db"))
    a = s.add_node("system", "023.00.00", label="СВЯЗНОЕ РАДИООБОРУДОВАНИЕ")
    b = s.add_node("system", "023.00.00")
    assert a == b
    sub = s.add_node("subsystem", "023.11.30")
    s.add_edge(sub, a, "contained_in"); s.add_edge(sub, a, "contained_in")
    st = s.stats()
    assert st["nodes"] == 2 and st["edges"] == 1 and st["pending"] == 0
    s.close()

def test_pages_round_trip(tmp_path):
    from pdfgraph.store import Store
    s = Store(str(tmp_path / "g.db"))
    s.upsert_page("RTЭ кн.1.pdf", 47, "Ми-8МТВ", "023.00.00", "023.11.30", "полный текст", "полный текст")
    assert s.page_text("RTЭ кн.1.pdf", 47) == "полный текст"
    s.close()
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_store.py -v` → FAIL

- [ ] **Step 3: Реализовать** схему и методы; `add_node` — `INSERT ... ON CONFLICT(kind,key) DO UPDATE ... RETURNING id`; `add_edge` — `INSERT OR IGNORE`; включить `PRAGMA journal_mode=WAL`.

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_store.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/__init__.py pdfgraph/store.py tests/test_store.py
git commit -m "feat(store): SQLite graph store with nodes, edges, pages and FTS5"
```

---

### Task 2.2: Сборка графа из результатов подготовки

**Тир:** `general` · **Файлы:** Create `pdfgraph/build.py`, `tests/test_build.py`

**Interfaces:**
- Consumes: `DocResult`, `PageInfo`, `Table`, `Figure`, `Reference` (1.1/1.6), `Store` (2.1), `normalize_for_search`/`normalize_part_number` (1.5).
- Produces: `build(results: list[DocResult], aircraft_type: str, registry: dict[str, str], store: Store) -> dict[str, int]` — создаёт узел типа ВС, системы/подсистемы (только те, что реально встречаются, с названием из `registry`), документ, листы, детали из ячеек таблиц, рисунки; рёбра по таблице выше; ссылки на отсутствующие цели — узел `pending_reference` с ключом цели. Возвращает `{"nodes": n, "edges": m, "sheets": k, "pending": p}`. Для кодов третьего уровня (`XXX.YY.ZZ`), которых нет в справочнике, `label` — строка текста листа, содержащая этот код (обрезанная до 120 символов), иначе пусто.

- [ ] **Step 1: Написать падающий тест (форма графа + идемпотентность)**

```python
def test_build_shapes_the_graph(tmp_path):
    from pdfprepare.models import Word, PageInfo, DocResult, Figure, Reference
    from pdfgraph.store import Store
    from pdfgraph.build import build
    p1 = PageInfo(page_index=47, is_native_text=False, is_blank=False,
                  system_code="023.00.00", subsystem_code="023.11.30",
                  full_text="АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А",
                  words=[Word("ПАРМ-406А", 100, 100, 200, 120, 0.9)],
                  figures=[Figure(47, "12", "Рис. 12")],
                  references=[Reference(47, "system", "028.10.00", "см. 028.10.00")])
    p2 = PageInfo(page_index=48, is_native_text=False, is_blank=True,
                  system_code="023.00.00", subsystem_code="023.11.30",
                  full_text="", words=[])
    res = DocResult(source_path="РТЭ кн.1.pdf", page_count=2, pages=[p1, p2],
                    system_names={"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    s = Store(str(tmp_path / "g.db"))
    stats = build([res], "Ми-8МТВ", {"023.11.30": "АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А"}, s)
    assert stats["sheets"] == 2
    again = build([res], "Ми-8МТВ", {}, s)          # повторная сборка не удваивает
    assert again["nodes"] == stats["nodes"] and again["edges"] == stats["edges"]
    assert again["pending"] == 1                    # цель 028.10.00 в графе отсутствует
    s.close()
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_build.py -v` → FAIL

- [ ] **Step 3: Реализовать** сборку. Пустой лист (`is_blank=True`) — обычный узел листа, **без** флага дефекта и без попадания в отчёт о перескане.

- [ ] **Step 4: Проверка + честный прогон на реальных данных**

Run: `pytest tests/test_build.py -v` → PASS

Run (замер, в отчёт задачи): собрать граф по уже подготовленной папке (например, результат Task 1.6 на `OCRfile`) и вывести `stats()`.
Expected: числа зафиксированы в отчёте — сколько узлов/рёбер/листов/«висящих» ссылок.

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/build.py tests/test_build.py
git commit -m "feat(build): assemble knowledge graph from prepared documents"
```

---

### Task 2.3: Поиск по графу

**Тир:** `general` · **Файлы:** Create `pdfgraph/query.py`, `tests/test_query.py`,
`tests/conftest.py`

**Interfaces:**
- Consumes: `Store` (2.1), `codes._CODE_FIND_RE`/`normalize_code` (MVP Task 3), `normalize_for_search`/`normalize_part_number` (1.5).
- Produces (фикстуры `tests/conftest.py`, используются задачами 2.3–3.2 — заготовка одна и та же, чтобы ожидания тестов совпадали):
  - `fx_store` — `Store` с тремя листами: лист 47 (`system_code="023.00.00"`, `subsystem_code="023.11.30"`, текст содержит «АВАРИЙНЫЙ ПЕРЕНОСНОЙ РАДИОМАЯК ПАРМ-406А», таблица с деталью `8МТ.6101.100`), лист 48 (тот же код, текст «радиомаяк»), лист 12 (код `028.10.00`, без деталей).
  - `fx_store_path` — путь к тому же хранилищу (строка) для тестов окна.
  - `fx_prepared_dir` — папка с JSON схемы v2 тех же трёх листов (для CLI-тестов).
- Produces:
  - `@dataclass Fragment: document: str; page: int; system_code: str; subsystem_code: str; snippet: str; char_start: int; char_end: int; score: float; kind: str`
  - `search(store: Store, query: str, aircraft_type: str | None = None, limit: int = 10) -> list[Fragment]`
    - запрос-код → все листы этого кода и подсистемы, `kind="code"`, `score=1.0`;
    - запрос-номер детали → листы, где деталь встречается в таблице, `kind="part"`, `score=1.0`;
    - иначе FTS5 `MATCH` с `bm25()`, `score = 1 / (1 + |bm25|)`, `kind="text"`;
    - `snippet` — 200 символов вокруг совпадения; `char_start/char_end` — смещения **в сохранённом тексте листа** (нужны для проверки цитат).

- [ ] **Step 1: Написать падающий тест (фикстура: 3 листа, 2 кода, 1 деталь)**

```python
def test_code_query_returns_every_sheet_of_that_code(fx_store):
    from pdfgraph.query import search
    hits = search(fx_store, "023.11.30")
    assert hits and all(h.kind == "code" for h in hits)
    assert {h.page for h in hits} == {47, 48}          # оба листа подсистемы

def test_part_number_query_hits_table_sheet(fx_store):
    from pdfgraph.query import search
    hits = search(fx_store, "8МТ.6101.100")
    assert hits[0].kind == "part" and hits[0].page == 47

def test_free_text_is_ranked_and_unknown_gives_nothing(fx_store):
    from pdfgraph.query import search
    hits = search(fx_store, "радиомаяк ПАРМ-406А")
    assert hits and hits[0].page == 47 and 0.0 < hits[0].score <= 1.0
    assert search(fx_store, "совершенно отсутствующая тема") == []
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_query.py -v` → FAIL

- [ ] **Step 3: Реализовать** три ветки поиска; в FTS искать по `normalize_for_search`, а `snippet` брать из исходного `text` (смещения — по исходному тексту).

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_query.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/query.py tests/test_query.py
git commit -m "feat(query): code, part-number and BM25 text search over the graph"
```

---

### Task 2.4: Ответ с доказательствами и честный отказ

**Тир:** `strong` (здесь решается «не галлюцинировать») · **Файлы:** Create
`pdfgraph/answer.py`, `tests/test_answer.py`

**Interfaces:**
- Consumes: `Fragment`, `search` (2.3).
- Produces:
  - `@dataclass Claim: text: str; evidence: list[int]`
  - `@dataclass Answer: question: str; claims: list[Claim]; fragments: list[Fragment]; refusal: bool = False; reason: str = ""`
  - `class Answerer(Protocol): def answer(self, question: str, fragments: list[Fragment]) -> Answer: ...`
  - `class ExtractiveAnswerer:` — детерминированный, **без модели**: до `max_claims=3` утверждений, текст утверждения = `fragment.snippet`, `evidence=[индекс]`.
  - `def build_answer(store, question: str, aircraft_type: str | None = None, answerer: Answerer | None = None, min_score: float = 0.35) -> Answer` — если фрагментов нет или лучший `score < min_score` → `Answer(refusal=True, reason="нет подтверждений в документах")`.

- [ ] **Step 1: Написать падающий тест (отказ + доказательства + отсутствие выдумки)**

```python
def test_refusal_when_nothing_found(fx_store):
    from pdfgraph.answer import build_answer
    a = build_answer(fx_store, "какой момент затяжки болтов шасси?")
    assert a.refusal is True and a.claims == [] and "подтвержд" in a.reason

def test_every_claim_has_evidence_and_is_substring_of_it(fx_store):
    from pdfgraph.answer import build_answer
    a = build_answer(fx_store, "радиомаяк ПАРМ-406А")
    assert a.refusal is False and a.claims
    for claim in a.claims:
        assert claim.evidence
        for idx in claim.evidence:
            assert claim.text in a.fragments[idx].snippet      # текст не выдуман
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_answer.py -v` → FAIL

- [ ] **Step 3: Реализовать** `ExtractiveAnswerer` и `build_answer`. Интерфейс `Answerer` оставить открытым для будущей модели, но **любой** ответ обязан проходить `verify_citations` (Task 2.5) — это условие записи в документации модуля.

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_answer.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/answer.py tests/test_answer.py
git commit -m "feat(answer): extractive answers with mandatory evidence and refusal"
```

---

### Task 2.5: Машинная проверка цитат

**Тир:** `general` · **Файлы:** Create `pdfgraph/verify.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `Answer`, `Claim` (2.4), `Store` (2.1).
- Produces:
  - `@dataclass CitationCheck: document: str; page: int; claim_index: int; ok: bool; reason: str`
  - `def verify_citations(answer: Answer, store: Store) -> list[CitationCheck]` — для каждого утверждения и каждой ссылки: лист существует в графе **и** текст утверждения буквально содержится в тексте этого листа. Иначе `ok=False` с причиной `"лист отсутствует в графе"` / `"текст не найден на листе"`.
  - `def all_verified(checks: list[CitationCheck]) -> bool`

- [ ] **Step 1: Написать падающий тест (включая подделку — это и есть гарантия «не галлюцинировать»)**

```python
def test_correct_answer_verifies(fx_store):
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations, all_verified
    a = build_answer(fx_store, "радиомаяк ПАРМ-406А")
    assert all_verified(verify_citations(a, fx_store)) is True

def test_tampered_claim_is_rejected(fx_store):
    from pdfgraph.answer import build_answer
    from pdfgraph.verify import verify_citations, all_verified
    a = build_answer(fx_store, "радиомаяк ПАРМ-406А")
    a.claims[0].text = "ПАРМ-406А устанавливается на хвостовой балке"
    checks = verify_citations(a, fx_store)
    assert all_verified(checks) is False
    assert any(c.reason == "текст не найден на листе" for c in checks)

def test_missing_sheet_is_rejected(fx_store):
    from pdfgraph.answer import Answer, Claim
    from pdfgraph.query import Fragment
    from pdfgraph.verify import verify_citations
    a = Answer(question="q", claims=[Claim("есть", [0])],
               fragments=[Fragment("нет-такого.pdf", 999, "", "", "есть", 0, 4, 1.0, "text")])
    checks = verify_citations(a, fx_store)
    assert checks[0].ok is False and checks[0].reason == "лист отсутствует в графе"
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_verify.py -v` → FAIL

- [ ] **Step 3: Реализовать** проверку через `Store.page_text` и подстрочный поиск по нормализованному тексту (нормализация только для сверки, `raw` не меняется).

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_verify.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/verify.py tests/test_verify.py
git commit -m "feat(verify): machine-check every citation against the stored sheet text"
```

---

### Task 2.6: Консольный интерфейс графа

**Тир:** `general` · **Файлы:** Create `pdfgraph/cli.py`, `pdfgraph/__main__.py`,
`tests/test_graph_cli.py`

**Interfaces:**
- Produces:
  - `python -m pdfgraph build <prepared_dir> <store_path> --type <ВС> [--registry <path>]` — читает `<prepared_dir>/**/*.json` через `read_result` (1.6), строит граф (2.2), печатает `stats()`.
  - `python -m pdfgraph ask "<вопрос>" --store <path> [--type <ВС>] [--json]` — отвечает, **всегда** печатает результат `verify_citations`: количество подтверждённых цитат и вердикт.
  - `main(argv) -> int`; код возврата 1 при неподтверждённых цитатах (в `--json`-режиме факт виден полем `verified`).

- [ ] **Step 1: Написать падающий тест (без окна, всё через CLI)**

```python
def test_cli_build_then_ask_json(fx_prepared_dir, tmp_path):
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    assert main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"]) == 0
    rc = main(["ask", "радиомаяк ПАРМ-406А", "--store", store, "--json"])
    # вывод перехватывается capsys; проверяем форму и вердикт
    assert rc == 0

def test_cli_ask_unknown_topic_refuses(fx_prepared_dir, tmp_path, capsys):
    from pdfgraph.cli import main
    store = str(tmp_path / "g.db")
    main(["build", str(fx_prepared_dir), store, "--type", "Ми-8МТВ"])
    assert main(["ask", "момент затяжки болтов шасси", "--store", store]) == 1
    assert "подтвержд" in capsys.readouterr().out
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_graph_cli.py -v` → FAIL

- [ ] **Step 3: Реализовать** `argparse`-подкоманды, JSON-вывод (`ensure_ascii=False`), и печать блока «Доказательства» с `документ, лист, код`.

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_graph_cli.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/cli.py pdfgraph/__main__.py tests/test_graph_cli.py
git commit -m "feat(cli): build and ask subcommands with citation verdict"
```

---

### Task 2.7: Приёмочный тест «граф точно работает»

**Тир:** `general` · **Файлы:** Create `tests/test_graph_acceptance.py`,
`tests/fixtures/graph_corpus/` (генерируется в тесте через PyMuPDF + Tesseract)

**Interfaces:**
- Consumes: весь конвейер (1.x) + граф (2.x).
- Produces: `graph_corpus` (fixture) — три PDF: (1) техкарта с таблицей 3×3 и кодом
  `023.11.30`, (2) лист с рисунком `Рис. 12` и ссылкой `см. 028.10.00, рис. 1`,
  (3) пустой лист-разделитель с кодом `023.00.00`.
- Produces: `Store.defect_sheets() -> list[tuple[str, int]]` — листы, попавшие в отчёт о
  пересканировании. Пустые листы туда не попадают **никогда**.

- [ ] **Step 1: Написать падающий тест с вопросами и ожидаемыми ссылками**

```python
QUESTIONS = [
    ("023.11.30",                        {"023.11.30"}, {"docs/techcard.pdf"}, {1}),
    ("8МТ.6101.100",                     None,          {"docs/techcard.pdf"}, {1}),
    ("аварийный переносной радиомаяк",   None,          {"docs/techcard.pdf"}, {1}),
    ("рисунок 12",                        None,         {"docs/figure.pdf"},   {1}),
    ("что сказано про ремонт двигателя", "ОТКАЗ",       set(),                 set()),
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
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_graph_acceptance.py -v` → FAIL (нет фикстуры/метода `defect_sheets`)

- [ ] **Step 3: Реализовать** фикстуру (генерация PDF+tesseract один раз на сессию) и
  `Store.defect_sheets() -> list[tuple[str, int]]` — листы, попавшие в отчёт о
  пересканировании (пустые листы туда не попадают **никогда**).

- [ ] **Step 4: Проверка — это и есть доказательство работы графа**

Run: `pytest tests/test_graph_acceptance.py -v`
Expected: все 6 вопросов: либо ответ со **100 % подтверждённых цитат**, либо честный отказ.

- [ ] **Step 5: Коммит**

```bash
git add tests/test_graph_acceptance.py pdfgraph/store.py
git commit -m "test(acceptance): golden questions with verified citations and honest refusal"
```

---

# ФАЗА 3. Приложение и exe с понятным дизайном

### Task 3.1: Чистая логика экрана (view-model)

**Тир:** `fast` · **Файлы:** Create `pdfgraph/gui/__init__.py`, `pdfgraph/gui/viewmodel.py`,
`tests/test_gui_viewmodel.py`

**Interfaces:**
- Produces:
  - `@dataclass AnswerRow: text: str; document: str; page: int; code: str; verified: bool; snippet: str`
  - `def answer_rows(answer: Answer, checks: list[CitationCheck]) -> list[AnswerRow]`
  - `def verdict_text(rows: list[AnswerRow]) -> str` — «Подтверждено N из N» либо «Ответ отклонён: нет подтверждений на листах».

- [ ] **Step 1: Написать падающий тест**

```python
def test_rows_mark_verified_and_build_verdict():
    from pdfgraph.gui.viewmodel import answer_rows, verdict_text
    from pdfgraph.answer import Answer, Claim
    from pdfgraph.query import Fragment
    from pdfgraph.verify import CitationCheck
    frags = [Fragment("РТЭ кн.1.pdf", 47, "023.00.00", "023.11.30", "ПАРМ-406А", 0, 9, 1.0, "text")]
    a = Answer(question="q", claims=[Claim("ПАРМ-406А", [0])], fragments=frags)
    checks = [CitationCheck("РТЭ кн.1.pdf", 47, 0, True, "")]
    rows = answer_rows(a, checks)
    assert rows[0].verified is True and rows[0].page == 47 and rows[0].code == "023.00.00"
    assert verdict_text(rows) == "Подтверждено 1 из 1"
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_gui_viewmodel.py -v` → FAIL

- [ ] **Step 3: Реализовать** обе функции без импорта Qt (чистый Python — тестируется мгновенно).

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_gui_viewmodel.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/gui/__init__.py pdfgraph/gui/viewmodel.py tests/test_gui_viewmodel.py
git commit -m "feat(gui): pure view-model for answers, sources and verdict"
```

---

### Task 3.2: Окно приложения (дизайн через скилл `impeccable`)

**Тир:** `general` · **Файлы:** Create `pdfgraph/gui/app.py`, `tests/test_gui_smoke.py`

**Design brief (для скилла `impeccable`):** одно окно, ничего лишнего, крупный
шрифт, высокий контраст; сверху — поле вопроса и кнопка «Спросить»; под ними —
таблица «Ответ / Документ / Лист / Код / Проверено»; ниже — панель
«Подтверждение» с текстом фрагмента; внизу — строка вердикта
(«Подтверждено N из N» / «Ответ отклонён — нет подтверждений на листах»).
Обязательно: отказ показывается **явно и спокойно**, не выглядит как ошибка.

**Interfaces:**
- Consumes: `build_answer` (2.4), `verify_citations` (2.5), `answer_rows`/`verdict_text` (3.1), `search` (2.3), фикстура `fx_store_path` (2.3).
- Produces: `class MainWindow(QMainWindow):` — `__init__(store_path: str, registry_path: str)`, `ask(question: str) -> list[AnswerRow]` (вызывается и тестом, и кнопкой), `open_sheet(document: str, page: int) -> None`.

- [ ] **Step 1: Написать падающий тест (невидимый Qt, без `pytest-qt`)**

```python
def test_window_answers_and_shows_verified(fx_store_path, monkeypatch):
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from pdfgraph.gui.app import MainWindow
    w = MainWindow(fx_store_path, "Коды систем Вертолета.txt")
    rows = w.ask("радиомаяк ПАРМ-406А")
    assert rows and rows[0].verified is True
    assert "Подтверждено" in w.verdict_label.text()
    w.close()
```

- [ ] **Step 2: Убедиться, что тест падает.** Run: `pytest tests/test_gui_smoke.py -v` → FAIL

- [ ] **Step 3: Реализовать окно** по брифу (перед реализацией — вызвать скилл `impeccable`).

- [ ] **Step 4: Проверка.** Run: `pytest tests/test_gui_smoke.py -v` → PASS

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph/gui/app.py tests/test_gui_smoke.py
git commit -m "feat(gui): single-window knowledge-graph UI with visible verdict"
```

---

### Task 3.3: Один portable-каталог, два exe

**Тир:** `general` · **Файлы:** Create `pdfgraph.spec`; Modify `pyproject.toml`

**Interfaces:**
- Produces: `dist/pdfgraph/` с `pdfgraph.exe` (оконный, `console=False`,
  запуск двойным щелчком) и `pdfgraph-cli.exe` (`console=True`, подкоманды из 2.6);
  внутрь попадают `tesseract/` + `tessdata` (rus, eng) и иконка.

- [ ] **Step 1: Написать spec** — один `Analysis`, два `EXE` (`console=False/True`),
  `COLLECT(name="pdfgraph")`, `datas=[("pdfprepare/tesseract", "tesseract")]`.

- [ ] **Step 2: Собрать**

Run: `venv\Scripts\pyinstaller.exe pdfgraph.spec`
Expected: `dist\pdfgraph\pdfgraph.exe` и `dist\pdfgraph\pdfgraph-cli.exe` существуют.

- [ ] **Step 3: Проверка консольного exe на приёмочном корпусе**

Run: `dist\pdfgraph\pdfgraph-cli.exe ask "радиомаяк ПАРМ-406А" --store tests\fixtures\graph.db --json`
Expected: exit 0, валидный JSON, `"verified": true`.

- [ ] **Step 4: Проверка оконного exe (запускается и не падает)**

Run: запустить `dist\pdfgraph\pdfgraph.exe`, подождать 3 с, убедиться, что процесс жив, затем закрыть.
Expected: процесс жив 3 с (нет падения на старте); в отчёт — размер каталога `dist\pdfgraph`.

- [ ] **Step 5: Коммит**

```bash
git add pdfgraph.spec pyproject.toml
git commit -m "feat(packaging): one-folder build with windowed and console exe"
```

---

### Task 3.4: Ярлык и инструкция

**Тир:** `fast` · **Файлы:** Create `Граф знаний.bat`; Create ярлык на рабочем столе

- [ ] **Step 1: Создать `.bat`** (ASCII-содержимое, кириллица только в названии ярлыка)
  — запускает `dist\pdfgraph\pdfgraph.exe`, `cd /d "%~dp0"`.

- [ ] **Step 2: Создать ярлык на рабочем столе** с иконкой и проверкой: цель существует,
  рабочая папка = корень проекта, имя русское (`Граф знаний.lnk`).

- [ ] **Step 3: Проверка двойным щелчком** — окно открывается, вопрос задаётся, вердикт виден.

- [ ] **Step 4: Коммит**

```bash
git add "Граф знаний.bat"
git commit -m "feat: desktop launcher for the knowledge-graph app"
```

---

## Критерии приёмки (что значит «проверено»)

| № | Критерий | Как проверяется |
|---|---|---|
| 1 | Все тесты зелёные | `venv\Scripts\python.exe -m pytest -q` — не менее 60 тестов |
| 2 | Каждый ответ подтверждён | `test_acceptance_answers_with_verified_citations` — `all_verified(checks) is True` |
| 3 | Выдумка отбраковывается | `test_tampered_claim_is_rejected` |
| 4 | Нет подтверждений — отказ | `test_refusal_when_nothing_found`, `test_cli_ask_unknown_topic_refuses` |
| 5 | Поиск по коду даёт **все** листы кода | `test_code_query_returns_every_sheet_of_that_code` |
| 6 | Пустой лист — не дефект | `test_blank_page_with_code_not_reported`, `test_blank_divider_is_not_a_defect` |
| 7 | Повторная сборка графа идемпотентна | `test_nodes_and_edges_are_idempotent`, `test_build_shapes_the_graph` |
| 8 | Числа берутся из таблиц | `test_part_number_query_hits_table_sheet` |
| 9 | exe работает на чистой машине | `pdfgraph-cli.exe ask … --json` → `"verified": true` |

## Риски и честные ограничения

1. **Таблицы на 150 dpi** — главный риск. Замер Task 1.2 даёт число страниц с найденной
   таблицей; при нуле задача не закрывается, а эскалируется (`strong`), и в отчёте
   фиксируется вывод «нужен пересканированный лист». Это не «недоделка», а свойство
   исходника, которое дизайн признаёт (раздел 5, не-цели).
2. **Ответы пока экстрактивные** (цитаты вместо пересказа) — сознательный выбор ради
   запрета на выдумку. Подключение модели — отдельная задача: интерфейс `Answerer`
   готов, проверка цитат обязательна для любого исполнителя.
3. **Размер каталога** после упаковки (PyQt5 + Tesseract) — порядка сотен мегабайт;
   число записывается в отчёт Task 3.3.
4. **«Висячие» ссылки** (`см. 028.10.00`, документа которого нет) — узел
   `pending_reference` + счётчик в `stats()`; это честный сигнал «книга не загружена».

## Порядок исполнения и роли субагентов

| Задача | Тир | Ревью |
|---|---|---|
| 0.1 | человек | — |
| 0.2 | general | worker-review |
| 1.1, 1.3, 1.4 | fast | worker-review |
| 1.2 | **strong** | worker-review (+ раунды 2–5 при провале замера) |
| 1.5, 1.6, 1.7 | general | worker-review |
| 2.1, 2.4 | **strong** | worker-review |
| 2.2, 2.3, 2.5, 2.6, 2.7 | general | worker-review |
| 3.1, 3.4 | fast | worker-review |
| 3.2, 3.3 | general | worker-review |
| финал | — | **worker-review: ревью всей ветки** |

Правила для каждого субагента: перед правкой — `search_graph`/`trace_path` по
затронутым символам; после — `check_index_coverage` по изменённым путям; в отчёте —
команда проверки и её вывод дословно. Задача считается закрытой только при зелёном
тесте и подтверждённом ревью.

## Что нужно от вас до старта

1. **Список типов ВС** для узла `type` (сейчас: только `Ми-8МТВ`?).
2. **Модель сейчас или позже:** начинаем с экстрактивных ответов (без выдумки, но без
   «своих слов») — или сразу подключаем LLM с обязательной проверкой цитат?
3. **Окно:** только оконный exe, или оконный + консольный (рекомендую оба — консольный
   нужен для автоматической проверки и для пакетной работы).
