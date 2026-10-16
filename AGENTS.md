# AGENTS.md

## Что это за проект
Инструмент подготовки отсканированных PDF-документов (вертолёт Ми-8МТВ) к поиску и будущему «агенту-инженеру»: OCR текста, сохранение структуры «система → подсистема → листы», контроль качества сканов. Целевое требование — портативность: одна папка, работает на любом Windows без установки.

## Два поколения кода — не перепутать
- **Legacy (ветка `master`):** `pdf_ocr_app.py` (GUI PyQt5) и `pdfocrm.py` (CLI). Не портативны — требуют системных Tesseract (`C:\Program Files\Tesseract-OCR\`), Ghostscript, poppler. `requirements.txt` описывает только их (`PyQt5`, `ocrmypdf`).
- **Новый пакет (ветка `pdf-prepare`, НЕ слит в master):** `pdfprepare/` — портативный конвейер: рендер PyMuPDF → предобработка OpenCV → OCR pytesseract → searchable PDF + JSON + отчёт о качестве. Тесты в `tests/`.

## Команды
- Единственный рабочий Python — `venv\Scripts\python.exe` в корне (venv не в git и не в requirements.txt; в нём уже есть `pytest`, `numpy`, `opencv-python-headless`, `pymupdf`, `pytesseract`, `Pillow`). Каталог `.venv/` сломан (ссылается на отсутствующий Python310) — не использовать.
- Тесты (из корня): `venv\Scripts\python.exe -m pytest tests/test_<модуль>.py -v`
- `pip install -r requirements.txt` НЕ ставит ни pytest, ни зависимости нового пакета.

## Кодировка (ловушка Windows)
- Весь код и данные — UTF-8 с кириллицей; консоль Windows — cp866.
- Не пайпить `git diff` в PowerShell `Out-File` — UTF-8 портится (кириллица превращается в кракозябры). Использовать `[Console]::OutputEncoding=UTF8` или Python `subprocess` + `.decode('utf-8')`.

## Доменные факты (не видны из кода)
- `pdfs/` — 49 сканов Ми-8МТВ: часть «сырые» (высокий DPI), часть уже распознаны (скан внутри ~150 DPI — потолок качества без перескана оригинала).
- `Коды систем Вертолета.txt` (корень) — справочник ATA-100: система `XX.00.00`, секция `XX.YY.00`; в колонтитулах страниц стоят коды третьего уровня `XX.YY.ZZ`.
- Навигация — по кодам системы/подсистемы (они есть на каждой странице), НЕ по номеру листа.
- **Пустая страница = граница раздела/техкарты, а НЕ дефект скана** — не помечать её как ошибку.

## Источник истины
- Дизайн: `docs/superpowers/specs/2026-10-16-pdf-ocr-preparation-design.md`
- План (10 задач, MVP): `docs/superpowers/plans/2026-10-16-pdf-preparation-mvp.md`

## Git-ловушки
- `.gitignore` добавлен коммитом `5595f84`: `venv/`, `pdfs/`, `OCRfile/`, `build/`, `dist/`, `out/`, `*.db`, `.superpowers/`, `.idea/`, `pdfprepare/tesseract/`. Правило «не делать `git add -A`» всё равно действует — в дереве есть чужие WIP-правки.
- Первый коммит называется «Initial commit: Personnel training system» — это не этот проект, не пугаться.

## Локальный opencode.json
- Настраивает MCP `codebase-memory-mcp` и кастомных субагентов: `fast`/`general`/`strong` (исполнители) и `worker-review` (ревьюер, только чтение). В subagent-driven разработке использовать эти тиры вместо дефолтной модели.

## Текущая работа: граф знаний (идёт поэтапно, возобновляемо)
- План: `docs/superpowers/plans/2026-10-16-knowledge-graph-plan.md`. Снимок состояния и точка возобновления: `docs/superpowers/plans/2026-10-16-knowledge-graph-progress.md`.
- **Если пользователь пишет «продолжи» (или «продолжай»)** — это значит: прочитать снимок состояния, затем журнал `.superpowers/sdd/2026-10-16-knowledge-graph-plan/progress.md`, свериться с `git log --oneline -12` и продолжить выполнение плана субагентами через скилл `subagent-driven-development`. Тиры — из `opencode.json`. Брифы задач и отчёты исполнителей складывать в ту же папку журнала.
- Не начинать следующую задачу до закрытия предыдущей: ревью каждой задачи — обязательный гейт. Субагенты не поручают работу другим субагентам и не запускают ревью.
- Рабочий Python — только `venv\Scripts\python.exe` (в системном нет `cv2`). Тесты: `venv\Scripts\python.exe -m pytest -q` (базовая линия — смотреть в снимке состояния).
- Python 3, файлы UTF-8 с кириллицей. Консоль Windows cp866 — кракозябры в выводе это норма, не ошибка и не повод «чинить» кодировку.
