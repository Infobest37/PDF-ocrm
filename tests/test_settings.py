import os
import pytest
from urllib.error import HTTPError, URLError


def _ini_settings(monkeypatch, tmp_path):
    from PyQt5.QtCore import QSettings as RealQSettings
    import pdfgraph.gui.settings as settings_module

    path = tmp_path / "pdfgraph.ini"

    def qsettings(_organization, _application):
        return RealQSettings(str(path), RealQSettings.IniFormat)

    monkeypatch.setattr(settings_module, "QSettings", qsettings)
    return settings_module, path


def test_load_returns_qsettings_defaults(monkeypatch, tmp_path):
    settings_module, _path = _ini_settings(monkeypatch, tmp_path)

    settings = settings_module.load()

    assert settings.api_key == ""
    assert settings.base_url == "https://aitunnel.ru/v1"
    assert settings.model == "gpt-4o-mini"
    assert settings.temperature == 0.0
    assert settings.max_tokens == 500
    assert settings.use_synthesis is False
    assert settings.use_rag_fusion is False


def test_save_and_load_round_trip_all_settings(monkeypatch, tmp_path):
    settings_module, path = _ini_settings(monkeypatch, tmp_path)
    settings = settings_module.LLMSettings(
        api_key="secret-for-test",
        base_url="https://gateway.example/v1/",
        model="qwen-plus",
        temperature=0.4,
        max_tokens=850,
        use_synthesis=True,
        use_rag_fusion=True,
    )

    settings_module.save(settings)
    loaded = settings_module.load()

    assert loaded == settings
    assert path.exists()


def test_settings_instance_save_uses_qsettings(monkeypatch, tmp_path):
    settings_module, _path = _ini_settings(monkeypatch, tmp_path)
    settings = settings_module.LLMSettings(api_key="test-key")

    settings.save()

    assert settings_module.load().api_key == "test-key"


def test_settings_dialog_password_model_and_ranges(monkeypatch, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QLineEdit
    from pdfgraph.gui.settings import LLMSettings
    from pdfgraph.gui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(LLMSettings())

    assert dialog.api_key_edit.echoMode() == QLineEdit.Password
    assert dialog.base_url_edit.text() == "https://aitunnel.ru/v1"
    assert dialog.model_combo.isEditable()
    assert {
        dialog.model_combo.itemText(i)
        for i in range(dialog.model_combo.count())
    } >= {"gpt-4o-mini", "claude-3-5-haiku", "deepseek-chat", "qwen-plus"}
    assert dialog.temperature_spin.minimum() == 0.0
    assert dialog.temperature_spin.maximum() == 1.0
    assert dialog.max_tokens_spin.minimum() == 100
    assert dialog.max_tokens_spin.maximum() == 2000
    dialog.close()


def test_settings_dialog_save_persists_fields(monkeypatch, tmp_path):
    settings_module, _path = _ini_settings(monkeypatch, tmp_path)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QDialog
    from pdfgraph.gui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(settings_module.load())
    dialog.api_key_edit.setText("aitunnel-test-key")
    dialog.base_url_edit.setText("https://aitunnel.example/v1")
    dialog.model_combo.setCurrentText("deepseek-chat")
    dialog.temperature_spin.setValue(0.2)
    dialog.max_tokens_spin.setValue(700)

    dialog.save_button.click()

    saved = settings_module.load()
    assert dialog.result() == QDialog.Accepted
    assert saved.api_key == "aitunnel-test-key"
    assert saved.base_url == "https://aitunnel.example/v1"
    assert saved.model == "deepseek-chat"
    assert saved.temperature == 0.2
    assert saved.max_tokens == 700
    dialog.close()


def test_settings_dialog_cancel_does_not_persist(monkeypatch, tmp_path):
    settings_module, _path = _ini_settings(monkeypatch, tmp_path)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from pdfgraph.gui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(settings_module.load())
    dialog.api_key_edit.setText("unsaved-key")
    dialog.cancel_button.click()

    assert settings_module.load().api_key == ""
    dialog.close()


def test_settings_dialog_test_connection_posts_bearer_and_reports_success(
        monkeypatch, tmp_path):
    _settings_module, _path = _ini_settings(monkeypatch, tmp_path)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from pdfgraph.gui.settings_dialog import SettingsDialog
    import urllib.request

    app = QApplication.instance() or QApplication([])
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    messages = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args: messages.append(args))
    dialog = SettingsDialog(_settings_module.load())
    dialog.api_key_edit.setText("test-bearer")
    dialog.base_url_edit.setText("https://gateway.example/v1/")

    dialog.test_button.click()

    request, timeout = calls[0]
    assert request.full_url == "https://gateway.example/v1/models"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-bearer"
    assert timeout <= 15
    assert messages
    dialog.close()


def test_settings_dialog_test_connection_reports_failure(monkeypatch, tmp_path):
    _settings_module, _path = _ini_settings(monkeypatch, tmp_path)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from pdfgraph.gui.settings_dialog import SettingsDialog
    import urllib.request

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    messages = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args: messages.append(args))
    dialog = SettingsDialog(_settings_module.load())
    dialog.api_key_edit.setText("test-key")

    dialog.test_button.click()

    assert messages
    assert "offline" in messages[0][2]
    dialog.close()


@pytest.mark.parametrize("post_status", [400, 404, 405, 501])
def test_settings_dialog_retries_models_endpoint_with_get_when_post_rejected(
        monkeypatch, tmp_path, post_status):
    """Keep the requested POST probe, but support GET-only /models servers."""
    _settings_module, _path = _ini_settings(monkeypatch, tmp_path)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from pdfgraph.gui.settings_dialog import SettingsDialog
    import urllib.request

    app = QApplication.instance() or QApplication([])
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if request.get_method() == "POST":
            raise HTTPError(request.full_url, post_status, "POST not accepted",
                            hdrs=None, fp=None)
        return _ResponseForGet()

    class _ResponseForGet:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return self.status

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args: messages.append(args))
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args: messages.append(args))
    dialog = SettingsDialog(_settings_module.load())
    dialog.api_key_edit.setText("test-key")

    dialog.test_button.click()

    assert [request.get_method() for request in calls] == ["POST", "GET"]
    assert all(request.full_url == "https://aitunnel.ru/v1/models"
               for request in calls)
    assert all(request.get_header("Authorization") == "Bearer test-key"
               for request in calls)
    assert len(messages) == 1
    dialog.close()
