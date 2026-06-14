"""PySide6 desktop tray application for Voice Automation."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import sys
import time
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
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QSpinBox,
        QStackedWidget,
        QStatusBar,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised by users without Qt.
    raise SystemExit("PySide6 is required. Install it with: pip install PySide6") from exc


BACKENDS = {
    "Online - Deepgram": "deepgram",
    "Offline - Moonshine": "moonshine",
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

DESKTOP_PASTE_MODE = "type"
DESKTOP_DEFAULT_MAX_RECORD_SECONDS = 300


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


class MainWindow(QMainWindow):
    """Main desktop window with Home and Settings pages."""

    start_requested = Signal()
    stop_requested = Signal()
    config_saved = Signal(Config)
    download_requested = Signal(int)
    test_requested = Signal(Config)
    check_requested = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.setWindowTitle("Voice Automation")
        self.setMinimumSize(540, 430)

        self._download_button: QPushButton | None = None
        self._download_status: QLabel | None = None

        self.pages = QStackedWidget(self)
        self.home_page = self._build_home_page()
        self.settings_page = self._build_settings_page()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.settings_page)
        self.setCentralWidget(self.pages)
        self.setStatusBar(QStatusBar())

        self._load_config(config)
        self._show_home()

    def set_status(self, status: str) -> None:
        self.status_label.setText(status.title())
        if status.startswith("error"):
            self.status_detail.setText(status)
        elif status == "running":
            self.status_detail.setText("Hold the configured key to dictate.")
        elif status == "recording":
            self.status_detail.setText("Listening while the key is held.")
        else:
            self.status_detail.setText(self._current_backend_summary())

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def set_download_busy(self, busy: bool) -> None:
        if self._download_button is not None:
            self._download_button.setEnabled(not busy)
        if busy and self._download_status is not None:
            self._download_status.setText("Downloading...")

    def show_download_result(self, result: ModelDownloadResult | str) -> None:
        if self._download_status is None:
            return
        self._download_status.setText(result.message if isinstance(result, ModelDownloadResult) else result)

    def _build_home_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(18)

        title = QLabel("Voice Automation")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 700;")

        self.status_label = QLabel("Stopped")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #1f7a5a;")

        self.status_detail = QLabel("")
        self.status_detail.setAlignment(Qt.AlignCenter)
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("font-size: 14px; color: #4b5563;")

        button_row = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.start_button.setMinimumHeight(56)
        self.stop_button.setMinimumHeight(56)
        self.start_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.stop_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)

        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self._show_settings)
        check_button = QPushButton("Check Environment")
        check_button.clicked.connect(self.check_requested.emit)
        secondary_row = QHBoxLayout()
        secondary_row.addStretch(1)
        secondary_row.addWidget(check_button)
        secondary_row.addWidget(settings_button)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(self.status_label)
        layout.addWidget(self.status_detail)
        layout.addSpacing(10)
        layout.addLayout(button_row)
        layout.addLayout(secondary_row)
        layout.addStretch(1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(self._show_home)
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        header_row.addWidget(back_button)
        header_row.addWidget(title)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(BACKENDS.keys())
        self.backend_combo.currentTextChanged.connect(self._sync_backend_visibility)
        self.form.addRow("Backend", self.backend_combo)

        self.deepgram_row = QWidget()
        key_layout = QHBoxLayout(self.deepgram_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.deepgram_key_status = QLabel("No API key saved.")
        self.deepgram_key = QLineEdit()
        self.deepgram_key.setEchoMode(QLineEdit.Password)
        self.deepgram_key.setPlaceholderText("Deepgram API key")
        self.save_key_button = QPushButton("Save")
        self.save_key_button.clicked.connect(self._save_deepgram_key)
        self.change_key_button = QPushButton("Change API Key")
        self.change_key_button.clicked.connect(self._edit_deepgram_key)
        test_key_button = QPushButton("Test")
        test_key_button.clicked.connect(self._test_deepgram_key)
        key_layout.addWidget(self.deepgram_key_status)
        key_layout.addWidget(self.deepgram_key, 1)
        key_layout.addWidget(self.save_key_button)
        key_layout.addWidget(self.change_key_button)
        key_layout.addWidget(test_key_button)
        self.form.addRow("Deepgram Key", self.deepgram_row)

        self.model_row = QWidget()
        model_layout = QHBoxLayout(self.model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.addItems(MOONSHINE_MODELS.keys())
        self._download_button = QPushButton("Download")
        self._download_button.clicked.connect(self._request_download)
        model_layout.addWidget(self.model_combo, 1)
        model_layout.addWidget(self._download_button)
        self.form.addRow("Moonshine Model", self.model_row)

        self.hotkey_combo = QComboBox()
        self.hotkey_combo.addItems(HOTKEYS)
        self.form.addRow("Hotkey", self.hotkey_combo)

        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(1000)
        self.form.addRow("Sample Rate", self.sample_rate)

        self.max_record_seconds = QSpinBox()
        self.max_record_seconds.setRange(30, 1800)
        self.max_record_seconds.setSingleStep(30)
        self.max_record_seconds.setSuffix(" seconds")
        self.form.addRow("Max Recording", self.max_record_seconds)

        layout.addLayout(self.form)

        self._download_status = QLabel("")
        self._download_status.setWordWrap(True)
        self._download_status.setStyleSheet("color: #4b5563;")
        layout.addWidget(self._download_status)

        action_row = QHBoxLayout()
        test_backend_button = QPushButton("Test Backend")
        test_backend_button.clicked.connect(self._request_backend_test)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self._save_settings)
        action_row.addStretch(1)
        action_row.addWidget(test_backend_button)
        action_row.addWidget(save_button)
        layout.addStretch(1)
        layout.addLayout(action_row)
        return page

    def _load_config(self, config: Config) -> None:
        backend_label = next(
            (label for label, provider in BACKENDS.items() if provider == config.model_provider),
            "Online - Deepgram",
        )
        self.backend_combo.setCurrentText(backend_label)
        self._set_deepgram_key_saved(bool(get_deepgram_api_key()))
        self._set_combo_by_value(self.model_combo, MOONSHINE_MODELS, config.model_arch)
        self.hotkey_combo.setCurrentText(config.hotkey)
        self.sample_rate.setValue(config.sample_rate)
        self.max_record_seconds.setValue(config.max_record_seconds)
        self._sync_backend_visibility()
        self.status_detail.setText(self._current_backend_summary())

    def _current_config(self) -> Config:
        config = load_config(use_app_data=True)
        config.model_provider = BACKENDS[self.backend_combo.currentText()]
        config.model_arch = MOONSHINE_MODELS[self.model_combo.currentText()]
        config.hotkey = self.hotkey_combo.currentText()
        config.sample_rate = self.sample_rate.value()
        config.max_record_seconds = self.max_record_seconds.value()
        config.paste_mode = DESKTOP_PASTE_MODE
        config.deepgram_api_key = ""
        return config

    def _save_deepgram_key(self) -> bool:
        key = self.deepgram_key.text().strip()
        if not key and get_deepgram_api_key():
            self._set_deepgram_key_saved(True)
            return True
        try:
            set_deepgram_api_key(key)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Deepgram Key", str(exc))
            return False
        self._set_deepgram_key_saved(bool(key))
        self.statusBar().showMessage("Deepgram key saved.", 3000)
        return True

    def _test_deepgram_key(self) -> None:
        if not self._save_deepgram_key():
            return
        if get_deepgram_api_key():
            QMessageBox.information(self, "Deepgram Key", "Deepgram key is saved.")
        else:
            QMessageBox.warning(self, "Deepgram Key", "Deepgram key is empty.")

    def _save_settings(self) -> None:
        if self.deepgram_key.text().strip() and not self._save_deepgram_key():
            return

        config = self._current_config()
        errors = validate_config(config, require_deepgram_key=config.model_provider == "deepgram")
        if errors:
            QMessageBox.warning(self, "Settings", "\n".join(errors))
            return
        save_config(config, use_app_data=True)
        self.config_saved.emit(config)
        self.statusBar().showMessage("Settings saved.", 3000)
        self.status_detail.setText(self._current_backend_summary())
        self._show_home()

    def _request_download(self) -> None:
        self.download_requested.emit(MOONSHINE_MODELS[self.model_combo.currentText()])

    def _request_backend_test(self) -> None:
        config = self._current_config()
        if config.model_provider == "deepgram" and self.deepgram_key.text().strip():
            if not self._save_deepgram_key():
                return
        errors = validate_config(config, require_deepgram_key=config.model_provider == "deepgram")
        if errors:
            QMessageBox.warning(self, "Test Backend", "\n".join(errors))
            return
        QMessageBox.information(
            self,
            "Test Backend",
            "After you click OK, say a short sentence. The app will record for 3 seconds.",
        )
        self.test_requested.emit(config)

    def _edit_deepgram_key(self) -> None:
        self.deepgram_key.clear()
        self.deepgram_key_status.setVisible(False)
        self.deepgram_key.setVisible(True)
        self.save_key_button.setVisible(True)
        self.change_key_button.setVisible(False)
        self.deepgram_key.setFocus()

    def _set_deepgram_key_saved(self, saved: bool) -> None:
        self.deepgram_key.clear()
        self.deepgram_key_status.setText(
            "API key saved." if saved else "No API key saved."
        )
        self.deepgram_key_status.setVisible(True)
        self.deepgram_key.setVisible(not saved)
        self.save_key_button.setVisible(not saved)
        self.change_key_button.setVisible(saved)

    def _sync_backend_visibility(self) -> None:
        is_moonshine = BACKENDS[self.backend_combo.currentText()] == "moonshine"
        self.form.setRowVisible(self.deepgram_row, not is_moonshine)
        self.form.setRowVisible(self.model_row, is_moonshine)
        self.model_combo.setEnabled(is_moonshine)
        if self._download_button is not None:
            self._download_button.setEnabled(is_moonshine)
        if self._download_status is None:
            return
        self._download_status.setText(
            "Select a Moonshine model and download it before offline use."
            if is_moonshine
            else "Deepgram runs online and does not need a local model."
        )

    def _show_home(self) -> None:
        self.pages.setCurrentWidget(self.home_page)

    def _show_settings(self) -> None:
        self.pages.setCurrentWidget(self.settings_page)

    def show_settings_page(self) -> None:
        self._show_settings()
        self.show()
        self.raise_()
        self.activateWindow()

    def show_home_page(self) -> None:
        self._show_home()
        self.show()
        self.raise_()
        self.activateWindow()

    def _current_backend_summary(self) -> str:
        provider = BACKENDS[self.backend_combo.currentText()]
        if provider == "deepgram":
            return "Online backend: Deepgram"
        return f"Offline backend: Moonshine {self.model_combo.currentText()}"

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
        self.config = self._load_desktop_config()
        self.service = VoiceAutomationService(self.config)
        self.service.on_status_change(self.status_changed.emit)

        self.window = MainWindow(self.config)
        self.window.start_requested.connect(self.start_service)
        self.window.stop_requested.connect(self.stop_service)
        self.window.config_saved.connect(self.apply_config)
        self.window.download_requested.connect(self.download_model)
        self.window.test_requested.connect(self.test_backend)
        self.window.check_requested.connect(self.check_environment)
        self.status_changed.connect(self._set_status)

        self.tray = QSystemTrayIcon(self._build_icon(), app)
        self.tray.setToolTip("Voice Automation")
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._set_status("stopped")
        self.window.show_home_page()
        self.tray.showMessage(
            "Voice Automation",
            "Desktop app is running in the system tray.",
            QSystemTrayIcon.Information,
            3000,
        )

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        self.start_action = QAction("Start", self)
        self.stop_action = QAction("Stop", self)
        home_action = QAction("Home", self)
        settings_action = QAction("Settings", self)
        check_action = QAction("Check Environment", self)
        quit_action = QAction("Quit", self)

        self.start_action.triggered.connect(self.start_service)
        self.stop_action.triggered.connect(self.stop_service)
        home_action.triggered.connect(self.window.show_home_page)
        settings_action.triggered.connect(self.window.show_settings_page)
        check_action.triggered.connect(self.check_environment)
        quit_action.triggered.connect(self.quit)

        menu.addAction(self.start_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(home_action)
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
        config.paste_mode = DESKTOP_PASTE_MODE
        if config.model_provider == "deepgram" and not config.deepgram_api_key:
            config.deepgram_api_key = get_deepgram_api_key()
        self.config = dataclasses.replace(config)
        if self.service.is_running:
            worker = FunctionWorker(lambda: self.service.restart(self.config))
            worker.signals.error.connect(lambda message: self.status_changed.emit(f"error: {message}"))
            self.thread_pool.start(worker)
        else:
            self.service.config = self.config

    @Slot(int)
    def download_model(self, model_arch: int) -> None:
        self.window.set_download_busy(True)
        worker = FunctionWorker(lambda: download_moonshine_model(model_arch))
        worker.signals.result.connect(self.window.show_download_result)
        worker.signals.error.connect(self.window.show_download_result)
        worker.signals.finished.connect(lambda: self.window.set_download_busy(False))
        self.thread_pool.start(worker)

    def check_environment(self) -> None:
        worker = FunctionWorker(self._run_check)
        worker.signals.result.connect(self._show_check_result)
        worker.signals.error.connect(lambda message: QMessageBox.warning(self.window, "Check Environment", message))
        self.thread_pool.start(worker)

    @Slot(Config)
    def test_backend(self, config: Config) -> None:
        if config.model_provider == "deepgram" and not config.deepgram_api_key:
            config.deepgram_api_key = get_deepgram_api_key()
        self.window.statusBar().showMessage("Testing backend...", 3000)
        worker = FunctionWorker(lambda: self._run_backend_test(config))
        worker.signals.result.connect(self._show_backend_test_result)
        worker.signals.error.connect(lambda message: QMessageBox.warning(self.window, "Test Backend", message))
        self.thread_pool.start(worker)

    def quit(self) -> None:
        self.service.stop()
        self.tray.hide()
        self.app.quit()

    def _set_status(self, status: str) -> None:
        self.window.set_status(status)
        self.tray.setToolTip(f"Voice Automation - {status}")
        running = self.service.is_running
        self.start_action.setEnabled(not running)
        self.stop_action.setEnabled(running)
        self.window.set_running(running)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.window.show_home_page()

    @staticmethod
    def _load_desktop_config() -> Config:
        config = load_config(use_app_data=True)
        changed = False
        if config.paste_mode != DESKTOP_PASTE_MODE:
            config.paste_mode = DESKTOP_PASTE_MODE
            changed = True
        if config.max_record_seconds < DESKTOP_DEFAULT_MAX_RECORD_SECONDS:
            config.max_record_seconds = DESKTOP_DEFAULT_MAX_RECORD_SECONDS
            changed = True
        if changed:
            save_config(config, use_app_data=True)
        return config

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
        QMessageBox.information(self.window, "Check Environment", output)

    @staticmethod
    def _run_backend_test(config: Config) -> str:
        from voice_automation.audio import AudioCapture
        from voice_automation.stt import create_stt_adapter

        audio = AudioCapture(
            sample_rate=config.sample_rate,
            chunk_ms=config.chunk_ms,
            max_record_seconds=5,
        )
        audio.start_recording()
        time.sleep(3)
        samples = audio.stop_recording()
        if len(samples) == 0:
            return "No audio was captured."

        if config.model_provider == "deepgram":
            adapter = create_stt_adapter(
                "deepgram",
                api_key=config.deepgram_api_key,
                sample_rate=config.sample_rate,
            )
        elif config.model_provider == "moonshine":
            adapter = create_stt_adapter("moonshine", model_arch=config.model_arch)
        else:
            adapter = create_stt_adapter(config.model_provider, model_size=config.model_size)

        try:
            if not adapter.load_model():
                return "Backend did not load. Check settings and dependencies."
            transcript = adapter.transcribe(samples, config.sample_rate)
        finally:
            adapter.unload()

        if transcript:
            return f"Transcript:\n\n{transcript}"
        return "Audio captured, but no transcript was returned."

    def _show_backend_test_result(self, output: str) -> None:
        QMessageBox.information(self.window, "Test Backend", output)


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
