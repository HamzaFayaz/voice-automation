"""Reusable voice automation service for CLI and desktop entrypoints."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from voice_automation.audio import AudioCapture
from voice_automation.config import Config, validate_config
from voice_automation.hotkey import HotkeyController
from voice_automation.logger import setup_logging
from voice_automation.paste import TextInserter
from voice_automation.state import AppState, StateManager

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


class VoiceAutomationService:
    """Manage the lifecycle of the dictation engine."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = StateManager()
        self.last_error = ""

        self._lock = threading.RLock()
        self._status_callbacks: list[StatusCallback] = []
        self._running = False

        self._audio: AudioCapture | None = None
        self._stt = None
        self._hotkey: HotkeyController | None = None
        self._overlay = None

        self.state.on_state_change(self._on_engine_state_change)

    @property
    def is_running(self) -> bool:
        """Return whether the service has active runtime resources."""
        with self._lock:
            return self._running

    def on_status_change(self, callback: StatusCallback) -> None:
        """Register a callback that receives service status strings."""
        with self._lock:
            self._status_callbacks.append(callback)

    def remove_status_callback(self, callback: StatusCallback) -> None:
        """Remove a previously registered status callback."""
        with self._lock:
            try:
                self._status_callbacks.remove(callback)
            except ValueError:
                pass

    def start(self) -> None:
        """Start the voice automation engine without blocking the caller."""
        with self._lock:
            if self._running:
                return
            self.last_error = ""
            self._emit_status("starting")

            try:
                self._start_locked()
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Voice automation service failed to start")
                self._cleanup_locked()
                self._emit_status("error")
                raise

            self._running = True
            self._emit_status("running")

    def stop(self) -> None:
        """Stop the engine and release hotkey, audio, overlay, and model resources."""
        with self._lock:
            if not self._running and self._hotkey is None and self._stt is None:
                self._emit_status("stopped")
                return

            self._cleanup_locked()
            self._running = False
            self._emit_status("stopped")

    def restart(self, config: Config) -> None:
        """Restart the engine with a new configuration."""
        self.stop()
        self.config = config
        self.start()

    def wait_forever(self) -> None:
        """Block until interrupted, matching the original CLI behavior."""
        while True:
            time.sleep(0.5)

    def _start_locked(self) -> None:
        setup_logging()

        validation_errors = validate_config(
            self.config,
            require_deepgram_key=self.config.model_provider == "deepgram",
        )
        if validation_errors:
            raise RuntimeError("; ".join(validation_errors))

        from voice_automation.orchestrator import _Orchestrator, _load_model

        logger.info("Voice Automation starting up")
        logger.info(
            "Config: hotkey=%s provider=%s paste=%s language=%s",
            self.config.hotkey,
            self.config.model_provider,
            self.config.paste_mode,
            self.config.language,
        )

        self._audio = AudioCapture(
            sample_rate=self.config.sample_rate,
            chunk_ms=self.config.chunk_ms,
            max_record_seconds=self.config.max_record_seconds,
        )
        inserter = TextInserter(
            paste_mode=self.config.paste_mode,
            clipboard_restore_delay=self.config.clipboard_restore_delay,
        )

        self._stt = _load_model(self.config)
        self._stt.set_state_manager(self.state)

        orch = _Orchestrator(self.config, self.state, self._audio, self._stt, inserter)
        self._hotkey = HotkeyController(
            hotkey_name=self.config.hotkey,
            on_press=orch.on_press,
            on_release=orch.on_release,
        )
        self._hotkey.start()

        try:
            from voice_automation.overlay import DictationOverlay

            self._overlay = DictationOverlay(self.state, self._audio)
            self._overlay.start()
            logger.info("Dictation overlay HUD started")
        except Exception as exc:
            self._overlay = None
            logger.warning("Could not start dictation overlay HUD: %s", exc)

    def _cleanup_locked(self) -> None:
        if self._overlay is not None:
            try:
                self._overlay.stop()
                logger.info("Dictation overlay HUD stopped")
            except Exception:
                logger.exception("Error stopping dictation overlay HUD")
            finally:
                self._overlay = None

        if self._hotkey is not None:
            try:
                self._hotkey.stop()
                logger.info("Hotkey listener stopped")
            except Exception:
                logger.exception("Error stopping hotkey listener")
            finally:
                self._hotkey = None

        if self._audio is not None and self._audio.is_recording:
            try:
                self._audio.stop_recording()
                logger.info("In-progress recording stopped")
            except Exception:
                logger.exception("Error stopping active recording")

        if self._stt is not None:
            try:
                self._stt.unload()
                logger.info("STT model unloaded")
            except Exception:
                logger.exception("Error unloading STT model")
            finally:
                self._stt = None

        self._audio = None
        self.state.reset()
        logger.info("Voice Automation shut down cleanly")

    def _on_engine_state_change(self, old_state: AppState, new_state: AppState) -> None:
        if new_state is AppState.IDLE and self.is_running:
            self._emit_status("running")
            return
        self._emit_status(new_state.value)

    def _emit_status(self, status: str) -> None:
        callbacks = list(self._status_callbacks)
        for callback in callbacks:
            try:
                callback(status)
            except Exception:
                logger.exception("Status callback failed: %r", callback)
