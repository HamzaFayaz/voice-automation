"""PySide6 desktop tray application for Voice Automation."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Pre-import pynput BEFORE PySide6 to avoid a conflict where PySide6's
# shiboken intercepts the `six` module and breaks pynput's import chain.
try:
    import pynput.keyboard  # noqa: F401
except Exception:
    pass

from voice_automation.config import (
    Config,
    get_app_config_path,
    get_deepgram_api_key,
    load_config,
    save_config,
    set_deepgram_api_key,
    validate_config,
)
from voice_automation.downloader import (
    get_default_moonshine_cache_dir,
    is_moonshine_model_downloaded,
    ModelDownloadProgress,
    ModelDownloadResult,
    download_moonshine_model,
)
from voice_automation.service import VoiceAutomationService
from voice_automation import __version__

try:
    from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSizePolicy,
        QSpinBox,
        QStackedWidget,
        QStatusBar,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
        QScrollArea,
        QFrame,
        QGroupBox,
        QRadioButton,
        QButtonGroup,
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
    progress = Signal(object)
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


class ModelDownloadWorker(QRunnable):
    """Run a cancellable model download on the Qt thread pool."""

    def __init__(self, model_arch: int, cache_dir: str) -> None:
        super().__init__()
        self.model_arch = model_arch
        self.cache_dir = cache_dir
        self.cancel_event = threading.Event()
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = download_moonshine_model(
                self.model_arch,
                cache_dir=self.cache_dir,
                cancel_event=self.cancel_event,
                progress_callback=self.signals.progress.emit,
            )
            self.signals.result.emit(result)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class DownloadDialog(QDialog):
    """Modal model-download progress dialog with background minimization support."""

    minimize_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, model_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Downloading Model")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(440)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        self.title_label = QLabel(f"Downloading {model_name}")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")

        self.detail_label = QLabel("Initializing download...")
        self.detail_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet("QProgressBar { border: 1px solid #2d2d34; border-radius: 4px; text-align: center; } QProgressBar::chunk { background-color: #10b981; }")

        self.status_label = QLabel("Preparing download...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #a1a1aa;")

        self.minimize_button = QPushButton("Minimize to Background")
        self.minimize_button.clicked.connect(self.minimize_requested.emit)
        
        self.cancel_button = QPushButton("Cancel Download")
        self.cancel_button.clicked.connect(self._request_cancel)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.minimize_button)
        button_row.addWidget(self.cancel_button)

        layout.addWidget(self.title_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addLayout(button_row)

    def update_progress(self, progress: ModelDownloadProgress) -> None:
        self.detail_label.setText(
            f"Downloaded {progress.completed_files} of {progress.total_files} files."
        )
        if progress.total_files:
            current_fraction = 0.0
            if progress.total_bytes:
                current_fraction = min(
                    progress.current_bytes / progress.total_bytes,
                    1.0,
                )
            overall = (
                (progress.completed_files + current_fraction)
                / progress.total_files
            )
            self.progress.setValue(max(0, min(int(overall * 100), 100)))
        if progress.current_file and progress.total_bytes:
            mb_done = progress.current_bytes / (1024 * 1024)
            mb_total = progress.total_bytes / (1024 * 1024)
            self.status_label.setText(
                f"{progress.status}: {mb_done:.1f} MB of {mb_total:.1f} MB"
            )
        elif progress.status:
            self.status_label.setText(progress.status)

    def _request_cancel(self) -> None:
        answer = QMessageBox.question(
            self,
            "Cancel Download",
            "Are you sure you want to cancel the model download? Completed files will be preserved.",
        )
        if answer != QMessageBox.Yes:
            return
        self.cancel_button.setEnabled(False)
        self.minimize_button.setEnabled(False)
        self.status_label.setText("Cancelling download...")
        self.cancel_requested.emit()

    def mark_finished(self, message: str, success: bool) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if success else 0)
        self.status_label.setText(message)
        self.minimize_button.setEnabled(False)
        self.cancel_button.setEnabled(False)


class MainWindow(QMainWindow):
    """Main desktop window with Home and Settings pages."""

    start_requested = Signal()
    stop_requested = Signal()
    config_saved = Signal(Config)
    download_requested = Signal(int)
    test_requested = Signal(Config)
    check_requested = Signal()
    diagnostics_requested = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.setWindowTitle("Voice Automation")
        self.setMinimumSize(560, 460)

        self._download_button: QPushButton | None = None
        self._download_status: QLabel | None = None
        self._model_dirs: dict[str, str] = {}

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
            self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #ef4444;")
            self.status_detail.setText(status)
        elif status == "running":
            self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #10b981;")
            self.status_detail.setText(
                f"Using {self._current_backend_summary()}. Hold the configured key to dictate."
            )
        elif status == "recording":
            self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #3b82f6;")
            self.status_detail.setText(
                f"Recording with {self._current_backend_summary()}."
            )
        elif status == "transcribing":
            self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #f59e0b;")
            self.status_detail.setText(
                f"Transcribing with {self._current_backend_summary()}."
            )
        else:
            self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #71717a;")
            self.status_detail.setText(self._current_backend_summary())

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def set_controls_busy(self, busy: bool) -> None:
        if busy:
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)

    def set_readiness_issues(self, issues: list[str]) -> None:
        if not issues:
            self.readiness_label.setText("")
            self.start_button.setToolTip("")
            return

        self.readiness_label.setText("Setup required: " + " ".join(issues))
        self.start_button.setToolTip("Finish setup in Settings before starting dictation.")

    def set_download_busy(self, busy: bool, minimized: bool = False) -> None:
        if self._download_button is not None:
            if busy:
                if minimized:
                    self._download_button.setEnabled(True)
                    self._download_button.setText("Show Progress")
                else:
                    self._download_button.setEnabled(False)
                    self._download_button.setText("Downloading...")
            else:
                self._download_button.setEnabled(True)
                self._download_button.setText("Download")
        if busy and self._download_status is not None:
            self._download_status.setText("Downloading...")

    def show_download_result(self, result: ModelDownloadResult | str) -> None:
        if self._download_status is None:
            return
        self._download_status.setText(result.message if isinstance(result, ModelDownloadResult) else result)

    def _build_home_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(35, 35, 35, 35)
        layout.setSpacing(20)

        title = QLabel("Voice Automation")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 700; color: #ffffff;")

        self.status_label = QLabel("Stopped")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 40px; font-weight: 700; color: #71717a;")

        self.status_detail = QLabel("")
        self.status_detail.setAlignment(Qt.AlignCenter)
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("font-size: 14px; color: #a1a1aa;")

        self.readiness_label = QLabel("")
        self.readiness_label.setAlignment(Qt.AlignCenter)
        self.readiness_label.setWordWrap(True)
        self.readiness_label.setStyleSheet("font-size: 13px; color: #f59e0b; font-weight: 500;")

        button_row = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.start_button.setProperty("class", "PrimaryButton")
        self.stop_button.setProperty("class", "DangerButton")
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
        diagnostics_button = QPushButton("Diagnostics")
        diagnostics_button.clicked.connect(self.diagnostics_requested.emit)
        
        secondary_row = QHBoxLayout()
        secondary_row.addStretch(1)
        secondary_row.addWidget(check_button)
        secondary_row.addWidget(diagnostics_button)
        secondary_row.addWidget(settings_button)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(self.status_label)
        layout.addWidget(self.status_detail)
        layout.addWidget(self.readiness_label)
        layout.addSpacing(15)
        layout.addLayout(button_row)
        layout.addLayout(secondary_row)
        layout.addStretch(1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        # Header bar
        header_widget = QWidget()
        header_widget.setObjectName("HeaderWidget")
        header_widget.setStyleSheet("background-color: #1a1a1e; border-bottom: 1px solid #2d2d34;")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 12, 20, 12)
        
        back_button = QPushButton("Back")
        back_button.clicked.connect(self._show_home)
        
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #ffffff;")
        
        header_layout.addWidget(back_button)
        header_layout.addSpacing(15)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        
        page_layout.addWidget(header_widget)

        # Scroll Area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("SettingsScrollArea")
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(20, 15, 20, 15)
        scroll_layout.setSpacing(20)

        # 1. Backend Section
        backend_group = self._build_backend_section()
        scroll_layout.addWidget(backend_group)

        # 2. Recording Section
        recording_group = self._build_recording_section()
        scroll_layout.addWidget(recording_group)

        # 3. Advanced Section
        advanced_group = self._build_advanced_section()
        scroll_layout.addWidget(advanced_group)

        # Save Settings Button at the bottom
        bottom_row = QHBoxLayout()
        save_button = QPushButton("Save Settings")
        save_button.setProperty("class", "PrimaryButton")
        save_button.setMinimumHeight(40)
        save_button.clicked.connect(self._save_settings)
        bottom_row.addStretch(1)
        bottom_row.addWidget(save_button)
        
        scroll_layout.addLayout(bottom_row)
        scroll.setWidget(scroll_content)
        page_layout.addWidget(scroll)

        return page

    def _build_backend_section(self) -> QGroupBox:
        group = QGroupBox("Speech Backend")
        layout = QVBoxLayout(group)
        layout.setSpacing(12)

        selector_layout = QHBoxLayout()
        self.deepgram_radio = QRadioButton("Online - Deepgram API")
        self.moonshine_radio = QRadioButton("Offline - Moonshine Local")
        
        self.backend_group = QButtonGroup(self)
        self.backend_group.addButton(self.deepgram_radio)
        self.backend_group.addButton(self.moonshine_radio)
        
        selector_layout.addWidget(self.deepgram_radio)
        selector_layout.addWidget(self.moonshine_radio)
        selector_layout.addStretch(1)

        badge_layout = QHBoxLayout()
        badge_label = QLabel("Readiness:")
        self.backend_readiness_badge = QLabel("Unknown")
        self.backend_readiness_badge.setObjectName("ReadinessBadge")
        self.backend_readiness_badge.setProperty("class", "StatusBadge")
        self.backend_readiness_badge.setStyleSheet(
            "border-radius: 4px; padding: 4px 8px; font-weight: bold; background-color: #27272a; color: #a1a1aa;"
        )
        badge_layout.addWidget(badge_label)
        badge_layout.addWidget(self.backend_readiness_badge)
        badge_layout.addStretch(1)

        self.deepgram_radio.toggled.connect(self._sync_backend_visibility)
        self.moonshine_radio.toggled.connect(self._sync_backend_visibility)

        self.backend_stack = QStackedWidget()
        
        self.deepgram_panel = self._build_deepgram_panel()
        self.moonshine_panel = self._build_moonshine_panel()
        
        self.backend_stack.addWidget(self.deepgram_panel)
        self.backend_stack.addWidget(self.moonshine_panel)

        layout.addLayout(selector_layout)
        layout.addLayout(badge_layout)
        layout.addWidget(self.backend_stack)
        
        return group

    def _build_deepgram_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 0, 0)
        
        box = QFrame()
        box.setObjectName("DeepgramBox")
        box.setStyleSheet("background-color: #1a1a1e; border: 1px solid #2d2d34; border-radius: 6px;")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(15, 15, 15, 15)
        box_layout.setSpacing(12)
        
        status_row = QHBoxLayout()
        self.deepgram_key_status = QLabel("No API key saved.")
        self.deepgram_key_status.setStyleSheet("font-weight: 500; color: #a1a1aa;")
        status_row.addWidget(self.deepgram_key_status)
        status_row.addStretch(1)
        
        input_row = QHBoxLayout()
        self.deepgram_key = QLineEdit()
        self.deepgram_key.setEchoMode(QLineEdit.Password)
        self.deepgram_key.setPlaceholderText("Paste your Deepgram API Key here")
        self.deepgram_key.setMinimumHeight(32)
        
        self.save_key_button = QPushButton("Save Key")
        self.save_key_button.clicked.connect(self._save_deepgram_key)
        
        self.change_key_button = QPushButton("Change Key")
        self.change_key_button.clicked.connect(self._edit_deepgram_key)
        
        input_row.addWidget(self.deepgram_key, 1)
        input_row.addWidget(self.save_key_button)
        input_row.addWidget(self.change_key_button)
        
        action_row = QHBoxLayout()
        test_backend_button = QPushButton("Test Deepgram Backend")
        test_backend_button.clicked.connect(self._request_backend_test)
        
        info_label = QLabel(
            "Deepgram requires a cloud API key. Your key is stored securely in the Windows Credential Manager."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #71717a; font-size: 11px;")
        
        action_row.addWidget(test_backend_button)
        action_row.addStretch(1)
        
        box_layout.addLayout(status_row)
        box_layout.addLayout(input_row)
        box_layout.addLayout(action_row)
        box_layout.addWidget(info_label)
        
        layout.addWidget(box)
        return panel

    def _build_moonshine_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 0, 0)
        
        box = QFrame()
        box.setStyleSheet("background-color: #1a1a1e; border: 1px solid #2d2d34; border-radius: 6px;")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(15, 15, 15, 15)
        box_layout.setSpacing(12)
        
        form_layout = QFormLayout()
        form_layout.setLabelAlignment(Qt.AlignRight)
        
        self.model_combo = QComboBox()
        self.model_combo.addItems(MOONSHINE_MODELS.keys())
        self.model_combo.currentTextChanged.connect(self._load_cache_path_for_selected_model)
        
        form_layout.addRow("Model Size", self.model_combo)
        
        cache_layout = QHBoxLayout()
        self.cache_path = QLineEdit()
        self.cache_path.setPlaceholderText("Storage folder path")
        self.cache_path.textChanged.connect(self._update_moonshine_model_status)
        
        browse_cache_button = QPushButton("Browse")
        browse_cache_button.clicked.connect(self._browse_moonshine_cache)
        default_cache_button = QPushButton("Default")
        default_cache_button.clicked.connect(self._use_default_moonshine_cache)
        
        cache_layout.addWidget(self.cache_path, 1)
        cache_layout.addWidget(browse_cache_button)
        cache_layout.addWidget(default_cache_button)
        
        form_layout.addRow("Storage Path", cache_layout)
        box_layout.addLayout(form_layout)
        
        status_row = QHBoxLayout()
        self._download_status = QLabel("Loading status...")
        self._download_status.setWordWrap(True)
        self._download_status.setStyleSheet("color: #a1a1aa;")
        
        self._download_button = QPushButton("Download")
        self._download_button.clicked.connect(self._request_download)
        
        status_row.addWidget(self._download_status, 1)
        status_row.addWidget(self._download_button)
        box_layout.addLayout(status_row)
        
        action_row = QHBoxLayout()
        test_backend_button = QPushButton("Test Moonshine Backend")
        test_backend_button.clicked.connect(self._request_backend_test)
        action_row.addWidget(test_backend_button)
        action_row.addStretch(1)
        box_layout.addLayout(action_row)
        
        info_label = QLabel(
            "Moonshine transcribes audio locally on your CPU. The medium model provides the best accuracy."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #71717a; font-size: 11px;")
        box_layout.addWidget(info_label)
        
        layout.addWidget(box)
        return panel

    def _build_recording_section(self) -> QGroupBox:
        group = QGroupBox("Dictation & Recording")
        layout = QFormLayout(group)
        layout.setLabelAlignment(Qt.AlignRight)
        
        self.hotkey_combo = QComboBox()
        self.hotkey_combo.addItems(HOTKEYS)
        
        self.max_record_seconds = QSpinBox()
        self.max_record_seconds.setRange(30, 1800)
        self.max_record_seconds.setSingleStep(30)
        self.max_record_seconds.setSuffix(" seconds")
        
        layout.addRow("Push-To-Talk Key", self.hotkey_combo)
        layout.addRow("Max Recording", self.max_record_seconds)
        
        return group

    def _build_advanced_section(self) -> QGroupBox:
        group = QGroupBox("Advanced Settings")
        layout = QVBoxLayout(group)
        layout.setSpacing(12)
        
        form_layout = QFormLayout()
        form_layout.setLabelAlignment(Qt.AlignRight)
        
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(1000)
        self.sample_rate.setSuffix(" Hz")
        form_layout.addRow("Audio Sample Rate", self.sample_rate)
        
        config_path_label = QLabel(str(get_app_config_path()))
        config_path_label.setWordWrap(True)
        config_path_label.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        form_layout.addRow("Config File Path", config_path_label)
        
        layout.addLayout(form_layout)
        
        buttons_layout = QHBoxLayout()
        
        diagnostics_button = QPushButton("Diagnostics Report")
        diagnostics_button.clicked.connect(self.diagnostics_requested.emit)
        
        reset_button = QPushButton("Reset Settings")
        reset_button.clicked.connect(self._reset_settings)
        
        buttons_layout.addWidget(diagnostics_button)
        buttons_layout.addWidget(reset_button)
        buttons_layout.addStretch(1)
        
        layout.addLayout(buttons_layout)
        return group

    def _load_config(self, config: Config) -> None:
        self.deepgram_radio.blockSignals(True)
        self.moonshine_radio.blockSignals(True)
        if config.model_provider == "moonshine":
            self.moonshine_radio.setChecked(True)
        else:
            self.deepgram_radio.setChecked(True)
        self.deepgram_radio.blockSignals(False)
        self.moonshine_radio.blockSignals(False)

        self._set_deepgram_key_saved(bool(get_deepgram_api_key()))
        self._model_dirs = dict(config.moonshine_model_dirs)
        if config.moonshine_cache_dir:
            self._model_dirs.setdefault(str(config.model_arch), config.moonshine_cache_dir)
        self._set_combo_by_value(self.model_combo, MOONSHINE_MODELS, config.model_arch)
        self._load_cache_path_for_selected_model()
        self.hotkey_combo.setCurrentText(config.hotkey)
        self.sample_rate.setValue(config.sample_rate)
        self.max_record_seconds.setValue(config.max_record_seconds)
        self._sync_backend_visibility()
        self.status_detail.setText(self._current_backend_summary())

    def _current_config(self) -> Config:
        config = load_config(use_app_data=True)
        config.model_provider = "moonshine" if self.moonshine_radio.isChecked() else "deepgram"
        config.model_arch = MOONSHINE_MODELS[self.model_combo.currentText()]
        config.moonshine_cache_dir = self.cache_path.text().strip()
        self._model_dirs[str(config.model_arch)] = config.moonshine_cache_dir
        config.moonshine_model_dirs = dict(self._model_dirs)
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
            self._update_readiness_badge()
            return True
        try:
            set_deepgram_api_key(key)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Deepgram Key", str(exc))
            return False
        self._set_deepgram_key_saved(bool(key))
        self.statusBar().showMessage("Deepgram key saved.", 3000)
        self._update_readiness_badge()
        return True

    def _test_deepgram_key(self) -> None:
        if not self._save_deepgram_key():
            return
        if get_deepgram_api_key():
            QMessageBox.information(self, "Deepgram Key", "Deepgram key is saved and verified.")
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
        if self._download_button.text() == "Show Progress":
            self.download_requested.emit(MOONSHINE_MODELS[self.model_combo.currentText()])
            return
        installed, message = is_moonshine_model_downloaded(
            MOONSHINE_MODELS[self.model_combo.currentText()],
            self.cache_path.text().strip(),
        )
        if installed:
            self.show_download_result(message)
            return
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
        self._update_readiness_badge()

    def _set_deepgram_key_saved(self, saved: bool) -> None:
        self.deepgram_key.clear()
        self.deepgram_key_status.setText(
            "API key saved & secured." if saved else "No API key saved."
        )
        self.deepgram_key_status.setVisible(True)
        self.deepgram_key.setVisible(not saved)
        self.save_key_button.setVisible(not saved)
        self.change_key_button.setVisible(saved)

    def _sync_backend_visibility(self) -> None:
        is_moonshine = self.moonshine_radio.isChecked()
        self.backend_stack.setCurrentIndex(1 if is_moonshine else 0)
        self.model_combo.setEnabled(is_moonshine)
        if is_moonshine:
            self._update_moonshine_model_status()
        else:
            if self._download_status is not None:
                self._download_status.setText("Deepgram runs online and does not need a local model.")
        self._update_readiness_badge()

    def _browse_moonshine_cache(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Moonshine Model Storage",
            self.cache_path.text().strip() or str(get_default_moonshine_cache_dir()),
        )
        if folder:
            self.cache_path.setText(folder)

    def _use_default_moonshine_cache(self) -> None:
        self.cache_path.setText(str(get_default_moonshine_cache_dir()))

    def _load_cache_path_for_selected_model(self) -> None:
        model_arch = MOONSHINE_MODELS[self.model_combo.currentText()]
        saved_path = self._model_dirs.get(str(model_arch), "")
        self.cache_path.blockSignals(True)
        self.cache_path.setText(saved_path or str(get_default_moonshine_cache_dir()))
        self.cache_path.blockSignals(False)
        self._update_moonshine_model_status()

    def _update_moonshine_model_status(self) -> None:
        if self._download_status is None or self._download_button is None:
            return
        if not self.moonshine_radio.isChecked():
            return
        model_arch = MOONSHINE_MODELS[self.model_combo.currentText()]
        cache_dir = self.cache_path.text().strip()
        self._model_dirs[str(model_arch)] = cache_dir
        installed, message = is_moonshine_model_downloaded(model_arch, cache_dir)
        self._download_button.setEnabled(not installed)
        self._download_button.setText("Downloaded" if installed else "Download Model")
        self._download_status.setText(
            message if installed else f"Download required. {message}"
        )
        self._update_readiness_badge()

    def _update_readiness_badge(self) -> None:
        if self.deepgram_radio.isChecked():
            saved_key = get_deepgram_api_key()
            if saved_key:
                self.backend_readiness_badge.setText("Ready")
                self.backend_readiness_badge.setStyleSheet(
                    "border-radius: 4px; padding: 4px 8px; font-weight: bold; background-color: #10b981; color: #ffffff;"
                )
            else:
                self.backend_readiness_badge.setText("Needs API Key")
                self.backend_readiness_badge.setStyleSheet(
                    "border-radius: 4px; padding: 4px 8px; font-weight: bold; background-color: #f59e0b; color: #ffffff;"
                )
        else:
            model_arch = MOONSHINE_MODELS[self.model_combo.currentText()]
            cache_dir = self.cache_path.text().strip()
            installed, message = is_moonshine_model_downloaded(model_arch, cache_dir)
            if installed:
                self.backend_readiness_badge.setText("Ready")
                self.backend_readiness_badge.setStyleSheet(
                    "border-radius: 4px; padding: 4px 8px; font-weight: bold; background-color: #10b981; color: #ffffff;"
                )
            else:
                self.backend_readiness_badge.setText("Needs Download")
                self.backend_readiness_badge.setStyleSheet(
                    "border-radius: 4px; padding: 4px 8px; font-weight: bold; background-color: #f59e0b; color: #ffffff;"
                )

    def _reset_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset Settings",
            "Are you sure you want to reset all settings to defaults? This will not clear your saved Deepgram key.",
        )
        if answer != QMessageBox.Yes:
            return
        
        default_cfg = Config()
        self._load_config(default_cfg)
        save_config(default_cfg, use_app_data=True)
        self.config_saved.emit(default_cfg)
        self.statusBar().showMessage("Settings reset to default.", 3000)

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
        if self.deepgram_radio.isChecked():
            return "Deepgram online"
        return f"Moonshine {self.model_combo.currentText()} local"

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
        self.download_dialog: DownloadDialog | None = None
        self.download_worker: ModelDownloadWorker | None = None
        self._download_minimized = False
        self._active_download_arch: int | None = None
        self._service_busy = False
        self._readiness_issues: list[str] = []

        icon = self._build_icon()
        self.app.setWindowIcon(icon)

        self.window = MainWindow(self.config)
        self.window.setWindowIcon(icon)
        self.window.start_requested.connect(self.start_service)
        self.window.stop_requested.connect(self.stop_service)
        self.window.config_saved.connect(self.apply_config)
        self.window.download_requested.connect(self.download_model)
        self.window.test_requested.connect(self.test_backend)
        self.window.check_requested.connect(self.check_environment)
        self.window.diagnostics_requested.connect(self.show_diagnostics)
        self.status_changed.connect(self._set_status)

        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("Voice Automation")
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._refresh_readiness()
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
        diagnostics_action = QAction("Diagnostics", self)
        quit_action = QAction("Quit", self)

        self.start_action.triggered.connect(self.start_service)
        self.stop_action.triggered.connect(self.stop_service)
        home_action.triggered.connect(self.window.show_home_page)
        settings_action.triggered.connect(self.window.show_settings_page)
        check_action.triggered.connect(self.check_environment)
        diagnostics_action.triggered.connect(self.show_diagnostics)
        quit_action.triggered.connect(self.quit)

        menu.addAction(self.start_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(home_action)
        menu.addAction(settings_action)
        menu.addAction(check_action)
        menu.addAction(diagnostics_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def start_service(self) -> None:
        if self.service.is_running or self._service_busy:
            return
        issues = self._refresh_readiness()
        if issues:
            QMessageBox.warning(
                self.window,
                "Setup Required",
                "Finish setup before starting dictation:\n\n" + "\n".join(issues),
            )
            self.window.show_settings_page()
            return
        self._service_busy = True
        self.status_changed.emit("starting")
        worker = FunctionWorker(self.service.start)
        worker.signals.error.connect(lambda message: self.status_changed.emit(f"error: {message}"))
        worker.signals.finished.connect(self._service_action_finished)
        self.thread_pool.start(worker)

    def stop_service(self) -> None:
        if self._service_busy:
            return
        self._service_busy = True
        self.status_changed.emit("stopping")
        worker = FunctionWorker(self.service.stop)
        worker.signals.error.connect(lambda message: self.status_changed.emit(f"error: {message}"))
        worker.signals.finished.connect(self._service_action_finished)
        self.thread_pool.start(worker)

    @Slot(Config)
    def apply_config(self, config: Config) -> None:
        config.paste_mode = DESKTOP_PASTE_MODE
        if config.model_provider == "deepgram" and not config.deepgram_api_key:
            config.deepgram_api_key = get_deepgram_api_key()
        self.config = dataclasses.replace(config)
        issues = self._refresh_readiness()
        if self.service.is_running:
            if issues:
                QMessageBox.warning(
                    self.window,
                    "Settings Saved",
                    "Settings were saved, but they were not applied to the running dictation service because setup is incomplete.",
                )
                self._set_status("running")
                return
            if self._service_busy:
                QMessageBox.warning(
                    self.window,
                    "Settings",
                    "Wait for the current start or stop operation to finish before applying settings.",
                )
                return
            self._service_busy = True
            self.status_changed.emit("restarting")
            worker = FunctionWorker(lambda: self.service.restart(self.config))
            worker.signals.error.connect(lambda message: self.status_changed.emit(f"error: {message}"))
            worker.signals.finished.connect(self._service_action_finished)
            self.thread_pool.start(worker)
        else:
            self.service.config = self.config
            self._set_status("stopped")

    @Slot(int)
    def download_model(self, model_arch: int) -> None:
        if self.download_dialog is not None:
            if self._download_minimized:
                self._download_minimized = False
                self.window.set_download_busy(True, minimized=False)
                self.download_dialog.show()
                self.download_dialog.raise_()
                self.download_dialog.activateWindow()
            else:
                self.window.show_download_result("A model download is already running.")
            return

        model_name = next(
            (name for name, arch in MOONSHINE_MODELS.items() if arch == model_arch),
            f"Model {model_arch}",
        )
        self._download_minimized = False
        self.download_dialog = DownloadDialog(model_name, self.window)
        self.download_dialog.minimize_requested.connect(self._minimize_download)
        self.download_dialog.cancel_requested.connect(self._cancel_model_download)
        self.download_dialog.show()

        self.window.set_download_busy(True, minimized=False)
        worker = ModelDownloadWorker(model_arch, self.window.cache_path.text().strip())
        self._active_download_arch = model_arch
        self.download_worker = worker
        worker.signals.progress.connect(self._update_model_download_progress)
        worker.signals.result.connect(self._finish_model_download)
        worker.signals.error.connect(self._finish_model_download)
        worker.signals.finished.connect(lambda: self.window.set_download_busy(False))
        self.thread_pool.start(worker)

    def _minimize_download(self) -> None:
        self._download_minimized = True
        if self.download_dialog is not None:
            self.download_dialog.hide()
        self.window.set_download_busy(True, minimized=True)
        self.tray.showMessage(
            "Moonshine Download",
            "Model download is running in the background. You can restore progress via the Settings screen.",
            QSystemTrayIcon.Information,
            3000,
        )

    def _cancel_model_download(self) -> None:
        if self.download_worker is not None:
            self.download_worker.cancel()

    def _update_model_download_progress(self, progress: ModelDownloadProgress) -> None:
        if self.download_dialog is not None:
            self.download_dialog.update_progress(progress)

    def _finish_model_download(self, result: ModelDownloadResult | str) -> None:
        success = isinstance(result, ModelDownloadResult) and result.success
        cancelled = isinstance(result, ModelDownloadResult) and result.cancelled
        message = result.message if isinstance(result, ModelDownloadResult) else str(result)

        if self.download_dialog is not None:
            self.download_dialog.mark_finished(message, success)
            if not self._download_minimized:
                QMessageBox.information(
                    self.download_dialog,
                    (
                        "Moonshine Download"
                        if success
                        else "Moonshine Download Cancelled"
                        if cancelled
                        else "Moonshine Download Failed"
                    ),
                    message,
                )
            self.download_dialog.close()
            self.download_dialog = None

        self.download_worker = None
        if success and self._active_download_arch is not None:
            self.window._model_dirs[str(self._active_download_arch)] = (
                self.window.cache_path.text().strip()
            )
            config = self.window._current_config()
            save_config(config, use_app_data=True)
            self.apply_config(config)
        self._active_download_arch = None
        self.window.setEnabled(True)
        self.window.show_download_result(result)
        self.window._update_moonshine_model_status()
        self.tray.showMessage(
            (
                "Moonshine Download Complete"
                if success
                else "Moonshine Download Cancelled"
                if cancelled
                else "Moonshine Download Failed"
            ),
            message,
            QSystemTrayIcon.Information if success or cancelled else QSystemTrayIcon.Warning,
            5000,
        )

    def check_environment(self) -> None:
        worker = FunctionWorker(self._run_check)
        worker.signals.result.connect(self._show_check_result)
        worker.signals.error.connect(lambda message: QMessageBox.warning(self.window, "Check Environment", message))
        self.thread_pool.start(worker)

    def show_diagnostics(self) -> None:
        QMessageBox.information(
            self.window,
            "Diagnostics",
            self._build_diagnostics_text(),
        )

    @Slot(Config)
    def test_backend(self, config: Config) -> None:
        if self.service.is_running or self._service_busy:
            QMessageBox.warning(
                self.window,
                "Test Backend",
                "Stop dictation before testing a backend. The test needs exclusive microphone access.",
            )
            return
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
        ready = not self._readiness_issues
        self.start_action.setEnabled(not running and not self._service_busy and ready)
        self.stop_action.setEnabled(running and not self._service_busy)
        self.window.set_running(running)
        self.window.set_controls_busy(self._service_busy)
        if not running and not self._service_busy and self._readiness_issues:
            self.window.start_button.setEnabled(False)

    def _service_action_finished(self) -> None:
        self._service_busy = False
        if not self.service.is_running:
            self.service.config = self.config
        self._refresh_readiness()
        if self.service.last_error and not self.service.is_running:
            self._set_status(f"error: {self.service.last_error}")
            return
        self._set_status("running" if self.service.is_running else "stopped")

    def _refresh_readiness(self) -> list[str]:
        self._readiness_issues = self._get_readiness_issues(self.config)
        self.window.set_readiness_issues(self._readiness_issues)
        return self._readiness_issues

    @staticmethod
    def _get_readiness_issues(config: Config) -> list[str]:
        errors = validate_config(
            config,
            require_deepgram_key=config.model_provider == "deepgram",
        )
        issues = [f"- {error}" for error in errors]
        if config.model_provider == "moonshine":
            installed, message = is_moonshine_model_downloaded(
                config.model_arch,
                config.get_moonshine_cache_dir(),
            )
            if not installed:
                issues.append(f"- Moonshine model is not ready. {message}")
        return issues

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
        """Load the application icon from the assets directory, with a fallback."""
        icon_path = Path(__file__).parent / "assets" / "icon.ico"
        if icon_path.exists():
            return QIcon(str(icon_path))
        # Fallback: draw a simple icon programmatically
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
        from voice_automation.config import load_config

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            passed = run_checks(load_config(use_app_data=True))
        result = _strip_ansi(buffer.getvalue()).strip()
        return result if passed else result + "\n\nOne or more checks failed."

    def _show_check_result(self, output: str) -> None:
        QMessageBox.information(self.window, "Check Environment", output)

    def _build_diagnostics_text(self) -> str:
        lines = [
            f"Voice Automation {__version__}",
            f"Status: {'running' if self.service.is_running else 'stopped'}",
            f"Config: {get_app_config_path()}",
            f"Backend: {self.config.model_provider}",
            f"Hotkey: {self.config.hotkey}",
            f"Sample rate: {self.config.sample_rate} Hz",
            f"Max recording: {self.config.max_record_seconds} seconds",
        ]
        if self.config.model_provider == "deepgram":
            lines.append(
                "Deepgram key: saved"
                if get_deepgram_api_key()
                else "Deepgram key: missing"
            )
        else:
            installed, message = is_moonshine_model_downloaded(
                self.config.model_arch,
                self.config.get_moonshine_cache_dir(),
            )
            lines.extend(
                [
                    f"Moonshine model: {self.config.model_arch}",
                    f"Model storage: {self.config.get_moonshine_cache_dir() or get_default_moonshine_cache_dir()}",
                    f"Model ready: {'yes' if installed else 'no'}",
                    f"Model detail: {message}",
                ]
            )
        if self._readiness_issues:
            lines.append("")
            lines.append("Setup issues:")
            lines.extend(self._readiness_issues)
        return "\n".join(lines)

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
        else:  # moonshine
            adapter = create_stt_adapter(
                "moonshine",
                model_arch=config.model_arch,
                sample_rate=config.sample_rate,
                cache_dir=config.get_moonshine_cache_dir(),
            )

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


DARK_THEME_QSS = """
/* Base / global styles */
QWidget {
    background-color: #121214;
    color: #e4e4e7;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 13px;
}

