"""Settings dialog for the OpenAI-compatible aitunnel endpoint."""

import dataclasses
import json
import urllib.request
from urllib.error import HTTPError

from PyQt5 import QtCore, QtWidgets

from pdfgraph.gui.settings import LLMSettings

MODEL_PRESETS = (
    "gpt-4o-mini",
    "claude-3-5-haiku",
    "deepseek-chat",
    "qwen-plus",
)


def _check_connection(base_url: str, api_key: str) -> tuple[bool, str]:
    """Probe ``/models`` with the requested POST and a GET compatibility fallback."""
    if not api_key.strip():
        return False, "Укажите API-ключ."
    url = base_url.strip().rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key.strip()}"}

    def request_models(method: str) -> int:
        request = urllib.request.Request(
            url,
            data=(json.dumps({}).encode("utf-8") if method == "POST" else None),
            headers=({"Content-Type": "application/json", **headers}
                     if method == "POST" else headers),
            method=method,
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return getattr(response, "status", None) or response.getcode()

    get_fallback_statuses = {400, 404, 405, 501}
    try:
        try:
            status = request_models("POST")
        except HTTPError as exc:
            if exc.code not in get_fallback_statuses:
                return False, f"HTTP {exc.code}: {exc.reason}"
            status = exc.code
        # OpenAI-compatible providers commonly expose /models as GET-only;
        # keep POST first as specified, then retry method/request rejections.
        if status in get_fallback_statuses:
            status = request_models("GET")
        if status == 200:
            return True, "Соединение успешно."
        return False, f"Сервер вернул HTTP {status}."
    except HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        return False, str(exc)


class SettingsDialog(QtWidgets.QDialog):
    """Edit and test the persisted aitunnel profile."""

    def __init__(self, settings: LLMSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки LLM")
        self.setModal(True)
        self._settings = dataclasses.replace(settings or LLMSettings())
        self.saved_settings: LLMSettings | None = None
        self.setStyleSheet("QDialog { background: #f4f4f1; }")
        self._build_widgets()

    def _build_widgets(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.api_key_edit = QtWidgets.QLineEdit(objectName="llmApiKey")
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setText(self._settings.api_key)
        self.base_url_edit = QtWidgets.QLineEdit(objectName="llmBaseUrl")
        self.base_url_edit.setText(self._settings.base_url)

        self.model_combo = QtWidgets.QComboBox(objectName="llmModel")
        self.model_combo.setEditable(True)
        self.model_combo.addItems(MODEL_PRESETS)
        self.model_combo.setCurrentText(self._settings.model)

        self.temperature_spin = QtWidgets.QDoubleSpinBox(
            objectName="llmTemperature")
        self.temperature_spin.setRange(0.0, 1.0)
        self.temperature_spin.setDecimals(2)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(self._settings.temperature)

        self.max_tokens_spin = QtWidgets.QSpinBox(objectName="llmMaxTokens")
        self.max_tokens_spin.setRange(100, 2000)
        self.max_tokens_spin.setValue(self._settings.max_tokens)

        form.addRow("API-ключ", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("Модель", self.model_combo)
        form.addRow("Temperature", self.temperature_spin)
        form.addRow("Максимум токенов", self.max_tokens_spin)
        layout.addLayout(form)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.test_button = QtWidgets.QPushButton(
            "Тест соединения", objectName="testConnection")
        self.cancel_button = QtWidgets.QPushButton("Отмена", objectName="cancel")
        self.save_button = QtWidgets.QPushButton("Сохранить", objectName="save")
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.test_button.clicked.connect(self._test_connection)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)

    def _collect_settings(self) -> LLMSettings:
        return dataclasses.replace(
            self._settings,
            api_key=self.api_key_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_combo.currentText().strip(),
            temperature=self.temperature_spin.value(),
            max_tokens=self.max_tokens_spin.value(),
        )

    def _test_connection(self) -> None:
        settings = self._collect_settings()
        ok, message = _check_connection(settings.base_url, settings.api_key)
        if ok:
            QtWidgets.QMessageBox.information(self, "Тест соединения", message)
        else:
            QtWidgets.QMessageBox.warning(self, "Тест соединения", message)

    def _save(self) -> None:
        from pdfgraph.gui.settings import save

        self.saved_settings = self._collect_settings()
        save(self.saved_settings)
        self.accept()
