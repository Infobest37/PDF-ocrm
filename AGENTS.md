# Инструкции для агентов

## Проект
- Windows-приложение состоит из двух пакетов: `pdfprepare/` готовит PDF (OCR, searchable PDF, JSON, отчёт качества); `pdfgraph/` строит SQLite-граф из JSON и отвечает по документам с проверяемыми цитатами.
- Зависимости описаны в `pyproject.toml`. Старый legacy-код (`pdf_ocr_app.py`, `pdfocrm.py`, `_massA.py`, `_massB.py`, `requirements.txt`, `install.bat`, `OCR.bat`) удалён 29.10.2026 вместе со старым бэкапом `graph.db.backup-20261028-docswap` и перенесён в соседний каталог-архив `PDF_OCR_APP_legacy_archive_2026-10-29` (вне репозитория). Восстановление — только из архива, не из git.
- В корне есть рабочее окружение Python 3.12: `venv\Scripts\python.exe`. Используйте его, не системный Python.

## Команды
- Все тесты: `venv\Scripts\python.exe -m pytest -q`; один модуль: `venv\Scripts\python.exe -m pytest tests\test_graph_cli.py -v`.
- GUI-тесты запускаются с Qt offscreen; приёмочные `tests/test_graph_acceptance.py` прогоняют настоящий OCR и требуют установленный Tesseract с данными `rus` и `eng`. Если он не находится в `pdfprepare\tesseract\`, укажите каталог установки через `PDFPREPARE_TESSERACT`.
- Подготовить PDF: `venv\Scripts\python.exe -m pdfprepare <src_dir> <dst_dir> [--registry <path>] [--force-ocr]`. CLI обрабатывает PDF только в корне `src_dir`; по умолчанию создаёт searchable PDF, JSON и `.report.txt`.
- Построить граф: `venv\Scripts\python.exe -m pdfgraph build <prepared_dir> graph.db --type Ми-8МТВ [--registry <path>]`; задать вопрос: `venv\Scripts\python.exe -m pdfgraph ask "<вопрос>" --store graph.db`. `build` ищет JSON рекурсивно; для ответов CLI код 0 означает подтверждённые цитаты, код 1 — отказ или неподтверждённые цитаты.
- Путь реестра `Коды систем Вертолета.txt` по умолчанию относительный к текущей рабочей папке; при запуске не из корня передавайте `--registry` явно.
- Portable-сборка графа: сначала `powershell -ExecutionPolicy Bypass -File scripts\bundle_tesseract.ps1`, затем `venv\Scripts\python.exe -m PyInstaller pdfgraph.spec`. Получается `dist/pdfgraph/` с тремя exe; Tesseract и `rus`/`eng` данные берутся из игнорируемого `pdfprepare/tesseract/`.

## Неочевидные ограничения
- Навигация по документации вертолёта — по кодам системы/подсистемы ATA-100, не по номеру листа. Пустая страница — граница раздела/техкарты, не дефект скана; не добавляйте её в отчёт пересканирования.
- Не переписывайте исходный OCR-текст нормализованным: нормализация — только для поисковых ключей. Ответы графа должны сохранять ссылки, которые проходят машинную проверку.
- Файлы проекта и данные — UTF-8; Windows-консоль может показывать кириллицу как cp866. Искажённое отображение само по себе не означает повреждение файла.
- В рабочем дереве могут быть пользовательские изменения и удалённые артефакты сборки. Проверяйте `git status` и не используйте `git add -A`/`git add .`; выбирайте файлы для staging явно.
- План графа знаний в `docs/superpowers/plans/2026-10-16-knowledge-graph-progress.md` отмечен завершённым; при просьбе «продолжи» не переисполняйте старый план автоматически. Сверяйте актуальное состояние по коду и git, так как снимок прогресса и базовые числа тестов могут устареть.
- правила исполнения задач — в .clinerules, они приоритетнее этого файла