QStatusBar {
    background-color: #1a1a1e;
    color: #a1a1aa;
    border-top: 1px solid #2d2d34;
}

/* Scroll Area styling */
QScrollArea {
    border: none;
    background-color: #121214;
}
QScrollArea > QWidget > QWidget {
    background-color: #121214;
}

/* Scrollbar styling */
QScrollBar:vertical {
    border: none;
    background: #18181b;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #3f3f46;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #52525b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
    height: 0px;
}

/* GroupBox Section styling */
QGroupBox {
    background-color: #1a1a1e;
    border: 1px solid #2d2d34;
    border-radius: 8px;
    margin-top: 24px;
    padding-top: 20px;
    font-weight: bold;
    font-size: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 2px 6px;
    color: #10b981;
}

/* Frames / Cards styling */
QFrame {
    border: none;
}

/* Labels */
QLabel {
    background: transparent;
}

/* Input elements styling */
QLineEdit, QSpinBox, QComboBox {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 6px 12px;
    color: #f4f4f5;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #10b981;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: none;
}

/* Radio buttons */
QRadioButton {
    spacing: 8px;
    font-weight: 500;
}
QRadioButton::indicator {
    width: 18px;
    height: 18px;
}
QRadioButton::indicator::unchecked {
    border: 2px solid #3f3f46;
    border-radius: 9px;
    background-color: #1e1e22;
}
QRadioButton::indicator::checked {
    border: 2px solid #10b981;
    border-radius: 9px;
    background-color: #10b981;
}

