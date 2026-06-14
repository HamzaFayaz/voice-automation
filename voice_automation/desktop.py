"""PySide6 desktop tray application for Voice Automation."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import sys
from collections.abc import Callable
from typing import Any

from voice_automation.config import (
    Config,
    get_deepgram_api_key,
    load_config,
    save_config,
    set_deepgram_api_key,
    validate_config,
)
from voice_automation.downloader import ModelDownloadResult, download_moonshine_model
from voice_automation.service import VoiceAutomationService

try:
    from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QStatusBar,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised by users without Qt.
    raise SystemExit("PySide6 is required. Install it with: pip install PySide6") from exc


BACKENDS = {
    "Deepgram API": "deepgram",
    "Moonshine Local": "moonshine",
}

MOONSHINE_MODELS = {
    "Tiny": 0,
    "Base": 1,
    "Tiny Streaming": 2,
    "Base Streaming": 3,
    "Small Streaming": 4,
    "Medium Streaming": 5,
}

HOTKEYS = [
    "right_ctrl",
    "left_ctrl",
    "right_alt",
    "left_alt",
    "right_shift",
    *[f"f{i}" for i in range(1, 13)],
]

PASTE_MODES = ["clipboard", "type"]


class WorkerSignals(QObject):
    """Signals emitted by a background worker."""

    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    """Run a callable on the global Qt thread pool."""

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.result.emit(self.function())
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class SettingsWindow(QMainWindow):
    """Settings window for desktop configuration."""

    config_saved = Signal(Config)
    download_requested = Signal(int)
    check_requested = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.setWindowTitle("Voice Automation Settings")
        self.setMinimumWidth(480)

        self._download_button: QPushButton | None = None
        self._download_status: QLabel | None = None
        self._status_label: QLabel | None = None

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(BACKENDS.keys())
        form.addRow("Backend", self.backend_combo)

        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.deepgram_key = QLineEdit()
        self.deepgram_key.setEchoMode(QLineEdit.Password)
        self.deepgram_key.setPlaceholderText("Deepgram API key")
        save_key_button = QPushButton("Save Key")
        save_key_button.clicked.connect(self._save_deepgram_key)
        key_layout.addWidget(self.deepgram_key, 1)
        key_layout.addWidget(save_key_button)
        form.addRow("Deepgram Key", key_row)

        self.model_combo = QComboBox()
        self.model_combo.addItems(MOONSHINE_MODELS.keys())
        form.addRow("Moonshine Model", self.model_combo)

        self.hotkey_combo = QComboBox()
        self.hotkey_combo.addItems(HOTKEYS)
        form.addRow("Hotkey", self.hotkey_combo)

        self.paste_combo = QComboBox()
        self.paste_combo.addItems(PASTE_MODES)
        form.addRow("Paste Mode", self.paste_combo)

        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(1000)
        form.addRow("Sample Rate", self.sample_rate)

        layout.addLayout(form)

        download_row = QHBoxLayout()
        self._download_button = QPushButton("Download Moonshine Model")
        self._download_button.clicked.connect(self._request_download)
        self._download_status = QLabel("No local model download is required for Deepgram.")
        self._download_status.setWordWrap(True)
        download_row.addWidget(self._download_button)
        download_row.addWidget(self._download_status, 1)
        layout.addLayout(download_row)

        action_row = QHBoxLayout()
        save_button = QPushButton("Save / Apply")
        save_button.clicked.connect(self._save_settings)
        check_button = QPushButton("Check Environment")
        check_button.clicked.connect(self.check_requested.emit)
        action_row.addStretch(1)
        action_row.addWidget(check_button)
        action_row.addWidget(save_button)
        layout.addLayout(action_row)

        self._status_label = QLabel("Status: stopped")
        layout.addWidget(self._status_label)

        self.setStatusBar(QStatusBar())
        self.setCentralWidget(root)
        self._load_config(config)
        self.backend_combo.currentTextChanged.connect(self._sync_backend_visibility)
        self._sync_backend_visibility()

    def set_status(self, status: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(f"Status: {status}")

    def set_download_busy(self, busy: bool) -> None:
        if self._download_button is not None:
            self._download_button.setEnabled(not busy)
        if busy and self._download_status is not None:
            self._download_status.setText("Downloading...")

    def show_download_result(self, result: ModelDownloadResult | str) -> None:
        if self._download_status is None:
            return
        if isinstance(result, ModelDownloadResult):
            self._download_status.setText(result.message)
        else:
            self._download_status.setText(result)

    def _load_config(self, config: Config) -> None:
        backend_label = next(
            (label for label, provider in BACKENDS.items() if provider == config.model_provider),
            "Moonshine Local",
        )
        self.backend_combo.setCurrentText(backend_label)
        self.deepgram_key.setText(get_deepgram_api_key())
        self._set_combo_by_value(self.model_combo, MOONSHINE_MODELS, config.model_arch)
        self.hotkey_combo.setCurrentText(config.hotkey)
        self.paste_combo.setCurrentText(config.paste_mode)
        self.sample_rate.setValue(config.sample_rate)

    def _current_config(self) -> Config:
        config = load_config(use_app_data=True)
        config.model_provider = BACKENDS[self.backend_combo.currentText()]
        config.model_arch = MOONSHINE_MODELS[self.model_combo.currentText()]
        config.hotkey = self.hotkey_combo.currentText()
        config.paste_mode = self.paste_combo.currentText()
        config.sample_rate = self.sample_rate.value()
        config.deepgram_api_key = ""
        return config

    def _save_deepgram_key(self) -> None:
        try:
            set_deepgram_api_key(self.deepgram_key.text().strip())
        except RuntimeError as exc:
            QMessageBox.warning(self, "Deepgram Key", str(exc))
            return
        self.statusBar().showMessage("Deepgram key saved.", 3000)

    def _save_settings(self) -> None:
        if self.deepgram_key.text().strip():
            try:
                set_deepgram_api_key(self.deepgram_key.text().strip())
            except RuntimeError as exc:
                QMessageBox.warning(self, "Deepgram Key", str(exc))
                return

        config = self._current_config()
        errors = validate_config(config, require_deepgram_key=config.model_provider == "deepgram")
        if errors:
            QMessageBox.warning(self, "Settings", "\n".join(errors))
            return
        save_config(config, use_app_data=True)
        self.config_saved.emit(config)
        self.statusBar().showMessage("Settings saved.", 3000)

    def _request_download(self) -> None:
        self.download_requested.emit(MOONSHINE_MODELS[self.model_combo.currentText()])

    def _sync_backend_visibility(self) -> None:
        is_moonshine = BACKENDS[self.backend_combo.currentText()] == "moonshine"
        self.model_combo.setEnabled(is_moonshine)
        if self._download_button is not None:
            self._download_button.setEnabled(is_moonshine)
        if self._download_status is not None and not is_moonshine:
            self._download_status.setText("No local model download is required for Deepgram.")

    @staticmethod
    def _set_combo_by_value(combo: QComboBox, mapping: dict[str, int], value: int) -> None:
        for label, mapped_value in mapping.items():
            if mapped_value == value:
                combo.setCurrentText(label)
                return


class DesktopApp(QObject):
    """Coordinate tray UI, settings, and the voice automation service."""

    status_changed = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.thread_pool = QThreadPool.globalInstance()
        self.config = load_config(use_app_data=True)
        self.service = VoiceAutomationService(self.config)
        self.service.on_status_change(self.status_changed.emit)

        self.settings_window = SettingsWindow(self.config)
        self.settings_window.config_saved.connect(self.apply_config)
        self.settings_window.download_requested.connect(self.download_model)
        self.settings_window.check_requested.connect(self.check_environment)
        self.status_changed.connect(self._set_status)

        self.tray = QSystemTrayIcon(self._build_icon(), app)
        self.tray.setToolTip("Voice Automation")
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._set_status("stopped")
        self.show_settings()
        self.tray.showMessage(
            "Voice Automation",
            "Desktop app is running in the system tray.",
            QSystemTrayIcon.Information,
            3000,
        )

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        self.start_action = QAction("Start Dictation", self)
        self.stop_action = QAction("Stop Dictation", self)
        settings_action = QAction("Settings", self)
        check_action = QAction("Check Environment", self)
        quit_action = QAction("Quit", self)

        self.start_action.triggered.connect(self.start_service)
        self.stop_action.triggered.connect(self.stop_service)
        settings_action.triggered.connect(self.show_settings)
        check_action.triggered.connect(self.check_environment)
        quit_action.triggered.connect(self.quit)

        menu.addAction(self.start_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addAction(check_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def start_service(self) -> None:
        if self.service.is_running:
            return
        self.status_changed.emit("starting")
        worker = FunctionWorker(self.service.start)
        worker.signals.error.connect(lambda message: self.status_changed.emit(f"error: {message}"))
        self.thread_pool.start(worker)

    def stop_service(self) -> None:
        worker = FunctionWorker(self.service.stop)
        self.thread_pool.start(worker)

    @Slot(Config)
    def apply_config(self, config: Config) -> None:
        self.config = dataclasses.replace(config)
        if self.service.is_running:
            worker = FunctionWorker(lambda: self.service.restart(self.config))
            worker.signals.error.connect(lambda message: self.status_changed.emit(f"error: {message}"))
            self.thread_pool.start(worker)
        else:
            self.service.config = self.config

    @Slot(int)
    def download_model(self, model_arch: int) -> None:
        self.settings_window.set_download_busy(True)
        worker = FunctionWorker(lambda: download_moonshine_model(model_arch))
        worker.signals.result.connect(self.settings_window.show_download_result)
        worker.signals.error.connect(self.settings_window.show_download_result)
        worker.signals.finished.connect(lambda: self.settings_window.set_download_busy(False))
        self.thread_pool.start(worker)

    def check_environment(self) -> None:
        worker = FunctionWorker(self._run_check)
        worker.signals.result.connect(self._show_check_result)
        worker.signals.error.connect(lambda message: QMessageBox.warning(self.settings_window, "Check Environment", message))
        self.thread_pool.start(worker)

    def show_settings(self) -> None:
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def quit(self) -> None:
        self.service.stop()
        self.tray.hide()
        self.app.quit()

    def _set_status(self, status: str) -> None:
        self.settings_window.set_status(status)
        self.tray.setToolTip(f"Voice Automation - {status}")
        running = self.service.is_running
        self.start_action.setEnabled(not running)
        self.stop_action.setEnabled(running)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_settings()

    @staticmethod
    def _build_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#1f7a5a"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(8, 8, 48, 48)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(29, 18, 6, 22, 3, 3)
        painter.drawRoundedRect(23, 28, 18, 6, 3, 3)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _run_check() -> str:
        from voice_automation.check import run_checks

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            passed = run_checks()
        result = buffer.getvalue().strip()
        return result if passed else result + "\n\nOne or more checks failed."

    def _show_check_result(self, output: str) -> None:
        QMessageBox.information(self.settings_window, "Check Environment", output)


def main() -> int:
    """Run the desktop application."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Voice Automation", "System tray is not available.")
        return 1

    desktop = DesktopApp(app)
    app.aboutToQuit.connect(desktop.service.stop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
