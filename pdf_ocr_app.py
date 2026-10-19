import sys
import os
import traceback
from pathlib import Path
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import subprocess


class PDFOCRApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setup_tesseract()
        self.setup_ui()

    def setup_tesseract(self):
        """Настраиваем пути к Tesseract"""
        # Стандартные пути к Tesseract на Windows
        possible_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files\Tesseract-OCR\tessdata",
            os.environ.get("TESSERACT_PATH", ""),
        ]

        # Ищем Tesseract
        tesseract_found = False
        for path in possible_paths:
            if path and os.path.exists(path):
                if path.endswith(".exe"):
                    os.environ["PATH"] = os.path.dirname(path) + ";" + os.environ["PATH"]
                elif "tessdata" in path:
                    os.environ["TESSDATA_PREFIX"] = path
                tesseract_found = True

        if not tesseract_found:
            self.show_tesseract_warning()

    def show_tesseract_warning(self):
        """Показывает предупреждение об установке Tesseract"""
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Внимание!")
        msg.setText("Tesseract OCR не найден")
        msg.setInformativeText(
            "Для работы приложения необходимо установить Tesseract OCR.\n\n"
            "Скачайте и установите с официального сайта:\n"
            "https://github.com/UB-Mannheim/tesseract/wiki\n\n"
            "По умолчанию установите в: C:\\Program Files\\Tesseract-OCR\\"
        )
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()

    def setup_ui(self):
        self.setWindowTitle("PDF OCR Конвертер")
        self.setGeometry(100, 100, 500, 300)

        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Заголовок
        title = QLabel("📄 PDF OCR Конвертер")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(20)

        # Выбор файла
        file_layout = QHBoxLayout()
        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("Выберите PDF файл...")
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self.browse_file)
        file_layout.addWidget(QLabel("Файл:"))
        file_layout.addWidget(self.file_input)
        file_layout.addWidget(btn_browse)
        layout.addLayout(file_layout)

        # Язык
        lang_layout = QHBoxLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["rus", "eng", "rus+eng"])
        self.lang_combo.setCurrentText("rus+eng")
        lang_layout.addWidget(QLabel("Язык:"))
        lang_layout.addWidget(self.lang_combo)
        layout.addLayout(lang_layout)

        # Дополнительные настройки
        settings_layout = QHBoxLayout()
        self.chk_rotate = QCheckBox("Автоповорот")
        self.chk_rotate.setChecked(True)
        settings_layout.addWidget(self.chk_rotate)
        settings_layout.addStretch()
        layout.addLayout(settings_layout)

        layout.addSpacing(20)

        # Кнопка конвертации
        self.btn_convert = QPushButton("🚀 Конвертировать в OCR PDF")
        self.btn_convert.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
                padding: 12px;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #BDBDBD; }
        """)
        self.btn_convert.clicked.connect(self.start_conversion)
        layout.addWidget(self.btn_convert)

        layout.addSpacing(20)

        # Статус
        self.status_label = QLabel("Готов к работе")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Лог
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(100)
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        layout.addStretch()

        # Статус бар
        self.statusBar().showMessage("Для начала выберите PDF файл")

    def browse_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Выберите PDF файл", "", "PDF Files (*.pdf)"
        )
        if file:
            self.file_input.setText(file)

    def log_message(self, message):
        """Добавляет сообщение в лог"""
        self.log_text.append(f"{message}")
        QApplication.processEvents()  # Обновляем интерфейс

    def start_conversion(self):
        input_file = self.file_input.text().strip()

        if not input_file or not os.path.exists(input_file):
            QMessageBox.warning(self, "Ошибка", "Выберите существующий PDF файл!")
            return

        # Выходной файл
        input_path = Path(input_file)
        output_file = str(input_path.with_name(f"{input_path.stem}_OCR{input_path.suffix}"))

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить как", output_file, "PDF Files (*.pdf)"
        )

        if not save_path:
            return

        # Очищаем лог
        self.log_text.clear()

        # Блокируем кнопку
        self.btn_convert.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Обработка...")
        self.log_message("Начинаю обработку...")

        # Запуск в отдельном потоке
        self.worker = OCRWorker(
            input_file,
            save_path,
            self.lang_combo.currentText(),
            self.chk_rotate.isChecked()
        )
        self.worker.progress.connect(self.update_status)
        self.worker.log.connect(self.log_message)
        self.worker.finished.connect(self.conversion_done)
        self.worker.error.connect(self.conversion_error)
        self.worker.start()

    def update_status(self, message, percent=None):
        self.status_label.setText(message)
        if percent is not None:
            self.progress_bar.setValue(percent)

    def conversion_done(self, output_file):
        self.progress_bar.setValue(100)
        self.status_label.setText("✅ Готово!")
        self.btn_convert.setEnabled(True)
        self.log_message("✅ Конвертация завершена успешно!")

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("PDF успешно конвертирован!")
        msg.setInformativeText(f"Файл сохранен:\n{output_file}")
        msg.setWindowTitle("Готово")

        open_btn = msg.addButton("Открыть файл", QMessageBox.ActionRole)
        close_btn = msg.addButton("Закрыть", QMessageBox.RejectRole)

        msg.exec_()

        if msg.clickedButton() == open_btn:
            if os.path.exists(output_file):
                os.startfile(output_file)

    def conversion_error(self, error_msg):
        self.btn_convert.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("❌ Ошибка")
        self.log_message(f"❌ Ошибка: {error_msg}")

        QMessageBox.critical(self, "Ошибка",
                             f"Не удалось конвертировать файл.\n\n{error_msg}")


class OCRWorker(QThread):
    progress = pyqtSignal(str, int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, input_file, output_file, language, rotate=True):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.language = language
        self.rotate = rotate

    def run(self):
        try:
            # Проверяем ocrmypdf
            try:
                import ocrmypdf
            except ImportError as e:
                self.error.emit(f"ocrmypdf не установлен: {str(e)}\n\nУстановите: pip install ocrmypdf")
                return

            self.log.emit("Проверка Tesseract OCR...")

            # Проверяем Tesseract
            try:
                result = subprocess.run(
                    ["tesseract", "--version"],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if result.returncode != 0:
                    self.log.emit("⚠️ Tesseract не найден в PATH")
            except FileNotFoundError:
                self.log.emit("⚠️ Tesseract не найден")
                self.log.emit("Установите Tesseract OCR с https://github.com/UB-Mannheim/tesseract/wiki")

            self.progress.emit("Запуск OCR...", 20)
            self.log.emit(f"Начало обработки: {os.path.basename(self.input_file)}")
            self.log.emit(f"Язык: {self.language}")

            # Параметры для ocrmypdf
            kwargs = {
                'input_file': self.input_file,
                'output_file': self.output_file,
                'language': self.language,
                'force_ocr': True,
                'optimize': 1,
                'progress_bar': False,
            }

            if self.rotate:
                kwargs['rotate_pages'] = True
                kwargs['rotate_pages_threshold'] = 14.0

            self.log.emit("Запускаю ocrmypdf...")

            # Конвертация
            try:
                import ocrmypdf
                ocrmypdf.ocr(**kwargs)

                self.progress.emit("Завершено!", 100)
                self.log.emit("✅ OCR выполнен успешно")
                self.finished.emit(self.output_file)

            except Exception as e:
                # Пробуем альтернативный способ
                self.log.emit(f"⚠️ Ошибка ocrmypdf: {str(e)}")
                self.log.emit("Пробую альтернативный способ...")

                # Простой вызов через командную строку
                cmd = [
                    'ocrmypdf',
                    '--language', self.language,
                    '--force-ocr',
                    '--optimize', '1',
                    self.input_file,
                    self.output_file
                ]

                if self.rotate:
                    cmd.insert(1, '--rotate-pages')
                    cmd.insert(2, '--rotate-pages-threshold')
                    cmd.insert(3, '14.0')

                self.log.emit(f"Команда: {' '.join(cmd)}")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )

                if result.returncode == 0:
                    self.progress.emit("Завершено!", 100)
                    self.log.emit("✅ OCR выполнен успешно")
                    self.finished.emit(self.output_file)
                else:
                    error_msg = f"Код ошибки: {result.returncode}\n"
                    if result.stderr:
                        error_msg += f"Ошибка: {result.stderr[:500]}"
                    self.error.emit(error_msg)

        except Exception as e:
            self.error.emit(f"Критическая ошибка: {str(e)}\n\n{traceback.format_exc()}")


def check_dependencies():
    """Проверяет установлены ли зависимости"""
    missing = []

    try:
        import PyQt5
    except ImportError:
        missing.append("PyQt5")

    try:
        import ocrmypdf
    except ImportError:
        missing.append("ocrmypdf")

    return missing


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Проверяем зависимости
    missing = check_dependencies()
    if missing:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Отсутствуют зависимости")
        msg.setText("Не установлены необходимые библиотеки:")
        msg.setInformativeText("\n".join(missing) + "\n\nУстановите командой:\npip install " + " ".join(missing))
        msg.exec_()
        return

    window = PDFOCRApp()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()