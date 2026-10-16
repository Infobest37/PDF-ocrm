import sys
import os
import subprocess
import tempfile
from pathlib import Path
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *


class SimpleOCRApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle("PDF OCR Конвертер")
        self.setGeometry(100, 100, 500, 300)

        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Заголовок
        title_label = QLabel("PDF OCR Конвертер")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        layout.addSpacing(20)

        # Поле для выбора файла
        file_layout = QHBoxLayout()
        file_label = QLabel("PDF файл:")
        self.file_path = QLineEdit()
        self.file_path.setPlaceholderText("Выберите PDF файл...")
        browse_btn = QPushButton("Обзор...")
        browse_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(file_label)
        file_layout.addWidget(self.file_path, 1)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)

        layout.addSpacing(10)

        # Язык OCR
        lang_layout = QHBoxLayout()
        lang_label = QLabel("Язык OCR:")
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["rus", "eng", "rus+eng", "eng+rus"])
        self.lang_combo.setCurrentText("rus+eng")
        lang_layout.addWidget(lang_label)
        lang_layout.addWidget(self.lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        layout.addSpacing(20)

        # Кнопка конвертации
        self.convert_btn = QPushButton("Конвертировать в OCR PDF")
        self.convert_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.convert_btn.clicked.connect(self.convert_pdf)
        layout.addWidget(self.convert_btn)

        layout.addSpacing(20)

        # Прогресс
        self.progress_label = QLabel("Готов к работе")
        self.progress_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        layout.addStretch()

        # Статус бар
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готов к работе")

    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Выберите PDF файл", "", "PDF Files (*.pdf)"
        )
        if file_name:
            self.file_path.setText(file_name)

    def convert_pdf(self):
        input_file = self.file_path.text().strip()

        if not input_file:
            QMessageBox.warning(self, "Ошибка", "Выберите PDF файл!")
            return

        if not os.path.exists(input_file):
            QMessageBox.warning(self, "Ошибка", "Файл не существует!")
            return

        # Создаем имя выходного файла
        input_path = Path(input_file)
        output_file = str(input_path.with_name(f"{input_path.stem}_OCR{input_path.suffix}"))

        # Спрашиваем куда сохранить
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить OCR PDF как", output_file, "PDF Files (*.pdf)"
        )

        if not save_path:
            return

        # Отключаем кнопку и показываем прогресс
        self.convert_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Начинаю обработку...")

        # Запускаем ocrmypdf в отдельном потоке
        self.worker_thread = OCRThread(input_file, save_path, self.lang_combo.currentText())
        self.worker_thread.progress.connect(self.update_progress)
        self.worker_thread.finished.connect(self.conversion_finished)
        self.worker_thread.error.connect(self.conversion_error)
        self.worker_thread.start()

    def update_progress(self, message, value=None):
        self.progress_label.setText(message)
        self.status_bar.showMessage(message)
        if value is not None:
            self.progress_bar.setValue(value)

    def conversion_finished(self, output_file):
        self.progress_bar.setValue(100)
        self.progress_label.setText("✅ Конвертация завершена!")

        QMessageBox.information(self, "Готово",
                                f"PDF успешно конвертирован!\n\nСохранен как:\n{output_file}")

        # Спрашиваем открыть ли файл
        reply = QMessageBox.question(self, "Открыть файл",
                                     "Хотите открыть сконвертированный файл?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.open_file(output_file)

        self.convert_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

    def conversion_error(self, error_message):
        QMessageBox.critical(self, "Ошибка", f"Произошла ошибка:\n\n{error_message}")
        self.progress_label.setText("Ошибка при конвертации")
        self.progress_bar.setValue(0)
        self.convert_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

    def open_file(self, file_path):
        # Открываем файл в стандартном приложении
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))


class OCRThread(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, input_file, output_file, language):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.language = language

    def run(self):
        try:
            # Проверяем, установлен ли ocrmypdf
            try:
                import ocrmypdf
            except ImportError:
                self.error.emit("ocrmypdf не установлен! Установите: pip install ocrmypdf")
                return

            self.progress.emit("Запускаю ocrmypdf...", 10)

            # Запускаем ocrmypdf
            ocrmypdf.ocr(
                input_file=self.input_file,
                output_file=self.output_file,
                language=self.language,
                rotate_pages=True,
                output_type='pdf',
                optimize=1,
                force_ocr=True,
                progress_bar=False
            )

            self.progress.emit("Конвертация завершена!", 100)
            self.finished.emit(self.output_file)

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n\n{traceback.format_exc()}"
            self.error.emit(error_msg)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Проверяем наличие зависимостей
    try:
        import ocrmypdf
    except ImportError:
        QMessageBox.critical(None, "Ошибка",
                             "ocrmypdf не установлен!\n\n"
                             "Установите через командную строку:\n"
                             "pip install ocrmypdf")
        return

    window = SimpleOCRApp()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()