/* Push buttons styling */
QPushButton {
    background-color: #27272a;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    padding: 8px 16px;
    color: #f4f4f5;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #3f3f46;
    border-color: #52525b;
}
QPushButton:pressed {
    background-color: #18181b;
}
QPushButton:disabled {
    background-color: #18181b;
    border-color: #27272a;
    color: #71717a;
}

QPushButton[class="PrimaryButton"] {
    background-color: #10b981;
    border: 1px solid #059669;
    color: #ffffff;
}
QPushButton[class="PrimaryButton"]:hover {
    background-color: #059669;
}
QPushButton[class="PrimaryButton"]:pressed {
    background-color: #047857;
}

QPushButton[class="DangerButton"] {
    background-color: #ef4444;
    border: 1px solid #dc2626;
    color: #ffffff;
}
QPushButton[class="DangerButton"]:hover {
    background-color: #dc2626;
}
QPushButton[class="DangerButton"]:pressed {
    background-color: #b91c1c;
}
"""


def _strip_ansi(text: str) -> str:
    """Remove terminal color/control sequences before showing text in Qt."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def main() -> int:
    """Run the desktop application."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "VoiceAutomation.Desktop"
        )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(DARK_THEME_QSS)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Voice Automation", "System tray is not available.")
        return 1

    desktop = DesktopApp(app)
    app.aboutToQuit.connect(desktop.service.stop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
